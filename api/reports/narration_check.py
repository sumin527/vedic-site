"""서술층 사후 검사 — LLM 생성 문장의 결정론적 검증.

LLM 을 신뢰하는 대신 출력을 기계로 검증한다.
판정층이 만든 JSON(입력)과 LLM 이 쓴 문장(출력)을 대조해
입력에 없는 내용이 출력에 나타났는지 찾는다.

검사 항목 (constants.yaml output_contract.narration.post_checks):
    prohibited_expression  금지 표현
    s3_keyword             S3 어휘
    numeric_leak           입력에 없는 수치
    term_leak              입력에 없는 점성 용어
    conditional_voice      conditional 블록의 확정 어투
    citation_integrity     입력에 없는 문서·절 인용
    cross_tradition        타 전통 용어 (§1.1)

하나라도 걸리면 FAIL — 발송 차단.

사용:
    python narration_check.py block.json
    python narration_check.py --demo
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONSTANTS = Path(__file__).with_name("constants.yaml")

# 베딕 고유 용어 → 입력 JSON 에서 찾을 별칭
# 판정층 JSON 은 영문 키를 쓰므로 한글 용어와 대조하려면 별칭이 필요하다.
VEDIC_TERMS: dict[str, list[str]] = {
    "태양": ["sun"], "달": ["moon"], "화성": ["mars"], "수성": ["mercury"],
    "목성": ["jupiter"], "금성": ["venus"], "토성": ["saturn"],
    "라후": ["rahu"], "케투": ["ketu"],
    "양자리": ["aries", "Ar"], "황소자리": ["taurus", "Ta"],
    "쌍둥이자리": ["gemini", "Ge"], "게자리": ["cancer", "Cn"],
    "사자자리": ["leo", "Le"], "처녀자리": ["virgo", "Vi"],
    "천칭자리": ["libra", "Li"], "전갈자리": ["scorpio", "Sc"],
    "사수자리": ["sagittarius", "Sg"], "염소자리": ["capricorn", "Cp"],
    "물병자리": ["aquarius", "Aq"], "물고기자리": ["pisces", "Pi"],
    "라그나": ["lagna", "ascendant"], "나밤샤": ["d9", "navamsa"],
    "다샴샤": ["d10", "dasamsa"], "다샤": ["dasha", "dasa"],
    "안타르다샤": ["antardasha"], "프라티안타르다샤": ["pratyantardasha"],
    "아쉬타카바르가": ["ashtaka", "sav", "bav"], "카라카": ["karaka"],
    "요가": ["yoga"], "마하푸루샤": ["mahapurusha"], "라자요가": ["rajayoga"],
    "다나요가": ["dhana"], "트랜짓": ["transit"], "우파차야": ["upachaya"],
    "켄드라": ["kendra"], "트리코나": ["trikona"], "두스타나": ["dusthana"],
    "낙샤트라": ["nakshatra"], "파다": ["pada"],
}

# conditional 블록에 나타나면 안 되는 확정 어투
# 완화어(편·경향·쉽습니다 등)가 앞에 붙으면 조건부이므로 제외한다.
HEDGE = r"(?<!편)(?<!경향)(?<!쪽)(?<!가능성)(?<!수 있)"
CERTAINTY_PATTERNS = [
    r"할 것입니다",
    HEDGE + r"됩니다(?![ ]*만)",
    HEDGE + r"입니다(?![ ]*만)",
    r"이룹니다", r"옵니다", r"열립니다", r"나타납니다",
    r"반드시\s", r"틀림없이", r"확실히\s+\S+합니다",
]

# 완화 표현 — 하나라도 있으면 확정 어투 검사를 완화한다
HEDGE_WORDS = ["편입니다", "경향", "쉽습니다", "가능성", "수 있", "라면", "면 ",
               "검토", "여지", "보입니다", "듯", "쪽입니다"]

# conditional 블록에 최소 1개는 있어야 하는 조건 표지
CONDITIONAL_MARKERS = ["면", "라면", "경우", "조건", "검토", "가능성", "여지"]


@dataclass
class Finding:
    check: str
    severity: str          # fail / warn
    detail: str


@dataclass
class Result:
    block_id: str
    level: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "fail" for f in self.findings)


def load_constants(path: Path = CONSTANTS) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _corpus(source: dict) -> str:
    """입력 JSON 전체를 하나의 문자열로 — 근거 존재 여부 판정용."""
    return json.dumps(source, ensure_ascii=False)


def check_block(block: dict, cfg: dict) -> Result:
    oc = cfg["output_contract"]
    text = "\n".join(
        str(block.get(k, "")) for k in ("fact", "claim", "disclosure")
    )
    source = block.get("source", {})
    src = _corpus(source)
    level = block.get("level", "verdict")

    r = Result(block_id=block.get("id", "?"), level=level)

    tier = block.get("report_tier", "base")
    # author_draft 는 작성자 전용이므로 완곡화·중립 검사를 면제한다.
    # 다만 정신 건강 진술만은 층과 무관하게 차단한다(constants.yaml
    # output_contract.author_draft.mental_health_handling).
    author_layer = tier == "author_draft"

    if author_layer:
        # 메타 표현(고지·규칙 언급)은 제외하고 실제 상태 진술만 잡는다
        META = ["정신 건강 진술", "정신 건강 판정", "정신 건강 관련",
                "정신적 증상", "존재 고지", "정신 건강 서술"]
        scan = text
        for m in META:
            scan = scan.replace(m, "")
        MH = ["우울", "불안장애", "정신질환", "조울", "공황"]
        for w in MH:
            if w in scan:
                r.findings.append(Finding("mental_health_leak", "fail",
                    f"정신 건강 진술 '{w}' — 작성자 검토본에도 옮기지 않는다. "
                    "존재 사실만 고지할 것"))
        return r

    # 1. 금지 표현
    for expr in oc["prohibited_expressions"]:
        if expr in text:
            r.findings.append(Finding("prohibited_expression", "fail",
                                      f"금지 표현 '{expr}'"))

    # 2. S3 어휘
    for kw in oc["s3_keywords"]:
        if kw in text:
            r.findings.append(Finding("s3_keyword", "fail",
                                      f"S3 어휘 '{kw}' — §5.1 위반"))

    # 3. 타 전통 용어
    for term in oc["cross_tradition_terms"]:
        if term in text:
            r.findings.append(Finding("cross_tradition", "fail",
                                      f"타 전통 용어 '{term}' — §1.1 위반"))

    # 3b. 영어 잔재 — 베딕 용어 허용 목록에 없는 라틴어 토큰
    latin_allow = {a.lower() for aliases in VEDIC_TERMS.values()
                   for a in aliases}
    latin_allow |= {"rashi", "bhava", "gochara", "sasa",
                    "ruchaka", "hamsa", "malavya", "bhadra"}
    for tok in set(re.findall(r"[A-Za-z]{3,}", text)):
        if tok.lower() not in latin_allow:
            r.findings.append(Finding("english_leak", "fail",
                                      f"영어 잔재 '{tok}' — 한국어 서술로 교체"))

    # 4. 수치 유출 — 3자리 이상 정수와 소수는 입력에 있어야 한다
    for num in set(re.findall(r"\d+\.\d+|\d{3,}", text)):
        if num not in src:
            r.findings.append(Finding("numeric_leak", "fail",
                                      f"입력에 없는 수치 '{num}'"))

    # 5. 용어 유출 — 입력에 근거 없는 점성 용어 (한글 표기 또는 영문 별칭으로 대조)
    src_lower = src.lower()
    for term, aliases in VEDIC_TERMS.items():
        # "필요가 있습니다"의 '요가' 같은 부분문자열 오탐 방지:
        # 용어 앞이 한글이 아니어야 한다. 합성어(다나요가·라자요가)는 별도 항목.
        # 한 글자 용어(달)는 "달라집니다" 오탐 방지로 뒤 경계도 요구한다.
        pat = (rf"(?<![가-힣]){re.escape(term)}(?![가-힣])" if len(term) == 1
               else rf"(?<![가-힣]){re.escape(term)}")
        if not re.search(pat, text):
            continue
        grounded = term in src or any(a.lower() in src_lower for a in aliases)
        if not grounded:
            r.findings.append(Finding("term_leak", "fail",
                                      f"입력에 근거 없는 용어 '{term}'"))

    # 6. 인용 무결성 — 문서·절 참조가 입력에 있어야 한다
    for cite in set(re.findall(r"§[\d.]+|\b\d{2}_[가-힣A-Za-z_]+", text)):
        if cite not in src:
            r.findings.append(Finding("citation_integrity", "fail",
                                      f"입력에 없는 인용 '{cite}'"))

    # 7-a. divergent — 조건절 구조(B안)를 전제로 검사한다.
    # 대칭 표지("한쪽/다른 쪽")를 요구하지 않는다. 자연스러운 서술을 막기 때문이다.
    # 대신 조건부와 divergent 를 가르는 유일한 요소인 "판별 갈림길" 을 검사한다.
    if level == "divergent":
        claim = str(block.get("claim", ""))

        # (1) 전환 — 첫 방향 뒤에 반대 방향이 실제로 오는가
        PIVOT = ["다만", "그런데", "반면", "한편", "그러나", "하지만"]
        if not any(m in claim for m in PIVOT):
            r.findings.append(Finding("divergent_structure", "fail",
                "전환 표지 없음 — 반대 방향으로 넘어가는 지점이 있어야 한다"))

        # (2) 갈림길 — 독자가 자기 경우를 판별할 분기가 최소 둘
        BRANCH = ["라면", "다면", "했다면", "경우", "쪽이라면"]
        n = sum(claim.count(m) for m in BRANCH)
        if n < 2:
            r.findings.append(Finding("divergent_structure", "fail",
                f"판별 갈림길 부족({n}개) — 독자가 자기 경우를 가릴 분기가 둘 이상이어야 한다"))

        # (3) 귀결 — 각 분기에 따라 행동이 달라지는가
        OUTCOME = ["낫습니다", "두시면", "보셔도", "먼저 정하", "무게를", "편이", "됩니다"]
        if not any(m in claim for m in OUTCOME):
            r.findings.append(Finding("divergent_structure", "fail",
                "분기별 귀결 없음 — 어느 쪽이냐에 따라 조언이 달라져야 한다"))

        for pat in [r"반드시\s", r"틀림없이", r"확실히\s+\S+합니다"]:
            m = re.search(pat, claim)
            if m:
                r.findings.append(Finding("divergent_structure", "fail",
                    f"확정 어투 '{m.group()}'"))

    # 7-b. conditional 어투
    if level == "conditional":
        claim = str(block.get("claim", ""))
        # 문장 단위로 본다 — 완화어가 있는 문장은 조건부로 인정
        for sent in re.split(r"[.\n]", claim):
            if not sent.strip() or any(h in sent for h in HEDGE_WORDS):
                continue
            for pat in CERTAINTY_PATTERNS:
                m = re.search(pat, sent)
                if m:
                    r.findings.append(Finding("conditional_voice", "fail",
                        f"확정 어투 '{m.group()}' — \"{sent.strip()[:34]}\""))
                    break
        if not any(m in claim for m in CONDITIONAL_MARKERS):
            r.findings.append(Finding("conditional_voice", "fail",
                                      "조건부 블록에 조건 표지 없음"))

    # 7-b2. 어휘 — 고객 발송본에서 피할 표현
    if tier != "author_draft":
        AVOID = {
            "단서": "계약서 말투. '다만 짚어둘 게' 등으로",
            "이 차트": "도구가 주어가 됨. '당신에게' 등으로",
            "차트가": "도구가 주어가 됨. '타고난 조건은' 등으로",
            "차트는": "도구가 주어가 됨",
            "당신 차트": "도구가 주어가 됨. '당신에게' 로",
        }
        for w, why in AVOID.items():
            if w in text:
                r.findings.append(Finding("wording", "fail", f"'{w}' — {why}"))

    # 7-c. 맥락 중립 — 기본 리포트는 고객 상태를 전제하지 않는다
    if block.get("report_tier", "base") == "base":
        CONTEXT_ASSUMING = {
            "조직": ["어떤 조직에서", "조직에서 유독", "어디에 있든", "회사에서"],
            "직업 상태": ["이직", "재직", "퇴사", "승진", "직장을 옮"],
            "관계 상태": ["새로운 만남", "배우자", "결혼하", "연인", "애인"],
            "재정 상태": ["연봉", "월급", "빚을", "대출을"],
        }
        for cat, pats in CONTEXT_ASSUMING.items():
            for pat in pats:
                if pat in text:
                    r.findings.append(Finding("context_neutrality", "fail",
                        f"{cat} 전제 표현 '{pat}' — 기본 리포트는 상태 중립이어야 한다"))

    # 8. 필드 소유권 — fact 는 템플릿 생성이어야 한다
    if block.get("fact_generated_by") not in (None, "template"):
        r.findings.append(Finding("field_ownership", "fail",
                                  "[Fact] 는 템플릿 생성이어야 함 (LLM 미개입)"))

    # 9. 반대 근거가 있으면 disclosure 에 반영되어야 한다 (§4.3)
    if source.get("opposing") and not str(block.get("disclosure", "")).strip():
        r.findings.append(Finding("disclosure_required", "fail",
                                  "반대 근거 존재 — §4.3 병기 누락"))

    return r


def report(results: list[Result], cfg: dict) -> int:
    fails = 0
    for res in results:
        mark = "PASS" if res.passed else "FAIL"
        print(f"[{mark}] {res.block_id} ({res.level})")
        for f in res.findings:
            print(f"       {f.severity.upper():<5}{f.check:<22}{f.detail}")
        if not res.passed:
            fails += 1

    print(f"\n통과 {len(results) - fails} / 차단 {fails}  (전체 {len(results)})")
    if fails:
        action = cfg["output_contract"]["narration"]["fail_action"]
        print(f"[NARRATION GATE] {fails}건 차단 — fail_action: {action}")
        print("  재생성하거나 수기 수정 후 재검사할 것. 검사 미통과 블록은 발송하지 않는다.")
    else:
        print("[NARRATION GATE] 전 블록 통과 — 사람 검토 단계로 진행")
    return fails


DEMO = [
    {   # 정상 블록
        "id": "C1-claim", "level": "verdict",
        "fact_generated_by": "template",
        "source": {
            "planets": {"saturn": {"sign": "염소자리", "house": 10}},
            "tiers": ["t1_d1", "t5_ashtaka", "t6_yoga"],
            "citations": ["19_하우스10", "§1", "§3"],
            "sav": 39,
        },
        "fact": "10하우스 로드 토성이 염소자리 10하우스에 위치합니다.",
        "claim": "자기 사인이자 자기 하우스에 놓인 배치로, 커리어 지향이 강화되는 구조입니다.",
        "disclosure": "분할차트에서 동일 강도가 반복되는지는 별도 확인이 필요합니다.",
    },
    {   # 여러 위반
        "id": "C2-claim", "level": "verdict",
        "fact_generated_by": "llm",
        "source": {"planets": {"jupiter": {"sign": "사자자리", "house": 5}}},
        "fact": "목성이 사자자리 5하우스에 있습니다.",
        "claim": "반드시 큰 성공을 이룹니다. 금성의 지원으로 2035년에 확정적 전환이 옵니다.",
        "disclosure": "",
    },
    {   # 조건부인데 확정 어투
        "id": "C3-claim", "level": "conditional",
        "fact_generated_by": "template",
        "source": {"planets": {"saturn": {"sign": "염소자리"}}, "opposing": ["t3_varga"]},
        "fact": "토성이 염소자리에 있습니다.",
        "claim": "재정 확장이 이루어집니다.",
        "disclosure": "",
    },
]


def main(argv: list[str]) -> int:
    cfg = load_constants()
    if "--demo" in argv:
        blocks = DEMO
    elif len(argv) > 1:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "narration_input" in data:
            blocks = list(data["narration_input"].get("conclusions", []))
        elif isinstance(data, dict) and "conclusions" in data:
            blocks = list(data["conclusions"])
        else:
            blocks = data if isinstance(data, list) else [data]
    else:
        print(__doc__)
        return 2
    return 1 if report([check_block(b, cfg) for b in blocks], cfg) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
