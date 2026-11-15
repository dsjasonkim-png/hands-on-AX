"""계약서 검토 예제 테스트."""
from pathlib import Path

import pytest

from axcore import Trigger, build_graph, pending, resume, start
from axtools.ocr import CachedOCR
from jobs.contract.clauses import split_clauses
from jobs.contract.spec import ContractDecision, Finding, make_spec

DIR = Path(__file__).resolve().parents[1] / "samples" / "contract"
TEXT = str(DIR / "logistics_contract.txt")
SCAN = str(DIR / "logistics_contract_scan.png")


@pytest.fixture
def spec(tmp_path):
    return make_spec(ocr=CachedOCR(None), review_dir=tmp_path)


def verdicts(decision):
    return {f["rule_id"]: f["verdict"] for f in decision["findings"]}


def test_split_clauses():
    _, clauses = split_clauses(Path(TEXT).read_text(encoding="utf-8"))
    assert [c["id"] for c in clauses] == [f"제{i}조" for i in range(1, 7)] and clauses[2]["title"] == "대금 지급"


def test_text_contract_review_and_memo(spec, tmp_path):
    graph = build_graph(spec)
    out = start(graph, Trigger(request_id="T1", payload={"text_path": TEXT}))
    d = pending(out)["decision"]
    assert verdicts(d) == {"PB-01": "deviation", "PB-02": "deviation", "PB-03": "ok",
                           "PB-04": "missing", "PB-05": "deviation", "PB-06": "ok"}
    out = resume(graph, "T1", {"action": "approve", "reviewer": "정OO"})
    memo = Path(out["commit_ref"]).read_text(encoding="utf-8")
    assert out["outcome"] == "committed" and "PB-04 비밀유지 | 누락" in memo


def test_scanned_contract_gives_same_verdicts_as_text(spec):
    graph = build_graph(spec)
    a = pending(start(graph, Trigger(request_id="S1", payload={"text_path": TEXT})))["decision"]
    b = pending(start(graph, Trigger(request_id="S2", payload={"image": SCAN})))["decision"]
    assert verdicts(a) == verdicts(b)


def test_compliant_contract_still_needs_human(spec):
    text = ("표준 계약서\n제1조 (대금 지급) 검수 완료 후 30일 이내에 대금을 지급한다.\n"
            "제2조 (손해배상) 을은 손해를 배상한다.\n제3조 (해지) 30일 전 서면 통지로 해지한다.\n"
            "제4조 (비밀유지) 비밀유지 의무는 종료 후 3년간 존속한다.\n제5조 (관할) 서울중앙지방법원을 관할 법원으로 한다.")
    out = start(build_graph(spec), Trigger(request_id="C1", payload={"text": text}))
    ask = pending(out)
    assert ask["decision"]["status"] == "completed" and ask["kind"] == "approval"   # 문제없어도 담당자 확인


def test_reviewer_revision_is_reflected(spec):
    graph = build_graph(spec)
    start(graph, Trigger(request_id="R1", payload={"text_path": TEXT}))
    out = resume(graph, "R1", {"action": "revise", "comment": "PB-02에 고의·중과실 제외를 명시"})
    pb02 = next(f for f in pending(out)["decision"]["findings"] if f["rule_id"] == "PB-02")
    assert "검토자 의견" in pb02["suggestion"]


def test_no_clauses_fails(spec):
    out = start(build_graph(spec), Trigger(request_id="N1", payload={"text": "조항 없는 문서"}))
    assert out["outcome"] == "failed"
    assert "조항을 찾지 못함" in out["log"][0]["reason"]   # 엉뚱한 예외로 통과하지 않게 이유까지 본다


@pytest.fixture
def inp(spec):
    return spec.load_input(Trigger(payload={"text_path": TEXT}))


def base_findings(**override):
    fs = {"PB-01": Finding(rule_id="PB-01", verdict="ok", clause_id="제3조", quote="익월 60일 이내"),
          "PB-02": Finding(rule_id="PB-02", verdict="ok", clause_id="제4조", quote="직전 3개월 대금 합계"),
          "PB-03": Finding(rule_id="PB-03", verdict="ok", clause_id="제5조", quote="30일 전 서면 통지"),
          "PB-04": Finding(rule_id="PB-04", verdict="missing", reason="없음", suggestion="추가"),
          "PB-06": Finding(rule_id="PB-06", verdict="ok", clause_id="제6조", quote="서울중앙지방법원")}
    fs.update(override)
    return list(fs.values())


def check(spec, inp, findings, status="needs_review"):
    return spec.verify(ContractDecision(status=status, summary="s", findings=findings), inp)


def test_verifier_accepts_grounded_findings(spec, inp):
    assert check(spec, inp, base_findings()) == []


def test_verifier_rejects_quote_not_in_clause(spec, inp):
    errs = check(spec, inp, base_findings(**{"PB-01": Finding(rule_id="PB-01", verdict="ok", clause_id="제3조", quote="15일 이내")}))
    assert any("인용문" in e for e in errs)


