"""Z몰 주문 DB(SQLite) 합성 데이터를 만든다. python -m samples.commerce_analysis.make_samples

실제 회사·사람 정보가 아닌 가상 데이터다. 2026-03부터 2026-10까지 8개월 치 고객 여정·주문·결제·배송을 만들고,
달마다 한 가지 변화를 넣어 평가 사례(jobs/commerce_analysis/eval_cases.py)가 그 변화를 찾게 한다.
변화의 이유는 DB에 적지 않는다. 분석 Agent가 숫자로 찾아야 한다.
무작위 표본 대신 비율대로 건수를 정확히 나눠 넣어, 지표가 설계한 값에서 크게 흔들리지 않게 한다.

sessions 한 행은 클릭 한 번으로 시작된 고객 여정이다(클릭 → 상품 조회 → 장바구니 → 주문서 → 주문).
클릭 수는 수요(demand)에서 정하고 전환율을 곱해 내려오므로, 전환율만 바꾼 달은 그 앞 단계의 건수가 그대로 유지된다.
"""
from __future__ import annotations

import calendar
import copy
import math
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DB_NAME = "zmall_orders.db"
SEED = 2026
TS = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE products (product_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL);
CREATE TABLE orders (order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, ordered_at TEXT NOT NULL, channel TEXT NOT NULL);
CREATE TABLE order_items (order_id TEXT NOT NULL REFERENCES orders(order_id), product_id TEXT NOT NULL REFERENCES products(product_id),
                          qty INTEGER NOT NULL, unit_price INTEGER NOT NULL, discount INTEGER NOT NULL);
CREATE TABLE payments (order_id TEXT PRIMARY KEY REFERENCES orders(order_id), method TEXT NOT NULL, status TEXT NOT NULL,
                       amount INTEGER NOT NULL, paid_at TEXT);
CREATE TABLE shipments (order_id TEXT PRIMARY KEY REFERENCES orders(order_id), shipped_at TEXT NOT NULL, delivered_at TEXT NOT NULL);
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, channel TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL,
                       viewed_product INTEGER NOT NULL, added_to_cart INTEGER NOT NULL, started_checkout INTEGER NOT NULL,
                       order_id TEXT REFERENCES orders(order_id));
