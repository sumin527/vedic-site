"""근거 계열 게이트 — evidence_checklist.md §3 의 기계 버전.

statement_policy 3등급 판정:
    verdict     근거 계열 3+ (AV 제외) + L1 + 안전 → 본문 판정
    conditional 근거 계열 2  (AV 제외) + L1 + 안전 → 본문, 조건부 어투, 시각적 구분
    withheld    근거 계열 1                        → ⚠️ 이견 및 불확실 사항 섹션
    silent      근거 계열 0                        → 미출력

강등은 게이트의 우회로가 아니다. conditional 은 verdict 의 약한 버전이 아니라
다른 종류의 진술이며, 사유 공개와 시각적 구분이 강제된다.

사용:
    python gate.py conclusions.json          # 결론 배열
    python gate.py engine_output.json        # 8단계 2계층 JSON
    python gate.py --demo
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONSTANTS = Path(__file__).with_name("constants.yaml")
TIER_ORDER = ["t1_d1", "t2_karaka", "t3_varga", "t4_dasha", "t5_ashtaka", "t6_yoga"]
AV_TIER = "t5_ashtaka"
L1_TIER = "t1_d1"

# 기준점 (ISSUE-019) — 서술은 둘, 계열은 하나
REF_PRIMARY = "direction"          # 라그나 기준
REF_SECONDARY = "moon_direction"   # 찬드라 라그나 기준

SAFETY_KEYS = ["no_s3", "no_certainty_language", "no_job_coercion", "allowed_frames_only"]


@dataclass
class Verdict:
    conclusion_id: str
    code: str
    level: str                      # verdict / conditional / withheld / silent
    count_a: int                    # AV 포함
    count_b: int                    # AV 제외
    l1: bool
    av_dependent: bool
    safety_ok: bool
    ref_agree: list[str] = field(default_factory=list)      # 두 기준 일치 계열
    ref_conflict: list[str] = field(default_factory=list)   # 두 기준 불일치 계열
    time_precision_flag: bool = False
    opposing: list[str] = field(default_factory=list)
    modifier_oppose: list[str] = field(default_factory=list)   # 강도 지표의 감속 신호
    missing: list[str] = field(default_factory=list)
    placement: str = ""
    reasons: list[str] = field(default_factory=list)
    format_required: list[str] = field(default_factory=list)


def load_constants(path: Path = CONSTANTS) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def evaluate(record: dict, cfg: dict) -> Verdict:
    policy = cfg["statement_policy"]["levels"]
    contract = cfg["output_contract"]

    tiers = record.get("tiers", {})
    tier_cfg = cfg["evidence_policy"]["tiers"]
    counted = {n for n, v in tier_cfg.items() if v.get("counts_as_evidence")}

    def has(name: str, direction: str) -> bool:
        t = tiers.get(name, {})
        return bool(t.get("present")) and t.get(REF_PRIMARY) == direction

    supporting = [n for n in TIER_ORDER if has(n, "support")]
    # 반대도 계열로 세는 것만 집계한다. AV(counts_as_evidence: false)는
    # 43 §5.1 에 따라 사건의 종류가 아니라 강도를 나타내므로 방향 충돌을 만들지 않는다.
    opposing = [n for n in TIER_ORDER if has(n, "oppose") and n in counted]
    modifier_oppose = [n for n in TIER_ORDER if has(n, "oppose") and n not in counted]
    missing = [n for n in TIER_ORDER if not tiers.get(n, {}).get("present")]

    # --- 기준점 대조 (ISSUE-019). 계열 수는 늘리지 않는다 ---
    ref_agree, ref_conflict = [], []
    for n in TIER_ORDER:
        t = tiers.get(n, {})
        sec = t.get(REF_SECONDARY)
        if not t.get("present") or sec is None:
            continue
        (ref_agree if sec == t.get(REF_PRIMARY) else ref_conflict).append(n)

    count_a = len(supporting)
    count_b = len([t for t in supporting if t in counted])
    l1 = L1_TIER in supporting

    safety = record.get("safety", {})
    safety_ok = all(safety.get(k, False) for k in SAFETY_KEYS)

    v_min = policy["verdict"]["requires"]["min_tiers_excl_av"]
    c_min = policy["conditional"]["requires"]["min_tiers_excl_av"]

    # divergent — 상반된 근거가 공존하고 한쪽으로 좁혀지지 않는 경우.
    # 계열 수로는 판정에 못 미치지만 서술할 내용은 있다.
    diverges = bool(opposing) and bool(supporting) and safety_ok

    if count_b >= v_min and l1 and safety_ok and not opposing:
        level = "verdict"
    elif count_b >= v_min and l1 and safety_ok:
        level = "divergent"          # 3계열 이상이어도 반대가 있으면 양면 서술
    elif count_b >= c_min and l1 and safety_ok:
        level = "conditional" if not opposing else "divergent"
    elif diverges:
        level = "divergent"
    elif count_a >= 1:
        level = "withheld"
    else:
        level = "silent"

    av_dependent = count_a >= v_min and count_b < v_min

    reasons: list[str] = []
    if level == "conditional":
        reasons.append(f"근거 계열 {v_min - count_b}개 부족 "
                       f"(현재 {count_b}, 판정 요건 {v_min})")
        if missing:
            reasons.append(f"미충족 계열 {', '.join(missing)}")
    if level == "withheld":
        reasons.append(f"근거 계열 {count_b}개 — 조건부 요건({c_min}) 미달")
        if not l1:
            reasons.append("L1(D1 직접 지시) 없음 — 결론의 앵커 부재")
    if av_dependent:
        reasons.append("AV 를 빼면 요건 미달 — AV 는 강도 지표이므로 계열로 세지 않음")
    if modifier_oppose:
        reasons.append(f"강도 감속 신호 {', '.join(modifier_oppose)} — "
                       "등급에는 영향 없음. 서술에서 현실화 속도를 낮춰 표현")
    if level == "divergent":
        reasons.append(f"근거 방향 충돌 — 지지 {', '.join(supporting)} / 반대 {', '.join(opposing)}")
        reasons.append("양면 서술. 한쪽을 선택하지 않는다")
    elif opposing:
        reasons.append(f"반대 근거 {len(opposing)}계열 — §4.3 병기 필요")
    if ref_conflict:
        reasons.append(f"기준점 불일치 {', '.join(ref_conflict)} — 라그나·달 병기 + 시각 정밀도 확인")
    if ref_agree:
        reasons.append(f"기준점 일치 {', '.join(ref_agree)} — 강도 상승 (계열 수 불변)")
    if not safety_ok:
        failed = [k for k in SAFETY_KEYS if not safety.get(k, False)]
        reasons.append(f"§5 안전 검토 미통과: {', '.join(failed)}")

    applies = list(contract["applies_to"]) + ["divergent"]
    fmt = list(contract["statement_format"]) if level in applies else []
    if opposing and level in applies:
        fmt.append("conflict_template")

    return Verdict(
        conclusion_id=record.get("id", "?"),
        code=record.get("code", ""),
        level=level,
        count_a=count_a,
        count_b=count_b,
        l1=l1,
        av_dependent=av_dependent,
        safety_ok=safety_ok,
        opposing=opposing,
        missing=missing,
        placement=policy[level]["report_placement"],
        reasons=reasons,
        format_required=fmt,
        ref_agree=ref_agree,
        ref_conflict=ref_conflict,
        time_precision_flag=bool(ref_conflict),
    )


def extract_records(data) -> list[dict]:
    """결론 배열 또는 8단계 judgment_input 에서 판정 레코드를 꺼낸다.

    8단계 출력은 {"judgment_input": {...,"conclusions":[...]}, "narration_input": {...}}
    구조다. narration_input 은 판정에 쓰지 않는다 — 수치가 제거되어 있다.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "judgment_input" in data:
            ji = data["judgment_input"]
            if not isinstance(ji, dict):
                raise ValueError("judgment_input 은 매핑이어야 함")
            return list(ji.get("conclusions", []))
        if "conclusions" in data:
            return list(data["conclusions"])
    raise ValueError("결론 배열 또는 judgment_input.conclusions 가 필요함")


