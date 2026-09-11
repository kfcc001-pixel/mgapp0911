import html
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st


SUPABASE_URL = "https://nxjqmfooxmyqufkzgpre.supabase.co"
SUPABASE_KEY = "sb_publishable_8pM7FWQIBXFKLHWj0xBKlQ_it3zttv_"
TABLE = "food_nutrition"
PAGE_SIZE = 50

LIST_COLUMNS = (
    "food_code,food_name,data_type_name,manufacturer_name,"
    "nutrient_reference_amount,energy_kcal,protein_g,fat_g,carbohydrate_g,"
    "sugars_g,dietary_fiber_g,sodium_mg"
)

NUTRIENTS = [
    ("energy_kcal", "에너지", "kcal"),
    ("moisture_g", "수분", "g"),
    ("protein_g", "단백질", "g"),
    ("fat_g", "지방", "g"),
    ("ash_g", "회분", "g"),
    ("carbohydrate_g", "탄수화물", "g"),
    ("sugars_g", "당류", "g"),
    ("dietary_fiber_g", "식이섬유", "g"),
    ("calcium_mg", "칼슘", "mg"),
    ("iron_mg", "철", "mg"),
    ("phosphorus_mg", "인", "mg"),
    ("potassium_mg", "칼륨", "mg"),
    ("sodium_mg", "나트륨", "mg"),
    ("vitamin_a_ug_rae", "비타민 A", "μg RAE"),
    ("retinol_ug", "레티놀", "μg"),
    ("beta_carotene_ug", "베타카로틴", "μg"),
    ("thiamine_mg", "티아민", "mg"),
    ("riboflavin_mg", "리보플라빈", "mg"),
    ("niacin_mg", "니아신", "mg"),
    ("vitamin_c_mg", "비타민 C", "mg"),
    ("vitamin_d_ug", "비타민 D", "μg"),
    ("cholesterol_mg", "콜레스테롤", "mg"),
    ("saturated_fat_g", "포화지방", "g"),
    ("trans_fat_g", "트랜스지방", "g"),
]


