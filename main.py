import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection

# --- КОНФИГУРАЦИЯ ---
DEFAULT_FF_COST = 400

SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]

# МАГАЗИНЫ БЕЗ ФУЛФИЛМЕНТА
SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]

st.set_page_config(page_title="Калькулятор Поставок", layout="centered", page_icon="📦")

# --- ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ---
# Создаем подключение один раз
conn = st.connection("gsheets", type=GSheetsConnection)

st.title("📦 Система расчета себестоимости")
st.markdown("---")

# 1. Выбор магазина
selected_shop = st.selectbox("🎯 Выберите магазин для расчета:", SHOPS)

# Определяем ставку FF
current_ff_rate = 0 if selected_shop in SHOPS_WITHOUT_FF else DEFAULT_FF_COST


# Функция загрузки данных из Google Sheets
@st.cache_data(ttl=600)  # Кэш на 10 минут, чтобы не дергать API постоянно
def load_data(sheet_name):
    try:
        # Читаем конкретный лист из таблицы, указанной в Secrets
        return conn.read(worksheet=sheet_name)
    except Exception as e:
        st.error(f"❌ Ошибка загрузки листа '{sheet_name}': {e}")
        return None


df_prices = load_data(selected_shop)

if df_prices is not None:
    st.subheader(f"Загрузите поставку: {selected_shop}")
    delivery_file = st.file_uploader("Excel (колонка F с 6-й строки)", type=['xlsx'])

    if delivery_file:
        try:
            # Читаем поставку
            df_raw = pd.read_excel(delivery_file, skiprows=4, usecols="F", names=['Артикул']).dropna()

            if not df_raw.empty:
                summary = df_raw['Артикул'].value_counts().reset_index()
                summary.columns = ['Артикул', 'Заказ (уп)']

                # Очистка артикулов от пробелов для точного совпадения
                summary['Артикул'] = summary['Артикул'].astype(str).str.strip()
                df_prices['Артикул'] = df_prices['Артикул'].astype(str).str.strip()

                # Сопоставление
                res = pd.merge(summary, df_prices[['Артикул', 'Количество в упаковке', 'Цена за штуку']],
                               on='Артикул', how='left')

                # Расчеты
                res['Всего шт'] = res['Заказ (уп)'] * res['Количество в упаковке']
                res['Цена товара'] = res['Всего шт'] * res['Цена за штуку']

                st.subheader("📊 Результаты расчета")


                # Функция "Зебра" с адаптацией под темы
                def zebra_style(x):
                    df_s = pd.DataFrame('', index=x.index, columns=x.columns)
                    # Используем полупрозрачный серый, чтобы он адаптировался под фон темы
                    df_s.iloc[1::2, :] = 'background-color: rgba(128, 128, 128, 0.1);'
                    return df_s


                final_display = res[['Артикул', 'Заказ (уп)', 'Всего шт', 'Цена товара']]

                # Стилизация
                styled_df = final_display.style.apply(zebra_style, axis=None).format({
                    "Цена товара": "{:,.0f}",
                    "Всего шт": "{:,.0f}",
                    "Заказ (уп)": "{:,.0f}"
                })

                st.table(styled_df)

                # --- ИТОГОВЫЙ БЛОК ---
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
                    st.caption(f"Данные из Google Sheets: {selected_shop}")

        except Exception as e:
            st.error(f"🔴 Ошибка обработки: {e}")