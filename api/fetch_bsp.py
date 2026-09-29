"""Railway/Docker 시작 시 de430.bsp 확보.

constants.yaml arithmetic.ephemeris.files 에서 URL·SHA256·크기를 읽는다.
파일이 있고 해시가 맞으면 아무것도 하지 않는다 (재배포 시 1회만 다운로드).
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine.constants import load_constants  # noqa: E402

CONSTANTS_PATH = HERE / "constants.yaml"
EPHE_DIR = HERE / "ephe_sky"
FILENAME = "de430.bsp"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    cfg = load_constants(str(CONSTANTS_PATH))
    spec = cfg["arithmetic"]["ephemeris"]["files"][FILENAME]
    url = spec["url"]
    expected_sha = spec["sha256"]
    expected_size = spec["size_bytes"]

    EPHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = EPHE_DIR / FILENAME

    if dest.exists():
        if dest.stat().st_size == expected_size and sha256_of(dest) == expected_sha:
            print(f"  OK {FILENAME} 이미 있음 (해시 일치) — 다운로드 생략")
            return 0
        print(f"  경고 {FILENAME} 해시/크기 불일치 — 다시 받음")
        dest.unlink()

    print(f"  다운로드 {url}")
    print(f"  → {dest} (약 {expected_size // 1024 // 1024}MB)")
    tmp = dest.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as e:  # noqa: BLE001
        print(f"  실패: {e}")
        if tmp.exists():
            tmp.unlink()
        return 1

    actual_sha = sha256_of(tmp)
    if actual_sha != expected_sha:
        print(f"  SHA256 불일치!\n  기대: {expected_sha}\n  실제: {actual_sha}")
        tmp.unlink()
        return 1
    tmp.rename(dest)
    print(f"  완료 (SHA256 일치)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
