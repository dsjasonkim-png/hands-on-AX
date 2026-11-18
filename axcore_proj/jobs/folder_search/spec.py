"""Z몰 사내 문서 폴더 검색 + 두 번째 뇌(second brain) 정리 — 원본 folder-search-agent를 공통 골격에 옮긴 job.

요청은 두 종류다.
- 질문 {"question": ...}: 검색·읽기 도구로 근거를 찾아 답한다 → 인용·숫자를 원본에서 확인 → 답변 기록 저장
- 정리 {"organize": "파일"}: markitdown으로 PPT·엑셀·PDF·워드까지 마크다운으로 바꿔 읽고(코드),
  제목·PARA 분류·태그·요약·핵심 줄·관련 문서를 정한다(AI) → 핵심 줄·요약 숫자·관련 파일을 원본에서 확인(코드)
  → 새 노트는 자동, 이미 있는 노트를 덮어쓰면 사람 확인 → brain/<분류>/<파일명>.md 노트와 목차(index.md) 저장

원본과 달라진 점
- 답을 자유 문장이 아니라 결과 형식(SearchAnswer)으로 받는다. 근거마다 파일·줄 ID·원문을 적어 검증기가 확인한다.
- 도구가 돌려주는 본문에 줄 ID(L12)를 붙인다. 모델은 그 ID로 근거를 적는다.
- 의미 검색(semantic_search)은 임베딩 객체를 넘길 때만 켠다. 기본 평가는 키워드 검색 + 읽기만 쓴다.
- 질문의 근거는 언제나 원본 파일이다. 노트는 AI가 만든 2차 자료라서 검증 기준으로 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from axcore import AXDecision, DecideContext, JobSpec, Trigger
from axcore.llm import agent_decider

from . import folder as fs

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "samples" / "folder_search" / "files"
CATEGORIES = ("프로젝트", "영역", "자료", "보관")     # PARA 분류
CONTENT_LINES = 200                                    # 정리 요청에서 프롬프트에 바로 넣는 줄 수. 나머지는 read_file로


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def numbers(s: str) -> set[str]:
    """'1,240백만 원' → {'1240'}. 쉼표를 떼고 끝의 점을 뗀다."""
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,.]*", s or "")}


# ④ 결과 형식 ─────────────────────────────────────────────────────────────
class Note(BaseModel):
    """두 번째 뇌에 저장할 정리 노트. 핵심 줄은 evidence에 적는다."""

    title: str = Field(description="문서 내용을 드러내는 노트 제목")
    category: Literal["프로젝트", "영역", "자료", "보관"] = Field(
        description="PARA 분류. 프로젝트: 마감이 있는 진행 중인 일, 영역: 계속 맡는 업무(정기 보고·운영 현황), "
                    "자료: 참고 자료·전략·계획, 보관: 끝났거나 중단된 일")
    tags: list[str] = Field(default_factory=list, description="태그 1~5개. 띄어쓰기와 # 없이 한 단어씩")
    summary: str = Field(default="", description="문서 요약 2~3문장. 숫자는 문서에 적힌 그대로")
    related: list[str] = Field(default_factory=list, description="함께 볼 폴더 파일(상대경로). 정리 대상 파일은 빼고")


class SearchAnswer(AXDecision):
    """폴더 검색의 최종 답 또는 문서 정리 노트. 검색·읽기를 마친 뒤 이 도구로 결과를 낸다."""   # CLOVA는 도구 설명이 비면 400을 낸다

    answer: str = Field(default="", description="질문에 대한 답. 숫자는 문서에 적힌 그대로 쓴다(단위 환산 금지). 정리 요청이면 비워 둔다")
    primary_file: str | None = Field(default=None, description="답의 주된 근거 파일(폴더 기준 상대경로)")
    note: Note | None = Field(default=None, description="정리 요청일 때만 채운다")


# ③ 지침 ──────────────────────────────────────────────────────────────────
def make_instructions(semantic: bool) -> str:
    pick = ("키워드가 분명하면 search, '~에 대한 내용'처럼 의미로 찾아야 하면 semantic_search를 쓴다."
            if semantic else "search로 핵심 단어를 띄어쓰기로 여러 개 넣어 찾는다.")
    return f"""너는 Z몰 사내 문서 폴더 전용 도우미다. 질문에 답하거나, 문서 하나를 두 번째 뇌(지식 노트)로 정리한다.