CREATE INDEX idx_sessions_started_at ON sessions(started_at);
"""

PRODUCTS = [
    ("P-HA-01", "무선청소기", "가전", 389_000), ("P-HA-02", "에어프라이어", "가전", 189_000),
    ("P-HA-03", "서큘레이터", "가전", 129_000), ("P-HA-04", "전기포트", "가전", 69_000),
    ("P-FA-01", "반팔 티셔츠", "패션", 19_900), ("P-FA-02", "린넨 셔츠", "패션", 39_900),
    ("P-FA-03", "슬랙스", "패션", 45_900), ("P-FA-04", "운동화", "패션", 69_000),
    ("P-FO-01", "제주 감귤 3kg", "식품", 18_900), ("P-FO-02", "닭가슴살 20팩", "식품", 32_900),
    ("P-FO-03", "원두 1kg", "식품", 27_000), ("P-FO-04", "생수 2L×12", "식품", 9_900),
    ("P-LI-01", "수건 5매", "생활", 24_900), ("P-LI-02", "주방세제 리필", "생활", 8_900),
    ("P-LI-03", "수납함 3단", "생활", 35_900), ("P-LI-04", "극세사 이불", "생활", 59_000),
    ("P-BE-01", "선크림", "뷰티", 17_900), ("P-BE-02", "수분크림", "뷰티", 28_000),
    ("P-BE-03", "샴푸 1L", "뷰티", 15_900), ("P-BE-04", "립밤 세트", "뷰티", 12_900),
]
CATEGORY_WEIGHTS = {"가전": 0.10, "패션": 0.27, "식품": 0.27, "생활": 0.21, "뷰티": 0.15}   # 가전은 주문 10%, 매출 약 30%
METHOD_SHARES = {"card": 0.60, "easy_pay": 0.30, "bank": 0.10}
QTY_PATTERN = [1, 1, 2, 1, 2]     # 가전이 아닌 상품의 수량을 돌아가며 정한다
LINES_PATTERN = [1, 1, 2]         # 가전이 아닌 주문의 상품 줄 수

JOURNEY_RATES = {"click_to_view": 0.75, "view_to_cart": 0.35, "cart_to_checkout": 0.65, "checkout_to_order": 0.85}
APP_SOURCES = {"push": 0.30, "direct": 0.55, "social": 0.15}

BASE = {"attempts": {"app": 338, "web": 182}, "demand": {"app": 338, "web": 182}, "appliance_mult": 1.0, "discount": 0.03,
        "fail": {"card": 0.03, "easy_pay": 0.03, "bank": 0.03}, "late": {"가전": 0.05, "기타": 0.05},
        "sources": {"app": APP_SOURCES, "web": {"search_ad": 0.60, "social": 0.20, "direct": 0.20}},
        "step_mult": {}}     # (채널 또는 카테고리, 여정 단계) → 그 단계 전환율에 곱할 값

# (달, 그 달에 생긴 변화, 바뀌는 조건). 앞 달의 변화는 다음 달에도 이어진다.
SCENARIOS = [
    ("2026-03", "기준 달", {}),
    ("2026-04", "검색 광고 중단 → 웹 클릭 60% 감소, 전환율은 그대로",
     {"attempts": {"app": 338, "web": 73}, "demand": {"app": 338, "web": 73},
      "sources": {"app": APP_SOURCES, "web": {"search_ad": 0, "social": 0.50, "direct": 0.50}}}),
    ("2026-05", "전 품목 쿠폰 행사 → 할인율 3% → 18%", {"discount": 0.18}),
    ("2026-06", "가전 협력사 재고 부족 → 가전 조회→장바구니 전환 75% 감소, 가전 주문 75% 감소, 가전 배송 지연",
     {"appliance_mult": 0.25, "late": {"가전": 0.40, "기타": 0.05}, "step_mult": {("가전", "view_to_cart"): 0.25}}),
    ("2026-07", "카드 결제 장애 → 카드 결제 실패율 3% → 25%", {"fail": {"card": 0.25, "easy_pay": 0.03, "bank": 0.03}}),
    ("2026-08", "결제 장애 복구, 클릭 3% 증가",
     {"fail": {"card": 0.03, "easy_pay": 0.03, "bank": 0.03}, "attempts": {"app": 348, "web": 75}, "demand": {"app": 348, "web": 75}}),
    ("2026-09", "클릭 8% 감소 + 결제 실패율 8% + 할인율 24%가 함께",
     {"attempts": {"app": 320, "web": 69}, "demand": {"app": 320, "web": 69},
      "fail": {"card": 0.08, "easy_pay": 0.08, "bank": 0.08}, "discount": 0.24}),
    ("2026-10", "웹 주문서 페이지 오류 → 웹 주문서→주문 전환 60% 감소, 클릭은 그대로",
     {"attempts": {"app": 320, "web": 28},
      "step_mult": {("가전", "view_to_cart"): 0.25, ("web", "checkout_to_order"): 0.40}}),
]


def split(total: int, shares: dict[str, float]) -> dict[str, int]:
    """비율대로 정수 건수를 나눈다(최대 나머지 방식). 합이 정확히 total이 된다."""
    raw = {k: total * v / sum(shares.values()) for k, v in shares.items()}
    out = {k: int(x) for k, x in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: total - sum(out.values())]:
        out[k] += 1
    return out


def add_sessions(conn: sqlite3.Connection, rnd: random.Random, month: str, start: datetime, seconds: int,
                 placed: list[tuple[str, str, str, datetime]], state: dict) -> None:
    """그 달의 수요에서 클릭 수를 정하고 전환율을 곱해 내려와 고객 여정(세션)을 만든다. 마지막 단계는 실제 주문에 맞춘다."""
    rows = []
    for channel, demand in state["demand"].items():
        for cat, wanted in split(demand, CATEGORY_WEIGHTS).items():
            cell = [(oid, t) for oid, ch, c, t in placed if ch == channel and c == cat]
            rate = {step: base * state["step_mult"].get((cat, step), 1) * state["step_mult"].get((channel, step), 1)
                    for step, base in JOURNEY_RATES.items()}
            clicks = round(wanted / math.prod(JOURNEY_RATES.values()))
            views = round(clicks * rate["click_to_view"])
            carts = round(views * rate["view_to_cart"])
            checkouts = max(len(cell), round(carts * rate["cart_to_checkout"]))
            carts = max(carts, checkouts)      # 건수가 적은 칸에서 반올림 때문에 뒤 단계가 앞 단계를 넘지 않게 한다
            views = max(views, carts)
            clicks = max(clicks, views)
            sources = [s for s, k in split(clicks, state["sources"][channel]).items() for _ in range(k)]
            rnd.shuffle(sources)
            for i, source in enumerate(sources):
                if i < len(cell):        # 주문으로 이어진 세션은 주문 직전에 시작한다(달을 넘기지 않게)
                    oid, at = cell[i][0], max(start, cell[i][1] - timedelta(minutes=rnd.randint(1, 30)))
                else:
                    oid, at = None, start + timedelta(seconds=rnd.randrange(seconds))
                rows.append((at.strftime(TS), channel, source, cat, int(i < views), int(i < carts), int(i < checkouts), oid))
    rnd.shuffle(rows)
    conn.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     [(f"S{month[2:4]}{month[5:]}-{i + 1:05d}", *row) for i, row in enumerate(rows)])


def build(path: Path) -> None:
    rnd = random.Random(SEED)
    rnd_journey = random.Random(SEED + 1)     # 주문·결제 난수와 분리한다. 여정을 바꿔도 주문 숫자가 흔들리지 않는다
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
    by_cat = {c: [p for p in PRODUCTS if p[2] == c] for c in CATEGORY_WEIGHTS}
    turn = {c: 0 for c in CATEGORY_WEIGHTS}      # 카테고리 안에서 상품·수량을 돌아가며 고른다
    state = copy.deepcopy(BASE)
    for month, _, change in SCENARIOS:
        state.update(copy.deepcopy(change))
        orders = []
        for channel, n in state["attempts"].items():
            cats = split(n, CATEGORY_WEIGHTS)
            cats["가전"] = round(cats["가전"] * state["appliance_mult"])
            orders += [(channel, c) for c, k in cats.items() for _ in range(k)]
        rnd.shuffle(orders)
        start = datetime.strptime(f"{month}-01", "%Y-%m-%d")
        seconds = calendar.monthrange(start.year, start.month)[1] * 86400
        times = sorted(start + timedelta(seconds=rnd.randrange(seconds)) for _ in orders)
        methods = [m for m, k in split(len(orders), METHOD_SHARES).items() for _ in range(k)]
        rnd.shuffle(methods)
        failed = set()
        for m, rate in state["fail"].items():
            idx = [i for i, x in enumerate(methods) if x == m]
            failed |= set(rnd.sample(idx, round(len(idx) * rate)))
        approved = {"가전": [], "기타": []}
        placed = []
        for i, ((channel, cat), t, method) in enumerate(zip(orders, times, methods)):
            oid = f"O{month[2:4]}{month[5:]}-{i + 1:04d}"
            placed.append((oid, channel, cat, t))
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?)", (oid, f"C{rnd.randint(1, 4000):05d}", t.strftime(TS), channel))
            n_lines = 1 if cat == "가전" else LINES_PATTERN[turn[cat] % len(LINES_PATTERN)]
            amount = 0
            for _ in range(n_lines):
                pid, _, _, price = by_cat[cat][turn[cat] % len(by_cat[cat])]
                qty = 1 if cat == "가전" else QTY_PATTERN[turn[cat] % len(QTY_PATTERN)]
                turn[cat] += 1
                discount = round(qty * price * state["discount"], -1)
                amount += qty * price - discount
                conn.execute("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", (oid, pid, qty, price, discount))
            ok = i not in failed
            paid_at = (t + timedelta(minutes=1)).strftime(TS) if ok else None
            conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
                         (oid, method, "approved" if ok else "failed", amount, paid_at))
            if ok:
                approved["가전" if cat == "가전" else "기타"].append((oid, t))
        for group, rows in approved.items():      # 지연 건수도 비율대로 정확히 정한다
            late = set(rnd.sample(range(len(rows)), round(len(rows) * state["late"][group])))
            for j, (oid, t) in enumerate(rows):
                days = rnd.choice([4, 5]) if j in late else rnd.choice([1, 2])
                delivered = t + timedelta(days=days, hours=rnd.randint(0, 8))
                shipped = t + (delivered - t) / 2
                conn.execute("INSERT INTO shipments VALUES (?, ?, ?)", (oid, shipped.strftime(TS), delivered.strftime(TS)))
        add_sessions(conn, rnd_journey, month, start, seconds, placed, state)
    conn.commit()
    conn.close()


def main(out_dir: Path = ROOT) -> Path:
    path = Path(out_dir) / DB_NAME
    build(path)
    from jobs.commerce_analysis.metrics import connect_ro, fmt, value

    conn = connect_ro(path)
    print(f"{'달':8} {'매출':>14} {'클릭':>8} {'주문 시도':>8} {'결제 성공률':>10} {'객단가':>10}  변화")
    for month, note, _ in SCENARIOS:
        print(f"{month:8} {fmt('revenue', value(conn, 'revenue', month)):>14} "
              f"{fmt('clicks', value(conn, 'clicks', month)):>8} "
              f"{fmt('order_attempts', value(conn, 'order_attempts', month)):>8} "
              f"{fmt('payment_success_rate', value(conn, 'payment_success_rate', month)):>10} "
              f"{fmt('aov', value(conn, 'aov', month)):>10}  {note}")
    conn.close()
    print(f"샘플 생성 완료: {path}")
    return path


if __name__ == "__main__":
    main()
