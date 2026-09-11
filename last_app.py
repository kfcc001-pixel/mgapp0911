"""Supabase INSERT/UPDATE 알림을 Gmail로 전송하는 FastAPI 앱.

설치:
    pip install fastapi==0.116.1 uvicorn[standard]==0.35.0 \
        google-api-python-client==2.179.0 google-auth==2.40.3 \
        google-auth-oauthlib==1.2.2

환경변수:
    WEBHOOK_SECRET       Supabase Webhook의 Authorization 헤더와 맞출 긴 임의 문자열
    GMAIL_TO             수신 이메일 주소(쉼표로 여러 개 지정 가능)
    GOOGLE_TOKEN_JSON    Gmail OAuth token.json 전체 내용을 JSON 문자열로 저장
    ALLOWED_TABLES       선택 사항. 허용할 테이블명(쉼표 구분); 비우면 모든 테이블
    REDACT_FIELDS        선택 사항. 마스킹할 컬럼명(쉼표 구분)

최초 OAuth 토큰 생성:
    1. Google Cloud에서 Gmail API를 켜고 OAuth 데스크톱 앱 credentials.json을 받습니다.
    2. python app.py --authorize credentials.json
    3. 출력된 JSON 전체를 배포 환경의 GOOGLE_TOKEN_JSON Secret으로 등록합니다.

실행:
    uvicorn app:app --host 0.0.0.0 --port 8000

Supabase Dashboard > Database > Webhooks에서 INSERT와 UPDATE를 선택하고:
    URL: https://<배포주소>/supabase-webhook
    Header: Authorization = Bearer <WEBHOOK_SECRET 값>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from email.message import EmailMessage
from html import escape
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pydantic import BaseModel, ConfigDict

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
DEFAULT_REDACT_FIELDS = {
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "authorization", "api_key", "apikey", "ssn", "resident_number",
    "주민등록번호", "계좌비밀번호",
}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("supabase-gmail-webhook")
app = FastAPI(title="Supabase Gmail Notifier", docs_url=None, redoc_url=None)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    table: str
    schema_name: str | None = None
    record: dict[str, Any] | None = None
    old_record: dict[str, Any] | None = None


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 환경변수 {name}이(가) 없습니다.")
    return value


def csv_env(name: str) -> set[str]:
    return {v.strip() for v in os.getenv(name, "").split(",") if v.strip()}


def mask_rows(value: Any, redacted: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in redacted else mask_rows(item, redacted)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_rows(item, redacted) for item in value]
    return value


def gmail_credentials() -> Credentials:
    try:
        info = json.loads(required_env("GOOGLE_TOKEN_JSON"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_TOKEN_JSON이 올바른 JSON이 아닙니다.") from exc
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    if not creds.valid:
        raise RuntimeError("Gmail OAuth 토큰이 유효하지 않습니다. 다시 인증하세요.")
    return creds


def email_content(payload: WebhookPayload) -> tuple[str, str, str]:
    event = payload.type.upper()
    redacted = {x.lower() for x in DEFAULT_REDACT_FIELDS | csv_env("REDACT_FIELDS")}
    before = mask_rows(payload.old_record, redacted)
    after = mask_rows(payload.record, redacted)
    occurred_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    subject = f"[Supabase {event}] {payload.table} 테이블 변경 알림"
    text = (
        f"Supabase 데이터 변경이 감지되었습니다.\n\n"
        f"이벤트: {event}\n스키마: {payload.schema_name or 'public'}\n"
        f"테이블: {payload.table}\n수신 시각: {occurred_at}\n\n"
        f"변경 전:\n{json.dumps(before, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"변경 후:\n{json.dumps(after, ensure_ascii=False, indent=2, default=str)}"
    )
    html = f"""
    <h2>Supabase 데이터 변경 알림</h2>
    <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">
      <tr><th>이벤트</th><td>{escape(event)}</td></tr>
      <tr><th>스키마</th><td>{escape(payload.schema_name or 'public')}</td></tr>
      <tr><th>테이블</th><td>{escape(payload.table)}</td></tr>
      <tr><th>수신 시각</th><td>{escape(occurred_at)}</td></tr>
    </table>
    <h3>변경 전</h3><pre>{escape(json.dumps(before, ensure_ascii=False, indent=2, default=str))}</pre>
    <h3>변경 후</h3><pre>{escape(json.dumps(after, ensure_ascii=False, indent=2, default=str))}</pre>
    """
    return subject, text, html


def send_gmail(subject: str, text: str, html: str) -> str:
    recipients = [x.strip() for x in required_env("GMAIL_TO").split(",") if x.strip()]
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = build("gmail", "v1", credentials=gmail_credentials(), cache_discovery=False)
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return str(result["id"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/supabase-webhook")
async def supabase_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    expected = f"Bearer {required_env('WEBHOOK_SECRET')}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    raw = await request.body()
    try:
        data = json.loads(raw)
        # Supabase payload uses the key `schema`; avoid shadowing Pydantic internals.
        if "schema" in data:
            data["schema_name"] = data.pop("schema")
        payload = WebhookPayload.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    event = payload.type.upper()
    if event not in {"INSERT", "UPDATE"}:
        raise HTTPException(status_code=422, detail="Only INSERT and UPDATE are accepted")
    allowed = csv_env("ALLOWED_TABLES")
    if allowed and payload.table not in allowed:
        raise HTTPException(status_code=403, detail="Table is not allowed")

    # 요청 본문 해시는 문제 추적용이며 원문 데이터는 로그에 남기지 않습니다.
    event_hash = hashlib.sha256(raw).hexdigest()[:12]
    try:
        message_id = send_gmail(*email_content(payload))
    except Exception as exc:
        logger.exception("메일 발송 실패 event=%s", event_hash)
        raise HTTPException(status_code=502, detail="Gmail delivery failed") from exc
    logger.info("메일 발송 성공 event=%s gmail_message_id=%s", event_hash, message_id)
    return {"status": "sent", "message_id": message_id}


def authorize(credentials_path: str) -> None:
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)
    print(creds.to_json())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize", metavar="CREDENTIALS_JSON")
    args = parser.parse_args()
    if args.authorize:
        authorize(args.authorize)
    else:
        print("서버 실행: uvicorn app:app --host 0.0.0.0 --port 8000", file=sys.stderr)
