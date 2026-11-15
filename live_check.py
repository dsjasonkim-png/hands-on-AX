"""모든 실습(job)의 평가 사례를 실제 LLM으로 돌려 live_report.md / live_report.json으로 남긴다.

사용법 (이 파일은 axcore_proj 폴더 바로 바깥에 둔다):
    python live_check.py                                   # 기본 모델, 모든 job, 1회
    python live_check.py google_genai:gemini-3.5-flash-lite --repeat 3
    python live_check.py openai:gpt-4.1-mini --jobs <job1>,<job2>
    python live_check.py --fake                            # API 없이 가짜 판단기로 흐름만 확인(fake_report.md)

- 같은 폴더(또는 상위 폴더)의 .env에서 키를 읽는다. GEMINI_API_KEY는 GOOGLE_API_KEY로도 넣어 준다.
- 각 job은 jobs/<이름>/eval_cases.py에 make_graph(model, runtime)와 CASES를 둔다(axcore_proj/docs/GUIDE.md 참고).
- 실제 모델은 실행마다 결과가 달라질 수 있으니 --repeat로 여러 번 돌려 본다.
- API 키는 어디에도 기록하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE / "axcore_proj"
if not PROJ.exists():                       # 압축을 아직 풀지 않았으면 여기서 풀어 준다
    zip_path = HERE / "axcore_skeleton.zip"
    if not zip_path.exists():
        sys.exit(f"axcore_proj 폴더도 axcore_skeleton.zip도 없습니다: {HERE}")
    import zipfile
    zipfile.ZipFile(zip_path).extractall(HERE)
    print(f"[압축 해제] {zip_path.name} → {PROJ}")
sys.path.insert(0, str(PROJ))


def load_env() -> None:
    for folder in (HERE, HERE.parent):
        env = folder / ".env"
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        print(f"[.env] {env} 읽음")
        break
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


def last_message(graph, case_id: str) -> str:
    """판단 단계가 삼킨 오류(API 오류·검증 실패)의 마지막 메시지."""
    try:
        state = graph.get_state({"configurable": {"thread_id": f"eval-{case_id}"}}).values
        msgs = state.get("feedback") or state.get("errors") or []
        return str(msgs[-1])[:300] if msgs else ""
    except Exception:
        return ""


def run_job(name: str, model, runtime: Path) -> list[dict]:
    from axcore.evaluate import run_case
    from jobs import load_eval

    ev = load_eval(name)
    graph = ev.make_graph(model=model, runtime=runtime)
    results = []
    for case in ev.CASES:                    # 같은 그래프에서 순서대로 (중복 사례는 앞 사례에 의존)
        t0 = time.time()
        try:
            r = run_case(graph, case)
        except Exception as e:               # 모델·네트워크 오류도 기록하고 계속한다
            r = {"case_id": case["case_id"], "passed": False, "checks": {}, "result": {},
                 "error": f"{type(e).__name__}: {e}"}
        r["seconds"] = round(time.time() - t0, 1)
        if not r.get("error"):
            r["last_message"] = last_message(graph, case["case_id"])
        g = r.get("result", {})
        print(f"  {r['case_id']}: {'OK ' if r['passed'] else '차이'} outcome={g.get('outcome')} "
              f"status={g.get('status')} model_errors={g.get('model_errors_total')} {r['seconds']}초 "
              f"{r.get('error') or ('' if r['passed'] else r.get('last_message', ''))}")
        results.append(r)
    return results


def write_report(report: dict, stem: str) -> None:
    (HERE / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 실제 LLM 실행 결과" if stem == "live_report" else "# 가짜 판단기 실행 결과 (실제 모델 아님)", "",
             f"- 모델: `{report['model']}`", f"- 실행: {report['ran_at']}", f"- 반복: {report['repeat']}회",
             "- 기준: 각 job의 eval_cases.py에 적은 기대 경로(결과·판단 상태·승인 방식·질문 수)", "",
             "## 요약", "", "| job | 사례 | 기대와 같음 | 모델 오류 합계 | 평균 초 |", "|---|---|---|---|---|"]
    summary: dict[tuple[str, str], list[dict]] = {}
    for run in report["runs"]:
        for job, results in run.items():
            for r in results:
                summary.setdefault((job, r["case_id"]), []).append(r)
    for (job, cid), rs in summary.items():
        ok = sum(r["passed"] for r in rs)
        errs = sum(r.get("result", {}).get("model_errors_total") or 0 for r in rs)
        sec = sum(r["seconds"] for r in rs) / len(rs)
        lines.append(f"| {job} | {cid} | {ok}/{len(rs)} | {errs} | {sec:.1f} |")
    for i, run in enumerate(report["runs"], 1):
        lines += ["", f"## 실행 {i}", "",
                  "| job | 사례 | 같음 | 결과 | 판단 상태 | 승인 | 질문 | 판단 횟수 | 모델 오류 | 근거 수 | 초 | 비고 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for job, results in run.items():
            for r in results:
                g = r.get("result", {})
                fail = [k for k, ok in r.get("checks", {}).items() if not ok]
                note = r.get("error") or ("" if r["passed"] else r.get("last_message", ""))
                if fail:
                    note = f"다른 항목: {', '.join(fail)}" + (f" / {note}" if note else "")
                note = note.replace("|", "/").replace("\n", " ")
                lines.append(f"| {job} | {r['case_id']} | {'예' if r['passed'] else '아니오'} | {g.get('outcome')} | "
                             f"{g.get('status')} | {g.get('approval_mode')} | {g.get('questions')} | "
                             f"{g.get('decisions')} | {g.get('model_errors_total')} | {g.get('evidence_count')} | "
                             f"{r['seconds']} | {note} |")
    total = sum(len(rs) for rs in summary.values())
    ok = sum(r["passed"] for rs in summary.values() for r in rs)
    lines += ["", f"전체: 기대와 같은 실행 {ok}/{total}건.", "",
              "`모델 오류`는 검증기가 판단을 되돌려보낸 횟수(예외 포함)입니다. 크면 지침·결과 형식·검증 메시지를 손봅니다.",
              "기대와 다른 사례는 실패가 안전한 쪽인지(반영되지 않았는지)부터 확인합니다."]
    (HERE / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    from jobs import DEFAULT_MODEL, job_names

    ap = argparse.ArgumentParser()
    ap.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    ap.add_argument("--jobs", help="쉼표로 구분한 job 이름. 생략하면 모든 job(_template 제외)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--fake", action="store_true", help="API 없이 가짜 판단기로 실행")
    args = ap.parse_args()

    names = args.jobs.split(",") if args.jobs else job_names()
    model, label = None, "fake (가짜 판단기)"
    if not args.fake:
        load_env()
        from langchain.chat_models import init_chat_model
        model, label = init_chat_model(args.model, temperature=0), args.model

    runtime_root = HERE / "runtime_live"
    shutil.rmtree(runtime_root, ignore_errors=True)
    report = {"model": label, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "repeat": args.repeat,
              "jobs": names, "runs": []}
    for i in range(1, args.repeat + 1):
        run = {}
        for name in names:
            print(f"\n=== [{i}/{args.repeat}] {name} ({label}) ===")
            run[name] = run_job(name, model, runtime_root / f"run{i}" / name)
        report["runs"].append(run)
    stem = "fake_report" if args.fake else "live_report"   # 가짜 판단기 결과를 실제 결과로 오해하지 않게
    write_report(report, stem)
    total = sum(len(v) for run in report["runs"] for v in run.values())
    ok = sum(r["passed"] for run in report["runs"] for v in run.values() for r in v)
    print(f"\n정리: 기대와 같은 실행 {ok}/{total}건 · 보고서: {HERE / (stem + '.md')}")


if __name__ == "__main__":
    main()
