"""평가 사례를 하나씩 돌리며 멈춤·사람 입력·경로를 보여 주는 시연. python -m jobs.folder_search.run [--live [모델]]

--live 없이 실행하면 가짜 판단기로 흐름만 보여 준다(API 키 불필요).
--live를 주면 .env를 읽어 실제 LLM으로 판단한다(settings.py 참고).
  --live            .env의 LLM(공급자:모델), 없으면 DEFAULT_MODEL
  --live 모델이름    명령줄에서 직접 고르기 (예: openai:gpt-6-luna)
  OPENAI_API_KEY가 있으면 OpenAI 임베딩으로 semantic_search도 켠다(SEMANTIC=0이면 끔).
--organize를 주면 평가 사례 대신 폴더의 모든 파일을 두 번째 뇌 노트로 정리한다.
  노트와 목차는 runtime/folder_search/brain/에 쌓인다(옵시디언 볼트로 그대로 열 수 있다).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from axcore import Trigger, pending, resume, start

from jobs import DEFAULT_MODEL

from . import folder as fs
from .eval_cases import CASES, make_graph, org
from .spec import SAMPLE_DIR
from .settings import INDEX_PATH, load_env, make_embeddings, make_llm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="MODEL", nargs="?", const="", help=f"예: {DEFAULT_MODEL}")
    ap.add_argument("--organize", action="store_true", help="폴더의 모든 파일을 노트로 정리")
    args = ap.parse_args()
    runtime = Path("runtime") / Path(__file__).resolve().parent.name
    shutil.rmtree(runtime, ignore_errors=True)
    model = embeddings = None
    if args.live is not None:
        env = load_env()
        model, name = make_llm(args.live or None)
        embeddings = make_embeddings()
        print(f"[.env] {env or '없음'} | 모델 {name} | 의미 검색 {'켬 (OpenAI 임베딩)' if embeddings else '끔'}")
    graph = make_graph(model=model, runtime=runtime, embeddings=embeddings, index_path=INDEX_PATH)

    cases = CASES
    if args.organize:       # 덮어쓰기 승인 요청이 오면 시연에서는 승인으로 답한다
        cases = [{"case_id": f"ORG-{i:03d}", "trigger": org(f"ORG-{i:03d}", fs.rel(SAMPLE_DIR, p)),
                  "replies": [{"action": "approve", "reviewer": "시연"}]}
                 for i, p in enumerate(fs.list_files(SAMPLE_DIR), 1)]

    for case in cases:
        rid, replies = case["case_id"], list(case["replies"])
        print(f"\n━━ {rid} · {case['trigger']['payload']}")
        out = start(graph, Trigger(**case["trigger"]))
        while (ask := pending(out)) is not None:
            if ask["kind"] == "clarify":
                print(f"  ⏸ 질문: {ask['question']}")
            else:
                d = ask["decision"]
                print(f"  ⏸ 승인 요청: [{d['status']}] {d['summary']}")
            if not replies:
                print("  (입력 대기 중으로 남겨 둠)")
                break
            reply = replies.pop(0)
            print(f"  ▶ 사람 입력: {reply}")
            out = resume(graph, out["request_id"], reply)
        d = out.get("decision") or {}
        print(f"  결과: {out.get('outcome')} {out.get('commit_ref') or ''} | 판단: {d.get('status')} | {d.get('summary')}")
        print("  경로:", " → ".join(e["event"] for e in out["log"]))
        if reason := next((e["reason"] for e in out["log"] if e["event"] == "failed"), None):
            print("  실패 이유:", reason)

    if args.organize:
        print(f"\n노트 폴더: {runtime / 'brain'}\n" + (runtime / "brain" / "index.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
