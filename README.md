# SCCB Automation (Complete 필수필드 자동 세팅)

## 핵심
- End Date: **API 전송 형식은 `YYYY-MM-DD`로 고정** (UI 힌트가 yyyy/MM/dd여도 REST는 '-'가 안전)
- SCCB 상태: 'SCCB 완료'
- 해결책: '완료'

## 실행
```bash
pip install -r requirements.txt
python -m sccb_app.app
```
