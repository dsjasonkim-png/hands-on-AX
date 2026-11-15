---
name: zmall-orders-db
description: Z몰 주문 DB(SQLite)의 테이블 구조와 테이블 사이의 관계. SQL을 짜기 전에 읽는다.
---

# Z몰 주문 DB

시각은 모두 `YYYY-MM-DD HH:MM:SS` 형식의 문자열이고 한국 시간이다.

## 테이블

### sessions: 고객 여정

한 행이 클릭 한 번으로 시작된 세션 하나다.

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| session_id | TEXT, 기본 키 | 세션 ID |
| started_at | TEXT | 세션 시작 시각(클릭 시각) |
| channel | TEXT | 접속 채널. app, web |
| source | TEXT | 유입 경로. search_ad, social, push, direct |
| category | TEXT | 처음 본 상품의 카테고리. products.category와 같은 값 |
| viewed_product | INTEGER | 상품을 조회했으면 1, 아니면 0 |
| added_to_cart | INTEGER | 장바구니에 담았으면 1, 아니면 0 |
| started_checkout | INTEGER | 주문서에 들어갔으면 1, 아니면 0 |
| order_id | TEXT, NULL 가능 | 주문으로 이어졌으면 그 주문 ID, 아니면 NULL |

### orders: 주문

한 행이 주문 시도 하나다. 결제에 실패한 주문도 있다.

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| order_id | TEXT, 기본 키 | 주문 ID |
| customer_id | TEXT | 고객 ID |
| ordered_at | TEXT | 주문 시각 |
| channel | TEXT | 주문 채널. app, web |

### order_items: 주문 상품 줄

한 행이 주문에 담긴 상품 한 줄이다.

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| order_id | TEXT | 주문 ID |
| product_id | TEXT | 상품 ID |
| qty | INTEGER | 수량 |
| unit_price | INTEGER | 단가(원) |
| discount | INTEGER | 그 줄의 할인 금액(원) |

### payments: 결제

한 행이 주문 하나의 결제다.

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| order_id | TEXT, 기본 키 | 주문 ID |
| method | TEXT | 결제수단. card, easy_pay, bank |
| status | TEXT | 결제 결과. approved, failed |
| amount | INTEGER | 그 주문 전체의 결제 금액(원, 할인 차감 후) |
| paid_at | TEXT, NULL 가능 | 결제 시각. 실패하면 NULL |

### shipments: 배송

한 행이 주문 하나의 배송이다.

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| order_id | TEXT, 기본 키 | 주문 ID |
| shipped_at | TEXT | 출고 시각 |
| delivered_at | TEXT | 도착 시각 |

### products: 상품

| 컬럼 | 타입 | 뜻 |
|---|---|---|
| product_id | TEXT, 기본 키 | 상품 ID |
| name | TEXT | 상품명 |
| category | TEXT | 카테고리. 가전, 패션, 식품, 생활, 뷰티 |
| price | INTEGER | 정가(원) |

## 관계

| 연결 | 개수 | 설명 |
|---|---|---|
| sessions.order_id → orders.order_id | 세션 1 : 주문 0 또는 1 | 모든 주문은 세션 하나에서 나온다. 대부분의 세션은 주문으로 이어지지 않는다 |
| orders.order_id → payments.order_id | 주문 1 : 결제 1 | 모든 주문에 결제 행이 있다 |
| orders.order_id → order_items.order_id | 주문 1 : 상품 줄 N | 한 주문에 상품 줄이 여러 개일 수 있다 |
| order_items.product_id → products.product_id | 상품 줄 N : 상품 1 | |
| orders.order_id → shipments.order_id | 주문 1 : 배송 0 또는 1 | 결제가 승인된 주문에만 배송 행이 있다 |
