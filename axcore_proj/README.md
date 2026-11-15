# axcore · Hands-on AX 공통 골격

『Hands-on AX』 14장에서 만드는 공통 실행 골격과 새 실습 템플릿이에요.

- **사용법과 설계 설명:** `docs/GUIDE.md`

```bash
pip install -r requirements.txt
python -m pytest -q              # 전체 테스트 (골격 변경 감지 포함)
python -m jobs._template.run     # 새 실습 템플릿(비품 신청) 시연, API 키 불필요
cd .. && python live_check.py --repeat 3   # 실제 모델로 모든 job 평가 (.env 키 필요)
```

| 경로 | 내용 | 고쳐도 되나 |
|---|---|---|
| `axcore/` | 공통 골격 | 그대로 쓰는 것을 권장. 고쳤다면 `python -m tools.update_lock`으로 잠금 파일 갱신 |
| `axtools/ocr.py` | 공용 OCR 어댑터(이미지 입력을 쓰는 실습용) | 위와 같음 |
| `jobs/_template/` | 새 실습의 출발점(비품 신청 확인) | 직접 고치지 말고 복사해서 사용 |
| `jobs/<새 job>/` | 직접 만드는 실습 | 자유 |
| `samples/` | 실습 장 job의 합성 데이터(뼈대에는 비어 있음) | 새 job은 `samples/<job>/` 추가 |
