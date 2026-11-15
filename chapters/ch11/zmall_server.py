# zmall_server.py — 11.4.2·11.4.5 본문 서버에 실행기용 도구 몇 개를 더한 버전
import asyncio

from fastmcp import FastMCP, Context

mcp = FastMCP("ZMall")

@mcp.tool()
def check_stock(item_id: str) -> dict:
    """상품 ID로 현재 재고 수량을 조회합니다."""
    # 실제 서비스에서는 DB 조회 로직이 들어갑니다
    return {"item_id": item_id, "stock": 42, "status": "재고 있음"}

@mcp.tool()
def get_shipping_status(order_id: str) -> dict:
    """주문 번호로 현재 배송 상태를 조회합니다."""
    return {"order_id": order_id, "status": "배송중", "estimated": "내일 오전 중"}

# 11.4.5: 자원과 프롬프트
@mcp.resource("policy://return")
def return_policy() -> str:
    """Z몰 반품 규정"""
    return "Z몰 반품 규정 (2026년 기준)\n1. 단순 변심: 수령 후 7일 이내 가능\n2. 제품 불량: 수령 후 30일 이내 가능"

@mcp.prompt()
def customer_complaint(category: str, issue_type: str) -> str:
    """반품 상담 지침"""
    return (f"고객이 {category} {issue_type}을 요청하고 있습니다. 먼저 불편을 드려 죄송하다고 사과하고,\n"
            "반품 정책을 안내한 후 처리 절차를 설명하세요.")

# ── 여기부터 실행기 보조 도구 ──────────────────────────────
CART: list[dict] = []          # 세션(프로세스)이 살아 있는 동안만 유지
PRICES = {"A 운동화": 89000, "B 운동화": 129000}

@mcp.tool()
def add_to_cart(item_name: str) -> dict:
    """상품 이름으로 장바구니에 상품을 담습니다. (11.4.4)"""
    price = PRICES.get(item_name, 50000)
    CART.append({"item": item_name, "price": price})
    return {"added": item_name, "price": price, "count": len(CART)}

@mcp.tool()
def cart_total() -> dict:
    """지금까지 담은 장바구니의 총액을 계산합니다. (11.4.4)"""
    return {"items": CART, "total": sum(c["price"] for c in CART)}

@mcp.tool()
def list_recent_orders(user_id: str) -> dict:
    """고객의 최근 주문을 조회합니다. user_id는 인터셉터가 넣습니다. (11.4.6)"""
    return {"user_id": user_id, "orders": [{"order_id": "ZM-1029", "status": "배송중"},
                                           {"order_id": "ZM-0981", "status": "배송완료"}]}

@mcp.tool()
async def monthly_sales_report(month: str, ctx: Context) -> str:
    """월간 판매 통계를 만듭니다. 진행률을 클라이언트에 보냅니다. (11.4.6)"""
    steps = ["주문 데이터 수집 중...", "통계 계산 중...", "엑셀 파일 생성 중...", "완료"]
    for i, msg in enumerate(steps, 1):
        await ctx.report_progress(progress=i, total=len(steps), message=msg)
        await asyncio.sleep(0.3)
    return f"{month} 판매 통계 보고서를 만들었습니다."

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
