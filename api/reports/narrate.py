"""서술층 — 워크시트를 LLM API 로 한국어 서술 블록(JSON)으로 변환한다.

출력 형식은 narration_check.check_block 이 받는 block 스키마를 따른다:
  {id, level, fact, claim, disclosure, source, report_tier}

흐름:
  worksheet → 프롬프트 → LLM API → JSON 파싱 → check_block 전수 검사
  → 실패 시 피드백을 포함해 재생성 (최대 MAX_RETRIES)

프로바이더: "gemini" (기본값, Google AI Studio 무료 티어) 또는 "anthropic".
gemini는 저장된 커넥터 크레덴셜을 사용하므로 GEMINI_API_KEY 없이도 동작한다.
anthropic은 ANTHROPIC_API_KEY 환경변수가 필요하다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
# tone_guide.md: 사용자의 문체 처방 (232줄). 프롬프트에 직접 주입한다.
# 없으면(경로 이동 등) 조용히 생략 — 핵심 안전 규칙은 SYSTEM_PROMPT_TEMPLATE에 있음.
try:
    _TONE_GUIDE = (HERE / "tone_guide.md").read_text(encoding="utf-8").strip()
except OSError:
    _TONE_GUIDE = ""
PROVIDER_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.5-flash-lite",
}
PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
MAX_RETRIES = 3

COERCION_RULES = {
    # 카테고리별 "강요/권유 금지" 규칙 (SYSTEM 프롬프트 4번 자리)
    "CAREER_DIRECTION": "직업 강요 금지: '~해야 합니다' 대신 '~을 고려해볼 수 있습니다'를 사용하십시오.",
    "FINANCE_FLOW": "투자 권유 금지: 특정 자산·종목 추천이나 수익 확정 뉘앙스를 쓰지 마십시오. "
                    "'~에 투자해야 합니다' 대신 '~흐름을 점검해볼 수 있습니다'를 사용하십시오.",
    "RELATIONSHIP_PATTERN": "관계 단정 금지: 만남·이별·결혼의 확정이나 특정 상대와의 인연 단정을 쓰지 마십시오. "
                    "'~와 헤어져야 합니다' 대신 '~패턴을 돌아볼 수 있습니다'를 사용하십시오.",
    "CONDITION_RHYTHM": "진단·처방 금지: 질병명 확정이나 치료·복용 지시를 쓰지 마십시오. "
                    "'~약을 드십시오' 대신 '~리듬을 살펴볼 수 있습니다'를 사용하십시오.",
}

SYSTEM_PROMPT_TEMPLATE = """당신은 베딕 점성술 리포트의 한국어 서술을 작성하는 전문가입니다.
tone_guide.md의 문체 기준을 따릅니다: 번역체 금지, 한자어 남발 금지,
문장 길이 리듬 유지, 구어체(~세요/보세요) 적절 사용.

절대 규칙 (위반 시 발송 차단):
1. 워크시트의 chart_facts와 evidence에 없는 수치·용어·문헌을 인용하지 마십시오.
2. S3 주제(사망, 수명, 불치병, 불임, 이혼, 사별)에 대한 판정을 쓰지 마십시오.
3. 확정 어투 금지: '~할 것이다' 대신 '~경향이 있습니다'를 사용하십시오.
4. {coercion_rule}
5. gate_level이 conditional인 결론은 조건부 어투로만 서술하십시오
   ('~라면 ~를 검토할 수 있습니다').
6. gate_level이 withheld인 내용은 본문에 쓰지 말고 '이견 및 불확실 사항' 섹션에만
   조건부 어투로 서술하십시오.
7. 금지 표현을 절대 쓰지 마십시오: 반드시, 무조건, 100%, 운명적으로, 틀림없이,
   확정, 분명히, 당연히, 절대. ('확정적이지 않습니다' 같은 우회 표현도 금지 —
   대신 '단정할 수 없습니다'를 쓰십시오.)
8. 각 블록의 source에는 반드시 해당 블록의 근거 rule_id를 그대로 적으십시오.
9. 베딕 점성술 용어만 쓰십시오. 사주·중국 점성술·서양 점성술 용어 절대 금지:
   대운, 세운, 일간, 십신, 격국, 용신, 조후, 신살, 오행, 사주, 명리, 에센셜.
   '대운' 대신 '다샤(행성 주기)', '세운' 대신 '트랜짓(고차라)'을 쓰십시오.
10. 본문은 한국어만 쓰십시오. 영어 단어를 그대로 쓰지 마십시오
    (예: signification → '상징 작용', D1/D9/D10·요가명 등 베딕 고유명사는 예외).

