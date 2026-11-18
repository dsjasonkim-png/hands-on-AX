"""폴더 검색 실습용 합성 사내 문서를 만든다. python -m samples.folder_search.make_samples

가상 기업 Z몰의 합성 데이터다(실제 회사·사람 정보 아님). 평가 사례가 기대는 점:
- 회의록 두 개에 모두 '마감일'이 있어 "그 회의"만으로는 어느 회의인지 알 수 없다(되묻기).
- 부산 3분기 매출은 보고서와 ERP 추출본이 같고(자동), 대구는 650 / 610으로 다르다(사람 확인).
- 인사·연봉 문서는 없다(찾을 수 없음).
- 전략발표는 PPT, 반품 현황은 엑셀이다. markitdown으로 마크다운으로 바꿔 읽고 두 번째 뇌 노트로 정리하는 사례에 쓴다.
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).parent / "files"

FILES = {
    "회의록_마케팅_1015.md": """# Z몰 마케팅 회의록 (2026-10-15)
참석: 마케팅팀 김OO, 박OO, 디자인팀 최OO
안건: 블랙프라이데이 기획전 준비
- 기획전 기간은 2026-11-20부터 2026-11-30까지로 한다.
- 결정: 블랙프라이데이 배너 시안 마감일은 2026-11-07이다.
- 할인 쿠폰은 최대 20%로 한다.
""",
    "회의록_물류_1020.md": """# Z몰 물류 회의록 (2026-10-20)
참석: 물류팀 이OO, 정OO, CS팀 한OO
안건: 반품 처리 지연 대응
- 10월 반품 처리 평균 소요일은 4.2일이다.
- 결정: 물류센터 반품 처리 개선안 마감일은 2026-11-28로 한다.
- 반품 전담 인력 2명을 11월에 추가 배치한다.
""",
    "매출보고서_3분기.txt": """Z몰 2026년 3분기 매출 보고서 (재무팀, 2026-10-10 작성)
3분기 전체 매출은 9,870백만 원으로 전년 같은 분기보다 12% 늘었다.
지역별 요약
서울 3분기 매출은 4,820백만 원이다.
부산 3분기 매출은 1,240백만 원이다.
대구 3분기 매출은 650백만 원이다.
(지역별 수치는 10월 10일 기준 잠정치)
""",
    "지역매출_3분기.csv": """# 출처: 재무팀 ERP 확정치 (2026-10-24 추출), 단위 백만 원
지역,분기,매출
서울,3분기,4820
부산,3분기,1240
대구,3분기,610
""",
    "프로젝트회고_새벽배송.md": """# 새벽배송 시범사업 회고 (2026-09, 신사업팀)
- 기간: 2026-06-01 ~ 2026-08-31, 대상 지역: 서울 강남·서초
- 결과: 시범사업은 8월 말에 중단했다.
- 중단 이유 1: 건당 배송 원가가 4,300원으로 목표 3,000원을 넘었다.
- 중단 이유 2: 주문 마감(밤 11시) 이후 취소율이 9%로 높았다.
- 다음 단계: 배송 파트너 재선정 후 2027년 재검토.
""",
}


def make_pptx(path: Path) -> None:
    from pptx import Presentation                # markitdown[pptx]와 함께 설치된다
    prs = Presentation()
    for title, body in [
        ("Z몰 2027 사업 전략 발표", "경영기획팀\n2027년 거래액 목표는 1조 2,000억 원이다."),
        ("핵심 과제", "셀러 수수료 체계 개편\n자체 브랜드(PB) 확대\n앱 개인화 추천"),
        ("PB 확대 계획", "PB 상품 비중을 8%에서 15%로 올린다."),
    ]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    prs.save(path)


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook                # markitdown[xlsx]와 함께 설치된다
    wb = Workbook()
    ws = wb.active
    ws.title = "주차별"
    for row in [["주차", "반품건수", "평균처리일"], ["10월 1주", 310, 3.8], ["10월 2주", 355, 4.1],
                ["10월 3주", 402, 4.5], ["10월 4주", 371, 4.3]]:
        ws.append(row)
    ws = wb.create_sheet("사유별")
    for row in [["사유", "비율"], ["단순 변심", "42%"], ["상품 불량", "27%"], ["오배송", "18%"], ["기타", "13%"]]:
        ws.append(row)
    wb.save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        (OUT / name).write_text(text, encoding="utf-8")
    make_pptx(OUT / "전략발표_2027.pptx")
    make_xlsx(OUT / "반품현황_10월.xlsx")
    print(f"{len(FILES) + 2}개 파일 → {OUT}")


if __name__ == "__main__":
    main()
