import json
from collections import Counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import streamlit as st

st.set_page_config(page_title="Supabase 데이터 대시보드", page_icon="🗃️", layout="wide")


def get_secret(name: str) -> str:
    """Reads Streamlit Cloud Secrets without a repository config file."""
    try:
        return str(st.secrets[name])
    except (KeyError, FileNotFoundError):
        st.error("Supabase 연결 정보가 없습니다.")
        st.info("Streamlit Cloud → App settings → Secrets에 SUPABASE_URL과 SUPABASE_KEY를 추가하세요.")
        st.code('SUPABASE_URL = "https://your-project.supabase.co"\nSUPABASE_KEY = "sb_publishable_..."', language="toml")
        st.stop()


def rest_request(url: str, key: str, table: str, method: str = "GET", query: dict | None = None, body: dict | None = None) -> list[dict[str, Any]]:
    endpoint = f"{url.rstrip('/')}/rest/v1/{quote(table, safe='')}"
    if query:
        endpoint += "?" + urlencode(query)
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers.update({"Content-Type": "application/json", "Prefer": "return=representation"})
        data = json.dumps(body).encode("utf-8")
    try:
        with urlopen(Request(endpoint, data=data, headers=headers, method=method), timeout=30) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else []
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8')}") from exc
    except URLError as exc:
        raise RuntimeError(f"연결 실패: {exc.reason}") from exc


def read_all(url: str, key: str, table: str) -> list[dict[str, Any]]:
    rows, offset, batch_size = [], 0, 1000
    while True:
        batch = rest_request(url, key, table, query={"select": "*", "offset": offset, "limit": batch_size})
        rows.extend(batch)
        if len(batch) < batch_size:
            return rows
        offset += batch_size


def parse_object(label: str, raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not value:
            raise ValueError
        return value
    except (ValueError, json.JSONDecodeError):
        st.error(f"{label}에는 비어 있지 않은 JSON 객체를 입력하세요.")
        return None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


st.title("🗃️ Supabase 데이터 대시보드")
st.caption("선택 테이블을 전체 조회해 분석하고, RLS 권한 범위에서 CRUD 작업을 실행합니다.")
url, key = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")

with st.sidebar:
    st.header("조회 설정")
    table = st.text_input("테이블명", value="bank_rates", help="public 스키마의 테이블명을 입력하세요.")
    st.warning("service_role 키는 사용하지 마세요. publishable/anon 키와 적절한 RLS 정책을 사용해야 합니다.")

if not table.strip():
    st.info("왼쪽에서 테이블명을 입력하세요.")
    st.stop()

try:
    rows = read_all(url, key, table.strip())
except Exception as exc:
    st.error(f"`{table}`을 조회하지 못했습니다: {exc}")
    st.info("테이블명, Data API 노출, SELECT RLS 정책을 확인하세요.")
    st.stop()

columns = list(dict.fromkeys(key for row in rows for key in row))
missing = sum(value is None for row in rows for value in row.values())
numeric_columns = [col for col in columns if any(is_number(row.get(col)) for row in rows)]
overview, analysis, crud = st.tabs(["데이터", "분석", "CRUD"])

with overview:
    c1, c2, c3 = st.columns(3)
    c1.metric("전체 행", f"{len(rows):,}")
    c2.metric("컬럼 수", f"{len(columns):,}")
    c3.metric("결측값", f"{missing:,}")
    if not rows:
        st.info("이 테이블에는 현재 데이터가 없습니다.")
    else:
        search = st.text_input("검색", placeholder="모든 컬럼에서 포함 문자를 검색합니다.")
        shown = rows if not search else [row for row in rows if search.lower() in json.dumps(row, ensure_ascii=False).lower()]
        st.caption(f"표시 행: {len(shown):,}")
        st.dataframe(shown, use_container_width=True, hide_index=True)

with analysis:
    if not rows:
        st.info("분석할 데이터가 없습니다.")
    else:
        st.subheader("데이터 품질")
        st.dataframe([{"컬럼": c, "결측값": sum(row.get(c) is None for row in rows), "고유값": len({str(row.get(c)) for row in rows if row.get(c) is not None})} for c in columns], use_container_width=True, hide_index=True)
        if numeric_columns:
            st.subheader("숫자형 요약")
            summary = []
            for c in numeric_columns:
                values = [float(r[c]) for r in rows if is_number(r.get(c))]
                summary.append({"컬럼": c, "개수": len(values), "평균": round(sum(values) / len(values), 4), "최솟값": min(values), "최댓값": max(values)})
            st.dataframe(summary, use_container_width=True, hide_index=True)
            selected = st.selectbox("분포를 볼 숫자 컬럼", numeric_columns)
            st.bar_chart([float(r[selected]) for r in rows if is_number(r.get(selected))])
        categories = [c for c in columns if c not in numeric_columns and len({str(r.get(c)) for r in rows if r.get(c) is not None}) <= 30]
        if categories:
            st.subheader("범주 분포")
            category = st.selectbox("집계를 볼 범주 컬럼", categories)
            st.bar_chart(dict(Counter(str(r.get(category, "(결측)")) for r in rows).most_common(20)))
        st.caption("분석 결과는 선택한 테이블에서 현재 조회한 데이터만 사용합니다.")

with crud:
    st.warning("변경은 되돌리기 어렵습니다. CRUD 권한은 Supabase RLS 정책이 결정합니다.")
    create_tab, update_tab, delete_tab = st.tabs(["등록", "수정", "삭제"])
    with create_tab:
        with st.form("create"):
            raw = st.text_area("새 레코드(JSON)", placeholder='{"bank_name": "예시은행", "base_rate": 3.2}', height=140)
            submitted = st.form_submit_button("등록 실행", type="primary")
        if submitted and (payload := parse_object("새 레코드", raw)):
            try:
                st.success(f"등록 완료: {len(rest_request(url, key, table.strip(), 'POST', body=payload))}건")
            except Exception as exc:
                st.error(f"등록 실패: {exc}")
    with update_tab:
        with st.form("update"):
            col, value = st.text_input("일치 컬럼", placeholder="id"), st.text_input("일치 값", placeholder="예: 1")
            raw = st.text_area("수정할 값(JSON)", placeholder='{"base_rate": 3.4}', height=140)
            submitted = st.form_submit_button("수정 실행", type="primary")
        if submitted:
            payload = parse_object("수정할 값", raw)
            if not (col and value): st.error("일치 컬럼과 값을 모두 입력하세요.")
            elif payload:
                try:
                    st.success(f"수정 완료: {len(rest_request(url, key, table.strip(), 'PATCH', {col: f'eq.{value}'}, payload))}건")
                except Exception as exc: st.error(f"수정 실패: {exc}")
    with delete_tab:
        with st.form("delete"):
            col, value = st.text_input("삭제 일치 컬럼", placeholder="id"), st.text_input("삭제 일치 값", placeholder="예: 1")
            confirmed = st.checkbox("이 삭제 작업은 되돌릴 수 없음을 확인했습니다.")
            submitted = st.form_submit_button("삭제 실행", type="primary")
        if submitted:
            if not (col and value and confirmed): st.error("일치 컬럼, 값, 확인란을 모두 입력하세요.")
            else:
                try:
                    st.success(f"삭제 완료: {len(rest_request(url, key, table.strip(), 'DELETE', {col: f'eq.{value}'}))}건")
                except Exception as exc: st.error(f"삭제 실패: {exc}")
