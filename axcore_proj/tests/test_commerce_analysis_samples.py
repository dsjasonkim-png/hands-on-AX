"""매출 변동 분석 샘플 DB가 평가 사례에서 설계한 변화를 그대로 담고 있는지 확인한다.

생성 스크립트(samples/commerce_analysis/make_samples.py)의 조건을 바꾸면 이 테스트가 먼저 알려 준다.
"""
from contextlib import closing

import pytest

from jobs.commerce_analysis.metrics import analyze, connect_ro, totals, value
from jobs.commerce_analysis.policies import AUTO_SHARE
from jobs.commerce_analysis.spec import DEFAULT_DB
from samples.commerce_analysis import make_samples

# (비교할 달, 분석할 달, 방향, 주원인, 원인이 분명한가, 변화가 몰린 채널)
SCENARIOS = [
    ("2026-03", "2026-04", "down", "clicks", True, ["web"]),
    ("2026-04", "2026-05", "down", "aov", True, []),
    ("2026-05", "2026-06", "down", "aov", True, []),
    ("2026-06", "2026-07", "down", "payment_success_rate", True, []),
    ("2026-07", "2026-08", "up", "payment_success_rate", True, []),
    ("2026-08", "2026-09", "down", "clicks", False, []),
    ("2026-09", "2026-10", "down", "checkout_to_order", True, ["web"]),
]


@pytest.mark.parametrize("base, cur, direction, primary, clear, concentrated", SCENARIOS)
def test_sample_months_carry_designed_change(base, cur, direction, primary, clear, concentrated):
    with closing(connect_ro(DEFAULT_DB)) as conn:
        a = analyze(conn, base, cur)
    assert (a.direction, a.primary) == (direction, primary)
    assert (a.primary_share >= AUTO_SHARE) == clear
    assert a.concentrated == concentrated
    # 경계에 붙어 있으면 실제 모델 평가가 흔들리므로 기준에서 5%p 이상 떨어져 있어야 한다
    assert abs(a.primary_share - AUTO_SHARE) >= 0.05


def test_journey_never_grows_and_matches_orders():
    """여정의 뒤 단계가 앞 단계를 넘지 않고, 모든 주문이 세션 하나로 이어진다."""
    with closing(connect_ro(DEFAULT_DB)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE added_to_cart > viewed_product OR started_checkout > added_to_cart "
                            "OR (order_id IS NOT NULL AND started_checkout = 0)").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*), COUNT(DISTINCT order_id) FROM sessions WHERE order_id IS NOT NULL").fetchone() == \
               conn.execute("SELECT COUNT(*), COUNT(*) FROM orders").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM sessions s JOIN orders o ON o.order_id = s.order_id "
                            "WHERE substr(s.started_at, 1, 7) <> substr(o.ordered_at, 1, 7) OR s.channel <> o.channel").fetchone()[0] == 0


@pytest.mark.parametrize("month", ["2026-04", "2026-06", "2026-10"])
def test_factors_multiply_to_revenue(month):
    with closing(connect_ro(DEFAULT_DB)) as conn:
        t = totals(conn, month)
    product = t.clicks * (t.views / t.clicks) * (t.carts / t.views) * (t.checkouts / t.carts) * (t.attempts / t.checkouts) \
        * (t.paid / t.attempts) * (t.revenue / t.paid)
    assert product == pytest.approx(t.revenue)


def source_clicks(conn, month, source):
    return conn.execute("SELECT COUNT(*) FROM sessions WHERE substr(started_at, 1, 7) = ? AND source = ?", (month, source)).fetchone()[0]


def test_only_search_ads_stop_in_april():
    """검색 광고만 끊겼고 다른 유입은 그대로다."""
    with closing(connect_ro(DEFAULT_DB)) as conn:
        assert source_clicks(conn, "2026-03", "search_ad") > 0 and source_clicks(conn, "2026-04", "search_ad") == 0
        assert source_clicks(conn, "2026-04", "social") == pytest.approx(source_clicks(conn, "2026-03", "social"), rel=0.03)


def test_web_checkout_drop_keeps_web_clicks():
    """2026-10은 웹 주문서에서 이탈했을 뿐 웹 클릭은 그대로다(예제 ②)."""
    with closing(connect_ro(DEFAULT_DB)) as conn:
        assert value(conn, "clicks", "2026-10", "web") == pytest.approx(value(conn, "clicks", "2026-09", "web"), rel=0.03)


def test_make_samples_is_deterministic(tmp_path, capsys):
    path = make_samples.main(tmp_path)
    with closing(connect_ro(path)) as fresh, closing(connect_ro(DEFAULT_DB)) as saved:
        assert list(fresh.iterdump()) == list(saved.iterdump())
