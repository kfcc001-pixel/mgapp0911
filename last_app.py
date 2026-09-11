"""Single-file Streamlit Supabase CRUD dashboard with Gmail notifications.

Install:
  pip install streamlit==1.49.1 requests==2.32.5 pandas==2.3.2 google-api-python-client==2.179.0 google-auth==2.40.3
Run: streamlit run app.py

Streamlit Secrets: GMAIL_TO and GOOGLE_TOKEN_JSON.
"""
from __future__ import annotations
import base64
import json
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DEFAULT_URL = "https://nxjqmfooxmyqufkzgpre.supabase.co"
DEFAULT_KEY = "sb_publishable_8pM7FWQIBXFKLHWj0xBKlQ_it3zttv_"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
MASK_FIELDS = {"password", "passwd", "secret", "token", "access_token",
               "refresh_token", "authorization", "api_key", "apikey",
               "ssn", "resident_number", "주민등록번호", "계좌비밀번호"}
st.set_page_config(page_title="Supabase CRUD", page_icon="🗄️", layout="wide")


def setting(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default


URL = setting("SUPABASE_URL", DEFAULT_URL).rstrip("/")
KEY = setting("SUPABASE_KEY", DEFAULT_KEY)


def headers(prefer: str | None = None) -> dict[str, str]:
    value = {"apikey": KEY, "Authorization": f"Bearer {KEY}",
             "Content-Type": "application/json"}
    if prefer:
        value["Prefer"] = prefer
    return value


def check(response: requests.Response) -> Any:
    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Supabase 오류 ({response.status_code}): {detail}")
    return response.json() if response.content else None


@st.cache_data(ttl=300, show_spinner=False)
def schema_spec(url: str, key: str) -> dict[str, Any]:
    return check(requests.get(f"{url}/rest/v1/",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15))


def rows(table: str, limit: int) -> list[dict[str, Any]]:
    return check(requests.get(f"{URL}/rest/v1/{quote(table, safe='')}",
        headers=headers(), params={"select": "*", "limit": limit}, timeout=30))


def mutate(method: str, table: str, data=None, params=None) -> list[dict[str, Any]]:
    response = requests.request(method, f"{URL}/rest/v1/{quote(table, safe='')}",
        headers=headers("return=representation"), json=data, params=params, timeout=30)
    return check(response) or []


def mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "***REDACTED***" if k.lower() in MASK_FIELDS else mask(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def notify(event: str, table: str, before: Any, after: Any) -> None:
    recipient, token = setting("GMAIL_TO"), setting("GOOGLE_TOKEN_JSON")
    if not recipient or not token:
        st.warning("DB 작업은 완료됐지만 Gmail Secrets가 없어 메일은 보내지 않았습니다.")
        return
    try:
        creds = Credentials.from_authorized_user_info(json.loads(token), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        if not creds.valid:
            raise RuntimeError("Gmail OAuth 토큰이 유효하지 않습니다.")
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = f"[Supabase {event}] {table} 변경 알림"
        message.set_content(
            f"작업: {event}\n테이블: {table}\n\n변경 전:\n"
            f"{json.dumps(mask(before), ensure_ascii=False, indent=2, default=str)}\n\n"
            f"변경 후:\n{json.dumps(mask(after), ensure_ascii=False, indent=2, default=str)}")
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        st.success(f"Gmail 발송 완료 (ID: {result['id']})")
    except Exception as exc:
        st.warning(f"DB 작업은 완료됐지만 Gmail 발송에 실패했습니다: {exc}")


def object_json(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('JSON 객체여야 합니다. 예: {"name": "홍길동"}')
    return value


st.title("Supabase CRUD 대시보드")
st.caption("설정된 Supabase 프로젝트에 자동 연결됩니다. URL이나 키를 입력할 필요가 없습니다.")
try:
    spec = schema_spec(URL, KEY)
    tables = sorted(spec.get("definitions", {}).keys())
except Exception as exc:
    st.error(f"Supabase 연결 실패: {exc}")
    st.stop()
if not tables:
    st.warning("현재 publishable key와 RLS 정책으로 조회 가능한 테이블이 없습니다.")
    st.stop()

c1, c2 = st.columns([3, 1])
table = c1.selectbox("테이블", tables)
limit = c2.number_input("최대 조회 건수", 1, 10000, 100, 50)
columns = list(spec.get("definitions", {}).get(table, {}).get("properties", {}).keys())
try:
    current = rows(table, int(limit))
    frame = pd.DataFrame(current)
except Exception as exc:
    st.error(str(exc))
    current, frame = [], pd.DataFrame()

m1, m2, m3 = st.columns(3)
m1.metric("조회 행", len(frame))
m2.metric("컬럼", len(frame.columns) if not frame.empty else len(columns))
m3.metric("Gmail", "설정됨" if setting("GMAIL_TO") and setting("GOOGLE_TOKEN_JSON") else "미설정")
st.dataframe(frame, use_container_width=True, hide_index=True)
if st.button("새로고침", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

available = list(frame.columns) if not frame.empty else columns
insert_tab, update_tab, delete_tab = st.tabs(["등록 (INSERT)", "수정 (UPDATE)", "삭제 (DELETE)"])

with insert_tab:
    raw = st.text_area("등록 데이터(JSON)", "{}", height=160, key="insert")
    if st.button("데이터 등록", type="primary", use_container_width=True):
        try:
            result = mutate("POST", table, object_json(raw))
            st.success(f"{len(result)}건을 등록했습니다.")
            st.json(result)
            notify("INSERT", table, None, result)
            st.cache_data.clear()
        except Exception as exc:
            st.error(str(exc))

with update_tab:
    if available:
        key_col = st.selectbox("조건 컬럼", available, key="uk")
        key_val = st.text_input("조건 값", key="uv", placeholder="예: 55")
        raw = st.text_area("수정 데이터(JSON)", "{}", height=140, key="update")
        st.caption("조건과 일치하는 모든 행이 수정됩니다.")
        if st.button("데이터 수정", type="primary", use_container_width=True):
            try:
                if not key_val:
                    raise ValueError("조건 값을 입력하세요.")
                before = [r for r in current if str(r.get(key_col)) == key_val]
                result = mutate("PATCH", table, object_json(raw), {key_col: f"eq.{key_val}"})
                st.success(f"{len(result)}건을 수정했습니다.")
                st.json(result)
                notify("UPDATE", table, before, result)
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))
    else:
        st.info("수정 가능한 컬럼이 없습니다.")

with delete_tab:
    if available:
        key_col = st.selectbox("조건 컬럼", available, key="dk")
        key_val = st.text_input("조건 값", key="dv", placeholder="예: 55")
        confirm = st.text_input("삭제 확인", key="dc", placeholder="DELETE 입력")
        st.warning("조건과 일치하는 모든 행이 삭제되며 되돌릴 수 없습니다.")
        if st.button("데이터 삭제", type="primary", use_container_width=True):
            try:
                if not key_val or confirm != "DELETE":
                    raise ValueError("조건 값을 입력하고 DELETE로 확인하세요.")
                result = mutate("DELETE", table, params={key_col: f"eq.{key_val}"})
                st.success(f"{len(result)}건을 삭제했습니다.")
                st.json(result)
                notify("DELETE", table, result, None)
                st.cache_data.clear()
            except Exception as exc:
                st.error(str(exc))
    else:
        st.info("삭제 조건으로 사용할 컬럼이 없습니다.")

with st.expander("운영 및 보안 안내"):
    st.markdown("""
- 내장된 키는 공개용 publishable key이며 실제 CRUD 범위는 Supabase RLS 정책을 따릅니다.
- service-role key는 코드에 넣지 마세요.
- Gmail은 이 화면에서 성공한 CRUD 작업에 대해 발송됩니다.
- 다른 프로그램의 DB 변경까지 상시 감지하려면 별도의 Database Webhook 서버가 필요합니다.
""")
