"""폴더 검색 job 테스트. ① 입력 오류는 LLM 호출 없이 의도한 이유로 실패 ② 지어낸 인용·숫자를 검증기가 잡음
③ 오류 메시지에 원본 값이 들어 있음. 평가 사례 경로는 tests/test_eval_cases.py가 확인한다."""
import pytest

from axcore import Trigger, build_graph, start
from jobs.folder_search import folder as fs
from jobs.folder_search.eval_cases import org, req
from jobs.folder_search.spec import SAMPLE_DIR, Note, SearchAnswer, make_spec


def failure_reason(out) -> str:
    return next((e["reason"] for e in out["log"] if e.get("event") == "failed"), "")


def tool(spec_kwargs, name):
    """make_spec 안의 도구를 꺼내려고 agent_decider 대신 도구 목록을 받는 decide를 끼운다."""
    import jobs.folder_search.spec as m
    captured = {}
    orig = m.agent_decider
    m.agent_decider = lambda model, tools, *a, **k: captured.update({t.name: t for t in tools}) or (lambda ctx: None)
    try:
        make_spec(model=object(), **spec_kwargs)
    finally:
        m.agent_decider = orig
    return captured[name]


def test_empty_question_fails_before_llm():
    called = []
    graph = build_graph(make_spec(decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(**req("B1", "  ")))
    assert out["outcome"] == "failed" and called == []
    assert "질문(question)이 비어 있음" in failure_reason(out)


def test_missing_folder_fails_before_llm(tmp_path):
    called = []
    graph = build_graph(make_spec(folder=tmp_path / "없는폴더", decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(**req("B2", "부산 매출")))
    assert out["outcome"] == "failed" and called == [] and "검색 폴더가 없음" in failure_reason(out)


def test_verifier_catches_made_up_quote_and_number():
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("V1", "부산 3분기 매출 얼마야?")))
    d = SearchAnswer(status="completed", summary="s", answer="부산 3분기 매출은 12.4억 원", primary_file="매출보고서_3분기.txt",
                     evidence=[{"source": "매출보고서_3분기.txt", "locator": "L5", "quote": "부산 매출 12.4억"}])
    msg = " ".join(spec.verify(d, inp))
    assert "원문은 '부산 3분기 매출은 1,240백만 원이다.'" in msg      # 지어낸 인용 + 원문
    assert "숫자 12.4" in msg                                          # 근거에 없는 숫자


def test_verifier_lists_real_files_and_line_range():
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("V2", "대구 매출")))
    d = SearchAnswer(status="completed", summary="s", answer="610", primary_file="sales.xlsx",
                     evidence=[{"source": "sales.xlsx", "locator": "L1", "quote": "610"},
                               {"source": "지역매출_3분기.csv", "locator": "L99", "quote": "610"}])
    msg = " ".join(spec.verify(d, inp))
    assert "지역매출_3분기.csv" in msg and "L1~L5" in msg and "primary_file" in msg


def test_correct_answer_passes():
    spec = make_spec()
    inp = spec.load_input(Trigger(**req("V3", "부산 3분기 매출 얼마야?")))
    d = SearchAnswer(status="completed", summary="s", answer="1,240백만 원", primary_file="매출보고서_3분기.txt",
                     evidence=[{"source": "매출보고서_3분기.txt", "locator": "L5", "quote": "부산 3분기 매출은 1,240백만 원이다."}])
    assert spec.verify(d, inp) == []


def test_tools_add_line_ids_and_block_path_escape():
    read_file = tool({}, "read_file")
    assert read_file.invoke({"path": "지역매출_3분기.csv"}).splitlines()[4] == "L5: 대구,3분기,610"
    assert read_file.invoke({"path": "../../jobs/__init__.py"}).startswith("오류")
    search = tool({}, "search")
    assert "매출보고서_3분기.txt:L5:" in search.invoke({"keyword": "부산"})
    assert search.invoke({"keyword": "연봉"}).startswith("일치 없음")


