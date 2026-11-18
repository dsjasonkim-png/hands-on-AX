"""처음 실행할 때 패키지를 설치하면서 진행 상황을 보여 준다. run_book_runner.bat / .command가 부른다.

pip의 긴 출력은 logs/install.log에 남기고, 화면에는 지금 무엇을 받는지와 경과 시간만 보여 준다.
출력이 한동안 없으면(설치 단계는 몇 분씩 조용하다) 15초마다 아직 작업 중이라고 알린다.
"""
from __future__ import annotations

import os
import re
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "install.log"
QUIET_SEC = 15


def clock(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}분 {s:02d}초" if m else f"{s}초"


def run(title: str, cmd: list[str], log, t0: float) -> int:
    print(f"\n▶ {title}", flush=True)
    log.write(f"\n$ {' '.join(cmd)}\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=lambda: ([lines.put(l) for l in proc.stdout], lines.put(None)), daemon=True).start()

    got, last = 0, time.time()
    while True:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            if time.time() - last >= QUIET_SEC:
                print(f"  … 아직 작업 중입니다 (경과 {clock(time.time() - t0)})", flush=True)
                last = time.time()
            continue
        if line is None:
            break
        log.write(line)
        log.flush()
        text = line.strip()
        if text.startswith("Collecting "):
            got += 1
            name = re.split(r"[<>=~!;\[ ]", text.split()[1])[0]
            print(f"  [{got:>3}] 받는 중: {name}  ({clock(time.time() - t0)})", flush=True)
        elif text.startswith("Installing collected packages"):
            n = len(text.split(":", 1)[1].split(","))
            print(f"  내려받기 끝. 패키지 {n}개를 설치합니다. 가장 오래 걸리는 단계라 몇 분 동안 조용할 수 있습니다.", flush=True)
        elif text.startswith("Successfully installed"):
            print(f"  설치 완료 ({clock(time.time() - t0)})", flush=True)
        elif text.startswith(("ERROR", "error:")) or "Error" in text.split(":")[0]:
            print(f"  {text}", flush=True)
        else:
            continue
        last = time.time()
    return proc.wait()


def main() -> int:
    LOG.parent.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"설치 기록은 {LOG.relative_to(ROOT)}에 남습니다. 인터넷 속도에 따라 5~15분 걸립니다.", flush=True)
    with LOG.open("w", encoding="utf-8") as log:
        pip = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
        if run("pip 최신으로 바꾸기", pip + ["install", "--upgrade", "pip"], log, t0):
            print("pip을 바꾸지 못했지만 그대로 진행합니다.", flush=True)
        code = run("책 예제에 필요한 패키지 받기와 설치", pip + ["install", "--prefer-binary", "-r", str(ROOT / "requirements.txt")],
                   log, t0)
    if code:
        print(f"\n패키지 설치에 실패했습니다. {LOG.relative_to(ROOT)}의 마지막 부분에 원인이 있습니다.", flush=True)
    else:
        print(f"\n모든 패키지를 설치했습니다. 걸린 시간 {clock(time.time() - t0)}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
