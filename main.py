"""
FFCalculator (минимальная версия) — расчёт себестоимости поставки Wildberries
через WB API + прайс из Google Sheets.
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

WB_SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]
WB_SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond","Шукурова","Fariza","Bonitas","Мамутова","Тлеубаева"]

GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Соответствие "магазин в интерфейсе" -> "ключ в st.secrets['wb_api_keys']"
WB_SHOP_TO_SECRET_KEY = {
    "Абеденов": "Абеденов",
    "Bastau": "Bastau",
    "Диханбаев": "Диханбаев",
    "Тлеубаева": "Тлеубаева",
    "Fariza": "Fariza",
    "Шукурова": "Шукурова",
    "Bonitas": "Bonitas",
    "Мамутова": "Мамутова", 
    "Тастанов": "Тастанов",
    "Шукурова": "Шукурова",
    "Diamond": "Diamond",
    "Хаким": "Хаким",
    "Махамбетова": "Махамбетова",
    "Кыдырова": "Кыдырова",
    "Жораев": "Жораев",
}

st.set_page_config(page_title="Калькулятор Поставок WB", layout="centered", page_icon="📦")
st.title("📦 Калькулятор себестоимости поставки WB")
st.markdown("---")


# =========================================================
# GOOGLE SHEETS
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
# WILDBERRIES API
# =========================================================

@st.cache_data(ttl=60)
def get_supply_orders(supply_id: str, api_key: str):
    """Возвращает (summary_df, error_message)."""
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
                summary.columns = ["Артикул", "Заказ (уп)"]
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
                summary.columns = ["Артикул", "Заказ (уп)"]
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


# =========================================================
# РАСЧЁТ И ВЫВОД
# =========================================================

def show_results(summary: pd.DataFrame, df_prices: pd.DataFrame, selected_shop: str, current_ff_rate: float):
    res = pd.merge(
        summary,
        df_prices[["Артикул", "Количество в упаковке", "Цена за штуку"]],
        on="Артикул",
        how="left",
    )

    unmatched = res[res["Цена за штуку"].isna()]["Артикул"].tolist()
    if unmatched:
        st.warning(f"⚠️ **{len(unmatched)} SKU** не найдены в прайсе и пропущены:\n{', '.join(map(str, unmatched))}")

    res = res.dropna(subset=["Цена за штуку"])

    if res.empty:
        st.error("❌ Нет данных для расчета. Проверьте артикулы.")
        return

    res["Всего шт"] = res["Заказ (уп)"] * res["Количество в упаковке"]
    res["Цена товара"] = res["Всего шт"] * res["Цена за штуку"]

    st.subheader("📊 Результаты расчёта")
    st.dataframe(
        res[["Артикул", "Заказ (уп)", "Всего шт", "Цена за штуку", "Цена товара"]].style.format({
            "Цена товара": "{:,.0f} ₸",
            "Цена за штуку": "{:,.0f} ₸",
            "Всего шт": "{:,.0f}",
            "Заказ (уп)": "{:,.0f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

    total_packs = res["Заказ (уп)"].sum()
    total_items_cost = res["Цена товара"].sum()
    total_ff = total_packs * current_ff_rate
    grand_total = total_items_cost + total_ff

    st.markdown("---")
    c_res1, c_res2 = st.columns(2)
    with c_res1:
        st.write(f"📦 **Заказов:** {total_packs} уп.")
        st.write(f"⚙️ **Фулфилмент:** {total_ff:,.0f} ₸")
        st.write(f"💰 **Стоимость товара:** {total_items_cost:,.0f} ₸")
    with c_res2:
        st.metric(label="ИТОГО К ОПЛАТЕ", value=f"{grand_total:,.0f} ₸")

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            res.to_excel(writer, index=False, sheet_name="Расчет")
        st.download_button(
            "⬇️ Скачать Excel",
            data=output.getvalue(),
            file_name=f"Расчет_{selected_shop}_{time.strftime('%d%m')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# =========================================================
# UI
# =========================================================

col_main, col_refresh = st.columns([4, 1])
with col_main:
    selected_shop = st.selectbox("🎯 Выберите магазин:", WB_SHOPS)
with col_refresh:
    st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
    if st.button("🔄", help="Обновить прайс из Google Sheets"):
        st.cache_data.clear()
        st.rerun()

current_ff_rate = 0 if selected_shop in WB_SHOPS_WITHOUT_FF else DEFAULT_FF_COST

with st.spinner("⏳ Синхронизация с Google Sheets..."):
    df_prices, error = load_prices_from_gsheets(selected_shop)

if error:
    st.error(f"❌ {error}")
    st.stop()

if df_prices is None or df_prices.empty:
    st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
    st.stop()

st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")
st.subheader(f"🚚 Поставка: {selected_shop}")

secret_key_name = WB_SHOP_TO_SECRET_KEY.get(selected_shop)
api_key = None
if secret_key_name:
    raw_key = st.secrets.get("wb_api_keys", {}).get(secret_key_name)
    if raw_key:
        api_key = raw_key.strip()

if not api_key:
    st.error(f"❌ Для магазина «{selected_shop}» не задан API-ключ WB в secrets (`wb_api_keys`).")
    st.stop()

supply_id = st.text_input("Номер поставки WB", placeholder="Например: WB-GI-123456789")

if st.button("📥 Получить заказы по поставке", use_container_width=True):
    if not supply_id.strip():
        st.warning("⚠️ Введите номер поставки.")
    else:
        with st.spinner("Запрашиваем данные у Wildberries..."):
            summary_api, api_error = get_supply_orders(supply_id, api_key)
        if api_error:
            st.error(f"❌ {api_error}")
        else:
            st.success(f"✅ Найдено позиций: {len(summary_api)}")
            show_results(summary_api, df_prices, selected_shop, current_ff_rate)
