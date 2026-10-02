# -*- coding: utf-8 -*-
"""선택 로그인 + 저장 기능 (카카오 OAuth / 이메일 매직링크).

원칙:
- 로그인은 절대 필수가 아니다. /api/chart 등 기존 무료 기능은 비로그인으로 그대로 동작한다.
- 비밀값(JWT, 매직링크 원문, 토큰)은 절대 로그에 남기지 않는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

log = logging.getLogger("vedic.auth")

# --- 환경 변수 (전부 미설정이어도 앱이 정상 부팅되어야 한다) ---
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://vedic.co.kr")
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    log.warning("JWT_SECRET 미설정: 개발용 임시 키를 사용합니다. 배포 환경에서는 반드시 설정해 주세요.")

DATABASE_URL = os.environ.get("DATABASE_URL", "") or "sqlite:///./dev.db"
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
_engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite는 tz-aware 컬럼도 naive로 돌려주므로 UTC로 정규화."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _uid() -> str:
    return uuid.uuid4().hex


# --- 테이블 ---
class User(Base):
    __tablename__ = "users"
    id = Column(String(32), primary_key=True, default=_uid)
    provider = Column(String(16), nullable=False)          # 'kakao' | 'email'
    provider_key = Column(String(128), nullable=False)    # kakao id 또는 이메일
    nickname = Column(String(64), nullable=False, default="회원")
    email = Column(String(254), nullable=True)
    consent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    __table_args__ = (UniqueConstraint("provider", "provider_key", name="uq_user_provider"),)


class Chart(Base):
    __tablename__ = "charts"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    dob = Column(String(32), nullable=False)
    tob = Column(String(32), nullable=True)
    place = Column(String(64), nullable=False)
    chandra_mode = Column(Boolean, nullable=False, default=False)
    label = Column(String(128), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class EmailToken(Base):
    __tablename__ = "email_tokens"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, nullable=False, default=False)


class Inquiry(Base):
    __tablename__ = "inquiries"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    contact = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


Base.metadata.create_all(_engine)
log.info("DB 테이블 준비 완료")


# --- JWT ---
JWT_EXP_DAYS = 30


def _issue_jwt(user_id: str) -> str:
    payload = {"sub": user_id, "exp": _now() + dt.timedelta(days=JWT_EXP_DAYS)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _user_payload(user: User) -> dict:
    return {"id": user.id, "nickname": user.nickname, "provider": user.provider}


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "로그인이 필요합니다")
    raw = authorization[7:].strip()
    try:
        payload = jwt.decode(raw, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "로그인이 필요합니다")
    user = db.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """토큰이 없거나 유효하지 않으면 None (401을 내지 않음)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(authorization[7:].strip(), JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return db.get(User, payload.get("sub"))


router = APIRouter()


# --- 카카오 로그인 ---
class KakaoCodeRequest(BaseModel):
    code: str = Field(..., max_length=1024)


@router.post("/api/auth/kakao")
def kakao_login(req: KakaoCodeRequest, db: Session = Depends(get_db)):
    import httpx

    token_url = "https://kauth.kakao.com/oauth/token"
    try:
        tr = httpx.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": KAKAO_REST_KEY,
                "redirect_uri": FRONTEND_URL + "/",
                "code": req.code,
            },
            timeout=10,
        )
        tdata = tr.json()
        access_token = tdata.get("access_token")
        if not access_token:
            log.warning("카카오 토큰 발급 실패: status=%s", tr.status_code)
            raise HTTPException(502, "카카오 인증에 실패했습니다")
        mr = httpx.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        mdata = mr.json()
        kakao_id = mdata.get("id")
        if kakao_id is None:
            log.warning("카카오 사용자 조회 실패: status=%s", mr.status_code)
            raise HTTPException(502, "카카오 인증에 실패했습니다")
    except HTTPException:
        raise
    except Exception as exc:  # 네트워크 오류 등
        log.warning("카카오 인증 통신 오류: %s", type(exc).__name__)
        raise HTTPException(502, "카카오 인증에 실패했습니다")

    profile = (mdata.get("kakao_account") or {}).get("profile") or {}
    nickname = profile.get("nickname") or "회원"

    user = (
        db.query(User)
        .filter_by(provider="kakao", provider_key=str(kakao_id))
        .one_or_none()
    )
    is_new = user is None
    if is_new:
        user = User(provider="kakao", provider_key=str(kakao_id), nickname=nickname)
        db.add(user)
        db.commit()
        db.refresh(user)

    return {
        "token": _issue_jwt(user.id),
        "user": _user_payload(user),
        "is_new": is_new,
        "need_consent": user.consent_at is None,
    }


# --- 이메일 매직링크 ---
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailRequest(BaseModel):
    email: str = Field(..., max_length=254)


def _send_magic_link(email: str, link: str) -> None:
    if RESEND_API_KEY:
        try:
            import resend

            resend.api_key = RESEND_API_KEY
            resend.Emails.send(
                {
                    "from": RESEND_FROM,
                    "to": [email],
                    "subject": "베딕 쿤달리 로그인 링크",
                    "text": (
                        "아래 링크를 눌러 로그인하세요.\n\n"
                        f"{link}\n\n"
                        "링크는 15분 동안 유효합니다."
                    ),
                    "html": (
                        "<p>아래 링크를 눌러 로그인하세요.</p>"
                        f'<p><a href="{link}">{link}</a></p>'
                        "<p>링크는 15분 동안 유효합니다.</p>"
                    ),
                }
            )
        except Exception as exc:
            log.error("매직링크 메일 발송 실패: %s", type(exc).__name__)
    else:
        # 개발 모드: 콘솔에 링크 출력 (실제 발송 안 함)
        print(f"DEV MAGIC LINK for {email}: {link}", flush=True)


