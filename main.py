import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from io import BytesIO
import time

DEFAULT_FF_COST = 400

SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]
SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

st.set_page_config(page_title="Калькулятор Поставок", layout="centered", page_icon="📦")
st.title("📦 Система расчёта себестоимости")
st.markdown("---")

# --- ЗАГРУЗКА ИЗ GOOGLE SHEETS ---
@st.cache_data(ttl=300)
def load_prices_from_gsheets(shop_name):
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=SCOPES
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(st.secrets["sheet_url"])
        worksheet = spreadsheet.worksheet(shop_name)  # лист по имени магазина
        df = pd.DataFrame(worksheet.get_all_records())
        return df, None
    except gspread.WorksheetNotFound:
        return None, f"Лист '{shop_name}' не найден в таблице"
    except Exception as e:
        return None, str(e)


col_main, col_refresh = st.columns([4, 1])
with col_main:
    selected_shop = st.selectbox("🎯 Выберите магазин:", SHOPS)
with col_refresh:
    st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
    if st.button("🔄", help="Обновить прайс из Google Sheets"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

current_ff_rate = 0 if selected_shop in SHOPS_WITHOUT_FF else ff_rate_input

# Загружаем прайс
with st.spinner("Загружаем прайс..."):
    df_prices, error = load_prices_from_gsheets(selected_shop)

if error:
    st.error(f"❌ Не удалось загрузить прайс: {error}")
    st.info("Проверьте доступ к Google Sheets и настройки секретов.")
    st.stop()

if df_prices.empty:
    st.warning(f"⚠️ Для магазина '{selected_shop}' нет данных в прайсе.")
    st.stop()

last_updated = time.strftime("%H:%M:%S")
st.caption(f"Прайс загружен в {last_updated} · {len(df_prices)} позиций · обновляется каждые 5 мин")

# --- ЗАГРУЗКА ПОСТАВКИ ---
st.subheader(f"Загрузите поставку: {selected_shop}")
delivery_file = st.file_uploader("Excel (колонка F с 6-й строки)", type=["xlsx"])

if delivery_file:
    try:
        df_raw = (
            pd.read_excel(delivery_file, skiprows=4, usecols="F", names=["Артикул"])
            .dropna()
        )

        if df_raw.empty:
            st.warning("Файл пустой или данные не найдены в колонке F.")
            st.stop()

        summary = df_raw["Артикул"].value_counts().reset_index()
        summary.columns = ["Артикул", "Заказ (уп)"]

        res = pd.merge(
            summary,
            df_prices[["Артикул", "Количество в упаковке", "Цена за штуку"]],
            on="Артикул",
            how="left"
        )

        # --- АЛЕРТ: артикулы не найдены в прайсе ---
        unmatched = res[res["Цена за штуку"].isna()]["Артикул"].tolist()
        if unmatched:
            st.warning(
                f"⚠️ {len(unmatched)} артикул(ов) не найдено в прайсе и исключены из расчёта:\n"
                + ", ".join(str(a) for a in unmatched)
            )

        # Исключаем строки с NaN перед расчётом
        res = res.dropna(subset=["Цена за штуку"])

        if res.empty:
            st.error("Ни один артикул не совпал с прайсом. Проверьте файл поставки.")
            st.stop()

        res["Всего шт"] = res["Заказ (уп)"] * res["Количество в упаковке"]
        res["Цена товара"] = res["Всего шт"] * res["Цена за штуку"]

        # --- ТАБЛИЦА ---
        st.subheader("📊 Результаты расчёта")

        final_display = res[["Артикул", "Заказ (уп)", "Всего шт", "Цена за штуку", "Цена товара"]]

        def zebra_style(x):
            df_s = pd.DataFrame("", index=x.index, columns=x.columns)
            df_s.iloc[1::2, :] = "background-color: #EEEEEE; color: #31333F;"
            return df_s

        styled_df = final_display.style.apply(zebra_style, axis=None).format({
            "Цена товара": "{:,.0f}",
            "Цена за штуку": "{:,.0f}",
            "Всего шт": "{:,.0f}",
            "Заказ (уп)": "{:,.0f}",
        })
        st.table(styled_df)

        # --- ИТОГИ ---
        total_packs = res["Заказ (уп)"].sum()
        total_sum_items = res["Цена товара"].sum()
        total_ff = total_packs * current_ff_rate
        grand_total = total_sum_items + total_ff

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**Количество заказов:** {total_packs} шт")
            if current_ff_rate == 0:
                st.write("**Фулфилмент (FF):** 0 тг (исключение)")
            else:
                st.write(f"**Фулфилмент (FF):** {total_ff:,.0f} тг")
            st.write(f"**Сумма за товар:** {total_sum_items:,.0f} тг")
        with col2:
            st.success(f"### ИТОГО: {grand_total:,.0f} тг")
            st.caption(f"Прайс: {selected_shop} | FF: {current_ff_rate} тг/уп")

        # --- ЭКСПОРТ ---
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            final_display.to_excel(writer, index=False, sheet_name="Результат")
        st.download_button(
            label="⬇️ Скачать результат (.xlsx)",
            data=output.getvalue(),
            file_name=f"результат_{selected_shop}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        st.error(f"🔴 Ошибка: {e}")