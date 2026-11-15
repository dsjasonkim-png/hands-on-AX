# profile_server.py — 11.4.6 엘리시테이션 서버(본문 코드 + 실행 줄)
from pydantic import BaseModel
from mcp.server.fastmcp import Context, FastMCP

server = FastMCP("Profile")

class RecipientDetails(BaseModel):
    email: str
    age: int

@server.tool()
async def create_gift_profile(recipient_name: str, ctx: Context) -> str:
    """선물 수신자 프로필을 생성합니다."""
    result = await ctx.elicit(
        message=f"{recipient_name}님의 프로필을 만들려면 추가 정보가 필요합니다:",
        schema=RecipientDetails,
    )
    if result.action == "accept" and result.data:
        return f"프로필 생성 완료: {result.data.email}, {result.data.age}세"
    if result.action == "decline":
        return "사용자가 정보 제공을 거부했습니다. 기본 프로필만 생성합니다."
    return "프로필 생성이 취소되었습니다."

if __name__ == "__main__":
    server.run(transport="stdio")