def release_gate(cfg: dict) -> list[dict]:
    return [i for i in cfg.get("open_issues", []) if i.get("blocking")]


LABEL = {"verdict": "판정", "conditional": "조건부", "divergent": "양면",
         "withheld": "보류", "silent": "미출력"}


def report(verdicts: list[Verdict], cfg: dict) -> None:
    print(f"{'ID':<5}{'A':>3}{'B':>3} {'L1':<3} {'등급':<8}{'게재':<24}사유")
    print("-" * 104)
    for v in verdicts:
        print(f"{v.conclusion_id:<5}{v.count_a:>3}{v.count_b:>3} "
              f"{'O' if v.l1 else 'X':<3} {LABEL[v.level]:<8}{v.placement:<24}"
              f"{'; '.join(v.reasons)}")

    tally = {k: sum(1 for v in verdicts if v.level == k) for k in LABEL}
    print(f"\n판정 {tally['verdict']} / 조건부 {tally['conditional']} / "
          f"양면 {tally['divergent']} / 보류 {tally['withheld']} / "
          f"미출력 {tally['silent']}   전체 {len(verdicts)}")
    body = tally["verdict"] + tally["conditional"] + tally["divergent"]
    print(f"본문 게재 가능: {body}건")

    dependent = [v for v in verdicts if v.av_dependent]
    print(f"\n[AV 의존] {len(dependent)}/{len(verdicts)}건 — AV 없이는 요건 미달인 결론"
          + (f"  ({', '.join(v.conclusion_id for v in dependent)})" if dependent else ""))

    flagged = [v for v in verdicts if v.time_precision_flag]
    if flagged:
        print(f"\n[시각 정밀도 확인] {len(flagged)}건 — 라그나·달 기준 불일치")
        print("  라그나 2시간 / 달 2.25일 주기. 불일치는 출생시각이 결과를 좌우한다는 신호.")
        for v in flagged:
            print(f"  {v.conclusion_id}: {', '.join(v.ref_conflict)}")

    print("\n[서술층 형식 요구]")
    for v in verdicts:
        if v.format_required:
            print(f"  {v.conclusion_id}: {' + '.join(v.format_required)}")
    fal = cfg["output_contract"]
    if fal.get("falsification_required"):
        print(f"  전체: '{fal['falsification_field']}' — 리포트 말미 필수")

    blocking = release_gate(cfg)
    if blocking:
        print(f"\n[RELEASE GATE] blocking {len(blocking)}건 — 유료 발행 차단")
        for i in blocking:
            print(f"  - {i['id']} {i['title']}")
    else:
        print("\n[RELEASE GATE] blocking 없음")