출력은 반드시 아래 JSON 배열만 반환하십시오 (마크다운 코드펜스 없이):
[
  {
    "id": "결론 ID (예: {rec_id}-T1)",
    "level": "verdict | conditional | withheld",
    "fact": "근거 사실 서술 (evidence 기반, 2-4문장)",
    "claim": "해석 주장 (조건부 어투 준수, 2-4문장)",
    "disclosure": "불확실성·조건 고지 (1-2문장)",
    "source": {"rule_id": "...", "doc": "..."},
    "report_tier": "base"
  }
]
"""


def build_user_prompt(worksheet: dict) -> str:
    w = worksheet
    lines = [
        f"# 리포트 요청: {w['report_code']}",
        f"대상: {w['meta'].get('name', '')} "
        f"({w['meta'].get('birth_date', '')} {w['meta'].get('birth_time', '')}, "
        f"{w['meta'].get('place', '')})",
        f"게이트 판정: {w['gate_level']}",
        "",
        "## 차트 사실 (이 범위 안에서만 서술)",
        json.dumps(w["chart_facts"], ensure_ascii=False, indent=2),
        "",
        "## 판정 블록 (각 블록을 하나의 conclusion으로 서술)",
    ]
    for i, b in enumerate(w["verdict_blocks"], 1):
        lines.append(f"\n### 블록 {i} [{b['tier']}] 방향={b['direction']}")
        for e in b["evidence"]:
            lines.append(f"- 근거({e['rule_id']}, {e['source']}): {e['text']}")
            if e["detail"]:
                lines.append(f"  조건: {e['detail']}")
    if w.get("review_notes"):
        lines.append("\n## 사람이 확인할 사항 (서술에 반영하되 단정하지 말 것)")
        for n in w["review_notes"]:
            lines.append(f"- {n}")
    lines += [
        "",
        "## 안전 규칙",
        *[f"- {s}" for s in w["safety_rules"]],
    ]
    return "\n".join(lines)


def _call_anthropic(system: str, user: str, api_key: str, model: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _call_gemini(system: str, user: str, api_key: str, model: str) -> str:
    """Google AI Studio 무료 티어.

    api_key가 직접 주어지면 x-goog-api-key 헤더로 호출하고,
    없으면 저장된 커넥터 크레덴셜을 쓰는 스킬 CLI를 경유한다
    (원시 키를 코드·로그에 노출하지 않기 위함).
    """
    import subprocess
    if api_key:
        import urllib.request
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4000,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": api_key,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(
                f"Gemini 응답 파싱 실패: {json.dumps(data)[:500]}")
    cli = os.path.expanduser(
        "~/workspace/skills/google-gemini/bin/gemini_generate.py")
    r = subprocess.run(
        [sys.executable, cli, "--model", model, "--json",
         "--system", system, "--prompt", user],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"Gemini 호출 실패: {r.stderr[:500]}")
    return r.stdout


PROVIDER_CALL = {
    "anthropic": _call_anthropic,
    "gemini": _call_gemini,
}


def _parse_blocks(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _attach_grounding(blocks: list[dict], worksheet: dict) -> list[dict]:
    """블록의 source에 워크시트 근거를 deterministic하게 주입한다.

    narration_check의 term_leak/numeric_leak/citation_integrity는
    block["source"]를 근거 코퍼스로 삼는다. LLM이 쓴 source를 그대로 믿지 않고
    rule_id → 해당 tier evidence를 매칭해 주입한다. 매칭 실패 시 전체 evidence를
    넣어 검사를 느슨하게 하되, 워크시트 밖 내용은 여전히 차단된다.
    """
    by_rule: dict[str, tuple[dict, dict]] = {}
    for b in worksheet["verdict_blocks"]:
        for e in b["evidence"]:
            by_rule[e["rule_id"]] = (b, e)
    for blk in blocks:
        src = blk.get("source")
        src = dict(src) if isinstance(src, dict) else {}
        hit = by_rule.get(src.get("rule_id", ""))
        if hit:
            _, e = hit
            grounding = {"evidence": e["text"], "doc": e["source"],
                         "detail": e["detail"]}
        else:
            grounding = {"all_evidence": [
                e["text"] for b in worksheet["verdict_blocks"]
                for e in b["evidence"]]}
        grounding["chart_facts"] = worksheet["chart_facts"]
        src["grounding"] = grounding
        blk["source"] = src
    return blocks


def narrate(worksheet: dict, check_block, cfg: dict,
            api_key: str | None = None, provider: str = "gemini",
            model: str | None = None) -> list[dict]:
    """워크시트 → 검증 통과한 서술 블록. 실패 시 RuntimeError.

    provider: "gemini" (무료 티어, 기본값) 또는 "anthropic".
    model 생략 시 프로바이더별 기본 모델 사용.
    """
    if provider not in PROVIDER_CALL:
        raise ValueError(f"지원하지 않는 provider: {provider}")
    call_api = PROVIDER_CALL[provider]
    model = model or PROVIDER_MODELS[provider]
    env_var = PROVIDER_ENV[provider]
    key = api_key or os.environ.get(env_var)
    if not key and provider == "anthropic":
        raise RuntimeError(
            f"{env_var} 가 없습니다 — 환경변수로 설정하거나 인자로 전달하세요.")
    # provider == "gemini" 이고 키가 없으면 _call_gemini 가 저장된
    # 커넥터 크레덴셜(스킬 CLI 경유)을 사용한다.
    report_code = worksheet.get("report_code", "CAREER_DIRECTION")
    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
        "{coercion_rule}",
        COERCION_RULES.get(report_code, COERCION_RULES["CAREER_DIRECTION"]))
    rec_id = {"CAREER_DIRECTION": "C1", "FINANCE_FLOW": "F1",
              "RELATIONSHIP_PATTERN": "R1",
              "CONDITION_RHYTHM": "H1"}.get(report_code, "C1")
    system_prompt = system_prompt.replace("{rec_id}", rec_id)
    if _TONE_GUIDE:
        system_prompt += ("\n\n## 문체 기준 (tone_guide.md — 반드시 준수)\n"
                          + _TONE_GUIDE)
    user_prompt = build_user_prompt(worksheet)
    feedback = ""
    last_errors: list[str] = []
    debug_dir = os.environ.get("NARRATE_DEBUG_DIR")
    for attempt in range(1, MAX_RETRIES + 1):
        prompt = user_prompt + feedback
        raw = call_api(system_prompt, prompt, key, model)
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, f"attempt{attempt}.json"),
                      "w", encoding="utf-8") as f:
                f.write(raw)
        try:
            blocks = _parse_blocks(raw)
        except json.JSONDecodeError as ex:
            last_errors = [f"JSON 파싱 실패: {ex}"]
            feedback = "\n\n## 이전 시도 실패\n- 유효한 JSON 배열만 출력하세요 (설명 문구 금지)."
            continue
        blocks = _attach_grounding(blocks, worksheet)
        results = [check_block(b, cfg) for b in blocks]
        failed = [r for r in results if not r.passed]
        if debug_dir:
            with open(os.path.join(debug_dir, f"attempt{attempt}.check.json"),
                      "w", encoding="utf-8") as f:
                json.dump([{"id": r.block_id, "passed": r.passed,
                            "findings": [f"{x.check}/{x.severity}: {x.detail}"
                                         for x in r.findings]}
                           for r in results], f, ensure_ascii=False, indent=1)
        if not failed:
            return blocks
        last_errors = [
            f"{r.block_id}: " + "; ".join(
                f"[{f.check}/{f.severity}] {f.detail}" for f in r.findings)
            for r in failed]
        feedback = ("\n\n## 이전 시도 검증 실패 — 아래를 고쳐 다시 작성하세요\n"
                    + "\n".join(f"- {e}" for e in last_errors))
    # MAX_RETRIES 소진 — 문서화된 설계대로 실패 블록은 withheld로 강등한다.
    # 등급을 올릴 수는 없고 내릴 수만 있다: 검증 실패한 텍스트는 본문에 못 쓴다.
    # 전부 실패하면 전달할 게 없으므로 RuntimeError 유지.
    passed_ids = {r.block_id for r in results if r.passed}
    if not passed_ids:
        raise RuntimeError(f"서술 검증 {MAX_RETRIES}회 실패 (전 블록): {last_errors}")
    fail_map = {r.block_id: r for r in results if not r.passed}
    final: list[dict] = []
    for b in blocks:
        r = fail_map.get(b.get("id"))
        if r is None:
            final.append(b)
            continue
        reasons = "; ".join(f"[{f.check}] {f.detail}" for f in r.findings)
        final.append({
            "id": b.get("id"), "level": "withheld",
            "fact": "이 블록은 자동 서술 검증을 통과하지 못해 본문에서 제외되었습니다.",
            "claim": "사람 검토자가 직접 작성해야 합니다.",
            "disclosure": f"자동 검증 실패 사유: {reasons}",
            "source": b.get("source", {}),
            "report_tier": b.get("report_tier", "base"),
        })
    return final