@router.post("/api/auth/email/request")
def email_request(req: EmailRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "이메일 주소를 확인해 주세요.")

    user = (
        db.query(User).filter_by(provider="email", provider_key=email).one_or_none()
    )
    if user is None:
        user = User(provider="email", provider_key=email, nickname="회원", email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(
        EmailToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_now() + dt.timedelta(minutes=15),
        )
    )
    db.commit()

    link = FRONTEND_URL + "/?login_token=" + token
    _send_magic_link(email, link)
    return {"ok": True}


class EmailConsumeRequest(BaseModel):
    token: str = Field("", max_length=256)


@router.post("/api/auth/email/consume")
def email_consume(req: EmailConsumeRequest, db: Session = Depends(get_db)):
    token = req.token
    if not token:
        raise HTTPException(400, "링크가 만료되었거나 유효하지 않습니다")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = (
        db.query(EmailToken)
        .filter_by(token_hash=token_hash, used=False)
        .one_or_none()
    )
    exp = _aware(row.expires_at) if row else None
    if row is None or exp is None or exp < _now():
        raise HTTPException(400, "링크가 만료되었거나 유효하지 않습니다")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(400, "링크가 만료되었거나 유효하지 않습니다")

    created = _aware(user.created_at)
    is_new = (
        user.consent_at is None
        and created is not None
        and created >= _now() - dt.timedelta(minutes=20)
    )
    resp = {
        "token": _issue_jwt(user.id),
        "user": _user_payload(user),
        "is_new": is_new,
        "need_consent": user.consent_at is None,
    }
    # 응답을 다 만든 뒤에 사용 처리 (서버 오류 시 링크가 소모되지 않도록)
    row.used = True
    db.commit()
    return resp


# --- 동의 / 내 정보 / 탈퇴 ---
class ConsentRequest(BaseModel):
    agree: bool = False


@router.post("/api/auth/consent")
def consent(req: ConsentRequest, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    if req.agree is not True:
        raise HTTPException(400, "약관에 동의해 주세요.")
    user.consent_at = _now()
    db.commit()
    return {"ok": True}


@router.get("/api/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "nickname": user.nickname,
        "provider": user.provider,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.post("/api/auth/delete")
def delete_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(user)  # charts/email_tokens은 CASCADE, inquiries는 SET NULL
    db.commit()
    return {"ok": True}


# --- 저장 차트 ---
class ChartSaveRequest(BaseModel):
    dob: str = Field("", max_length=32)
    tob: str | None = Field(None, max_length=32)
    place: str = Field("", max_length=64)
    chandra_mode: bool = False


@router.post("/api/charts")
def save_chart(req: ChartSaveRequest, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    dob = req.dob.strip()
    place = req.place.strip()
    if not dob or not place:
        raise HTTPException(400, "생년월일과 출생지를 입력해 주세요.")
    chart = Chart(
        user_id=user.id,
        dob=dob,
        tob=(req.tob or "").strip() or None,
        place=place,
        chandra_mode=bool(req.chandra_mode),
        label=f"{dob} · {place}",
    )
    db.add(chart)
    db.commit()
    return {"id": chart.id}


@router.get("/api/charts")
def list_charts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Chart)
        .filter_by(user_id=user.id)
        .order_by(Chart.created_at.desc())
        .all()
    )
    return [
        {
            "id": c.id,
            "dob": c.dob,
            "tob": c.tob,
            "place": c.place,
            "chandra_mode": c.chandra_mode,
            "label": c.label,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows
    ]


@router.delete("/api/charts/{chart_id}")
def delete_chart(chart_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    chart = db.query(Chart).filter_by(id=chart_id, user_id=user.id).one_or_none()
    if chart is None:
        raise HTTPException(404, "저장된 차트를 찾을 수 없습니다.")
    db.delete(chart)
    db.commit()
    return {"ok": True}


# --- 문의 ---
class InquiryRequest(BaseModel):
    contact: str = Field("", max_length=100)
    message: str = Field("", max_length=2000)


@router.post("/api/inquiry")
def inquiry(req: InquiryRequest, db: Session = Depends(get_db),
            user: User | None = Depends(get_optional_user)):
    # 문의는 로그인 없이도 가능: 토큰이 없으면 user=None
    contact = req.contact.strip()
    message = req.message.strip()
    if not contact or not message:
        raise HTTPException(400, "연락처와 문의 내용을 입력해 주세요.")
    db.add(Inquiry(user_id=user.id if user else None, contact=contact, message=message))
    db.commit()
    return {"ok": True}


@router.get("/api/admin/inquiries")
def admin_inquiries(token: str = "", db: Session = Depends(get_db)):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "권한이 없습니다")
    rows = (
        db.query(Inquiry).order_by(Inquiry.created_at.desc()).limit(100).all()
    )
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "contact": r.contact,
            "message": r.message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
