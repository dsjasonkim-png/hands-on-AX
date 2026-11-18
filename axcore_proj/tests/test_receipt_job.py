"""영수증 정산 예제 테스트. OCR은 저장된 결과(캐시)를 써서 결과를 고정한다."""
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from axcore import Ledger, Trigger, build_graph, pending, resume, start
from axtools.ocr import CachedOCR, OCRLine, OCRResult
from jobs.receipt.register import ExpenseRegister
from jobs.receipt.spec import ReceiptDecision, ReceiptItem, make_spec
from tests.test_agent_decider import ScriptedToolModel

IMG = Path(__file__).resolve().parents[1] / "samples" / "receipt"


def trig(rid, image, purpose="교통비", **kw):
    return Trigger(request_id=rid, payload={"image": str(IMG / image), "purpose": purpose, "submitter": "홍OO", **kw})


@pytest.fixture
def env(tmp_path):
    register = ExpenseRegister(tmp_path / "register.json")
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register), Ledger(tmp_path / "ledger.json"))
    return graph, register


def test_small_taxi_receipt_is_auto_registered(env):
    graph, register = env
    out = start(graph, trig("A", "R001_taxi.png"))
    assert out["outcome"] == "committed" and out["approval"]["mode"] == "auto"
    assert register.rows[0]["total"] == 18_400 and register.rows[0]["merchant"] == "Z택시"   # OCR '2택시'를 바로잡음


def test_meeting_meal_asks_attendees_then_caps_amount(env):
    graph, register = env
    out = start(graph, trig("B", "R002_meeting_meal.png", purpose="회의 식비"))
    assert pending(out)["kind"] == "clarify"
    out = resume(graph, "B", "김OO, 이OO, 박OO, 최OO")
    ask = pending(out)
    assert ask["kind"] == "approval" and ask["decision"]["claim_amount"] == 120_000   # 1인당 3만 원 한도
    out = resume(graph, "B", {"action": "approve", "reviewer": "박OO"})
    assert out["outcome"] == "committed" and register.rows[0]["claim_amount"] == 120_000


def test_item_sum_mismatch_goes_to_human(env):
    graph, register = env
    out = start(graph, trig("C", "R003_stationery.png", purpose="사무용품"))
    assert pending(out)["decision"]["status"] == "needs_review"
    out = resume(graph, "C", {"action": "reject"})
    assert out["outcome"] == "rejected" and register.rows == []


def test_same_receipt_submitted_again_is_flagged(env):
    graph, register = env
    start(graph, trig("D1", "R001_taxi.png"))
    out = start(graph, trig("D2", "R004_taxi_again.png"))    # 다른 요청, 같은 영수증
    d = pending(out)["decision"]
    assert d["duplicate_of"] == "EXP-0001" and len(register.rows) == 1