def test_verifier_rejects_false_missing(spec, inp):
    errs = check(spec, inp, base_findings(**{"PB-03": Finding(rule_id="PB-03", verdict="missing", reason="r", suggestion="s")}))
    assert any("누락으로 판단했지만" in e for e in errs)


def test_verifier_requires_all_required_rules(spec, inp):
    errs = check(spec, inp, [f for f in base_findings() if f.rule_id != "PB-06"])
    assert any("PB-06" in e for e in errs)


def test_verifier_rejects_completed_with_issues(spec, inp):
    assert any("completed" in e for e in check(spec, inp, base_findings(), status="completed"))


PARTIAL = str(DIR / "logistics_contract_scan_partial.png")


def test_input_loader_reports_missing_clause_numbers(spec):
    """CT-005: 2쪽이 빠진 스캔본. 빠진 조항 번호는 모델이 아니라 코드가 찾는다."""
    inp = spec.load_input(Trigger(payload={"image": PARTIAL}))
    assert [c["id"] for c in inp["clauses"]] == ["제1조", "제2조", "제3조", "제6조"]
    assert inp["missing_clause_ids"] == ["제4조", "제5조"]


def test_partial_scan_marks_unseen_rules_unverified(spec):
    graph = build_graph(spec)
    d = pending(start(graph, Trigger(request_id="P1", payload={"image": PARTIAL})))["decision"]
    v = verdicts(d)
    assert v["PB-02"] == v["PB-03"] == v["PB-04"] == "unverified" and d["status"] == "needs_review"


def test_verifier_rejects_missing_when_clauses_are_absent(spec):
    """빠진 페이지가 있는데 '누락'으로 단정하면 오류. 메시지에 빠진 조항 번호를 함께 돌려준다."""
    inp = spec.load_input(Trigger(payload={"image": PARTIAL}))
    fs = [Finding(rule_id="PB-01", verdict="deviation", clause_id="제3조", quote="익월 60일 이내", reason="r", suggestion="s"),
          Finding(rule_id="PB-02", verdict="missing", reason="손해배상 조항 없음", suggestion="추가"),
          Finding(rule_id="PB-03", verdict="unverified", reason="r", suggestion="s"),
          Finding(rule_id="PB-04", verdict="unverified", reason="r", suggestion="s"),
          Finding(rule_id="PB-06", verdict="ok", clause_id="제6조", quote="서울중앙지방법원")]
    errs = check(spec, inp, fs)
    assert any("PB-02" in e and "제4조, 제5조" in e and "unverified" in e for e in errs)


def test_verifier_rejects_unverified_without_gaps(spec, inp):
    errs = check(spec, inp, base_findings(**{"PB-04": Finding(rule_id="PB-04", verdict="unverified", reason="r", suggestion="s")}))
    assert any("unverified로 둘 수 없음" in e and "missing으로 적을 것" in e for e in errs)   # 무엇으로 고칠지까지 알려 준다


def test_verifier_rejects_old_playbook_version(spec):
    """CT-006: 요청서의 이전 버전을 따라 결과에 2026.03을 적으면 오류. 현재 버전을 메시지에 적는다."""
    inp = spec.load_input(Trigger(payload={"text_path": TEXT, "playbook_version": "2026.03"}))
    errs = spec.verify(ContractDecision(status="needs_review", summary="s", playbook_version="2026.03",
                                        findings=base_findings()), inp)
    assert any("2026.03" in e and "2026.09" in e for e in errs)



def test_memo_ignores_clause_id_on_absent_verdicts(spec, tmp_path):
    """실제 모델 실행에서 나온 경우: not_applicable·missing인데 조항 칸에 '없음', '-', ',quote:' 등을 적음.
    처음에는 검증기로 막았지만 모델 오류의 대부분이 이 경우였다. 의견서를 만들 때 코드가 정리한다."""
    inp = spec.load_input(Trigger(payload={"text_path": TEXT}))
    d = ContractDecision(status="needs_review", summary="s",
                         findings=base_findings(**{"PB-04": Finding(rule_id="PB-04", verdict="missing", clause_id="없음",
                                                                     reason="r", suggestion="s")}))
    assert spec.verify(d, inp) == []
    memo = Path(spec.commit(d, inp, "M1")).read_text(encoding="utf-8")
    assert "| PB-04 비밀유지 | 누락 | - |" in memo and "없음" not in memo

def test_quote_with_clause_header_is_accepted(spec, inp):
    """실제 모델 실행에서 나온 경우: render에 보이는 '[제3조] (대금 지급)' 머리말까지 인용. 본문이 맞으면 통과시킨다."""
    fs = base_findings(**{"PB-01": Finding(rule_id="PB-01", verdict="ok", clause_id="제3조",
                                           quote="[제3조] (대금 지급) 갑은 매월 말일 기준 검수가 완료된 물량에 대하여 익월 60일 이내에")})
    assert check(spec, inp, fs) == []


def test_quote_error_shows_model_quote_and_clause_body(spec, inp):
    errs = check(spec, inp, base_findings(**{"PB-01": Finding(rule_id="PB-01", verdict="ok", clause_id="제3조", quote="15일 이내")}))
    assert any("'15일 이내'" in e and "익월 60일 이내에 대금을 지급한다" in e for e in errs)
