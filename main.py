import streamlit as st
import pandas as pd
import os

# --- КОНФИГУРАЦИЯ ---
FILE_NAME_PRICE = "prices_database.xlsx"
FF_COST = 400  # Стоимость фулфилмента за 1 упаковку

# Список твоих магазинов
SHOPS = [
    "Тлеубаева",
    "Bonitas",
    "Мамутова",
    "Тастанов",
    "Bastau",
    "Шукурова",
    "Диханбаев",
    "Diamond",
    "Хаким",
    "Fariza",
    "Aibar",
    "Байпакова",
    "Абеденов",
    "Махамбетова",
    "Кыдырова",
    "Жораев",
]

st.set_page_config(page_title="Калькулятор Поставок", layout="centered", page_icon="📦")

# --- ИНТЕРФЕЙС ---
st.title("📦 Система расчета себестоимости")
st.markdown("---")

# 1. Выбор магазина
selected_shop = st.selectbox("🎯 Выберите магазин для расчета:", SHOPS)

# 2. Проверка наличия базы
if not os.path.exists(FILE_NAME_PRICE):
    st.error(f"❌ Файл '{FILE_NAME_PRICE}' не найден в папке проекта!")
    st.stop()


# Функция загрузки прайса конкретного магазина
@st.cache_data
def load_shop_price(shop_name):
    try:
        # Читаем только нужный лист
        return pd.read_excel(FILE_NAME_PRICE, sheet_name=shop_name)
    except Exception as e:
        st.error(f"❌ Не найден лист с названием '{shop_name}' в Excel файле!")
        return None


df_prices = load_shop_price(selected_shop)

if df_prices is not None:
    # 3. Загрузка файла поставки
    st.subheader(f"Загрузите поставку для: {selected_shop}")
    delivery_file = st.file_uploader("Перетащите Excel (чтение колонки F с 6-й строки)", type=['xlsx'])

    if delivery_file:
        try:
            # Читаем Артикулы: skiprows=5 (начинаем с 6-й строки), только колонка F
            df_raw = pd.read_excel(delivery_file, skiprows=5, usecols="F", names=['Артикул']).dropna()

            if df_raw.empty:
                st.warning("⚠️ В колонке F не найдено данных (пусто).")
            else:
                # Группировка (УНИК + СЧЁТЕСЛИ)
                summary = df_raw['Артикул'].value_counts().reset_index()
                summary.columns = ['Артикул', 'Заказ (уп)']

                # Сопоставление с прайсом выбранного магазина (ВПР)
                # Оставляем только нужные колонки из прайса
                res = pd.merge(summary, df_prices[['Артикул', 'Кол-во в упак', 'Цена за шт']], on='Артикул', how='left')

                # Проверка на отсутствующие товары
                missing = res[res['Цена за шт'].isnull()]
                if not missing.empty:
                    st.warning(
                        f"⚠️ В прайсе {selected_shop} не найдены артикулы: {', '.join(missing['Артикул'].tolist())}")

                # --- РАСЧЕТЫ ---
                # Всего штук = Кол-во упаковок в заказе * Кол-во штук в 1 упаковке
                res['Кол-во всего (шт)'] = res['Заказ (уп)'] * res['Кол-во в упак']

                # Итоговая цена товара = Общее кол-во штук * Цена за 1 штуку
                res['Цена товара'] = res['Кол-во всего (шт)'] * res['Цена за шт']

                # 4. Вывод красивой таблицы
                st.subheader("📊 Результаты расчета")
                final_df = res[['Артикул', 'Заказ (уп)', 'Кол-во всего (шт)', 'Цена товара']]
                st.table(final_df.style.format({"Цена товара": "{:,.0f}"}))

                # 5. Итоговый блок
                total_packs = res['Заказ (уп)'].sum()
                total_sum_items = res['Цена товара'].sum()
                total_ff = total_packs * FF_COST
                grand_total = total_sum_items + total_ff

                st.markdown("---")
                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Количество упаковок:** {total_packs} шт")
                    st.write(f"**Фулфилмент (FF):** {total_ff:,.0f} тг")
                    st.write(f"**Сумма за товар:** {total_sum_items:,.0f} тг")

                with col2:
                    st.success(f"### ИТОГО: {grand_total:,.0f} тг")
                    st.caption(f"Расчет произведен по прайсу: {selected_shop}")
        except Exception as e:
            st.error(f"🔴 Ошибка при обработке файла: {e}")