def test_semantic_search_index_is_incremental(tmp_path):
    from langchain_core.embeddings import Embeddings

    class CharEmbedding(Embeddings):     # API 없이 쓰는 가짜 임베딩: 글자 코드 분포
        def embed_documents(self, texts):
            return [self.embed_query(t) for t in texts]

        def embed_query(self, text):
            v = [0.0] * 16
            for ch in text:
                v[ord(ch) % 16] += 1
            return v

    emb, index = CharEmbedding(), tmp_path / "index.json"
    store = fs.build_index(SAMPLE_DIR, emb, index)
    chunks = sum(-(-len(fs.read_lines(SAMPLE_DIR, fs.rel(SAMPLE_DIR, p))) // fs.CHUNK_LINES) for p in fs.list_files(SAMPLE_DIR))
    assert index.exists() and len(store.store) == chunks
    again = fs.build_index(SAMPLE_DIR, emb, index)
    assert set(again.store) == set(store.store)
    pytest.importorskip("numpy")         # InMemoryVectorStore 유사도 계산에 numpy가 필요(골격 requirements에는 없음)
    out = tool({"embeddings": emb, "index_path": index}, "semantic_search").invoke({"query": "새벽배송 중단"})
    assert "L1:" in out


# ── 두 번째 뇌 정리 ─────────────────────────────────────────────────────
needs_markitdown = pytest.mark.skipif(not fs.has_markitdown(), reason="markitdown 미설치(jobs/folder_search/requirements.txt)")


def test_organize_missing_file_fails_before_llm():
    called = []
    graph = build_graph(make_spec(decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(**org("O1", "없는파일.pptx")))
    assert out["outcome"] == "failed" and called == [] and "정리할 파일이 폴더에 없음" in failure_reason(out)


@needs_markitdown
def test_verifier_catches_made_up_note():
    spec = make_spec()
    inp = spec.load_input(Trigger(**org("O2", "전략발표_2027.pptx")))
    assert "L4: 2027년 거래액 목표는 1조 2,000억 원이다." in inp["content"]      # markitdown 변환본에 줄 ID
    d = SearchAnswer(status="completed", summary="s", primary_file="전략발표_2027.pptx",
                     evidence=[{"source": "전략발표_2027.pptx", "locator": "L14", "quote": "PB 비중 20%"},
                               {"source": "매출보고서_3분기.txt", "locator": "L2", "quote": "3분기 전체 매출"}],
                     note=Note(title="2027 전략", category="자료", tags=["전략 발표"],
                               summary="거래액 목표는 1.2조 원이다.", related=["전략발표_2027.pptx", "없는.md"]))
    msg = " ".join(spec.verify(d, inp))
    assert "원문은 'PB 상품 비중을 8%에서 15%로 올린다.'" in msg               # 지어낸 인용 + 원문
    assert "정리 대상 '전략발표_2027.pptx'의 줄만" in msg                       # 다른 파일 줄
    assert "tags는" in msg and "related에 쓸 수 없는 파일: 전략발표_2027.pptx, 없는.md" in msg
    assert "숫자 1.2이(가)" in msg and "2000" in msg                           # 요약 숫자 + 문서의 숫자 목록


@needs_markitdown
def test_note_written_with_origin_and_overwrite_needs_human(tmp_path):
    from axcore import pending, resume
    graph = build_graph(make_spec(brain_dir=tmp_path / "brain"))
    out = start(graph, Trigger(**org("O3", "반품현황_10월.xlsx")))
    assert out["outcome"] == "committed" and out["commit_ref"] == "NOTE 영역/반품현황_10월.md"
    note = (tmp_path / "brain" / "영역" / "반품현황_10월.md").read_text(encoding="utf-8")
    assert "source: 반품현황_10월.xlsx" in note and "L4 · 시트 주차별)" in note and "## 원문 (markitdown 변환)" in note
    assert "[[반품현황_10월]]" in (tmp_path / "brain" / "index.md").read_text(encoding="utf-8")
    out = start(graph, Trigger(**org("O4", "반품현황_10월.xlsx")))          # 같은 문서를 다시 정리 → 덮어쓰기
    assert pending(out)["kind"] == "approval"
    out = resume(graph, "O4", {"action": "reject", "reviewer": "r"})
    kept = (tmp_path / "brain" / "영역" / "반품현황_10월.md").read_text(encoding="utf-8")
    assert out["outcome"] == "rejected" and "request_id: O3" in kept                # 반려하면 기존 노트가 그대로
