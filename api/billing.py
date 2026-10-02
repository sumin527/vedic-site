# -*- coding: utf-8 -*-
"""유료 리포트: 주문·이용권·리포트 보관함.

흐름: POST /api/orders(주문 생성) → 결제(Toss, 연동 예정) → 이용권 발급
     → POST /api/report-jobs(리포트 생성 요청) → 초안 생성
     → 관리자 검수 → 공개 → GET /api/my-reports(보관함)

사이트 내 표시 원칙: 구매한 리포트는 계정에 귀속되어 평생 열람.
"""
from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (Column, DateTime, ForeignKey, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Session

from auth import (ADMIN_TOKEN, Base, _now, _uid, get_current_user, get_db,
                  User)

router = APIRouter()

# 상품: price(원), category=파이프라인 카테고리 (None=파이프라인 미구현)
PRODUCTS = {
    "total":      {"name": "총운 리포트", "price": 16500, "category": None},
    "yearly":     {"name": "1년 운세",   "price": 11000, "category": None},
    "compat":     {"name": "궁합 상세풀이", "price": 11000, "category": None},
    "cat_job":    {"name": "직업운", "price": 5500, "category": "career"},
    "cat_wealth": {"name": "금전운", "price": 5500, "category": "finance"},
    "cat_love":   {"name": "애정운", "price": 5500, "category": "relationship"},
    "cat_health": {"name": "건강운", "price": 5500, "category": "condition"},
}

# 파이프라인 카테고리 메타 (auto_report.CATEGORIES와 동기)
CATEGORIES = {
    "career":       {"rules": "rules_career.yaml",       "rec_id": "C1",
                     "code": "CAREER_DIRECTION",    "title": "직업운 리포트",
                     "sections": ["일하는 방식", "어디까지 갈까", "지금 흐름"]},
    "finance":      {"rules": "rules_finance.yaml",      "rec_id": "F1",
                     "code": "FINANCE_FLOW",        "title": "금전운 리포트",
                     "sections": ["돈이 들어오는 길", "버는 것과 남는 것", "지금 흐름"]},
    "relationship": {"rules": "rules_relationship.yaml", "rec_id": "R1",
                     "code": "RELATIONSHIP_PATTERN", "title": "애정운 리포트",
                     "sections": ["관계에서의 나", "어떤 사람이 맞을까", "지금 흐름"]},
    "condition":    {"rules": "rules_condition.yaml",    "rec_id": "H1",
                     "code": "CONDITION_RHYTHM",    "title": "건강운 리포트",
                     "sections": ["몸이 반응하는 법", "리듬 잡는 법", "지금 흐름"]},
}
SAFETY_KEYS = ["no_s3", "no_certainty_language", "no_job_coercion",
               "allowed_frames_only"]


class Order(Base):
    __tablename__ = "orders"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    product = Column(String(32), nullable=False)
    amount = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending")  # pending/paid/cancelled
    provider_order_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class Entitlement(Base):
    __tablename__ = "entitlements"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    product = Column(String(32), nullable=False)
    order_id = Column(String(32), ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    __table_args__ = (UniqueConstraint("user_id", "product",
                                       name="uq_entitlement_user_product"),)


class Report(Base):
    __tablename__ = "reports"
    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    product = Column(String(32), nullable=False)
    # queued/generating/draft/published/rejected
    status = Column(String(16), nullable=False, default="queued")
    birth_dob = Column(String(32), nullable=False, default="")
    birth_tob = Column(String(32), nullable=True)
    birth_place = Column(String(64), nullable=False, default="")
    content_md = Column(Text, nullable=False, default="")
    worksheet_json = Column(Text, nullable=False, default="")
    note = Column(String(512), nullable=False, default="")  # 검수 메모
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    published_at = Column(DateTime(timezone=True), nullable=True)


def _product_or_400(product: str) -> dict:
    p = PRODUCTS.get(product)
    if not p:
        raise HTTPException(400, "알 수 없는 상품입니다.")
    return p


def _has_entitlement(db: Session, user_id: str, product: str) -> bool:
    return db.query(Entitlement).filter_by(
        user_id=user_id, product=product).first() is not None


def _require_admin(token: str):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "권한이 없습니다")


class OrderRequest(BaseModel):
    product: str = Field(..., max_length=32)


@router.post("/api/orders")
def create_order(req: OrderRequest, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    p = _product_or_400(req.product)
    if _has_entitlement(db, user.id, req.product):
        raise HTTPException(400, "이미 보유한 상품입니다.")
    o = Order(user_id=user.id, product=req.product, amount=str(p["price"]))
    db.add(o)
    db.commit()
    return {"id": o.id, "product": req.product, "name": p["name"],
            "amount": p["price"], "status": o.status}


@router.post("/api/admin/orders/{order_id}/mark-paid")
def admin_mark_paid(order_id: str, token: str = "",
                    db: Session = Depends(get_db)):
    """Toss 연동 전까지 관리자 수동 결제 확인용."""
    _require_admin(token)
    return _fulfill_order(db, order_id)


def _fulfill_order(db: Session, order_id: str) -> dict:
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "주문을 찾을 수 없습니다.")
    if o.status == "paid":
        return {"ok": True, "already": True}
    o.status = "paid"
    if not _has_entitlement(db, o.user_id, o.product):
        db.add(Entitlement(user_id=o.user_id, product=o.product,
                           order_id=o.id))
    db.commit()
    return {"ok": True}


@router.post("/api/dev/orders/{order_id}/confirm")
def dev_confirm_order(order_id: str, db: Session = Depends(get_db)):
    """개발용 테스트 결제. DEV_MODE=1일 때만 동작 (운영에는 없음)."""
    if os.environ.get("DEV_MODE") != "1":
        raise HTTPException(404, "Not found")
    return _fulfill_order(db, order_id)


@router.get("/api/entitlements")
def list_entitlements(user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rows = db.query(Entitlement).filter_by(user_id=user.id).all()
    return [{"product": r.product, "name": PRODUCTS.get(r.product, {}).get("name", r.product),
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


class ReportJobRequest(BaseModel):
    product: str = Field(..., max_length=32)
    dob: str = Field(..., max_length=32)       # YYYY-MM-DD
    tob: str | None = Field(None, max_length=32)  # HH:MM 또는 null
    place: str = Field("", max_length=64)


@router.post("/api/report-jobs")
def create_report_job(req: ReportJobRequest, bg: BackgroundTasks,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    p = _product_or_400(req.product)
    if not _has_entitlement(db, user.id, req.product):
        raise HTTPException(402, "이 상품의 이용권이 필요합니다.")
    if not p["category"]:
        raise HTTPException(400, "이 리포트는 아직 준비 중입니다.")
    try:
        dt.date.fromisoformat(req.dob)
    except ValueError:
        raise HTTPException(400, "생년월일 형식이 올바르지 않습니다.")
    r = Report(user_id=user.id, product=req.product, status="queued",
               birth_dob=req.dob, birth_tob=req.tob, birth_place=req.place)
    db.add(r)
    db.commit()
    bg.add_task(_run_generation, r.id)
    return {"id": r.id, "status": r.status}


def _run_generation(report_id: str):
    """BackgroundTasks용 — DB 세션을 직접 연다."""
    from auth import SessionLocal
    from report_gen import generate_draft
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r or r.status != "queued":
            return
        r.status = "generating"
        db.commit()
        md, ws_json, note = generate_draft(
            r.product, r.birth_dob, r.birth_tob, r.birth_place)
        r.content_md = md
        r.worksheet_json = ws_json
        r.note = note
        r.status = "draft"
        db.commit()
    except Exception as exc:  # 생성 실패는 rejected + 사유 기록
        try:
            r = db.get(Report, report_id)
            if r:
                r.status = "rejected"
                r.note = f"생성 실패: {exc}"[:500]
                db.commit()
        finally:
            pass
    finally:
        db.close()


@router.get("/api/my-reports")
def list_my_reports(user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    rows = (db.query(Report)
            .filter_by(user_id=user.id, status="published")
            .order_by(Report.published_at.desc()).all())
    return [{"id": r.id, "product": r.product,
             "name": PRODUCTS.get(r.product, {}).get("name", r.product),
             "birth": f"{r.birth_dob} {r.birth_tob or '시간 모름'} · {r.birth_place}",
             "published_at": r.published_at.isoformat() if r.published_at else None}
            for r in rows]


@router.get("/api/my-reports/{report_id}")
def get_my_report(report_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if not r or r.user_id != user.id or r.status != "published":
        raise HTTPException(404, "리포트를 찾을 수 없습니다.")
    return {"id": r.id, "product": r.product,
            "name": PRODUCTS.get(r.product, {}).get("name", r.product),
            "birth": f"{r.birth_dob} {r.birth_tob or '시간 모름'} · {r.birth_place}",
            "content_md": r.content_md,
            "published_at": r.published_at.isoformat() if r.published_at else None}


@router.get("/api/report-jobs/{report_id}")
def get_report_job(report_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """내 리포트 생성 상태 조회 (폴링용)."""
    r = db.get(Report, report_id)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "리포트를 찾을 수 없습니다.")
    return {"id": r.id, "product": r.product,
            "name": PRODUCTS.get(r.product, {}).get("name", r.product),
            "status": r.status,
            "published": r.status == "published"}


# ---------- 관리자 검수 ----------
@router.get("/api/admin/reports")
def admin_reports(status: str = "", token: str = "",
                  db: Session = Depends(get_db)):
    _require_admin(token)
    q = db.query(Report)
    if status:
        q = q.filter_by(status=status)
    q = q.order_by(Report.created_at.desc()).limit(100)
    return [{"id": r.id, "user_id": r.user_id, "product": r.product,
             "status": r.status, "note": r.note,
             "birth": f"{r.birth_dob} {r.birth_tob or ''} {r.birth_place}".strip(),
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in q.all()]


@router.get("/api/admin/reports/{report_id}")
def admin_report_detail(report_id: str, token: str = "",
                        db: Session = Depends(get_db)):
    _require_admin(token)
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(404, "리포트를 찾을 수 없습니다.")
    return {"id": r.id, "user_id": r.user_id, "product": r.product,
            "status": r.status, "note": r.note,
            "content_md": r.content_md, "worksheet_json": r.worksheet_json}


class PublishRequest(BaseModel):
    content_md: str | None = None  # 검수 수정본 (없으면 기존 유지)
    note: str = ""


@router.post("/api/admin/reports/{report_id}/publish")
def admin_publish(report_id: str, req: PublishRequest, token: str = "",
                  db: Session = Depends(get_db)):
    _require_admin(token)
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(404, "리포트를 찾을 수 없습니다.")
    if r.status not in ("draft", "rejected"):
        raise HTTPException(400, "초안 상태의 리포트만 공개할 수 있습니다.")
    if req.content_md is not None:
        r.content_md = req.content_md
    r.note = req.note
    r.status = "published"
    r.published_at = _now()
    db.commit()
    return {"ok": True}


@router.post("/api/admin/reports/{report_id}/reject")
def admin_reject(report_id: str, req: PublishRequest, token: str = "",
                 db: Session = Depends(get_db)):
    _require_admin(token)
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(404, "리포트를 찾을 수 없습니다.")
    r.status = "rejected"
    r.note = req.note or "검수 반려"
    db.commit()
    return {"ok": True}
