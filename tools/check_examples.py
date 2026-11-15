"""모든 장의 셀을 화면 없이 차례로 실행해 오류가 없는지 확인합니다. (저자·관리자용)

    python tools/check_examples.py                 # 가짜 모델로 모든 장
    python tools/check_examples.py --real ch05 ch11  # 실제 모델로 일부 장
    python tools/check_examples.py -v ch04         # 셀 출력까지 보기

원고를 고친 뒤 chapters/*.md를 갱신했다면 이 스크립트로 먼저 확인하고 배포합니다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "runner"
sys.path.insert(0, str(RUNNER))
import server  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chapters", nargs="*", help="장 ID (생략하면 전부)")
    ap.add_argument("--real", action="store_true", help="실제 모델로 실행 (.env의 키 사용, 요금 발생)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    for k, v in server.read_env().items():
        server.os.environ.setdefault(k, v)
    mode = "real" if args.real else "fake"
    all_chapters = server.chapter_files.load_all(server.CHAPTERS)
    targets = [c for c in all_chapters if not args.chapters or c["id"] in args.chapters]
    total_fail = 0
    for ch in targets:
        w = server.Worker(ch["id"], mode)
        ok = fail = 0
        t0 = time.time()
        try:
            for i, c in enumerate(ch["cells"]):
                if c["type"] != "code" or not c["run"]:
                    continue
                code = c.get("fake_code") if mode == "fake" and c.get("fake_code") else c["code"]
                res = w.run(code)
                passed = res["ok"] or c.get("expect_error", False)
                if args.verbose or not passed:
                    print(f"  [{i}] {'OK ' if passed else 'ERR'} {code.splitlines()[0][:70]}")
                    for o in res["outputs"]:
                        text = o.get("text") or f"{o.get('ename', '')}: {o.get('evalue', '')}" if o["type"] != "image" else "(그림)"
                        print("      " + text[:600].replace("\n", "\n      "))
                ok += passed
                fail += not passed
        finally:
            w.kill()
        total_fail += fail
        print(f"{ch['id']} {mode}: 통과 {ok} · 실패 {fail} · {time.time() - t0:.0f}초", flush=True)
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
