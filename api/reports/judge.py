"""판정층 — 규칙 DB를 엔진 JSON에 적용해 계열을 자동으로 채운다.

사람은 결과를 검토만 한다. 자동 판정이 불가능한 계열은 gaps 로 표시되어
사람이 채워야 할 곳이 명시된다.

when 조건 문법 (전부 AND):
  tenth_lord_house / tenth_lord_dignity / tenth_sign   10하우스 전용 (커리어 호환)
  lord_house: {lord_of: N, in_house: M}   N하우스 로드가 M하우스에 위치
  lord_dignity: {lord_of: N, dignity: own|exalted|debilitated|other}
  house_sign: {house: N, sign: S}         N하우스의 사인 번호
  karaka_house: {karaka: dk, in_house: N}  카라카(DK 등) 행성의 하우스
  planet_in_house: {행성: 하우스}
  sav_house: {house: n, min: x, max: y}   상한 미포함
  varga_sign_house / varga_contrast        바르가
  yoga: {planet, signs, houses}           요가 성립 조건
  maha_lord_of: N / antar_lord_of: N      현재 마하/안타르다샤 로드가 N하우스 로드와 동일
  maha_lord: planet / antar_lord: planet  현재 마하/안타르다샤 로드가 특정 행성

사용:
    python judge.py engine_output.json
    python judge.py engine_output.json --json conclusions.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
SIGN_LORD = {1: "mars", 2: "venus", 3: "mercury", 4: "moon", 5: "sun", 6: "mercury",
             7: "venus", 8: "mars", 9: "jupiter", 10: "saturn", 11: "saturn", 12: "jupiter"}
OWN = {"sun": [5], "moon": [4], "mars": [1, 8], "mercury": [3, 6],
       "jupiter": [9, 12], "venus": [2, 7], "saturn": [10, 11]}
EXALT = {"sun": 1, "moon": 2, "mars": 10, "mercury": 6,
         "jupiter": 4, "venus": 12, "saturn": 7}
DEBIL = {p: (s + 6 - 1) % 12 + 1 for p, s in EXALT.items()}
KENDRA = [1, 4, 7, 10]
DUSTHANA = [6, 8, 12]
PKO = {"sun": "태양", "moon": "달", "mars": "화성", "mercury": "수성", "jupiter": "목성",
       "venus": "금성", "saturn": "토성", "rahu": "라후", "ketu": "케투"}
SIGN = ["", "양", "황소", "쌍둥이", "게", "사자", "처녀", "천칭", "전갈",
        "사수", "염소", "물병", "물고기"]


class Chart:
    """엔진 JSON에서 판정에 필요한 사실만 뽑아 정규화한다."""

    def __init__(self, data: dict):
        ji = data.get("judgment_input", data)
        self.raw = ji
        ch = ji["chart"]
        self.asc_sign: int = ch["ascendant"]["sign"]
        self.planets = ch["planets"]
        self.sign_of = {p: v["sign"] for p, v in self.planets.items()}
        self.house_of = {p: v["whole_sign_houses"]["lagna"] for p, v in self.planets.items()}
        self.tenth_sign = (self.asc_sign + 9 - 1) % 12 + 1
        self.tenth_lord = SIGN_LORD[self.tenth_sign]
        self.sav = ji["ashtakavarga"]["sav"]
        self.bav = ji["ashtakavarga"]["bav"]
        self.varga = ji["varga"]["positions"]
        self.karaka = ji["karaka"]
        self.dasha = ji["dasha"]["birth_periods"]
        self.maha_lord, self.antar_lord = _current_dasha_lords(
            ji["dasha"].get("full_timeline", []))

    def dignity(self, planet: str) -> str:
        s = self.sign_of.get(planet)
        if s is None:
            return "unknown"
        if s in OWN.get(planet, []):
            return "own"
        if EXALT.get(planet) == s:
            return "exalted"
        if DEBIL.get(planet) == s:
            return "debilitated"
        return "other"

    def house_sign(self, house: int) -> int:
        """라그나 기준 N하우스의 사인 번호."""
        return (self.asc_sign + house - 2) % 12 + 1

    def lord_of(self, house: int) -> str:
        """N하우스 로드 행성."""
        return SIGN_LORD[self.house_sign(house)]

    def varga_sign(self, planet: str, varga: str) -> int | None:
        v = self.varga.get(planet, {}).get(varga)
        return v.get("result_sign") if v else None

    def varga_house(self, planet: str, varga: str) -> int | None:
        """해당 바르가 라그나 기준 하우스."""
        anchor = self.varga.get("ascendant") or self.varga.get("lagna") or {}
        asc = anchor.get(varga, {}).get("result_sign")
        s = self.varga_sign(planet, varga)
        if asc is None or s is None:
            return None
        return (s - asc) % 12 + 1


def _current_dasha_lords(timeline: list) -> tuple[str | None, str | None]:
    """full_timeline에서 현재 시점이 속한 마하/안타르다샤 로드."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    for md in timeline or []:
        try:
            s = dt.datetime.fromisoformat(str(md["start"]).replace("Z", "+00:00"))
            e = dt.datetime.fromisoformat(str(md["end"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if s <= now < e:
            antar = None
            for ad in md.get("antardashas", []):
                try:
                    as_ = dt.datetime.fromisoformat(str(ad["start"]).replace("Z", "+00:00"))
                    ae = dt.datetime.fromisoformat(str(ad["end"]).replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if as_ <= now < ae:
                    antar = ad.get("lord")
                    break
            return md.get("lord"), antar
    return None, None


def sav_grade(value: int, cfg: dict) -> str:
    for g in cfg["ashtakavarga"]["sav_grades"]:
        hi = g["max"] + (1 if g.get("max_inclusive") else 0)
        if g["min"] <= value < hi:
            return g["label"]
    return "범위 밖"


def _resolve_planet(spec: str, ch: Chart) -> str:
    """varga_contrast planet 지정자 해석.
    'tenth_lord' (기존 호환) | 'lord_of_N' | 행성명 그대로."""
    if spec == "tenth_lord":
        return ch.tenth_lord
    if spec.startswith("lord_of_"):
        return ch.lord_of(int(spec[len("lord_of_"):]))
    return spec


def match(rule: dict, ch: Chart, cfg: dict) -> tuple[bool, dict]:
    w = rule.get("when", {})
    info: dict[str, Any] = {}

    if "tenth_lord_house" in w:
        if ch.house_of.get(ch.tenth_lord) != w["tenth_lord_house"]:
            return False, info
        info["10로드"] = f"{PKO[ch.tenth_lord]} — {w['tenth_lord_house']}하우스"

    if "tenth_lord_dignity" in w:
        if ch.dignity(ch.tenth_lord) != w["tenth_lord_dignity"]:
            return False, info
        info["존엄"] = w["tenth_lord_dignity"]

    # --- 범용: N하우스 로드 ---
    if "lord_house" in w:
        lh = w["lord_house"]
        lord = ch.lord_of(lh["lord_of"])
        if ch.house_of.get(lord) != lh["in_house"]:
            return False, info
        info[f"{lh['lord_of']}로드"] = \
            f"{PKO[lord]} — {lh['in_house']}하우스"

    if "lord_dignity" in w:
        ld = w["lord_dignity"]
        lord = ch.lord_of(ld["lord_of"])
        if ch.dignity(lord) != ld["dignity"]:
            return False, info
        info[f"{ld['lord_of']}로드 존엄"] = \
            f"{PKO[lord]} — {ld['dignity']}"

    if "house_sign" in w:
        hs = w["house_sign"]
        if ch.house_sign(hs["house"]) != hs["sign"]:
            return False, info
        info[f"{hs['house']}하우스"] = SIGN[hs["sign"]]

    if "karaka_house" in w:
        kh = w["karaka_house"]
        planet = ch.karaka.get(kh["karaka"].upper())
        if planet is None or ch.house_of.get(planet) != kh["in_house"]:
            return False, info
        info[f"카라카 {kh['karaka'].upper()}"] = \
            f"{PKO[planet]} — {kh['in_house']}하우스"

    if "planet_in_house" in w:
        for p, h in w["planet_in_house"].items():
            if ch.house_of.get(p) != h:
                return False, info
            info["재실"] = f"{PKO[p]} — {h}하우스"

    if "tenth_sign" in w:
        if ch.tenth_sign != w["tenth_sign"]:
            return False, info
        info["10하우스"] = SIGN[ch.tenth_sign]

    if "sav_house" in w:
        h = w["sav_house"]["house"]
        sign = (ch.asc_sign + h - 2) % 12 + 1
        val = ch.sav[sign - 1]
        grade = sav_grade(val, cfg)
        info["SAV"] = f"{h}하우스({SIGN[sign]}) {val}점 — {grade}"
        info["_sav_value"] = val
        if val < rule.get("grade_threshold", 0):
            return False, info

    if "yoga" in w:
        y = w["yoga"]
        p = y["planet"]
        if ch.sign_of.get(p) not in y["signs"]:
            return False, info
        if ch.house_of.get(p) not in y["houses"]:
            return False, info
        info["요가"] = (f"{PKO[p]} — {SIGN[ch.sign_of[p]]} "
                      f"{ch.house_of[p]}하우스 (켄드라)")

    # --- 다샤: 현재 마하/안타르다샤 로드 ---
    if "maha_lord_of" in w:
        n = w["maha_lord_of"]
        if ch.maha_lord != ch.lord_of(n):
            return False, info
        info["마하다샤"] = f"{PKO.get(ch.maha_lord, ch.maha_lord)} ({n}로드)"

    if "antar_lord_of" in w:
        n = w["antar_lord_of"]
        if ch.antar_lord != ch.lord_of(n):
            return False, info
        info["안타르다샤"] = f"{PKO.get(ch.antar_lord, ch.antar_lord)} ({n}로드)"

    if "maha_lord" in w:
        if ch.maha_lord != w["maha_lord"]:
            return False, info
        info["마하다샤"] = PKO.get(ch.maha_lord, ch.maha_lord)

    if "antar_lord" in w:
        if ch.antar_lord != w["antar_lord"]:
            return False, info
        info["안타르다샤"] = PKO.get(ch.antar_lord, ch.antar_lord)

    if "varga_contrast" in w:
        p = _resolve_planet(w["varga_contrast"]["planet"], ch)
        parts = [f"D1 {SIGN[ch.sign_of[p]]} {ch.house_of[p]}H ({ch.dignity(p)})"]
        weak = 0
        for v in w["varga_contrast"]["vargas"]:
            s, h = ch.varga_sign(p, v), ch.varga_house(p, v)
            if s is None:
                continue
            parts.append(f"{v.upper()} {SIGN[s]}" + (f" {h}H" if h else ""))
            if h in DUSTHANA:
                weak += 1
        info["바르가"] = " / ".join(parts)
        info["_d1_strong"] = ch.dignity(p) in ("own", "exalted") or ch.house_of[p] in KENDRA
        info["_weak_count"] = weak

    return True, info


def judge(data: dict, rules: dict, cfg: dict) -> dict:
    ch = Chart(data)
    hits: dict[str, list] = {}
    for r in rules["rules"]:
        ok, info = match(r, ch, cfg)
        if not ok:
            continue
        hits.setdefault(r["tier"], []).append({**r, "_info": info})

    tiers, review = {}, []
    for tier, rs in hits.items():
        dirs = {r["direction"] for r in rs}
        if "computed" in dirs:
            i = rs[0]["_info"]
            strong, weak = i.get("_d1_strong"), i.get("_weak_count", 0)
            guess = "oppose" if (strong and weak) else "support"
            tiers[tier] = {"present": True, "direction": guess, "auto": "partial"}
            review.append(f"{tier}: 강약 비교 — 자동 추정 '{('반대' if guess=='oppose' else '지지')}'. "
                          f"사람 확정 필요 ({i.get('바르가','')})")
        else:
            tiers[tier] = {"present": True, "direction": "support", "auto": "full"}

    gaps = rules.get("gaps", {})
    if isinstance(gaps, dict):
        for tier, g in gaps.items():
            review.append(f"{tier}: 규칙 부재 ({g.get('issue', '?')}) — 자동 판정 불가")
    elif isinstance(gaps, list):
        for g in gaps:
            review.append(f"규칙 부재 ({g.get('issue', '?')}) — 자동 판정 불가")

    return {"chart": ch, "tiers": tiers, "hits": hits, "review": review}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine_json")
    ap.add_argument("--rules", default=str(HERE / "rules_career.yaml"))
    ap.add_argument("--constants", default=str(HERE / "constants.yaml"))
    ap.add_argument("--json", dest="out")
    a = ap.parse_args()

    data = json.loads(Path(a.engine_json).read_text(encoding="utf-8"))
    rules = yaml.safe_load(Path(a.rules).read_text(encoding="utf-8"))
    cfg = yaml.safe_load(Path(a.constants).read_text(encoding="utf-8"))
    res = judge(data, rules, cfg)
    ch = res["chart"]

    print(f"차트  라그나 {SIGN[ch.asc_sign]} · 10하우스 {SIGN[ch.tenth_sign]} · "
          f"10로드 {PKO[ch.tenth_lord]}({ch.dignity(ch.tenth_lord)}) "
          f"{ch.house_of[ch.tenth_lord]}하우스\n")

    print("자동 채워진 계열")
    for tier, rs in res["hits"].items():
        print(f"  [{tier}]")
        for r in rs:
            det = " · ".join(f"{k}={v}" for k, v in r["_info"].items()
                             if not k.startswith("_"))
            print(f"    {r['id']:<22}{r['source']}")
            print(f"      \"{r.get('text') or r.get('text_strong_weak','')}\"")
            if det:
                print(f"      조건: {det}")

    print("\n사람이 확인할 것")
    for x in res["review"]:
        print("  ·", x)

    rec = {"id": "C1", "code": "CAREER_DIRECTION",
           "tiers": {k: {"present": v["present"], "direction": v["direction"]}
                     for k, v in res["tiers"].items()},
           "safety": {k: False for k in
                      ["no_s3", "no_certainty_language",
                       "no_job_coercion", "allowed_frames_only"]}}
    print("\n안전 검토 4항목은 자동 판정하지 않는다 — 사람이 확인 후 true 로 바꿀 것.")
    if a.out:
        Path(a.out).write_text(json.dumps([rec], ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(f"저장: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
