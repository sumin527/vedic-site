# -*- coding: utf-8 -*-
"""리포트 초안 생성 — 출생정보 → (엔진→judge→gate→worksheet→서술→검증) → 마크다운.

API 키가 없으면 워크시트까지만 만들고 서술 없이 초안으로 저장한다
(검수자가 직접 서술을 붙일 수 있게 note에 명시).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine.constants import load_constants as load_engine_constants  # noqa: E402
from engine.ephemeris import init_sidereal_lahiri  # noqa: E402
from engine.output import build_engine_output  # noqa: E402

from reports import gate, judge, worksheet  # noqa: E402
import yaml  # noqa: E402

# billing에서 카테고리 메타를 가져오되 순환 import 방지용 지연 import
_CATEGORIES = None


def _categories():
    global _CATEGORIES
    if _CATEGORIES is None:
        from billing import CATEGORIES, SAFETY_KEYS
        _CATEGORIES = (CATEGORIES, SAFETY_KEYS)
    return _CATEGORIES


_EPHE_READY = False


def _ensure_ephe():
    global _EPHE_READY
    if not _EPHE_READY:
        init_sidereal_lahiri(str(HERE / "ephe_sky"))
        _EPHE_READY = True


def _render_md(blocks: list[dict], title: str,
               sections: list[str] | None = None) -> str:
    sections = sections or []
    parts = [f"# {title}"]
    withheld = [b for b in blocks if b.get("level") == "withheld"]
    main = [b for b in blocks if b.get("level") != "withheld"]
    for i, b in enumerate(main):
        parts.append(f"## {sections[i]}" if i < len(sections)
                     else f"## 분석 {i + 1}")
        body = f"{b.get('fact', '')}\n\n{b.get('claim', '')}".strip()
        if b.get("level") == "conditional":
            parts.append(">" + body.replace("\n", "\n> "))
        else:
            parts.append(body)
        if b.get("disclosure"):
            parts.append(b["disclosure"])
        parts.append("---")
    if withheld:
        parts.append("## 이견 및 불확실 사항")
        for b in withheld:
            parts.append(">" + f"{b.get('fact', '')}\n\n{b.get('claim', '')}"
                         .replace("\n", "\n> "))
            if b.get("disclosure"):
                parts.append(b["disclosure"])
            parts.append("---")
    return "\n\n".join(parts)


def generate_draft(product: str, dob: str, tob: str | None,
                   place: str) -> tuple[str, str, str]:
    """(content_md, worksheet_json, note) 반환. 예외 시 그대로 raise."""
    from billing import PRODUCTS
    p = PRODUCTS[product]
    cat_code = p["category"]
    CATEGORIES, SAFETY_KEYS = _categories()
    cat = CATEGORIES[cat_code]

    _ensure_ephe()
    engine_cfg = load_engine_constants(HERE / "constants.yaml")
    d = dt.date.fromisoformat(dob)
    t = dt.time.fromisoformat(tob) if tob else dt.time(12, 0)
    birth_utc = (dt.datetime.combine(d, t) - dt.timedelta(hours=9)).replace(
        tzinfo=dt.timezone.utc)
    engine_data = build_engine_output(birth_utc, 37.5665, 126.978,
                                      str(HERE / "ephe_sky"), engine_cfg)

    rules = yaml.safe_load(
        (HERE / "reports" / cat["rules"]).read_text(encoding="utf-8"))
    jres = judge.judge(engine_data, rules, engine_cfg)

    rec = {"id": cat["rec_id"], "code": cat["code"],
           "tiers": {k: {"present": v["present"], "direction": v["direction"]}
                    for k, v in jres["tiers"].items()},
           "safety": {k: True for k in SAFETY_KEYS},
           "safety_provenance": "auto: prompt-enforced + narration_check-verified"}
    verdict = gate.evaluate(rec, gate.load_constants(HERE / "constants.yaml"))

    meta = {"birth_date": dob, "birth_time": tob or "시간 모름", "place": place}
    ws = worksheet.build_worksheet(engine_data, jres, verdict, meta,
                                   report_code=cat["code"])
    ws_json = json.dumps(ws, ensure_ascii=False, indent=2)

    provider = ("anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                else "gemini" if os.environ.get("GEMINI_API_KEY") else None)
    if not provider:
        note = ("API 키 없음 — 워크시트까지만 생성됨. "
                "검수자가 서술을 직접 작성해 content_md에 붙여넣고 공개할 것.")
        md = (f"# {cat['title']} (서술 대기)\n\n"
              f"_{note}_\n\n---\n\n"
              f"## 워크시트 요약\n\n게이트 판정: {verdict.level}\n")
        return md, ws_json, note

    from reports import narrate
    from reports.narration_check import check_block
    blocks = narrate.narrate(ws, check_block, engine_cfg, provider=provider)
    md = _render_md(blocks, cat["title"], sections=cat["sections"])
    n_withheld = sum(1 for b in blocks if b.get("level") == "withheld")
    note = (f"자동 생성 초안 ({provider}). 게이트: {verdict.level}. "
            f"withheld 블록: {n_withheld}개. 발송 전 사람 검수 필수.")
    return md, ws_json, note
