#!/bin/bash
# Hands-on AX 예제 실행기 (macOS·Linux)
#   Finder에서 두 번 누르거나, 터미널에서 ./run_book_runner.command 로 실행합니다.
#   처음 한 번은 .venv_runner 가상 환경을 만들고 패키지를 설치합니다.
cd "$(dirname "$0")" || exit 1

PY=""
for cand in python3.12 python3.13 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done

if [ ! -x .venv_runner/bin/python ]; then
  if [ -z "$PY" ]; then
    echo "파이썬 3.11 이상을 찾지 못했습니다. https://www.python.org 에서 3.12를 설치한 뒤 다시 실행하세요."
    read -r -p "Enter를 누르면 창을 닫습니다." _; exit 1
  fi
  echo "[1/2] 가상 환경 .venv_runner 만드는 중 ($PY)"
  "$PY" -m venv .venv_runner || { echo "가상 환경을 만들지 못했습니다."; read -r -p "Enter" _; exit 1; }
fi

VPY=.venv_runner/bin/python
if ! cmp -s requirements.txt .venv_runner/installed.txt; then
  echo "[2/2] 패키지 설치 중 (처음 한 번만, 진행 상황이 아래에 나옵니다)"
  if ! "$VPY" runner/install.py; then
    read -r -p "Enter를 누르면 창을 닫습니다." _; exit 1
  fi
  cp requirements.txt .venv_runner/installed.txt
fi

echo
echo "예제 실행기를 시작합니다. 브라우저가 열리지 않으면 http://127.0.0.1:8765 로 접속하세요."
echo "끝내려면 이 창에서 Ctrl+C 를 누르세요."
exec "$VPY" runner/server.py "$@"
