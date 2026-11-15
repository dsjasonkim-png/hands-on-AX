"""매출 지표의 정답 계산과 숫자 채우기. 검증기·승인 정책·보고서·가짜 판단기가 함께 쓴다.

모델이 적은 숫자를 믿지 않고, 여기 정의한 쿼리로 원본 DB에서 다시 계산한다.
매출 = 클릭 수 × 클릭→조회율 × 조회→장바구니율 × 장바구니→주문서율 × 주문서→주문율 × 결제 성공률 × 객단가
이므로, 일곱 요인의 로그 변화가 매출의 로그 변화를 정확히 나눠 갖는다(기여율).
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .policies import CONCENTRATION_LIFT, CONCENTRATION_SHARE, FLAT_PCT

REQUIRED_TABLES = {   # 코드가 정답을 계산할 때 쓰는 테이블과 컬럼
    "sessions": {"started_at", "channel", "viewed_product", "added_to_cart", "started_checkout"},
    "orders": {"order_id", "ordered_at", "channel"},
    "order_items": {"order_id", "qty", "unit_price", "discount"},
    "payments": {"order_id", "status", "amount"},
}

FACTORS = ("clicks", "click_to_view", "view_to_cart", "cart_to_checkout", "checkout_to_order",
           "payment_success_rate", "aov")   # 일곱 요인을 곱하면 매출이 된다

METRICS = {
    "revenue": {"name": "매출", "unit": "원", "desc": "결제 승인된(payments.status='approved') 주문의 결제 금액 합(할인 차감 후)"},
    "clicks": {"name": "클릭 수", "unit": "건", "desc": "그 달에 클릭으로 시작된 세션 수(sessions 행 수)"},
    "product_views": {"name": "상품 조회 수", "unit": "건", "desc": "상품을 조회한 세션 수(viewed_product = 1)"},
    "cart_adds": {"name": "장바구니 담기 수", "unit": "건", "desc": "장바구니에 담은 세션 수(added_to_cart = 1)"},
    "checkout_starts": {"name": "주문서 진입 수", "unit": "건", "desc": "주문서에 들어간 세션 수(started_checkout = 1)"},
    "order_attempts": {"name": "주문 시도 수", "unit": "건", "desc": "그 달에 들어온 주문 수(결제 실패 포함)"},
    "paid_orders": {"name": "결제 완료 주문 수", "unit": "건", "desc": "결제가 승인된 주문 수"},
    "click_to_view": {"name": "클릭→조회율", "unit": "%", "desc": "상품 조회 수 ÷ 클릭 수 × 100 (소수 첫째 자리)"},
    "view_to_cart": {"name": "조회→장바구니율", "unit": "%", "desc": "장바구니 담기 수 ÷ 상품 조회 수 × 100 (소수 첫째 자리)"},
    "cart_to_checkout": {"name": "장바구니→주문서율", "unit": "%", "desc": "주문서 진입 수 ÷ 장바구니 담기 수 × 100 (소수 첫째 자리)"},
    "checkout_to_order": {"name": "주문서→주문율", "unit": "%", "desc": "주문 시도 수 ÷ 주문서 진입 수 × 100 (소수 첫째 자리)"},
    "payment_success_rate": {"name": "결제 성공률", "unit": "%", "desc": "결제 완료 주문 수 ÷ 주문 시도 수 × 100 (소수 첫째 자리)"},
    "aov": {"name": "객단가", "unit": "원", "desc": "매출 ÷ 결제 완료 주문 수 (원 단위 반올림)"},
    "discount_rate": {"name": "할인율", "unit": "%",
                      "desc": "결제 완료 주문의 할인 합 ÷ 정가(qty × unit_price) 합 × 100 (소수 첫째 자리)"},
}

TOLERANCE = {"원": 0.5, "건": 0.5, "%": 0.1}   # 금액·건수는 정확히, 비율은 소수 첫째 자리까지
AOV_TOLERANCE = 1.0                           # 객단가는 반올림 방식 차이로 1원까지 허용

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
YEAR_MONTH_IN_TEXT = re.compile(r"(\d{4})\s*(?:년|[-./])\s*(\d{1,2})")      # '2026년 4월', '2026-04'
BARE_MONTH_IN_TEXT = re.compile(r"(?<!\d)(\d{1,2})\s*월")                    # '4월'
LAST_MONTH_IN_TEXT = re.compile(r"(?<![가-힣])(?:(?:지난|저번)\s?달(?![라려리러])|전월)")   # '지지난달', '달라졌다'는 아님


# DB 연결과 달 ─────────────────────────────────────────────────────────────
def connect_ro(db: str | Path) -> sqlite3.Connection:
    """원본 DB를 읽기 전용으로 연다(검증 쪽에서도 원본을 바꾸지 않는다)."""
    return sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)


def prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def months_in(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT substr(ordered_at, 1, 7) FROM orders ORDER BY 1")]


def channels_in(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT channel FROM orders ORDER BY 1")]


def months_in_text(text: str, known: list[str]) -> list[str]:
    """문장에 적힌 달을 YYYY-MM으로 모은다. '4월'처럼 연도가 없으면 DB에 있는 같은 달로 보고, 없으면 '4월' 그대로 둔다."""
    found = [f"{y}-{int(m):02d}" for y, m in YEAR_MONTH_IN_TEXT.findall(text) if 1 <= int(m) <= 12]
    for m in BARE_MONTH_IN_TEXT.findall(YEAR_MONTH_IN_TEXT.sub(" ", text)):
        found += [k for k in known if k.endswith(f"-{int(m):02d}")] or [f"{int(m)}월"]
    return list(dict.fromkeys(found))


# 한 달의 합계와 지표 ──────────────────────────────────────────────────────
@dataclass
class Totals:
    clicks: int
    views: int
    carts: int
    checkouts: int
    attempts: int
    paid: int
    revenue: int
    gross: int
    discount: int


def totals(conn: sqlite3.Connection, month: str, channel: str = "") -> Totals:
    """한 달(채널을 주면 그 채널)의 기초 합계. 지표는 모두 이 값에서 계산한다."""
    args = [f"{month}-01 00:00:00", f"{next_month(month)}-01 00:00:00"] + ([channel] if channel else [])
    only = " AND channel = ?" if channel else ""
    clicks, views, carts, checkouts = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(viewed_product), 0), COALESCE(SUM(added_to_cart), 0), "
        "COALESCE(SUM(started_checkout), 0) FROM sessions WHERE started_at >= ? AND started_at < ?" + only, args).fetchone()
    only = " AND o.channel = ?" if channel else ""
    attempts, paid, revenue = conn.execute(    # 결제 기록이 없는 주문도 시도로 센다
        "SELECT COUNT(*), COALESCE(SUM(p.status = 'approved'), 0), "
        "COALESCE(SUM(CASE WHEN p.status = 'approved' THEN p.amount END), 0) "
        "FROM orders o LEFT JOIN payments p ON p.order_id = o.order_id "
        "WHERE o.ordered_at >= ? AND o.ordered_at < ?" + only, args).fetchone()
    gross, discount = conn.execute(
        "SELECT COALESCE(SUM(i.qty * i.unit_price), 0), COALESCE(SUM(i.discount), 0) "
        "FROM orders o JOIN payments p ON p.order_id = o.order_id JOIN order_items i ON i.order_id = o.order_id "
        "WHERE p.status = 'approved' AND o.ordered_at >= ? AND o.ordered_at < ?" + only, args).fetchone()
    return Totals(clicks, views, carts, checkouts, attempts, paid, revenue, gross, discount)


def metric_values(t: Totals) -> dict[str, float]:
    def rate(a: int, b: int) -> float:
        return round(a / b * 100, 1) if b else 0.0
    return {
        "revenue": t.revenue, "clicks": t.clicks, "product_views": t.views, "cart_adds": t.carts,
        "checkout_starts": t.checkouts, "order_attempts": t.attempts, "paid_orders": t.paid,
        "click_to_view": rate(t.views, t.clicks), "view_to_cart": rate(t.carts, t.views),
        "cart_to_checkout": rate(t.checkouts, t.carts), "checkout_to_order": rate(t.attempts, t.checkouts),
        "payment_success_rate": rate(t.paid, t.attempts), "aov": round(t.revenue / t.paid) if t.paid else 0,
        "discount_rate": rate(t.discount, t.gross),
    }


def value(conn: sqlite3.Connection, metric: str, month: str, channel: str = "") -> float:
    return metric_values(totals(conn, month, channel))[metric]


def contributions(t0: Totals, t1: Totals) -> dict[str, float]:
    """매출 변화에서 일곱 요인이 차지한 몫(합이 정확히 1). 0이 있거나 변화가 없으면 나눌 수 없어 비어 있다."""
    if t0.revenue == t1.revenue or not all(x for t in (t0, t1) for x in
                                           (t.clicks, t.views, t.carts, t.checkouts, t.attempts, t.paid, t.revenue)):
        return {}
    raw = lambda t: {"clicks": t.clicks, "click_to_view": t.views / t.clicks, "view_to_cart": t.carts / t.views,
                     "cart_to_checkout": t.checkouts / t.carts, "checkout_to_order": t.attempts / t.checkouts,
                     "payment_success_rate": t.paid / t.attempts, "aov": t.revenue / t.paid}
    f0, f1, total = raw(t0), raw(t1), math.log(t1.revenue / t0.revenue)
    return {k: math.log(f1[k] / f0[k]) / total for k in FACTORS}


def pct(share: float) -> str:
    """기여율 표시. -0%가 나오지 않게 반올림 뒤 0을 더한다."""
    return f"{round(share, 2) + 0.0:.0%}"


def fmt(metric: str, v: float | None) -> str:
    if v is None:
        return "-"
    unit = METRICS[metric]["unit"]
    return f"{v:.1f}%" if unit == "%" else f"{round(v):,}{unit}"


# 두 달 비교: 검증기가 모델 판단과 비교하는 정답 ─────────────────────────────
@dataclass
class ChannelRow:
    name: str
    base: dict[str, float]                 # 비교 달 지표
    current: dict[str, float]              # 분석 달 지표
    share_of_change: float | None          # 이 채널의 매출 변화 ÷ 전체 매출 변화
    base_share: float                      # 비교 달 매출에서 이 채널이 차지한 비중
    shares: dict[str, float]               # 채널 안의 요인별 기여율. 채널 매출이 ±1% 미만으로 변했으면 비어 있음

    @property
    def top_step(self) -> str | None:
        return max(self.shares, key=self.shares.get) if self.shares else None


@dataclass
class Analysis:
    base_month: str
    current_month: str
    base: dict[str, float]                 # 전체 지표 (비교 달)
    current: dict[str, float]              # 전체 지표 (분석 달)
    change_pct: float
    direction: str                         # up / down / flat
    shares: dict[str, float] = field(default_factory=dict)    # 요인별 기여율. 나눌 수 없으면 비어 있음
    primary: str | None = None
    primary_share: float | None = None
    channels: list[ChannelRow] = field(default_factory=list)
    concentrated: list[str] = field(default_factory=list)     # 변화가 몰린 채널 이름

    @property
    def revenue_base(self) -> int:
        return int(self.base["revenue"])

    @property
    def revenue_current(self) -> int:
        return int(self.current["revenue"])

    @property
    def factors(self) -> dict[str, tuple[float, float]]:
        return {k: (self.base[k], self.current[k]) for k in FACTORS}

    def shares_text(self) -> str:
        if not self.shares:
            return "요인으로 나눌 수 없음"
        return ", ".join(f"{METRICS[k]['name']}({k}) {pct(self.shares[k])}" for k in FACTORS)


def analyze(conn: sqlite3.Connection, base_month: str, current_month: str) -> Analysis:
    """두 달의 매출 변화를 일곱 요인과 채널로 나눈다."""
    t0, t1 = totals(conn, base_month), totals(conn, current_month)
    r0, r1 = t0.revenue, t1.revenue
    change_pct = (r1 - r0) / r0 * 100 if r0 else (100.0 if r1 else 0.0)   # 비교 달 매출이 0원이면 100%로 둔다
    direction = "flat" if abs(change_pct) < FLAT_PCT else ("up" if r1 > r0 else "down")
    a = Analysis(base_month, current_month, metric_values(t0), metric_values(t1), change_pct, direction)
    for name in channels_in(conn):
        c0, c1 = totals(conn, base_month, name), totals(conn, current_month, name)
        moved = c0.revenue and abs(c1.revenue - c0.revenue) / c0.revenue * 100 >= FLAT_PCT
        a.channels.append(ChannelRow(name, metric_values(c0), metric_values(c1),
                                     (c1.revenue - c0.revenue) / (r1 - r0) if r1 != r0 else None,
                                     c0.revenue / r0 if r0 else 0.0, contributions(c0, c1) if moved else {}))
    if direction == "flat" or not (shares := contributions(t0, t1)):
        return a
    a.shares = shares
    a.primary = max(FACTORS, key=shares.get)
    a.primary_share = shares[a.primary]
    a.concentrated = [c.name for c in a.channels if c.base_share and c.share_of_change >= CONCENTRATION_SHARE
                      and c.share_of_change / c.base_share >= CONCENTRATION_LIFT]
    return a


# 문장 속 숫자: 모델은 자리표시만 쓰고 코드가 원본 값으로 채운다 ────────────────
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")                              # {aov.base}, {web.revenue.share}
UNIT_AFTER_PLACEHOLDER = re.compile(r"\{[^{}]*\}\s*(?:%p|%|원|건)")      # 단위는 채울 때 붙으므로 따로 쓰지 않는다
DATE_IN_TEXT = re.compile(r"\d{4}\s*년|\d{4}-\d{2}(?:-\d{2})?|\d{1,2}\s*월")   # 날짜는 문장에 그대로 써도 된다
NUMBER_IN_TEXT = re.compile(r"\d[\d,.]*\s*(?:%p|%|원|건|배)?")
PARTS = {"base": "비교할 달 값", "current": "분석할 달 값", "change": "변화율(부호 없음. % 지표는 차이 %p)",
         "share": "기여율(채널 매출이면 전체 매출 변화 중 그 채널 몫)"}


def number_for(key: str, a: Analysis) -> str:
    """자리표시 하나('aov.base', 'web.revenue.share')를 원본 값으로 바꾼다. 쓸 수 없는 자리표시면 ValueError."""
    names = key.strip().split(".")
    channel = names.pop(0) if len(names) == 3 else ""
    row = next((c for c in a.channels if c.name == channel), None)
    if len(names) != 2 or names[0] not in METRICS or names[1] not in PARTS or (channel and row is None):
        raise ValueError(key)
    metric, part = names
    b, c = (row.base[metric], row.current[metric]) if row else (a.base[metric], a.current[metric])
    if part in ("base", "current"):
        return fmt(metric, b if part == "base" else c)
    if part == "change":
        if METRICS[metric]["unit"] == "%":
            return f"{abs(c - b):.1f}%p"
        if b:
            return f"{abs(c - b) / b * 100:.1f}%"
    elif metric == "revenue" and row and row.share_of_change is not None:
        return pct(row.share_of_change)
    elif metric in (row.shares if row else a.shares):
        return pct((row.shares if row else a.shares)[metric])
    raise ValueError(key)


def fill_numbers(text: str, a: Analysis) -> tuple[str, list[str]]:
    """문장의 자리표시를 원본 값으로 채운다. 돌려주는 값: (채운 문장, 쓸 수 없는 자리표시 목록)."""
    bad = []

    def one(m: re.Match) -> str:
        try:
            return number_for(m[1], a)
        except ValueError:
            bad.append(m[0])
            return m[0]

    return PLACEHOLDER.sub(one, text), bad


def written_numbers(text: str) -> list[str]:
    """자리표시와 날짜를 빼고 문장에 직접 쓴 숫자."""
    return [m.strip() for m in NUMBER_IN_TEXT.findall(DATE_IN_TEXT.sub(" ", PLACEHOLDER.sub(" ", text)))]