도구로 찾은 사실만 한국어로 간결히 쓴다.
- evidence에는 source=파일 상대경로, locator=줄 ID(L12처럼), quote=그 줄의 원문 일부를 고치지 말고 그대로 적는다.
- 숫자는 문서에 적힌 그대로 쓰고 단위를 바꾸지 않는다.
- 최종 결과는 일반 문장으로 쓰지 말고 반드시 SearchAnswer 도구를 호출해 낸다(needs_info·failed도 마찬가지).

[질문에 답할 때] ([질문]이 주어진 경우)
- {pick}
- 찾은 파일은 read_file로 읽어 확인한 뒤 답한다. search가 '일치 없음'이면 함께 받은 파일 목록에서
  질문과 가장 관련 있어 보이는 파일을 read_file로 읽어 본다.
- answer에 쓴 숫자는 모두 evidence의 quote에 있어야 한다. primary_file에는 답의 주된 근거 파일을 적는다.
- 질문이 가리키는 대상이 여러 문서에 걸쳐 모호하면(예: 어느 회의인지 모름) needs_info로 되묻는다.
  답을 받으면 그 대상에 맞춰 다시 찾는다.
- 같은 항목의 값이 문서끼리 다르면 needs_review로 올리고, 다른 값들을 모두 evidence에 적고 answer에 함께 쓴다.
- 폴더 어디에도 관련 내용이 없으면 failed로 하고, summary에 찾아본 검색어를 적는다.
- 그 밖에 근거로 답할 수 있으면 completed.

[문서를 정리할 때] ([정리할 문서]가 주어진 경우)
- 문서를 읽고 note를 채운다: title, category(프로젝트/영역/자료/보관), tags(1~5개), summary(2~3문장),
  related(같은 주제의 다른 폴더 파일. 필요하면 search로 찾는다. 없으면 빈 목록).
