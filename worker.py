"""
worker.py — фоновый процесс (отдельный сервис на Railway).

Периодически опрашивает WB API по всем магазинам из wb_api_keys,
отслеживает переход поставок из "на сборке" в "в доставке" (done: false -> true)
и при обнаружении шлёт в Telegram детальное сообщение с составом и суммой поставки.

Состояние (какие поставки уже видели / по каким уже отправлено уведомление)
хранится в JSON-файле state.json рядом со скриптом, чтобы не слать повторные
уведомления при рестарте процесса.
"""

import io
import json
import os
import time
import traceback
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import gspread
import toml
from google.oauth2.service_account import Credentials

# =========================================================
# КОНФИГ
# =========================================================

POLL_INTERVAL_SECONDS = 5 * 60  # как часто опрашивать WB внутри рабочего окна (раз в 5 минут)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Рабочее окно опроса — вне этого диапазона воркер просто спит.
ACTIVE_HOUR_START = 16  # 16:00
ACTIVE_HOUR_END = 19    # 19:00 (не включительно)
TIMEZONE = ZoneInfo("Asia/Almaty")

DEFAULT_FF_COST = 400
WB_SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]

# Магазин в интерфейсе -> ключ в secrets["wb_api_keys"]
# ВАЖНО: держим синхронно с main.py
WB_SHOP_TO_SECRET_KEY = {
    "Абеденов": "Абеденов",
    "Bastau": "Bastau",
}


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


# =========================================================
# SECRETS
# =========================================================

def load_secrets() -> dict:
    """
    Читает тот же TOML, что и Streamlit-приложение — из переменной окружения
    SECRETS_TOML (см. railway.toml), либо из локального .streamlit/secrets.toml
    при разработке.
    """
    raw = os.environ.get("SECRETS_TOML")
    if raw:
        return toml.loads(raw)

    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "secrets.toml")
    if os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            return toml.load(f)

    raise RuntimeError("Не найдены secrets: ни SECRETS_TOML, ни .streamlit/secrets.toml")


SECRETS = load_secrets()


# =========================================================
# STATE (какие поставки уже видели / по каким уведомили)
# =========================================================

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log("⚠️ Не удалось прочитать state.json, начинаю с чистого состояния")
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# =========================================================
# GOOGLE SHEETS (прайсы)
# =========================================================

_price_cache: dict[str, tuple[float, pd.DataFrame]] = {}
PRICE_CACHE_TTL = 300  # секунд