def test_verifier_rejects_amount_not_in_ocr(tmp_path):
    def hallucinate(ctx):   # OCR에 없는 금액을 적는 모델
        return ReceiptDecision(status="completed", summary="s", total=19_000, total_line="L6", claim_amount=19_000,
                               items=[ReceiptItem(name="요금", amount=19_000, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=ExpenseRegister(), decide=hallucinate))
    out = start(graph, trig("E", "R001_taxi.png"))
    assert out["outcome"] == "failed" and any("L6 줄에서 확인되지 않음" in f for f in out["feedback"])
    assert any("원문: '합계 18,400원'" in f for f in out["feedback"])   # 모델이 고칠 수 있게 원문을 함께 돌려준다


def test_verifier_catches_duplicate_even_if_model_misses_it(tmp_path):
    register = ExpenseRegister()
    register.add({"merchant_key": "1234567890", "paid_at": "2026-09-14 08:42", "total": 18_400})

    def careless(ctx):   # 중복 확인을 빠뜨린 모델
        return ReceiptDecision(status="completed", summary="s", business_no="123-45-67890", business_no_line="L2",
                               paid_at="2026-09-14 08:42", total=18_400, total_line="L6", claim_amount=18_400,
                               items=[ReceiptItem(name="기본요금", amount=4_800, line_id="L4"),
                                      ReceiptItem(name="주행요금", amount=13_600, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register, decide=careless))
    out = start(graph, trig("F", "R004_taxi_again.png"))
    assert out["outcome"] == "failed" and any("EXP-0001" in f for f in out["feedback"])


def test_duplicate_caught_even_if_model_omits_business_no(tmp_path):
    """처음 실제 모델로 돌렸을 때 나온 경우: 사업자번호·일시를 비우고 completed로 판단."""
    register = ExpenseRegister()
    register.add({"merchant_key": "1234567890", "paid_at": "2026-09-14 08:42", "total": 18_400})

    def forgetful(ctx):
        return ReceiptDecision(status="completed", summary="택시 18,400원 정상", merchant="택시",
                               total=18_400, total_line="L6", claim_amount=18_400,
                               items=[ReceiptItem(name="기본요금", amount=4_800, line_id="L4"),
                                      ReceiptItem(name="주행요금", amount=13_600, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register, decide=forgetful))
    out = start(graph, trig("F2", "R004_taxi_again.png"))
    assert out["outcome"] == "failed" and len(register.rows) == 1        # 자동 반영되지 않는다
    assert any("EXP-0001" in f for f in out["feedback"])


def test_commit_stores_ocr_keys_when_model_omits_them(tmp_path):
    register = ExpenseRegister()

    def no_keys(ctx):
        return ReceiptDecision(status="completed", summary="s", merchant="택시", total=18_400, total_line="L6",
                               claim_amount=18_400,
                               items=[ReceiptItem(name="기본요금", amount=4_800, line_id="L4"),
                                      ReceiptItem(name="주행요금", amount=13_600, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register, decide=no_keys))
    assert start(graph, trig("K1", "R001_taxi.png"))["outcome"] == "committed"
    assert register.rows[0]["merchant_key"] == "1234567890" and register.rows[0]["paid_at"] == "2026-09-14 08:42"


class LowConfidenceOCR:
    """OCR 엔진을 바꿔 끼우는 예: 합계 줄을 흐리게 읽은 상황."""
    name = "stub"

    def read(self, path):
        texts = ["Z택시", "사업자번호 123-45-67890", "일시 2026-09-14 08:42", "기본요금 4,800", "주행요금 13,600", "합계 18,400원"]
        return OCRResult(source=str(path), engine=self.name,
                         lines=[OCRLine(id=f"L{i}", text=t, conf=40 if "합계" in t else 95) for i, t in enumerate(texts, 1)])


def test_low_ocr_confidence_needs_human_even_under_auto_limit():
    graph = build_graph(make_spec(ocr=LowConfidenceOCR(), register=ExpenseRegister()))
    out = start(graph, trig("G", "R001_taxi.png"))
    assert pending(out)["kind"] == "approval" and out["approval"]["mode"] == "human"


def test_missing_image_fails_before_llm():
    called = []
    graph = build_graph(make_spec(ocr=CachedOCR(None), decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(request_id="H", payload={"image": "없음.png", "purpose": "교통비"}))
    assert out["outcome"] == "failed" and called == []
    assert "영수증 이미지가 없음" in out["log"][0]["reason"]   # 엉뚱한 예외로 통과하지 않게 이유까지 본다


def test_missing_purpose_fails_before_llm():
    called = []
    graph = build_graph(make_spec(ocr=CachedOCR(None), decide=lambda ctx: called.append(1)))
    out = start(graph, Trigger(request_id="H2", payload={"image": str(IMG / "R001_taxi.png")}))
    assert out["outcome"] == "failed" and called == [] and "정산 목적(purpose)이 없음" in out["log"][0]["reason"]


def test_live_agent_wiring_with_scripted_model(tmp_path):
    final = {"status": "completed", "summary": "Z택시 18,400원 정산 가능", "merchant": "Z택시",
             "business_no": "123-45-67890", "business_no_line": "L2", "paid_at": "2026-09-14 08:42",
             "items": [{"name": "기본요금", "amount": 4800, "line_id": "L4"}, {"name": "주행요금", "amount": 13600, "line_id": "L5"}],
             "total": 18400, "total_line": "L6", "claim_amount": 18400, "policy_ids": ["EXP-01", "EXP-05"],
             "evidence": [{"source": "ocr", "locator": "L6", "quote": "합계 18,400원"}, {"source": "EXP-05", "locator": "정책"}]}
    model = ScriptedToolModel(script=[
        AIMessage(content="", tool_calls=[{"name": "find_duplicate", "id": "1",
                                           "args": {"business_no": "123-45-67890", "paid_at": "2026-09-14 08:42", "total": 18400}}]),
        AIMessage(content="", tool_calls=[{"name": "lookup_policy", "id": "2", "args": {"policy_id": "EXP-05"}}]),
        AIMessage(content="", tool_calls=[{"name": "ReceiptDecision", "id": "3", "args": final}]),
    ], seen=[])
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=ExpenseRegister(), model=model))
    out = start(graph, trig("I", "R001_taxi.png"))
    assert out["outcome"] == "committed"
    first_prompt = model.seen[0][-1].content
    assert "L6 (신뢰도" in first_prompt           # OCR 줄 ID와 신뢰도가 모델에 전달됨


def test_meal_cap_is_checked_by_code():
    """실제 모델 실행에서 나온 경우: 1인당 한도를 잘못 계산해 126,000원 전액을 completed로 판단."""
    def overpay(ctx):
        return ReceiptDecision(status="completed", summary="한도 이내", merchant="한빛한정식",
                               total=126_000, total_line="L5", claim_amount=126_000,
                               attendees=["김OO", "이OO", "박OO", "최OO"],
                               items=[], evidence=[{"source": "ocr", "locator": "L5", "quote": "합계"}])
    spec = make_spec(ocr=CachedOCR(None), register=ExpenseRegister(), decide=overpay)
    inp = spec.load_input(trig("M1", "R002_meeting_meal.png", purpose="회의 식비"))
    errs = spec.verify(overpay(None), inp)
    assert any("120,000원" in e for e in errs)


def test_blurry_amount_lines_need_human_even_under_auto_limit(env):
    """RC-005: 14,000원이라 자동 등록 한도 안이지만 금액 줄(L5) OCR 신뢰도가 기준보다 낮다."""
    graph, register = env
    out = start(graph, trig("N1", "R005_taxi_blurry.png"))
    ask = pending(out)
    assert ask["kind"] == "approval" and out["approval"]["mode"] == "human"
    assert ask["decision"]["claim_amount"] == 14_000 and register.rows == []


def test_pub_is_blocked_even_if_model_drops_shop_type():
    """RC-006: 모델이 가맹점명을 '한빛'으로 줄여 적어도 영수증 첫 줄('한빚주점')로 차단한다."""
    def short_name(ctx):
        return ReceiptDecision(status="completed", summary="한빛 66,000원 정산 가능", merchant="한빛",
                               total=66_000, total_line="L6", claim_amount=66_000,
                               items=[ReceiptItem(name="생맥주", amount=30_000, line_id="L4"),
                                      ReceiptItem(name="안주", amount=36_000, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    register = ExpenseRegister()
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register, decide=short_name))
    out = start(graph, trig("N2", "R006_pub.png", purpose="팀 회식비"))
    assert out["outcome"] == "blocked" and out["approval"]["mode"] == "block" and register.rows == []


def test_misread_business_no_still_matches_duplicate_key():
    """R002의 OCR은 사업자번호 앞에 2를 더 붙여 읽는다(2214-88-10293). 모델이 그 값을 그대로 적어도
    대장의 키와 검증기의 원문 키가 같아야 같은 영수증의 재제출을 잡는다."""
    def meal(ctx):
        return ReceiptDecision(status="completed", summary="회의 식비 126,000원", merchant="한빛한정식",
                               business_no="2214-88-10293", business_no_line="L2", paid_at="2026-09-15 12:30",
                               total=126_000, total_line="L6", claim_amount=126_000,
                               attendees=["김OO", "이OO", "박OO", "최OO", "정OO"],
                               items=[ReceiptItem(name="한정식", amount=112_000, line_id="L4"),
                                      ReceiptItem(name="음료", amount=14_000, line_id="L5")],
                               evidence=[{"source": "ocr", "locator": "L6", "quote": "합계"}])
    register = ExpenseRegister()
    graph = build_graph(make_spec(ocr=CachedOCR(None), register=register, decide=meal))
    start(graph, trig("P1", "R002_meeting_meal.png", purpose="회의 식비"))
    assert resume(graph, "P1", {"action": "approve"})["outcome"] == "committed"
    assert register.rows[0]["merchant_key"] == "2148810293"
    out = start(graph, trig("P2", "R002_meeting_meal.png", purpose="회의 식비"))   # 같은 영수증을 다시 제출
    assert out["outcome"] == "failed" and any("EXP-0001" in f for f in out["feedback"])
