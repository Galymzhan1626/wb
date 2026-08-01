import os
import time
import codecs
from io import BytesIO

import requests
import pdfplumber
import openpyxl
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth

# --- НАСТРОЙКИ ---
DEFAULT_FF_COST = 400

WB_SHOPS = [
    "Тлеубаева", "Bonitas", "Мамутова", "Тастанов", "Bastau", "Шукурова",
    "Диханбаев", "Diamond", "Хаким", "Fariza", "Aibar", "Байпакова",
    "Абеденов", "Махамбетова", "Кыдырова", "Жораев",
]
WB_SHOPS_WITHOUT_FF = ["Диханбаев", "Хаким", "Diamond"]

OZON_SHOPS = [
    "Магазин 1",  # Сюда можно добавить другие магазины Ozon
]

ALL_SHOPS = WB_SHOPS + OZON_SHOPS

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
        "SeiE003YAN8J": {
            "name": "Менеджер",
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

authenticator.logout("Выйти", "sidebar")
st.sidebar.write(f"👤 {st.session_state.get('name')}")

st.title("📦 Система расчёта себестоимости")
st.markdown("---")


# --- GOOGLE SHEETS ---
@st.cache_data(ttl=300)
def load_prices_from_gsheets(shop_name):
    try:
        service_account_info = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES,
        )
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(st.secrets["sheet_url"])
        worksheet = spreadsheet.worksheet(shop_name)
        df = pd.DataFrame(worksheet.get_all_records())
        return df, None
    except Exception as e:
        return None, f"Ошибка доступа: {e}"


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
        res_all = requests.get(url_all, headers=headers, params=params, timeout=15)

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


# --- OZON PDF ПАРСЕР ---
def parse_ozon_pdf(file) -> pd.DataFrame:
    items = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if row and len(row) >= 5:
                        article = str(row[3]).strip() if row[3] else ""
                        qty_raw = str(row[4]).strip() if row[4] else ""
                        if article in ("", "Артикул", "None"):
                            continue
                        try:
                            qty = int(qty_raw)
                        except ValueError:
                            qty = 1
                        items.append({"Артикул": article, "Заказ (уп)": qty})

    if not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)
    summary = df.groupby("Артикул")["Заказ (уп)"].sum().reset_index()
    return summary


def show_results(summary, df_prices, selected_shop, current_ff_rate):
    """Общая функция расчёта и отображения результатов."""
    res = pd.merge(
        summary,
        df_prices[["Артикул", "Количество в упаковке", "Цена за штуку"]],
        on="Артикул",
        how="left"
    )

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


# --- ВЫБОР МАГАЗИНА ---
col_main, col_refresh = st.columns([4, 1])
with col_main:
    selected_shop = st.selectbox("🎯 Выберите магазин:", ALL_SHOPS)
with col_refresh:
    st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
    if st.button("🔄", help="Обновить прайс из Google Sheets"):
        st.cache_data.clear()
        st.rerun()

# Проверяем маркетплейс и настраиваем Фулфилмент
is_ozon_shop = selected_shop in OZON_SHOPS
if is_ozon_shop:
    current_ff_rate = DEFAULT_FF_COST
else:
    current_ff_rate = 0 if selected_shop in WB_SHOPS_WITHOUT_FF else DEFAULT_FF_COST

# --- ЗАГРУЗКА ПРАЙСА ---
with st.spinner("⏳ Синхронизация с Google Sheets..."):
    df_prices, error = load_prices_from_gsheets(selected_shop)

if error:
    st.error(f"❌ {error}")
    st.stop()

if df_prices is None or df_prices.empty:
    st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
    st.stop()

st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")

st.subheader(f"🚚 Поставка: {selected_shop} ({'Ozon' if is_ozon_shop else 'Wildberries'})")

# ==========================================
# ДИНАМИЧЕСКИЕ ВКЛАДКИ ПО МАРКЕТПЛЕЙСАМ
# ==========================================
if is_ozon_shop:
    # Вкладка для Ozon
    tab_ozon = st.tabs(["🔵 Загрузка Ozon PDF"])[0]

    with tab_ozon:
        ozon_file = st.file_uploader("Загрузите PDF от Ozon", type=["pdf"], key="ozon_pdf")
        if ozon_file:
            with st.spinner("Читаем PDF..."):
                summary_ozon = parse_ozon_pdf(ozon_file)
            if summary_ozon.empty:
                st.error("❌ Не удалось извлечь артикулы из PDF. Проверьте формат файла.")
            else:
                st.success(f"✅ Найдено позиций: {len(summary_ozon)}")
                show_results(summary_ozon, df_prices, selected_shop, current_ff_rate)

