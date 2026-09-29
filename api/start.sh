#!/bin/bash
# Railway 시작 스크립트: 천문력 확보 후 API 기동
set -e
cd "$(dirname "$0")"

python fetch_bsp.py

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
