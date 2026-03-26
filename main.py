import streamlit as st
import pandas as pd
import os

# Настройки
FILE_NAME_PRICE = "prices_database.xlsx"  # Файл должен лежать в папке с main.py
FF_COST = 400

st.set_page_config(page_title="Калькулятор", layout="centered")
st.title("📦 Расчет текущей поставки")

# 1. Проверяем наличие файла под капотом
if not os.path.exists(FILE_NAME_PRICE):
    st.error(f"Ошибка: Файл '{FILE_NAME_PRICE}' не найден в папке с проектом!")
    st.stop()

# Читаем базу цен один раз
df_prices = pd.read_excel(FILE_NAME_PRICE)

# 2. Загрузка поставки
delivery_file = st.file_uploader("Загрузите файл поставки (колонка F)", type=['xlsx'])

if delivery_file:
    try:
        # Читаем артикулы с F6 (skiprows=4, так как 5-я строка — заголовок, 6-я — данные)
        df_raw = pd.read_excel(delivery_file, skiprows=4, usecols="F", names=['Артикул']).dropna()

        # Группируем (УНИК + СЧЁТЕСЛИ)
        summary = df_raw['Артикул'].value_counts().reset_index()
        summary.columns = ['Артикул', 'Заказ (уп)']

        # Сопоставляем с базой (ВПР)
        res = pd.merge(summary, df_prices, on='Артикул', how='left')

        # --- ОБНОВЛЕННАЯ ЛОГИКА СТОЛБЦОВ ---
        # res.iloc[:, 2] это "Кол-во в упак" из прайса
        # res.iloc[:, 3] это "Цена за шт" из прайса

        kol_vo_v_upak = res.iloc[:, 2]
        cena_za_shtyk = res.iloc[:, 3]

        # Теперь считаем общее количество штук (Упаковки * Кол-во в упак)
        res['Кол-во всего (шт)'] = res['Заказ (уп)'] * kol_vo_v_upak

        # Считаем итоговую цену за товар
        res['Цена'] = res['Кол-во всего (шт)'] * cena_za_shtyk

        # Вывод таблицы (оставляем только нужные колонки)
        st.subheader("Список товаров")
        # Выводим: Артикул, Заказ (уп), Новую колонку с общим количеством шт, и Цену
        st.table(res[['Артикул', 'Заказ (уп)', 'Кол-во всего (шт)', 'Цена']])

        # Итоги
        total_units = res['Заказ (уп)'].sum()
        total_sum_items = res['Цена'].sum()
        total_ff = total_units * FF_COST

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.info(f"**Фулфилмент (FF):** {total_ff:,.0f} тг")
            st.write(f"(из расчета {total_units} упак. × {FF_COST})")
        with col2:
            st.success(f"### ИТОГО: {total_sum_items + total_ff:,.0f} тг")

    except Exception as e:
        st.error(f"Ошибка обработки: {e}")