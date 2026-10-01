# -*- coding: utf-8 -*-
"""무료 베딕 쿤달리 계산기 API (MVP).

POST /api/chart — 출생 정보 → 무료 해석 JSON
유료 리포트용 전체 데이터(varga/ashtakavarga/karaka/judge 결론)는 노출하지 않는다.
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# --- 엔진 경로 설정 ---
HERE = Path(__file__).resolve().parent
if (HERE / "engine").is_dir():
    # 배포용: api/ 안에 번들된 엔진
    ENGINE_DIR = HERE
    WEB_DIR = HERE.parent / "web"
else:
    # 로컬 개발용: 워크스페이스 원본 참조
    ENGINE_DIR = HERE.parents[1] / "uploads" / "engine"
    WEB_DIR = HERE.parents[1] / "mvp" / "web"
sys.path.insert(0, str(ENGINE_DIR))

from engine.constants import load_constants          # noqa: E402
from engine.output import build_engine_output        # noqa: E402
from free_content import (                            # noqa: E402
    DASHA_KO, NAKSHATRA_KO, PLANET_ABBR, PLANET_KO,
    RASHI_KO, SIGN_ORDER, TIME_UNKNOWN_NOTICE,
)
from auth import router as auth_router               # noqa: E402  (선택 로그인/저장; 기존 엔드포인트 불변)

CONSTANTS = load_constants(ENGINE_DIR / "constants.yaml")
EPHE_PATH = ENGINE_DIR / "ephe_sky"  # 2026-09-29: DE430 BSP (skyfield). 배포 시 다운로드 필요.

app = FastAPI(title="Vedic Free Chart API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(auth_router)  # /api/auth/*, /api/me, /api/charts, /api/inquiry, /api/admin/*

# --- 간단 rate limit (IP당 분당 60회) ---
_hits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 60


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = [t for t in _hits[ip] if now - t < 60]
        if len(bucket) >= RATE_LIMIT:
            raise HTTPException(429, "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
        bucket.append(now)
        _hits[ip] = bucket
    return await call_next(request)


# --- 수요 측정 카운터 (수요 검증용, 인메모리) ---
# 컨테이너 재시작 시 초기화된다. 로그에도 남기므로 Railway 로그에서 복원 가능.
_stats_total = {"chart_ok": 0, "chart_err": 0, "notify": 0,
               "interest_total": 0, "interest_yearly": 0, "interest_category": 0}
_stats_by_day: dict[str, dict[str, int]] = defaultdict(
    lambda: {"chart_ok": 0, "chart_err": 0, "notify": 0,
             "interest_total": 0, "interest_yearly": 0, "interest_category": 0})
_stats_started = dt.datetime.now(dt.timezone.utc).isoformat()


def _bump(key: str) -> None:
    _stats_total[key] += 1
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    _stats_by_day[day][key] += 1
    print(f"[stats] {day} {key} total={_stats_total[key]}", flush=True)


@app.middleware("http")
async def _stats(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/api/chart":
        _bump("chart_ok" if response.status_code < 400 else "chart_err")
    elif path == "/api/notify" and request.method == "POST" and response.status_code < 400:
        _bump("notify")
    return response


class ChartRequest(BaseModel):
    birth_date: str = Field(..., examples=["1990-05-15"])
    birth_time: str | None = Field(None, examples=["14:30"])  # null = 시간 모름
    tz_offset: float = 9.0
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    place: str = ""


def _parse_birth(req: ChartRequest) -> tuple[dt.datetime, bool]:
    try:
        d = dt.date.fromisoformat(req.birth_date)
    except ValueError:
        raise HTTPException(400, "birth_date 형식이 올바르지 않습니다 (YYYY-MM-DD).")
    # DE430 커널(1549-12-31 ~ 2650-01-25) + 다샤 120년 타임라인/역탐색 여유를 고려한 지원 범위
    if d < dt.date(1600, 1, 1) or d > dt.date(2500, 12, 31):
        raise HTTPException(400, "지원하는 출생 연도 범위는 1600–2500년입니다.")
    time_unknown = not req.birth_time
    t = dt.time(12, 0) if time_unknown else dt.time.fromisoformat(req.birth_time)
    local = dt.datetime.combine(d, t)
    utc = local - dt.timedelta(hours=req.tz_offset)
    return utc.replace(tzinfo=dt.timezone.utc), time_unknown


def _current_period(timeline: list[dict], now: dt.datetime) -> tuple[dict, dict | None]:
    for md in timeline:
        s = dt.datetime.fromisoformat(md["start"].replace("Z", "+00:00"))
        e = dt.datetime.fromisoformat(md["end"].replace("Z", "+00:00"))
        if s <= now < e:
            ad = None
            for sub in md.get("antardashas", []):
                ss = dt.datetime.fromisoformat(sub["start"].replace("Z", "+00:00"))
                ee = dt.datetime.fromisoformat(sub["end"].replace("Z", "+00:00"))
                if ss <= now < ee:
                    ad = sub
                    break
            return md, ad
    return timeline[-1], None


def _fmt_date(iso: str) -> str:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y.%m.%d")


@app.post("/api/chart")
def chart(req: ChartRequest):
    birth_utc, time_unknown = _parse_birth(req)
    now = dt.datetime.now(dt.timezone.utc)
    if birth_utc > now:
        raise HTTPException(400, "출생 시각이 미래입니다.")

    try:
        out = build_engine_output(birth_utc, req.lat, req.lon, EPHE_PATH, CONSTANTS)
    except Exception as exc:  # 엔진 계약 위반 등은 500 대신 422로
        raise HTTPException(422, f"차트 계산에 실패했습니다: {exc}")

    ji = out["judgment_input"]
    planets = ji["chart"]["planets"]
    asc = ji["chart"]["ascendant"]

    # --- 라그나 (시간 미상이면 제외) ---
    lagna = None
    if not time_unknown:
        s_idx = asc["sign"]
        ko, desc = RASHI_KO[SIGN_ORDER[s_idx - 1]]
        nak_ko, _ = NAKSHATRA_KO[asc["nakshatra"] - 1]
        lagna = {
            "sign_ko": ko, "sign_index": s_idx,
            "nakshatra_ko": nak_ko, "pada": asc["pada"],
            "description": desc,
        }

    # --- 달 ---
    moon = planets["moon"]
    m_idx = moon["sign"]
    m_ko, m_desc = RASHI_KO[SIGN_ORDER[m_idx - 1]]
    mn_ko, mn_desc = NAKSHATRA_KO[moon["nakshatra"] - 1]

    # --- 행성 요약 (유료 데이터 제외) ---
    planet_list = []
    for key in ["sun", "moon", "mars", "mercury", "jupiter", "venus", "saturn", "rahu", "ketu"]:
        p = planets[key]
        planet_list.append({
            "name_ko": PLANET_KO[key],
            "abbr": PLANET_ABBR[key],
            "sign_ko": RASHI_KO[SIGN_ORDER[p["sign"] - 1]][0],
            "sign_index": p["sign"],
            "house": None if time_unknown else p["whole_sign_houses"]["lagna"],
        })

    # --- 현재 다샤 ---
    md, ad = _current_period(ji["dasha"]["full_timeline"], now)
    md_ko, md_desc = DASHA_KO[md["lord"]]
    dasha = {
        "mahadasha": {"lord_ko": md_ko, "start": _fmt_date(md["start"]),
                      "end": _fmt_date(md["end"]), "description": md_desc},
        "antardasha": None,
    }
    if ad:
        ad_ko = DASHA_KO[ad["lord"]][0]
        dasha["antardasha"] = {"lord_ko": ad_ko, "start": _fmt_date(ad["start"]),
                                           "end": _fmt_date(ad["end"])}

    return {
        "time_unknown": time_unknown,
        "notice": TIME_UNKNOWN_NOTICE if time_unknown else None,
        "place": req.place,
        "lagna": lagna,
        "moon": {"sign_ko": m_ko, "sign_index": m_idx, "description": m_desc,
                 "nakshatra_ko": mn_ko, "nakshatra_index": moon["nakshatra"],
                 "pada": moon["pada"], "nakshatra_description": mn_desc},
        "planets": planet_list,
        "dasha": dasha,
    }


@app.get("/api/health")
def health():
    return {"ok": True, "constants_version": CONSTANTS["meta"]["version"]}


class NotifyRequest(BaseModel):
    email: str = Field(..., max_length=254)


@app.post("/api/notify")
def notify(req: NotifyRequest):
    """출시 알림 신청. MVP 단계에서는 이메일 저장 없이 신청 수만 집계한다."""
    addr = req.email.strip()
    if "@" not in addr or "." not in addr.rsplit("@", 1)[-1]:
        raise HTTPException(400, "이메일 주소를 확인해 주세요.")
    return {"ok": True}


_INTEREST_PRODUCTS = {"total", "yearly", "category"}


class InterestRequest(BaseModel):
    product: str = Field(..., max_length=32)


@app.post("/api/interest")
def interest(req: InterestRequest):
    """상품 카드 클릭 집계 (카테고리별 수요 검증용, 인메모리)."""
    if req.product not in _INTEREST_PRODUCTS:
        raise HTTPException(400, "알 수 없는 상품입니다.")
    _bump(f"interest_{req.product}")
    return {"ok": True}


@app.get("/api/stats")
def stats():
    """수요 검증용 집계 (차트 계산 수, 알림 신청 수)."""
    return {
        "started_at": _stats_started,
        "total": dict(_stats_total),
        "by_day": {d: dict(v) for d, v in sorted(_stats_by_day.items())},
    }


# --- 프론트 서빙 (로컬 개발용; 배포는 Cloudflare Pages) ---
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


@app.get("/", include_in_schema=False)
def _root():
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "web/ 디렉토리에 index.html을 넣어주세요."}
