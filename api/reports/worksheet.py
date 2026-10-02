"""워크시트 조립 — judge 판정 + gate 판정을 서술 프롬프트용 구조로 묶는다.

입력: engine JSON, judge() 결과, gate.evaluate() 결과(Verdict)
출력: narrate.py 가 프롬프트로 쓰는 worksheet dict

원칙:
- 서술에 써도 되는 사실(chart_facts)과 판정(verdict_blocks)을 분리한다.
  narration_check.py 가 "입력에 없는 내용" 을 잡는 기준이 이 분리다.
- silent 등급은 워크시트에서 제외한다 (미출력).
- withheld 는 '이견 및 불확실 사항' 섹션용으로 별도 보관한다.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# judge.py 와 동일한 표기
SIGN_KO = ["", "양자리", "황소자리", "쌍둥이자리", "게자리", "사자자리",
           "처녀자리", "천칭자리", "전갈자리", "사수자리", "염소자리",
           "물병자리", "물고기자리"]
PLANET_KO = {"sun": "태양", "moon": "달", "mars": "화성", "mercury": "수성",
             "jupiter": "목성", "venus": "금성", "saturn": "토성",
             "rahu": "라후", "ketu": "케투"}


def _planet_facts(planets: dict) -> dict:
    facts = {}
    for key, p in planets.items():
        facts[PLANET_KO.get(key, key)] = {
            "sign": SIGN_KO[p["sign"]],
            "house_lagna": p["whole_sign_houses"]["lagna"],
            "nakshatra_no": p["nakshatra"],
            "pada": p["pada"],
        }
    return facts


def _parse_dt(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _current_periods(dasha: dict,
                     ref: dt.datetime | None = None) -> tuple[str | None, str | None]:
    """full_timeline에서 ref 시점의 마하다샤/안타르다샤 lord를 찾는다.

    (버그 수정 2026-10-02) 기존 코드는 birth_periods(출생 시점 다샤)를
    '현재 다샤'로 읽어서, 30~40대 차트에 출생 당시 다샤가 들어갔다.
    """
    ref = ref or dt.datetime.now(dt.timezone.utc)
    maha_lord: str | None = None
    antar_lord: str | None = None
    for p in dasha.get("full_timeline", []) or []:
        try:
            s, e = _parse_dt(p["start"]), _parse_dt(p["end"])
        except (KeyError, ValueError):
            continue
        if s <= ref <= e:
            maha_lord = p.get("lord")
            for a in p.get("antardashas", []) or []:
                try:
                    sa, ea = _parse_dt(a["start"]), _parse_dt(a["end"])
                except (KeyError, ValueError):
                    continue
                if sa <= ref <= ea:
                    antar_lord = a.get("lord")
                    break
            break
    return maha_lord, antar_lord


def build_worksheet(engine_data: dict, judge_result: dict,
                    verdict: Any, meta: dict,
                    report_code: str = "CAREER_DIRECTION",
                    safety_rules: list[str] | None = None,
                    ref_date: dt.datetime | None = None) -> dict:
    """judge 결과 + gate Verdict → 서술용 워크시트.

    ref_date: '현재 다샤' 기준 시점 (기본값: 지금, UTC).
    """
    ji = engine_data.get("judgment_input", engine_data)
    chart = ji["chart"]
    dasha = ji["dasha"]
    now_maha, now_antar = _current_periods(dasha, ref_date)

    chart_facts = {
        "lagna": SIGN_KO[chart["ascendant"]["sign"]],
        "planets": _planet_facts(chart["planets"]),
        "moon_nakshatra_no": chart["planets"]["moon"]["nakshatra"],
        "moon_pada": chart["planets"]["moon"]["pada"],
        "current_mahadasha": now_maha,
        "current_antardasha": now_antar,
    }

    blocks = []
    tiers = judge_result.get("tiers", {})
    hits = judge_result.get("hits", {})
    for tier, tier_info in tiers.items():
        if not tier_info.get("present"):
            continue
        evidence = []
        for r in hits.get(tier, []):
            text = r.get("text") or r.get("text_strong_weak", "")
            evidence.append({
                "rule_id": r.get("id"),
                "source": r.get("source"),
                "text": text,
                "detail": {k: v for k, v in r.get("_info", {}).items()
                           if not str(k).startswith("_")},
            })
        blocks.append({
            "tier": tier,
            "direction": tier_info.get("direction"),
            "auto": tier_info.get("auto"),
            "evidence": evidence,
        })

    return {
        "meta": meta,  # {name, birth_date, birth_time, place}
        "report_code": report_code,
        "gate_level": verdict.level,
        "chart_facts": chart_facts,
        "verdict_blocks": blocks,
        "review_notes": judge_result.get("review", []),
        # 서술 시 강제할 안전 규칙 (프롬프트에 주입, narration_check 가 사후 검증)
        "safety_rules": safety_rules if safety_rules is not None else [
            "S3 주제(사망·수명·불치병·불임·이혼·사별)에 대한 판정을 쓰지 않는다.",
            "확정 어투를 쓰지 않는다 ('~할 것이다' 금지, '~경향이 있다' 사용).",
            "직업 강요 표현을 쓰지 않는다 ('~해야 한다' 금지).",
            "근거 없는 수치·용어·문헌 인용을 쓰지 않는다 (아래 evidence/source 밖 내용 금지).",
        ],
    }