st.set_page_config(page_title="식품 영양성분 검색", page_icon="🥗", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background: #f6f8f5; }
    .hero { padding: 2rem 2.2rem; border-radius: 24px; color: white;
            background: linear-gradient(125deg, #164e3d 0%, #2f7d5d 60%, #75a843 100%);
            box-shadow: 0 12px 30px rgba(22,78,61,.18); margin-bottom: 1.4rem; }
    .hero h1 { margin: 0; font-size: 2.2rem; letter-spacing: -.04em; }
    .hero p { margin: .55rem 0 0; opacity: .9; }
    .food-card { padding: 1.25rem 1.4rem; border: 1px solid #dce7df; border-radius: 18px;
                 background: white; margin: .5rem 0 1rem; }
    .muted { color: #65736b; font-size: .9rem; }
    div[data-testid="stMetric"] { background: white; border: 1px solid #dce7df;
                                  padding: 1rem; border-radius: 16px; }
    </style>
    <div class="hero">
      <h1>🥗 식품 영양성분 검색</h1>
      <p>식품의약품안전처 표준데이터에서 식품명 또는 제조사를 부분 검색합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def clean_term(value: str) -> str:
    """PostgREST 필터 구문 문자를 제거해 안전한 부분 검색어로 만든다."""
    return re.sub(r"[(),.*%_]", " ", value).strip()[:80]


def api_get(params: dict[str, str], *, count: bool = False) -> tuple[list[dict[str, Any]], int | None]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    if count:
        headers["Prefer"] = "count=exact"
    request = Request(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?{urlencode(params)}",
        headers=headers,
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
        content_range = response.headers.get("Content-Range", "")
    total = None
    if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
        total = int(content_range.rsplit("/", 1)[1])
    return payload, total


@st.cache_data(ttl=120, show_spinner=False)
def search_foods(term: str, page: int) -> tuple[list[dict[str, Any]], int | None]:
    start = page * PAGE_SIZE
    params = {
        "select": LIST_COLUMNS,
        "order": "food_name.asc",
        "offset": str(start),
        "limit": str(PAGE_SIZE),
    }
    if term:
        pattern = f"*{term}*"
        params["or"] = f"(food_name.ilike.{pattern},manufacturer_name.ilike.{pattern})"
    return api_get(params, count=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_food(food_code: str) -> dict[str, Any] | None:
    rows, _ = api_get({"select": "*", "food_code": f"eq.{food_code}", "limit": "1"})
    return rows[0] if rows else None


def show_value(value: Any, unit: str = "") -> str:
    if value is None or value == "":
        return "자료 없음"
    if isinstance(value, float):
        value = f"{value:g}"
    return f"{value} {unit}".strip()


query = st.text_input(
    "식품명 또는 제조사 검색",
    placeholder="예: 대추, 쿠키, 이디야",
    help="검색어 일부만 입력해도 ILIKE 방식으로 찾아줍니다.",
)
term = clean_term(query)
if query and not term:
    st.warning("한글, 영문 또는 숫자가 포함된 검색어를 입력해 주세요.")

if st.session_state.get("last_term") != term:
    st.session_state.page = 0
    st.session_state.last_term = term
page = st.session_state.get("page", 0)

try:
    with st.spinner("영양성분 데이터를 찾고 있습니다..."):
        foods, total = search_foods(term, page)
except (HTTPError, URLError, TimeoutError) as error:
    st.error("데이터베이스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    st.caption(f"연결 오류: {error}")
    st.stop()

title = f"‘{term}’ 검색 결과" if term else "등록 식품 둘러보기"
st.subheader(title)
if total is not None:
    st.caption(f"총 {total:,}건 중 {page * PAGE_SIZE + 1:,}~{page * PAGE_SIZE + len(foods):,}건")

if not foods:
    st.info("일치하는 식품이 없습니다. 검색어를 짧게 입력해 보세요.")
    st.stop()

labels = []
lookup = {}
for food in foods:
    maker = food.get("manufacturer_name")
    label = f"{food['food_name']} · {maker}" if maker and maker != "해당없음" else food["food_name"]
    if label in lookup:
        label = f"{label} · {food['food_code']}"
    labels.append(label)
    lookup[label] = food["food_code"]

selected_label = st.selectbox("상세 정보를 확인할 식품", labels)

left, middle, right = st.columns([1, 1, 4])
with left:
    if st.button("← 이전", disabled=page == 0, use_container_width=True):
        st.session_state.page = page - 1
        st.rerun()
with middle:
    no_next = len(foods) < PAGE_SIZE or (total is not None and (page + 1) * PAGE_SIZE >= total)
    if st.button("다음 →", disabled=no_next, use_container_width=True):
        st.session_state.page = page + 1
        st.rerun()

detail = get_food(lookup[selected_label])
if not detail:
    st.warning("선택한 식품의 상세정보를 찾지 못했습니다.")
    st.stop()

maker = detail.get("manufacturer_name")
safe_name = html.escape(str(detail["food_name"]))
safe_code = html.escape(str(detail["food_code"]))
safe_type = html.escape(str(detail.get("data_type_name") or "분류 없음"))
safe_maker = html.escape(str(maker if maker and maker != "해당없음" else "자료 없음"))
safe_reference = html.escape(str(detail.get("nutrient_reference_amount") or "자료 없음"))
st.markdown(
    f"""
    <div class="food-card">
      <h2 style="margin:0 0 .45rem">{safe_name}</h2>
      <div class="muted">식품코드 {safe_code} · {safe_type} ·
      제조사 {safe_maker} · 기준량 {safe_reference}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

metric_columns = st.columns(6)
for column, (key, label, unit) in zip(
    metric_columns,
    [NUTRIENTS[0], NUTRIENTS[2], NUTRIENTS[3], NUTRIENTS[5], NUTRIENTS[6], NUTRIENTS[12]],
):
    column.metric(label, show_value(detail.get(key), unit))

st.markdown("### 전체 영양성분")
nutrition_rows = [
    {"영양성분": label, "함량": show_value(detail.get(key), unit)}
    for key, label, unit in NUTRIENTS
]
st.dataframe(nutrition_rows, hide_index=True, use_container_width=True)

with st.expander("제품 및 데이터 출처 정보"):
    info = {
        "식품중량": detail.get("food_weight"),
        "수입 여부": detail.get("imported_yn"),
        "원산지": detail.get("origin_country_name"),
        "품목제조보고번호": detail.get("item_report_number"),
        "업체명": detail.get("company_name"),
        "제조사명": detail.get("manufacturer_name"),
        "수입업체명": detail.get("importer_name"),
        "유통업체명": detail.get("distributor_name"),
        "데이터 생성방법": detail.get("generation_method_name"),
        "데이터 생성일": detail.get("generated_date"),
        "데이터 기준일": detail.get("reference_date"),
        "출처": detail.get("source_name"),
    }
    st.dataframe(
        [{"항목": key, "내용": value or "자료 없음"} for key, value in info.items()],
        hide_index=True,
        use_container_width=True,
    )

st.caption("영양값이 비어 있는 항목은 ‘자료 없음’으로 표시됩니다. 식품의약품안전처 제공 데이터 기준입니다.")