def load_prices(shop_name: str) -> pd.DataFrame | None:
    now = time.time()
    cached = _price_cache.get(shop_name)
    if cached and now - cached[0] < PRICE_CACHE_TTL:
        return cached[1]

    try:
        creds = Credentials.from_service_account_info(
            dict(SECRETS["gcp_service_account"]), scopes=GSHEETS_SCOPES
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(SECRETS["sheet_url"])
        worksheet = spreadsheet.worksheet(shop_name)
        df = pd.DataFrame(worksheet.get_all_records())
        _price_cache[shop_name] = (now, df)
        return df
    except Exception as e:
        log(f"❌ Ошибка загрузки прайса для {shop_name}: {e}")
        return None


# =========================================================
# WILDBERRIES API
# =========================================================

def wb_get(url: str, api_key: str, params: dict | None = None) -> requests.Response:
    headers = {"Authorization": api_key.strip()}
    return requests.get(url, headers=headers, params=params, timeout=20)


def list_supplies(api_key: str) -> list[dict]:
    """
    Возвращает список поставок продавца (активные + недавно закрытые).
    Пагинация по курсору 'next', как в остальных методах WB API v3.
    """
    supplies = []
    next_cursor = 0
    for _ in range(20):  # защита от бесконечного цикла
        res = wb_get(
            "https://marketplace-api.wildberries.ru/api/v3/supplies",
            api_key,
            params={"limit": 1000, "next": next_cursor},
        )
        if res.status_code != 200:
            log(f"⚠️ Ошибка получения списка поставок: {res.status_code} {res.text[:200]}")
            break

        data = res.json()
        # WB может вернуть либо {"supplies": [...], "next": N}, либо просто список —
        # обрабатываем оба варианта на случай различий в версии API.
        if isinstance(data, dict):
            batch = data.get("supplies", [])
            new_next = data.get("next", 0)
        else:
            batch = data
            new_next = 0

        supplies.extend(batch)

        if not batch or new_next == next_cursor or new_next == 0:
            break
        next_cursor = new_next

    return supplies


def get_supply_orders_detail(supply_id: str, api_key: str) -> pd.DataFrame | None:
    """Состав поставки: артикул + количество."""
    res = wb_get(
        f"https://marketplace-api.wildberries.ru/api/v3/supplies/{supply_id}/orders",
        api_key,
    )
    if res.status_code != 200:
        log(f"⚠️ Не удалось получить состав поставки {supply_id}: {res.status_code}")
        return None

    orders = res.json().get("orders", [])
    if not orders:
        return None

    df = pd.DataFrame(orders)
    summary = df["article"].value_counts().reset_index()
    summary.columns = ["Артикул", "Заказ (уп)"]
    return summary


# =========================================================
# РАСЧЁТ СУММЫ
# =========================================================

def calculate(summary: pd.DataFrame, df_prices: pd.DataFrame, ff_rate: float) -> dict | None:
    res = pd.merge(
        summary,
        df_prices[["Артикул", "Количество в упаковке", "Цена за штуку"]],
        on="Артикул",
        how="left",
    )
    res = res.dropna(subset=["Цена за штуку"])
    if res.empty:
        return None

    res["Всего шт"] = res["Заказ (уп)"] * res["Количество в упаковке"]
    res["Цена товара"] = res["Всего шт"] * res["Цена за штуку"]

    total_packs = int(res["Заказ (уп)"].sum())
    total_items_cost = float(res["Цена товара"].sum())
    total_ff = total_packs * ff_rate
    grand_total = total_items_cost + total_ff

    return {
        "rows": res.to_dict("records"),
        "total_packs": total_packs,
        "total_items_cost": total_items_cost,
        "total_ff": total_ff,
        "grand_total": grand_total,
    }


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_message(text: str):
    tg = SECRETS.get("telegram", {})
    bot_token = tg.get("bot_token")
    chat_id = tg.get("chat_id")

    if not bot_token or not chat_id:
        log("❌ Не заданы telegram.bot_token / telegram.chat_id в secrets — уведомление не отправлено")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Telegram ограничивает сообщение 4096 символами — на случай длинных поставок режем.
    for chunk_start in range(0, len(text), 4000):
        chunk = text[chunk_start:chunk_start + 4000]
        res = requests.post(
            url,
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
            timeout=15,
        )
        if res.status_code != 200:
            log(f"❌ Ошибка отправки в Telegram: {res.status_code} {res.text[:300]}")


def format_supply_message(shop: str, supply_id: str, supply_name: str, calc: dict) -> str:
    lines = [
        f"📦 *Поставка передана в доставку*",
        f"🏬 Магазин: *{shop}*",
        f"🔖 Поставка: `{supply_id}`" + (f" ({supply_name})" if supply_name else ""),
        "",
        "*Состав поставки:*",
    ]
    for row in calc["rows"]:
        lines.append(
            f"• {row['Артикул']} — {int(row['Заказ (уп)'])} уп. "
            f"× {int(row['Количество в упаковке'])} = {int(row['Всего шт'])} шт. "
            f"— {row['Цена товара']:,.0f} ₸".replace(",", " ")
        )

    lines += [
        "",
        f"📦 Заказов: *{calc['total_packs']} уп.*",
        f"⚙️ Фулфилмент: *{calc['total_ff']:,.0f} ₸*".replace(",", " "),
        f"💰 Стоимость товара: *{calc['total_items_cost']:,.0f} ₸*".replace(",", " "),
        f"💵 *ИТОГО: {calc['grand_total']:,.0f} ₸*".replace(",", " "),
    ]
    return "\n".join(lines)


# =========================================================
# ОСНОВНОЙ ЦИКЛ
# =========================================================

def check_shop(shop: str, state: dict):
    secret_key = WB_SHOP_TO_SECRET_KEY.get(shop)
    if not secret_key:
        return

    api_key = SECRETS.get("wb_api_keys", {}).get(secret_key)
    if not api_key:
        return

    shop_state = state.setdefault(shop, {})

    supplies = list_supplies(api_key)
    for supply in supplies:
        supply_id = supply.get("id")
        if not supply_id:
            continue

        done = bool(supply.get("done"))
        prev = shop_state.get(supply_id, {"done": False, "notified": False})

        # Событие: поставка только что закрылась (передана в доставку), и мы ещё не уведомляли.
        if done and not prev["done"] and not prev["notified"]:
            log(f"🔔 {shop}: поставка {supply_id} перешла в статус «в доставке»")

            ff_rate = 0 if shop in WB_SHOPS_WITHOUT_FF else DEFAULT_FF_COST
            df_prices = load_prices(shop)
            order_summary = get_supply_orders_detail(supply_id, api_key)

            if df_prices is not None and order_summary is not None:
                calc = calculate(order_summary, df_prices, ff_rate)
                if calc:
                    msg = format_supply_message(shop, supply_id, supply.get("name", ""), calc)
                    send_telegram_message(msg)
                else:
                    send_telegram_message(
                        f"📦 Поставка {supply_id} ({shop}) передана в доставку, "
                        f"но не удалось сопоставить артикулы с прайсом."
                    )
            else:
                send_telegram_message(
                    f"📦 Поставка {supply_id} ({shop}) передана в доставку, "
                    f"но не удалось получить данные для расчёта суммы."
                )

            prev["notified"] = True

        prev["done"] = done
        shop_state[supply_id] = prev


def is_within_active_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(TIMEZONE)
    return ACTIVE_HOUR_START <= now.hour < ACTIVE_HOUR_END


def seconds_until_next_window(now: datetime | None = None) -> float:
    """Сколько секунд спать, если сейчас вне рабочего окна."""
    now = now or datetime.now(TIMEZONE)
    next_start = now.replace(hour=ACTIVE_HOUR_START, minute=0, second=0, microsecond=0)
    if now.hour >= ACTIVE_HOUR_END:
        next_start += timedelta(days=1)
    return max((next_start - now).total_seconds(), 0)


def main_loop():
    log(f"🚀 Воркер запущен. Рабочее окно: {ACTIVE_HOUR_START:02d}:00–{ACTIVE_HOUR_END:02d}:00 ({TIMEZONE.key})")
    state = load_state()

    while True:
        now = datetime.now(TIMEZONE)

        if not is_within_active_window(now):
            wait_seconds = seconds_until_next_window(now)
            wake_at = now + timedelta(seconds=wait_seconds)
            log(
                f"😴 Вне рабочего окна ({now.strftime('%H:%M')}). "
                f"Сплю до {wake_at.strftime('%Y-%m-%d %H:%M')}"
            )
            time.sleep(wait_seconds)
            continue

        for shop in WB_SHOP_TO_SECRET_KEY:
            try:
                check_shop(shop, state)
            except Exception:
                log(f"❌ Ошибка при обработке магазина {shop}:\n{traceback.format_exc()}")

        save_state(state)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main_loop()
