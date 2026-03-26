import streamlit as st
import pandas as pd
import os

# --- КОНФИГУРАЦИЯ ---
FILE_NAME_PRICE = "prices_database.xlsx"
DEFAULT_FF_COST = 400

SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]

# МАГАЗИНЫ БЕЗ ФУЛФИЛМЕНТА
SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]

st.set_page_config(page_title="Калькулятор Поставок", layout="centered", page_icon="📦")

st.title("📦 Система расчета себестоимости")
st.markdown("---")

selected_shop = st.selectbox("🎯 Выберите магазин для расчета:", SHOPS)

# Определяем ставку FF
current_ff_rate = 0 if selected_shop in SHOPS_WITHOUT_FF else DEFAULT_FF_COST

if not os.path.exists(FILE_NAME_PRICE):
    st.error(f"❌ Файл '{FILE_NAME_PRICE}' не найден!")
    st.stop()


@st.cache_data
def load_shop_price(shop_name):
    try:
        return pd.read_excel(FILE_NAME_PRICE, sheet_name=shop_name)
    except Exception:
        st.error(f"❌ Лист '{shop_name}' не найден в Excel!")
        return None


df_prices = load_shop_price(selected_shop)

if df_prices is not None:
    st.subheader(f"Загрузите поставку: {selected_shop}")
    delivery_file = st.file_uploader("Excel (колонка F с 6-й строки)", type=['xlsx'])

    if delivery_file:
        try:
            df_raw = pd.read_excel(delivery_file, skiprows=4, usecols="F", names=['Артикул']).dropna()

            if not df_raw.empty:
                summary = df_raw['Артикул'].value_counts().reset_index()
                summary.columns = ['Артикул', 'Заказ (уп)']

                # Сопоставление
                res = pd.merge(summary, df_prices[['Артикул', 'Количество в упаковке', 'Цена за штуку']], on='Артикул',
                               how='left')

                # Расчеты
                res['Всего шт'] = res['Заказ (уп)'] * res['Количество в упаковке']
                res['Цена товара'] = res['Всего шт'] * res['Цена за штуку']

                st.subheader("📊 Результаты расчета")
                st.table(
                    res[['Артикул', 'Заказ (уп)', 'Всего шт', 'Цена товара']].style.format({"Цена товара": "{:,.0f}"}))

                # --- ИТОГОВЫЙ БЛОК (ДВЕ КОЛОНКИ) ---
                total_packs = res['Заказ (уп)'].sum()
                total_sum_items = res['Цена товара'].sum()
                total_ff = total_packs * current_ff_rate
                grand_total = total_sum_items + total_ff

                st.markdown("---")
                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Количество заказов:** {total_packs} шт")
                    if current_ff_rate == 0:
                        st.write(f"**Фулфилмент (FF):** 0 тг (Исключение)")
                    else:
                        st.write(f"**Фулфилмент (FF):** {total_ff:,.0f} тг")
                    st.write(f"**Сумма за товар:** {total_sum_items:,.0f} тг")

                with col2:
                    st.success(f"### ИТОГО: {grand_total:,.0f} тг")
                    st.caption(f"Прайс: {selected_shop} | FF: {current_ff_rate} тг/уп")

        except Exception as e:
            st.error(f"🔴 Ошибка: {e}")