else:
    # Вкладки для Wildberries
    tab_api, tab_file, tab_esf = st.tabs(["🔗 Wildberries API", "📂 Загрузка Excel", "📝 ЭСФ"])

    shop_key_map = {
        "Абеденов": "Абеденов",
    }

    # ------------------------------------------
    # ВКЛАДКА 1: WILDBERRIES API
    # ------------------------------------------
    with tab_api:
        shop_name = shop_key_map.get(selected_shop)

        if shop_name:
            api_key = st.secrets.get("wb_api_keys", {}).get(shop_name)
        else:
            api_key = None

        if not api_key:
            st.info("ℹ️ Для этого магазина API-ключ WB не настроен в secrets. Используйте вкладку «Загрузка Excel».")
        else:
            supply_id = st.text_input("Номер поставки WB", key="wb_supply_id", placeholder="Например: WB-GI-123456789")
            if st.button("📥 Получить заказы по поставке", key="fetch_supply_btn"):
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

    # ------------------------------------------
    # ВКЛАДКА 2: ЗАГРУЗКА EXCEL
    # ------------------------------------------
    with tab_file:
        delivery_file = st.file_uploader("Файл поставки (колонка F)", type=["xlsx"], key="delivery_upload")
        if delivery_file:
            try:
                df_raw = pd.read_excel(delivery_file, skiprows=4, usecols="F").dropna()
                df_raw.columns = ["Артикул"]
                summary_file = df_raw["Артикул"].value_counts().reset_index()
                summary_file.columns = ["Артикул", "Заказ (уп)"]
                show_results(summary_file, df_prices, selected_shop, current_ff_rate)
            except Exception as e:
                st.error(f"❌ Ошибка файла: {e}")

    # ------------------------------------------
    # ВКЛАДКА 3: ЭСФ
    # ------------------------------------------
    with tab_esf:
        st.subheader("Генерация ЭСФ из уведомления о выкупе WB")

        file_wb = st.file_uploader(
            "Загрузите Excel (уведомление о выкупе WB)",
            type=["xlsx"],
            key="wb_notice"
        )

        if file_wb:
            try:
                tnved_dict = {}
                if "Артикул" in df_prices.columns and "ТН ВЭД" in df_prices.columns:
                    df_prices["Артикул_clean"] = df_prices["Артикул"].astype(str).str.strip()
                    tnved_dict = pd.Series(
                        df_prices["ТН ВЭД"].values,
                        index=df_prices["Артикул_clean"]
                    ).to_dict()

                wb_source = openpyxl.load_workbook(file_wb, data_only=True)
                ws_source = wb_source.active

                title = ws_source.cell(row=3, column=1).value
                if title:
                    st.caption(f"Документ: **{title}**")

                items = []
                for row in ws_source.iter_rows(min_row=11, values_only=True):
                    if isinstance(row[0], int):
                        raw_art = str(row[1]).strip() if row[1] is not None else ""
                        raw_qty = row[3]
                        raw_sum = row[4]

                        try:
                            qty = float(str(raw_qty).replace(" ", "").replace(",", ".")) if raw_qty is not None else 0
                            total_sum = float(str(raw_sum).replace(" ", "").replace(",", ".")) if raw_sum is not None else 0
                        except Exception:
                            qty = raw_qty if raw_qty else 0
                            total_sum = raw_sum if raw_sum else 0

                        price_per_item = total_sum / qty if qty > 0 else 0

                        item_tnved = tnved_dict.get(raw_art, "")
                        if pd.isna(item_tnved):
                            item_tnved = ""
                        tnved_str = str(item_tnved).strip()
                        if tnved_str.endswith(".0"):
                            tnved_str = tnved_str[:-2]
                        if tnved_str == "nan":
                            tnved_str = ""

                        items.append({
                            "article": raw_art,
                            "name": row[2],
                            "qty": qty,
                            "sum": total_sum,
                            "price": price_per_item,
                            "tnved": tnved_str,
                        })

                if not items:
                    st.warning("⚠️ Товары не найдены. Проверьте формат уведомления.")
                else:
                    st.success(f"✅ Найдено позиций: {len(items)}")
                    st.markdown("---")
                    st.write("**Выберите формат для скачивания:**")

                    txt_lines = []
                    txt_lines.append("Раздел G. Данные\u00a0по\u00a0товарам, работам,\u00a0услугам" + "\t" * 17)
                    txt_lines.append("\t" * 17)
                    headers_line = (
                        "Признак происхождения товара, работ, услуг*\t"
                        "Наименование товаров, работ, услуг*\t"
                        "Наименование товаров в соответствии с Декларацией на товары или заявлением о ввозе товаров и уплате косвенных налогов\t"
                        "Код товара (ТН ВЭД ЕАЭС)\tЕд. изм.\tКол-во (объем)\t"
                        "Цена (тариф) за единицу товара, работы, услуги без косвенных налогов\t"
                        "Стоимость товаров, работ, услуг без косвенных налогов*\t"
                        "Акциз- Ставка\tАкциз- Сумма\t"
                        "Размер оборота по реализации (облагаемый/необлагаемый оборот)*\t"
                        "НДС- Ставка\tНДС- Сумма\t"
                        "Стоимость товаров, работ, услуг с учетом косвенных налогов*\t"
                        "№ Декларации на товары, заявления в рамках ТС, СТ-1 или СТ-KZ\t"
                        "Номер товарной позиции из заявления в рамках ТС или Декларации на товары\t"
                        "Идентификатор товара, работ, услуг\tДополнительные данные"
                    )
                    txt_lines.append(headers_line)
                    txt_lines.append("\t" * 17)
                    txt_lines.append("2\t3\t3/1\t4\t5\t6\t7\t8\t9\t10\t11\t12\t13\t14\t15\t16\t17\t18")

                    for item in items:
                        qty_val = item["qty"]
                        qty_str = (
                            str(int(qty_val))
                            if isinstance(qty_val, float) and qty_val.is_integer()
                            else f"{qty_val:.2f}".replace(".", ",")
                        )
                        price_str = f"{item['price']:.2f}".replace(".", ",")
                        sum_str = f"{item['sum']:.2f}".replace(".", ",")

                        row_fields = [
                            "5", str(item["name"]), "", item["tnved"], "Штука",
                            qty_str, price_str, sum_str, "", "",
                            sum_str, "Без НДС", "0", sum_str, "", "", "1", ""
                        ]
                        txt_lines.append("\t".join(row_fields))

                    txt_content = "\r\n".join(txt_lines)
                    encoded_content = codecs.BOM_UTF16_LE + txt_content.encode("utf-16-le")

                    st.download_button(
                        label="📄 Скачать ЭСФ (.txt) — Рекомендуется",
                        data=encoded_content,
                        file_name=f"ЭСФ_{selected_shop}_{time.strftime('%d%m')}.txt",
                        mime="text/plain",
                        key="download_txt_btn"
                    )

                    st.caption("или")

                    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
                    TEMPLATE_ESF_FILE = os.path.join(BASE_DIR, "schedule.xlsx")

                    if not os.path.exists(TEMPLATE_ESF_FILE):
                        st.info("💡 Скачивание в Excel недоступно — файл 'schedule.xlsx' не найден в папке проекта.")
                    else:
                        try:
                            wb_template = openpyxl.load_workbook(TEMPLATE_ESF_FILE)
                            ws_template = wb_template.active

                            START_ROW = 6
                            for i, item in enumerate(items):
                                row_idx = START_ROW + i
                                ws_template.cell(row=row_idx, column=1, value=5)
                                ws_template.cell(row=row_idx, column=2, value=item["name"])
                                ws_template.cell(row=row_idx, column=3, value="")
                                ws_template.cell(row=row_idx, column=4, value=item["tnved"])
                                ws_template.cell(row=row_idx, column=5, value="Штука")
                                ws_template.cell(row=row_idx, column=6, value=item["qty"])
                                ws_template.cell(row=row_idx, column=7, value=item["price"])
                                ws_template.cell(row=row_idx, column=8, value=item["sum"])
                                ws_template.cell(row=row_idx, column=9, value="")
                                ws_template.cell(row=row_idx, column=10, value="")
                                ws_template.cell(row=row_idx, column=11, value=item["sum"])
                                ws_template.cell(row=row_idx, column=12, value="Без НДС")
                                ws_template.cell(row=row_idx, column=13, value=0)
                                ws_template.cell(row=row_idx, column=14, value=item["sum"])
                                ws_template.cell(row=row_idx, column=15, value="")
                                ws_template.cell(row=row_idx, column=16, value="")
                                ws_template.cell(row=row_idx, column=17, value=1)
                                ws_template.cell(row=row_idx, column=18, value="")

                            output_esf = BytesIO()
                            wb_template.save(output_esf)
                            output_esf.seek(0)

                            st.download_button(
                                label="⬇️ Скачать заполненный шаблон (.xlsx)",
                                data=output_esf.getvalue(),
                                file_name=f"ЭСФ_{selected_shop}_{time.strftime('%d%m')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="download_xlsx_btn"
                            )
                        except Exception as e:
                            st.error(f"Ошибка при сохранении в Excel-шаблон: {e}")

            except Exception as e:
                st.error(f"🔴 Ошибка при обработке уведомления: {e}")
