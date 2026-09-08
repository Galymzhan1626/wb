"""
FFCalculator — расчёт себестоимости поставки/заказа для Wildberries и Kaspi.kz.
"""

import time
from io import BytesIO

import requests
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials


# =========================================================
# КОНФИГУРАЦИЯ
# =========================================================

DEFAULT_FF_COST = 400
GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ---- Wildberries ----
WB_SHOPS = [
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Абеденов", "Махамбетова", "Кыдырова", "Жораев", "Pheonix"
]
WB_SHOPS_WITHOUT_FF = [
    "Диханбаев", "Хаким", "Diamond", "Fariza", "Bonitas"
]
WB_SHOP_TO_SECRET_KEY = {
    "Абеденов": "Абеденов",
    "Диханбаев": "Диханбаев",
    "Fariza": "Fariza",
    "Diamond": "Diamond",
    "Хаким": "Хаким",
    "Махамбетова": "Махамбетова",
    "Кыдырова": "Кыдырова",
    "Жораев": "Жораев",
    "Pheonix": "Pheonix"
}

st.set_page_config(page_title="Калькулятор поставок", layout="centered", page_icon="📦")
st.title("📦 Калькулятор себестоимости")
st.markdown("---")


# =========================================================
# GOOGLE SHEETS (общее для обеих платформ)
# =========================================================

@st.cache_data(ttl=300)
def load_prices_from_gsheets(shop_name: str):
    try:
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=GSHEETS_SCOPES
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(st.secrets["sheet_url"])
        worksheet = spreadsheet.worksheet(shop_name)
        df = pd.DataFrame(worksheet.get_all_records())
        return df, None
    except gspread.exceptions.WorksheetNotFound:
        return None, f"На листе Google Sheets нет вкладки «{shop_name}»."
    except Exception as e:
        return None, f"Ошибка доступа к Google Sheets: {e}"


# =========================================================
# ОБЩИЙ РАСЧЁТ И ВЫВОД
# =========================================================