SAFE = {k: True for k in SAFETY_KEYS}

DEMO = [
    {   # 파일럿 C1 재현 — T3 가 반대 방향
        "id": "C1", "code": "CAREER_DIRECTION_STRUCTURED",
        "tiers": {
            "t1_d1":      {"present": True, "direction": "support"},
            "t3_varga":   {"present": True, "direction": "oppose"},
            "t5_ashtaka": {"present": True, "direction": "support"},
            "t6_yoga":    {"present": True, "direction": "support"},
        },
        "safety": SAFE,
    },
    {   # 파일럿 C3 재현
        "id": "C3", "code": "ACHIEVEMENT_HIGH_POTENTIAL",
        "tiers": {
            "t1_d1":      {"present": True, "direction": "support"},
            "t5_ashtaka": {"present": True, "direction": "support"},
            "t6_yoga":    {"present": True, "direction": "support"},
        },
        "safety": SAFE,
    },
    {   # 파일럿 C4 재현
        "id": "C4", "code": "CURRENT_PHASE_ACTIVATION",
        "tiers": {
            "t1_d1":      {"present": True, "direction": "support"},
            "t5_ashtaka": {"present": True, "direction": "support"},
        },
        "safety": SAFE,
    },
    {   # ISSUE-013·014 가 메워진 경우
        "id": "C5", "code": "GROWTH_WINDOW",
        "tiers": {
            "t1_d1":     {"present": True, "direction": "support"},
            "t2_karaka": {"present": True, "direction": "support"},
            "t4_dasha":  {"present": True, "direction": "support"},
        },
        "safety": SAFE,
    },
    {   # 기준점 불일치 — 라그나는 지지, 달은 반대
        "id": "C6", "code": "CAREER_STABILITY",
        "tiers": {
            "t1_d1":     {"present": True, "direction": "support", "moon_direction": "oppose"},
            "t2_karaka": {"present": True, "direction": "support", "moon_direction": "support"},
            "t4_dasha":  {"present": True, "direction": "support"},
        },
        "safety": SAFE,
    },
]


def main(argv: list[str]) -> int:
    cfg = load_constants()
    if "--demo" in argv:
        records = DEMO
    elif len(argv) > 1:
        records = extract_records(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    else:
        print(__doc__)
        return 2
    report([evaluate(r, cfg) for r in records], cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
