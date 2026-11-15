"""Z몰 데이터분석팀 매출 분석 기준. 실습용 가상 기준이며 버전을 고정한다."""

VERSION = "2026.10"

FLAT_PCT = 1.0               # 매출 변화가 1% 미만이면 변화 없음(flat)으로 본다
AUTO_SHARE = 0.5             # 주원인 요인의 기여율이 50% 이상이면 원인이 분명하다(자동 저장 기준)
CONCENTRATION_SHARE = 0.5    # 매출 변화의 50% 이상이 한 채널에서 나오고
CONCENTRATION_LIFT = 1.5     # 그 비율이 채널의 매출 비중보다 1.5배 이상이면 '변화가 몰린 채널'

SQL_MAX_ROWS = 200           # run_sql이 돌려주는 최대 행 수
SQL_MAX_CHARS = 20_000       # run_sql이 돌려주는 최대 글자 수
SQL_TIMEOUT_SEC = 5          # run_sql 한 번의 실행 시간 상한
SQL_MAX_CALLS = 25           # 판단 한 번에 run_sql을 실행하는 횟수 상한. 넘으면 결과를 제출하라고 돌려준다
SQL_HARD_CALLS = 30          # 그래도 계속 부르면 그 판단을 오류로 끝낸다