- evidence에는 정리 대상 문서에서 꼭 기억할 줄 2~5개를 적는다. source는 정리 대상 경로 그대로.
- answer는 비워 두고, primary_file에는 정리 대상 경로를 적는다. 정리가 끝나면 completed.
"""


def render(ctx: DecideContext) -> list[dict[str, Any]]:
    """질문이면 파일 목록만 넘기고(모델이 도구로 찾아 읽는다), 정리면 대상 문서 본문을 줄 ID와 함께 넘긴다."""
    inp = ctx.job_input
    files = "\n".join(f"- {f} ({n}줄)" for f, n in inp["files"].items())
    if inp.get("mode") == "organize":
        notes = ", ".join(inp["notes"]) or "없음"
        parts = [f"[정리할 문서] {inp['target']} ({inp['files'][inp['target']]}줄, markitdown 변환본)\n{inp['content']}",
                 f"[폴더의 다른 파일]\n{files}", f"[이미 있는 노트] {notes}"]
    else:
        parts = [f"[질문]\n{inp['question']}", f"[검색 폴더의 파일]\n{files}"]
    if ctx.clarifications:
        parts.append("[추가로 받은 정보]\n" + "\n".join(f"- Q: {c['question']} / A: {c['answer']}" for c in ctx.clarifications))
    if ctx.feedback:
        parts.append("[이전 판단에 대한 피드백: 반드시 반영]\n" + "\n".join(f"- {f}" for f in ctx.feedback))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def make_spec(folder: str | Path | None = None, answers_path: str | Path | None = None,
              decide=None, model: Any = None, embeddings: Any = None,
              index_path: str | Path | None = None, brain_dir: str | Path | None = None) -> JobSpec:
    """decide를 주면 그것을, model을 주면 실제 LLM을, 둘 다 없으면 가짜 판단기를 쓴다.
    embeddings(예: langchain_openai.OpenAIEmbeddings)를 주면 의미 검색 도구를 더한다.
    brain_dir를 주면 정리 노트를 그 폴더에 쓴다(없으면 쓰지 않고 참조만 돌려준다)."""
    root = Path(folder or SAMPLE_DIR).resolve()
    answers = Path(answers_path) if answers_path else None
    brain = Path(brain_dir) if brain_dir else None

    def brain_notes() -> list[Path]:
        return sorted(p for p in brain.rglob("*.md") if p.name != "index.md") if brain and brain.is_dir() else []

    # ① 입력 로더: 요청 종류와 폴더 상태를 확인한다. 파일을 읽을 수 없으면 LLM 전에 끊는다 ──────
    def load_input(trigger: Trigger) -> dict:
        target = str(trigger.payload.get("organize") or "").strip()
        question = str(trigger.payload.get("question") or "").strip()
        if not target and not question:
            raise ValueError("질문(question)이 비어 있음")
        if not root.is_dir():
            raise ValueError(f"검색 폴더가 없음: {root}")
        files = {}
        for p in fs.list_files(root):
            try:
                files[fs.rel(root, p)] = len(fs.read_lines(root, fs.rel(root, p)))
            except Exception:
                continue                               # 추출 실패 파일은 목록에서 뺀다
        if not files:
            raise ValueError(f"검색할 수 있는 문서가 없음: {root}")
        if not target:
            return {"question": question, "files": files}
        if target not in files:
            if (root / target).suffix.lower() in fs.OFFICE and (root / target).is_file() and not fs.has_markitdown():
                raise ValueError(f"'{target}'을 읽으려면 markitdown이 필요함 (pip install 'markitdown[pptx,xlsx,docx,pdf]')")
            raise ValueError(f"정리할 파일이 폴더에 없음: '{target}'")
        lines = fs.read_lines(root, target)
        stem = Path(target).stem
        notes = brain_notes()
        return {"mode": "organize", "target": target, "files": files,
                "content": "\n".join(f"L{i}: {t}" for i, t in enumerate(lines[:CONTENT_LINES], 1)),
                "notes": [p.stem for p in notes],
                "existing_note": next((fs.rel(brain, p) for p in notes if p.stem == stem), None)}

    # ② 도구: 조회만 한다 ─────────────────────────────────────────────────
    @tool
    def search(keyword: str) -> str:
        """파일명과 파일 내용을 한 번에 검색. 질문의 핵심 단어들을 띄어쓰기로 여러 개 넣어라.
        하나라도 맞는 것을 모두 찾음. [파일명] 상대경로 목록, [내용] 파일:줄ID: 내용 형식."""
        names, hits = fs.keyword_search(root, keyword)
        if not names and not hits:   # '없음'만 주면 약한 모델은 포기한다. 파일 목록을 주면 다음 수를 둔다
            return "일치 없음. 폴더의 전체 파일:\n" + "\n".join(fs.rel(root, p) for p in fs.list_files(root))
        lines = [f"{f}:L{i}: {t[:200]}" for f, i, t in hits[:60]]   # 상한을 둬서 토큰 폭주 방지
        return f"[파일명]\n{chr(10).join(names[:100]) or '없음'}\n\n[내용]\n{chr(10).join(lines) or '없음'}"

    @tool
    def read_file(path: str, start_line: int = 1, max_lines: int = 80) -> str:
        """파일 본문 읽기. path는 search가 돌려준 상대경로 그대로. 각 줄 앞에 줄 ID(L12)가 붙는다.
        긴 파일은 start_line을 옮겨 이어 읽는다."""
        try:
            lines = fs.read_lines(root, path)
        except Exception as e:        # 예외 대신 문자열을 돌려줘야 모델이 다른 경로로 다시 시도한다
            return f"오류: {e}"
        s = max(start_line, 1)
        body = "\n".join(f"L{i}: {t}" for i, t in enumerate(lines[s - 1:s - 1 + max_lines], s))
        more = f"\n(전체 {len(lines)}줄 중 L{s}~L{min(s + max_lines - 1, len(lines))})" if len(lines) > max_lines else ""
        return (body or "(빈 파일)") + more

    tools = [search, read_file]
    if embeddings is not None:
        store_box: list = []

        @tool
        def semantic_search(query: str) -> str:
            """질문의 의미와 비슷한 내용을 찾는다. 키워드가 정확히 안 맞아도 됨. '파일 (유사도) | 줄ID: 내용' 형식."""
            if not store_box:          # 처음 쓸 때 인덱스를 맞춘다(바뀐 파일만 임베딩)
                store_box.append(fs.build_index(root, embeddings, Path(index_path) if index_path else None))
            hits = store_box[0].similarity_search_with_score(query, k=5)
            return "\n\n".join(f"{d.metadata['file']} ({s:.2f}) |\n{d.page_content[:600]}" for d, s in hits) or "없음"

        tools.insert(1, semantic_search)

    # ⑤ 검증기: 인용한 줄이 실제 파일에 있는지, 숫자가 원본에 있는지, 노트 필드가 규칙에 맞는지 ──────────
    def verify(d: SearchAnswer, inp: dict) -> list[str]:
        errors, quoted, cited = [], [], set()
        for e in d.evidence:
            if e.source not in inp["files"]:
                errors.append(f"없는 파일: '{e.source}'. 폴더의 파일은 {', '.join(inp['files'])}뿐이다. 경로를 그대로 적는다")
                continue
            m = re.fullmatch(r"L(\d+)", e.locator.strip())
            lines = fs.read_lines(root, e.source)     # 확인은 모델이 적은 값이 아니라 원본 파일로 한다
            if not m or not 1 <= int(m.group(1)) <= len(lines):
                errors.append(f"{e.source}의 줄 ID '{e.locator}'가 잘못됨. 이 파일은 L1~L{len(lines)}이다")
                continue
            line = lines[int(m.group(1)) - 1]
            if e.quote and squash(e.quote) not in squash(line):
                errors.append(f"{e.source} {e.locator} 인용이 원문과 다름: '{e.quote}'. 원문은 '{line.strip()}'")
                continue
            quoted.append(line)
            cited.add(e.source)
        if d.status not in ("completed", "needs_review"):
            return errors
        if inp.get("mode") == "organize":
            return errors + verify_note(d, inp)
        if not d.answer.strip():
            errors.append(f"{d.status}인데 answer가 비어 있음")
        if d.primary_file and d.primary_file not in cited:
            errors.append(f"primary_file '{d.primary_file}'이 확인된 근거 파일({', '.join(sorted(cited)) or '없음'})에 없음. "
                          "근거로 적은 파일 중에서 고른다")
        allowed = numbers(" ".join(quoted)) | numbers(inp["question"])
        missing = sorted(numbers(d.answer) - allowed)
        if missing:
            errors.append(f"answer의 숫자 {', '.join(missing)}이(가) 인용한 근거에 없음. "
                          f"인용한 줄의 숫자는 {', '.join(sorted(numbers(' '.join(quoted)))) or '없음'}이다. "
                          "문서에 적힌 숫자를 그대로 쓰거나 그 숫자가 있는 줄을 evidence에 더한다")
        return errors

    def verify_note(d: SearchAnswer, inp: dict) -> list[str]:
        t, n = inp["target"], d.note
        if n is None:
            return ["정리 요청인데 note가 비어 있음. title·category·tags·summary·related를 채운다"]
        errors = []
        if not n.title.strip() or not n.summary.strip():
            errors.append("note의 title과 summary는 비울 수 없음")
        others = sorted({e.source for e in d.evidence} - {t})
        if others:
            errors.append(f"evidence에는 정리 대상 '{t}'의 줄만 적는다. 다른 파일: {', '.join(others)}")
        if not 1 <= len(n.tags) <= 5 or any(not x or re.search(r"[\s#]", x) for x in n.tags):
            errors.append(f"tags는 띄어쓰기와 # 없는 단어 1~5개여야 함. 지금: {n.tags}")
        bad = [r for r in n.related if r not in inp["files"] or r == t]
        if bad:
            errors.append(f"related에 쓸 수 없는 파일: {', '.join(bad)}. 고를 수 있는 파일은 "
                          f"{', '.join(f for f in inp['files'] if f != t)}이다")
        doc = numbers(" ".join(fs.read_lines(root, t)))        # 요약의 숫자는 원본 문서 전체와 대조한다
        missing = sorted(numbers(n.summary) - doc)
        if missing:
            errors.append(f"note.summary의 숫자 {', '.join(missing)}이(가) '{t}'에 없음. "
                          f"문서의 숫자는 {', '.join(sorted(doc)) or '없음'}이다. 적힌 그대로 쓴다")
        return errors

    # ⑥ 승인 정책: 근거 검증을 통과한 답·새 노트는 바로 기록. 기존 노트를 덮어쓰면 사람이 본다
    def approval_policy(d: SearchAnswer, inp: dict) -> str:
        if inp.get("existing_note"):
            return "human"
        return "auto" if d.status == "completed" else "human"

    # ⑦ 반영 어댑터: 질문은 답변 기록, 정리는 노트 파일과 목차 ─────────────────────────────
    def commit(d: SearchAnswer, inp: dict, request_id: str) -> str:
        if inp.get("mode") == "organize":
            return write_note(d, inp, request_id)
        rows = json.loads(answers.read_text(encoding="utf-8")) if answers and answers.exists() else []
        ref = f"ANS-{len(rows) + 1:04d}"
        rows.append({"ref": ref, "request_id": request_id, "question": inp["question"], "answer": d.answer,
                     "sources": [f"{e.source} {e.locator}" for e in d.evidence]})
        if answers:
            answers.parent.mkdir(parents=True, exist_ok=True)
            answers.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return ref

    def write_note(d: SearchAnswer, inp: dict, request_id: str) -> str:
        n, t = d.note, inp["target"]
        rel_path = f"{n.category}/{Path(t).stem}.md"
        if not brain:
            return f"NOTE {rel_path}"
        lines = fs.read_lines(root, t)

        def cite(e) -> str:      # 줄 ID 옆에 원본 위치(슬라이드·시트)를 붙여 사람이 원본을 바로 찾게 한다
            where = fs.origin(t, lines, int(e.locator.strip()[1:]))
            return f"- {e.quote.strip()} (`{t}` {e.locator}{' · ' + where if where else ''})"

        body = [
            "---", f"title: {n.title}", f"category: {n.category}", f"tags: [{', '.join(n.tags)}]",
            f"source: {t}", f"request_id: {request_id}", "---", "",
            f"# {n.title}", "", "## 요약", n.summary, "",
            "## 핵심 내용", *[cite(e) for e in d.evidence], "",
            "## 관련 노트", *([f"- [[{Path(r).stem}]]" for r in n.related] or ["- 없음"]), "",
            "## 원문 (markitdown 변환)", "", *lines, "",
        ]
        if inp.get("existing_note"):          # 분류가 바뀌었으면 예전 자리의 노트를 지운다
            (brain / inp["existing_note"]).unlink(missing_ok=True)
        path = brain / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(body), encoding="utf-8")
        write_index()
        return f"NOTE {rel_path}"

    def write_index() -> None:
        """brain/index.md: 분류별 노트 목차(MOC). 노트를 쓸 때마다 폴더를 훑어 다시 만든다."""
        by_cat: dict[str, list[str]] = {}
        for p in brain_notes():
            title = re.search(r"^title: (.*)$", p.read_text(encoding="utf-8"), re.M)
            by_cat.setdefault(p.parent.name, []).append(f"- [[{p.stem}]] {title[1] if title else ''}")
        parts = ["# 두 번째 뇌 목차", ""]
        for c in CATEGORIES:
            if c in by_cat:
                parts += [f"## {c}", *by_cat[c], ""]
        (brain / "index.md").write_text("\n".join(parts), encoding="utf-8")

    if decide is None and model is not None:      # ②③④를 묶어 실제 LLM 판단 단계로
        decide = agent_decider(model, tools, make_instructions(embeddings is not None), SearchAnswer, render=render)
    if decide is None:
        from .fake_decider import make_fake_decide
        decide = make_fake_decide(root)

    return JobSpec(name="folder_search", decision_model=SearchAnswer, load_input=load_input, decide=decide,
                   verify=verify, approval_policy=approval_policy, commit=commit)