def show_results(
    summary: pd.DataFrame,
    df_prices: pd.DataFrame,
    selected_shop: str,
    current_ff_rate: float,
    article_col: str,
    qty_per_pack_col: str,
    unit_cost_col: str,
    name_col: str | None = None,
    qty_col_label: str = "Заказ (шт)",
    qty_unit_word: str = "шт.",
    show_ff_line: bool = False,
    metric_label: str = "ИТОГО",
):
    """
    summary        — DataFrame с колонками [article_col, "Заказ (шт)"]
    df_prices      — прайс из Google Sheets
    article_col    — колонка-артикул, по которой считаем (общая для summary и df_prices)
    qty_per_pack_col — колонка "Количество в упаковке" в прайсе
    unit_cost_col  — колонка себестоимости за штуку в прайсе
    name_col       — колонка с названием товара в прайсе (для вывода на экран)
    qty_col_label  — подпись колонки количества в таблице ("Заказ (уп)" для WB, "Заказ (шт)" для Kaspi)
    qty_unit_word  — единица измерения в итоговой строке ("уп." для WB, "шт." для Kaspi)
    show_ff_line   — показывать ли строку "Фулфилмент" (нужна только WB)
    metric_label   — подпись итоговой суммы
    """
    price_cols = [article_col, qty_per_pack_col, unit_cost_col]
    if name_col and name_col in df_prices.columns:
        price_cols.append(name_col)

    res = pd.merge(summary, df_prices[price_cols], on=article_col, how="left")

    unmatched = res[res[unit_cost_col].isna()][article_col].tolist()
    if unmatched:
        st.warning(f"⚠️ **{len(unmatched)} SKU** не найдены в прайсе и пропущены:\n{', '.join(map(str, unmatched))}")

    res = res.dropna(subset=[unit_cost_col])

    if res.empty:
        st.error("❌ Нет данных для расчета. Проверьте артикулы.")
        return

    res["Всего шт"] = res["Заказ (шт)"] * res[qty_per_pack_col]
    res["Цена товара"] = res["Всего шт"] * res[unit_cost_col]

    # Для показа проверяющему — название товара, если есть, иначе артикул.
    display_label_col = name_col if (name_col and name_col in res.columns) else article_col
    display_cols = [display_label_col, "Заказ (шт)", "Всего шт", unit_cost_col, "Цена товара"]

    display_df = res[display_cols].rename(columns={
        display_label_col: "Товар",
        "Заказ (шт)": qty_col_label,
        unit_cost_col: "Цена за штуку",
    }).reset_index(drop=True)
    display_df.index = display_df.index + 1

    st.subheader(selected_shop)
    st.table(
        display_df.style.format({
            "Цена товара": "{:,.0f} ₸",
            "Цена за штуку": "{:,.0f} ₸",
            "Всего шт": "{:,.0f}",
            qty_col_label: "{:,.0f}",
        })
    )

    total_units = res["Заказ (шт)"].sum()
    total_items_cost = res["Цена товара"].sum()
    total_ff = total_units * current_ff_rate
    grand_total = total_items_cost + total_ff

    st.markdown("---")
    c_res1, c_res2 = st.columns(2)
    with c_res1:
        st.write(f"📦 **Заказов:** {total_units:,.0f} {qty_unit_word}")
        if show_ff_line:
            st.write(f"⚙️ **Фулфилмент:** {total_ff:,.0f} ₸")
        st.write(f"💰 **Стоимость товара:** {total_items_cost:,.0f} ₸")
    with c_res2:
        st.metric(label=metric_label, value=f"{grand_total:,.0f} ₸")

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            res[display_cols].rename(columns={display_label_col: "Товар"}).to_excel(
                writer, index=False, sheet_name="Расчет"
            )
        st.download_button(
            "⬇️ Скачать Excel",
            data=output.getvalue(),
            file_name=f"Расчет_{selected_shop}_{time.strftime('%d%m')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# =========================================================
# WILDBERRIES
# =========================================================

@st.cache_data(ttl=60)
def wb_get_supply_orders(supply_id: str, api_key: str):
    """Возвращает (summary_df, error_message). summary_df: [Артикул, Заказ (шт)]."""
    clean_id = supply_id.strip()
    headers = {"Authorization": api_key.strip()}
    url_direct = f"https://marketplace-api.wildberries.ru/api/v3/supplies/{clean_id}/orders"

    try:
        res = requests.get(url_direct, headers=headers, timeout=15)

        if res.status_code == 200:
            orders = res.json().get("orders", [])
            if orders:
                df = pd.DataFrame(orders)
                summary = df["article"].value_counts().reset_index()
                summary.columns = ["Артикул", "Заказ (шт)"]
                return summary, None

        elif res.status_code == 401:
            return None, (
                "401 Unauthorized — WB не принял токен. Проверь: категорию доступа "
                "«Маркетплейс» у ключа, отсутствие лишних пробелов в secrets, "
                "не отозван ли ключ в ЛК WB."
            )
        elif res.status_code == 403:
            return None, "403 Forbidden — токен валиден, но нет прав на этот ресурс/магазин."

        url_all = "https://marketplace-api.wildberries.ru/api/v3/orders"
        params = {"limit": 1000, "next": 0}
        res_all = requests.get(url_all, headers=headers, params=params, timeout=15)

        if res_all.status_code == 200:
            all_orders = res_all.json().get("orders", [])
            filtered = [o for o in all_orders if str(o.get("supplyId")) == clean_id]
            if filtered:
                df = pd.DataFrame(filtered)
                summary = df["article"].value_counts().reset_index()
                summary.columns = ["Артикул", "Заказ (шт)"]
                return summary, None
            return None, f"Заказы для поставки {clean_id} не найдены."

        if res_all.status_code == 401:
            return None, "401 Unauthorized на общем эндпоинте заказов — проблема в токене."

        return None, f"Ошибка API: {res_all.status_code} — {res_all.text[:300]}"

    except requests.exceptions.Timeout:
        return None, "Таймаут запроса к WB API. Попробуй ещё раз."
    except requests.exceptions.ConnectionError as e:
        return None, f"Не удалось подключиться к WB API: {e}"
    except Exception as e:
        return None, f"Ошибка: {str(e)}"


def render_wb_tab():
    col_main, col_refresh = st.columns([4, 1])
    with col_main:
        selected_shop = st.selectbox("🎯 Выберите магазин:", WB_SHOPS, key="wb_shop")
    with col_refresh:
        st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
        if st.button("🔄", help="Обновить прайс из Google Sheets", key="wb_refresh"):
            st.cache_data.clear()
            st.rerun()

    current_ff_rate = 0 if selected_shop in WB_SHOPS_WITHOUT_FF else DEFAULT_FF_COST

    with st.spinner("⏳ Синхронизация с Google Sheets..."):
        df_prices, error = load_prices_from_gsheets(selected_shop)

    if error:
        st.error(f"❌ {error}")
        return
    if df_prices is None or df_prices.empty:
        st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
        return

    st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")

    secret_key_name = WB_SHOP_TO_SECRET_KEY.get(selected_shop)
    api_key = None
    if secret_key_name:
        raw_key = st.secrets.get("wb_api_keys", {}).get(secret_key_name)
        if raw_key:
            api_key = raw_key.strip()

    if not api_key:
        st.error(f"❌ Для магазина «{selected_shop}» не задан API-ключ WB в secrets (`wb_api_keys`).")
        return

    supply_id = st.text_input("Номер поставки WB", placeholder="Например: WB-GI-123456789", key="wb_supply_id")

    if st.button("📥 Получить заказы по поставке", use_container_width=True, key="wb_fetch"):
        if not supply_id.strip():
            st.warning("⚠️ Введите номер поставки.")
        else:
            with st.spinner("Запрашиваем данные у Wildberries..."):
                summary_api, api_error = wb_get_supply_orders(supply_id, api_key)
            if api_error:
                st.error(f"❌ {api_error}")
            else:
                show_results(
                    summary_api, df_prices, selected_shop, current_ff_rate,
                    article_col="Артикул",
                    qty_per_pack_col="Количество в упаковке",
                    unit_cost_col="Цена за штуку",
                    qty_col_label="Заказ (уп)",
                    qty_unit_word="уп.",
                    show_ff_line=True,
                    metric_label="ИТОГО К ОПЛАТЕ",
                )


# =========================================================
# WILDBERRIES — импорт "Лист подбора" из файла (без API)
# =========================================================
 
def wb_parse_picking_list(file) -> pd.DataFrame:
    """
    Парсит "Лист подбора" WB, выгружаемый из ЛК продавца (например,
    WB-GI-274402158.xlsx).
 
    Особенность файла: первые 4 строки — служебные ("Дата:", "Лист подбора
    WB-GI-...", пустая строка, "Количество товаров: N"), реальная шапка
    таблицы — в 5-й строке (header=4). Каждая строка таблицы = один товар
    в одном задании на сборку ("№ задания"), поэтому количество заказанных
    штук по артикулу = число строк с этим артикулом (считаем через
    value_counts, как в API-варианте).
    """
    df_raw = pd.read_excel(file, header=4)
 
    required_cols = {"Артикул продавца"}
    missing = required_cols - set(df_raw.columns)
    if missing:
        raise ValueError(
            f"В файле не найдены колонки: {', '.join(missing)}. "
            "Проверьте, что это лист подбора WB (экспорт .xlsx из ЛК)."
        )
 
    df_raw = df_raw.dropna(subset=["Артикул продавца"])
    df_raw["Артикул продавца"] = df_raw["Артикул продавца"].astype(str).str.strip()
 
    summary = df_raw["Артикул продавца"].value_counts().reset_index()
    summary.columns = ["Артикул", "Заказ (шт)"]
    return summary
 
 
def render_wb_tab():
    col_main, col_refresh = st.columns([4, 1])
    with col_main:
        selected_shop = st.selectbox("🎯 Выберите магазин:", WB_SHOPS, key="wb_shop")
    with col_refresh:
        st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
        if st.button("🔄", help="Обновить прайс из Google Sheets", key="wb_refresh"):
            st.cache_data.clear()
            st.rerun()
 
    current_ff_rate = 0 if selected_shop in WB_SHOPS_WITHOUT_FF else DEFAULT_FF_COST
 
    with st.spinner("⏳ Синхронизация с Google Sheets..."):
        df_prices, error = load_prices_from_gsheets(selected_shop)
 
    if error:
        st.error(f"❌ {error}")
        return
    if df_prices is None or df_prices.empty:
        st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
        return
 
    st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")
 
    source = st.radio(
        "Способ получения заказов:",
        ["По номеру поставки (API)", "Из файла (Лист подбора)"],
        horizontal=True,
        key="wb_source",
    )
 
    summary_api = None
 
    if source == "По номеру поставки (API)":
        secret_key_name = WB_SHOP_TO_SECRET_KEY.get(selected_shop)
        api_key = None
        if secret_key_name:
            raw_key = st.secrets.get("wb_api_keys", {}).get(secret_key_name)
            if raw_key:
                api_key = raw_key.strip()
 
        if not api_key:
            st.error(f"❌ Для магазина «{selected_shop}» не задан API-ключ WB в secrets (`wb_api_keys`).")
            return
 
        supply_id = st.text_input("Номер поставки WB", placeholder="Например: WB-GI-123456789", key="wb_supply_id")
 
        if st.button("📥 Получить заказы по поставке", use_container_width=True, key="wb_fetch"):
            if not supply_id.strip():
                st.warning("⚠️ Введите номер поставки.")
            else:
                with st.spinner("Запрашиваем данные у Wildberries..."):
                    summary_api, api_error = wb_get_supply_orders(supply_id, api_key)
                if api_error:
                    st.error(f"❌ {api_error}")
                    summary_api = None
 
    else:  # "Из файла (Лист подбора)"
        picking_file = st.file_uploader(
            "Загрузите лист подбора WB (.xlsx)", type=["xlsx"], key="wb_picking_list"
        )
        if picking_file:
            try:
                summary_api = wb_parse_picking_list(picking_file)
            except Exception as e:
                st.error(f"❌ Ошибка чтения файла: {e}")
                summary_api = None
            else:
                st.success(f"✅ Найдено уникальных артикулов: {len(summary_api)}")
 
    if summary_api is not None:
        show_results(
            summary_api, df_prices, selected_shop, current_ff_rate,
            article_col="Артикул",
            qty_per_pack_col="Количество в упаковке",
            unit_cost_col="Цена за штуку",
            qty_col_label="Заказ (уп)",
            qty_unit_word="уп.",
            show_ff_line=True,
            metric_label="ИТОГО К ОПЛАТЕ",
        )
# =========================================================
# KASPI.KZ (лист подбора — Excel, без API)
# =========================================================

KASPI_PRICE_SHEET_NAME = "Kaspi_Smart"


def normalize_kaspi_price_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Заголовки в Kaspi_Smart содержат лишние переносы строк и приписку
    "- НЕ ТРОГАТЬ!" (например: "Артикул в системе партнера - НЕ ТРОГАТЬ!\n").
    Приводим к каноническим именам, которые использует остальной код.
    """
    def clean(col: str) -> str:
        col = str(col).replace("\n", " ").replace("\r", " ")
        col = col.split(" - НЕ ТРОГАТЬ")[0]
        return " ".join(col.split()).strip()

    df = df.rename(columns={c: clean(c) for c in df.columns})
    return df


def kaspi_parse_picking_list(file) -> pd.DataFrame:
    """
    Парсит лист подбора Kaspi (экспорт из ЛК продавца).
    Колонки (заголовок в первой строке):
      №, Номер заказа, Название товара, Артикул в системе партнера,
      Категория, Товаров, Мест по умолчанию, Место по факту, Способ доставки
    Один и тот же артикул может встречаться в нескольких заказах — суммируем "Товаров".
    """
    df_raw = pd.read_excel(file)
    required_cols = {"Артикул в системе партнера", "Товаров"}
    missing = required_cols - set(df_raw.columns)
    if missing:
        raise ValueError(f"В файле не найдены колонки: {', '.join(missing)}")

    df_raw = df_raw.dropna(subset=["Артикул в системе партнера"])
    df_raw["Артикул в системе партнера"] = df_raw["Артикул в системе партнера"].astype(str).str.strip()
    df_raw["Товаров"] = pd.to_numeric(df_raw["Товаров"], errors="coerce").fillna(0)

    summary = df_raw.groupby("Артикул в системе партнера", as_index=False)["Товаров"].sum()
    summary.columns = ["Артикул в системе партнера", "Заказ (шт)"]
    return summary


def render_kaspi_tab():
    with st.spinner("⏳ Синхронизация с Google Sheets..."):
        df_prices, error = load_prices_from_gsheets(KASPI_PRICE_SHEET_NAME)

    if error:
        st.error(f"❌ {error}")
        return
    if df_prices is None or df_prices.empty:
        st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
        return

    df_prices = normalize_kaspi_price_columns(df_prices)
    if "Артикул в системе партнера" in df_prices.columns:
        df_prices["Артикул в системе партнера"] = df_prices["Артикул в системе партнера"].astype(str).str.strip()

    st.caption(f"✅ Прайс ({KASPI_PRICE_SHEET_NAME}) обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")
    if st.button("🔄 Обновить прайс", key="kaspi_refresh"):
        st.cache_data.clear()
        st.rerun()

    picking_file = st.file_uploader(
        "Загрузите лист подбора Kaspi (.xlsx)", type=["xlsx"], key="kaspi_picking_list"
    )

    if picking_file:
        try:
            summary = kaspi_parse_picking_list(picking_file)
        except Exception as e:
            st.error(f"❌ Ошибка чтения файла: {e}")
            return

        st.success(f"✅ Найдено уникальных артикулов: {len(summary)} (всего строк подбора обработано)")
        show_results(
            summary, df_prices, "Kaspi.kz", current_ff_rate=0,
            article_col="Артикул в системе партнера",
            qty_per_pack_col="Количество в упаковке",
            unit_cost_col="Себестоимость за 1 штуку",
            name_col="Название товара",
            qty_col_label="Заказ (шт)",
            qty_unit_word="шт.",
            show_ff_line=False,
            metric_label="ИТОГО",
        )


# =========================================================
# UI — ПЕРЕКЛЮЧАТЕЛЬ ПЛАТФОРМЫ
# =========================================================

platform = st.segmented_control(
    "Платформа",
    options=["🟣 Wildberries", "🔴 Kaspi.kz"],
    default="🟣 Wildberries",
    label_visibility="collapsed",
)
st.markdown("---")

if platform == "🔴 Kaspi.kz":
    render_kaspi_tab()
else:
    render_wb_tab()
