import streamlit as st
import pandas as pd
import gspread
import requests
from google.oauth2.service_account import Credentials
from io import BytesIO
import time
import streamlit_authenticator as stauth
import os
import openpyxl
import codecs

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
    selected_shop = st.selectbox("🎯 Выберите магазин:", SHOPS)
with col_refresh:
    st.markdown("<div style='margin-top: 28px'>", unsafe_allow_html=True)
    if st.button("🔄", help="Обновить прайс из Google Sheets"):
        st.cache_data.clear()
        st.rerun()

current_ff_rate = 0 if selected_shop in SHOPS_WITHOUT_FF else DEFAULT_FF_COST

# --- ЗАГРУЗКА ПРАЙСА ---
with st.spinner("⏳ Синхронизация с Google Sheets..."):
    df_prices, error = load_prices_from_gsheets(selected_shop, os.environ["SHEET_URL"])

if error:
    st.error(f"❌ {error}")
    st.stop()

if df_prices is None or df_prices.empty:
    st.error("❌ Прайс не загружен — получен пустой результат из Google Sheets")
    st.stop()

st.caption(f"✅ Прайс обновлен в {time.strftime('%H:%M')} | {len(df_prices)} SKU")

st.subheader(f"🚚 Поставка: {selected_shop}")

# ==========================================
# ТРИ ВКЛАДКИ ДЛЯ ВСЕХ МАГАЗИНОВ
# ==========================================
tab_api, tab_file, tab_esf = st.tabs(["🔗 Wildberries API", "📂 Загрузка Excel", "📝 ЭСФ"])

shop_env_map = {
    "Абеденов": "WB_KEY_ABEDENOV",
    # добавьте остальные магазины если появятся ключи
}

# ------------------------------------------
# ВКЛАДКА 1: WILDBERRIES API
# ------------------------------------------
with tab_api:
    env_var = shop_env_map.get(selected_shop)
    api_key = os.environ.get(env_var) if env_var else None
    if not api_key:
        st.info("⚠️ API ключ не найден для этого магазина. Используйте вкладку «Загрузка Excel» или добавьте ключ в настройки.")
    else:
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        sid = c1.text_input("ID Поставки", placeholder="WB-GI-...")
        if c2.button("Получить", use_container_width=True) and sid:
            summary_api, api_err = get_supply_orders(sid.strip(), api_key)
            if api_err:
                st.error(api_err)
            elif summary_api is not None:
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
# ТН ВЭД берётся из прайса Google Sheets (колонка "ТН ВЭД"),
# если колонки нет — поле остаётся пустым (каждый магазин имеет свои коды)
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
            # Словарь ТН ВЭД из прайса — если колонка есть, используем её
            tnved_dict = {}
            if "Артикул" in df_prices.columns and "ТН ВЭД" in df_prices.columns:
                df_prices["Артикул_clean"] = df_prices["Артикул"].astype(str).str.strip()
                tnved_dict = pd.Series(
                    df_prices["ТН ВЭД"].values,
                    index=df_prices["Артикул_clean"]
                ).to_dict()

            # Читаем уведомление о выкупе WB
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

                    # ТН ВЭД: берём из прайса по артикулу, если есть — иначе пусто
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
                st.warning("⚠️ Товары не найдены. Проверьте формат уведомления (данные с 11-й строки, номер п/п в колонке A).")
            else:
                st.success(f"✅ Найдено позиций: {len(items)}")
                st.markdown("---")
                st.write("**Выберите формат для скачивания:**")

                # Формируем TXT (UTF-16 LE для портала ЭСФ)
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

                # Excel-шаблон (schedule.xlsx)
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
                            ws_template.cell(row=row_idx, column=4, value=item["tnved"])  # пусто если нет в прайсе
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