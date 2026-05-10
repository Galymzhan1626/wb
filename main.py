import streamlit as st
import pandas as pd
import gspread
import requests
from google.oauth2.service_account import Credentials
from io import BytesIO
import time
import streamlit_authenticator as stauth
import os

# --- НАСТРОЙКИ ---
DEFAULT_FF_COST = 400
SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]
SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# --- PAGE CONFIG ---
st.set_page_config(page_title="Калькулятор Поставок", layout="centered", page_icon="📦")

st.markdown("""
    <style>
    .stTable {font-size: 14px;}
    .reportview-container .main .block-container {padding-top: 2rem;}
    </style>
    """, unsafe_allow_html=True)

# --- АВТОРИЗАЦИЯ ---
credentials = {
    "usernames": {
        "SeiE003YAN8J": {  # Теперь именно это твой логин для входа (Username)
            "name": "Менеджер", # Имя, которое будет красиво отображаться в сайдбаре (👤 Менеджер)
            "password": "$2b$12$OsSAaw38p2ICx2Xj3Yct6u.OnnwqaW99obBa1IcoTvi8GvIEbWnSa"
        }
    }
}

authenticator = stauth.Authenticate(
    credentials,
    "delivery_app",
    "super_secret_key_xyz_123",
    cookie_expiry_days=7
)

authenticator.login()

if st.session_state.get("authentication_status") is False:
    st.error("❌ Неверный логин или пароль")
    st.stop()

if st.session_state.get("authentication_status") is None:
    st.warning("Введите логин и пароль")
    st.stop()

# --- если дошли сюда — пользователь вошёл ---
authenticator.logout("Выйти", "sidebar")
st.sidebar.write(f"👤 {st.session_state.get('name')}")

st.title("📦 Система расчёта себестоимости")
st.markdown("---")


# --- GOOGLE SHEETS ---
@st.cache_data(ttl=300)
def load_prices_from_gsheets(shop_name, sheet_url):
    try:
        service_account_info = {
            "type": os.environ["GCP_TYPE"],
            "project_id": os.environ["GCP_PROJECT_ID"],
            "private_key_id": os.environ["GCP_PRIVATE_KEY_ID"],
            "private_key": os.environ["GCP_PRIVATE_KEY"].replace("\\n", "\n"),
            "client_email": os.environ["GCP_CLIENT_EMAIL"],
            "client_id": os.environ["GCP_CLIENT_ID"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(sheet_url)
        worksheet = spreadsheet.worksheet(shop_name)
        return pd.DataFrame(worksheet.get_all_records()), None
    except Exception as e:
        return None, f"Ошибка доступа: {str(e)}"


# --- WILDBERRIES API ---
@st.cache_data(ttl=60)
def get_supply_orders(supply_id: str, api_key: str):
    clean_id = supply_id.strip()
    headers = {"Authorization": api_key}
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

        url_all = "https://marketplace-api.wildberries.ru/api/v3/orders"
        params = {"limit": 1000, "next": 0}
        res_all = requests.get(url_all, headers=headers, params=params)

        if res_all.status_code == 200:
            all_orders = res_all.json().get("orders", [])
            filtered = [o for o in all_orders if str(o.get("supplyId")) == clean_id]

            if filtered:
                df = pd.DataFrame(filtered)
                summary = df["article"].value_counts().reset_index()
                summary.columns = ["Артикул", "Заказ (уп)"]
                return summary, None
            else:
                return None, f"Заказы для поставки {clean_id} не найдены."

        return None, f"Ошибка API: {res.status_code}"

    except Exception as e:
        return None, f"Ошибка: {str(e)}"


# --- ИНТЕРФЕЙС ВЫБОРА МАГАЗИНА ---
col_main, col_refresh = st.columns([4, 1])
with col_main:
    selected_shop = st.selectbox("🎯 Выберите магазин:", SHOPS)
with col_refresh:
    st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
    if st.button("🔄", help="Обновить прайс из Google Sheets"):
        st.cache_data.clear()
        st.rerun()

current_ff_rate = 0 if selected_shop in SHOPS_WITHOUT_FF else DEFAULT_FF_COST

# --- ЗАГРУЗКА ДАННЫХ ---
with st.spinner("⏳ Синхронизация с Google Sheets..."):
    df_prices, error = load_prices_from_gsheets(
        selected_shop,
        os.environ["SHEET_URL"]
    )

if error:
    st.error(f"❌ {error}")
    st.stop()

if df_prices is None or df_prices.empty:
    st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
    st.stop()

st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")

# --- ВВОД ДАННЫХ ПОСТАВКИ ---
st.subheader(f"🚚 Поставка: {selected_shop}")
tab_api, tab_file = st.tabs(["🔗 Wildberries API", "📂 Загрузка Excel"])

summary = None

shop_env_map = {
    "Абеденов": "WB_KEY_ABEDENOV",
    # добавьте остальные магазины если появятся ключи
}

with tab_api:
    env_var = shop_env_map.get(selected_shop)
    api_key = os.environ.get(env_var) if env_var else None
    if not api_key:
        st.info("⚠️ API ключ не найден. Используйте Excel или добавьте ключ в настройки.")
    else:
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        sid = c1.text_input("ID Поставки", placeholder="WB-GI-...")
        if c2.button("Получить", use_container_width=True) and sid:
            summary, api_err = get_supply_orders(sid.strip(), api_key)
            if api_err:
                st.error(api_err)

with tab_file:
    delivery_file = st.file_uploader("Файл поставки (колонка F)", type=["xlsx"])
    if delivery_file:
        try:
            df_raw = pd.read_excel(delivery_file, skiprows=4, usecols="F").dropna()
            df_raw.columns = ["Артикул"]
            summary = df_raw["Артикул"].value_counts().reset_index()
            summary.columns = ["Артикул", "Заказ (уп)"]
        except Exception as e:
            st.error(f"❌ Ошибка файла: {e}")

# --- РАСЧЕТЫ ---
if summary is not None:
    res = pd.merge(summary, df_prices[["Артикул", "Количество в упаковке", "Цена за штуку"]], on="Артикул", how="left")

    unmatched = res[res["Цена за штуку"].isna()]["Артикул"].tolist()
    if unmatched:
        st.warning(f"⚠️ **{len(unmatched)} SKU** не найдены в прайсе и пропущены:\n{', '.join(map(str, unmatched))}")

    res = res.dropna(subset=["Цена за штуку"])

    if not res.empty:
        res["Всего шт"] = res["Заказ (уп)"] * res["Количество в упаковке"]
        res["Цена товара"] = res["Всего шт"] * res["Цена за штуку"]

        st.subheader("📊 Результаты расчёта")

        st.dataframe(
            res[["Артикул", "Заказ (уп)", "Всего шт", "Цена за штуку", "Цена товара"]].style.format({
                "Цена товара": "{:,.0f} ₸",
                "Цена за штуку": "{:,.0f} ₸",
                "Всего шт": "{:,.0f}",
                "Заказ (уп)": "{:,.0f}"
            }),
            use_container_width=True,
            hide_index=True
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
                use_container_width=True
            )
    else:
        st.error("❌ Нет данных для расчета. Проверьте артикулы.")