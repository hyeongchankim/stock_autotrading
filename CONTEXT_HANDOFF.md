# 컨텍스트 핸드오프 요약 (stock_autotrading)

이 문서는 새 대화/세션에 그대로 붙여넣어서 지금까지의 작업을 이어갈 수 있도록 정리한 것이다.
자세한 수치와 표는 `README.md`에 전부 남아있고, 여긴 "무엇을 왜 결정했는지"의 압축본이다.

## 프로젝트 개요

- 목적: 100만원 시드로 국내주식 자동매매를 하기 위한 파이썬 시스템. 처음엔 스켈레톤(모의)으로
  시작해서, 이번 세션에서 실제 한국투자증권(KIS) API 연동까지 진행함.
- 저장소: https://github.com/hyeongchankim/stock_autotrading (main 브랜치)
- 실행: `python main.py --mode paper` (1회 실행) / `python main.py --mode backtest` (백테스트)
- 이 PC 특이사항: Windows 계정명에 한글(김형찬)이 포함되어 있어서 yfinance/pykrx가 SSL 인증서
  경로를 못 찾는 문제가 있음 → `SSL_CERT_FILE`/`CURL_CA_BUNDLE` 환경변수를 `C:\ca-certs\cacert.pem`
  으로 지정해야 정상 동작 (README "트러블슈팅" 섹션 참고).

## 지금까지 채택된 최종 설정 (config.yaml)

| 항목 | 값 | 이유 (요약) |
|---|---|---|
| `seed_capital` | 1,000,000원 | 사용자 시드 |
| `risk.stop_loss_pct` | 9% | 10%→9% 축소가 백테스트로 검증한 최적점 (8%는 MDD 악화) |
| `risk.take_profit_pct` | 7% | 즉시청산이 트레일링 스탑보다 나음 (자본회전율이 더 중요했음) |
| `risk.position_size_pct` | 30% | 20~80% 스윕 검증 결과 최적점 |
| `strategies.trend_following.whale_flow` | **활성 (단독)** | 외국인+기관 5일 순매수/거래대금 비율 ±5%. 이평선/돈치안보다 수익률·MDD·승률 다 나음 |
| `strategies.trend_following.ma_cross/bollinger/donchian/macd` | 전부 비활성 | whale_flow로 대체됨 |
| `strategies.mean_reversion.rsi`, `volatility_breakout` | 활성 | 유지 |
| `regime_filter` | 활성 (ADX 14, 25/20) | 추세/횡보 구간 나눠서 맞는 전략만 진입 허용 |
| `volume_filter` | 활성 (20일 평균 대비 1.5배) | 순수 개선 확인됨 |
| `hybrid.enabled` | true, `strategy_allocation_pct: 0.5` | 시드를 전략 50% + Buy&Hold 50%로 분리. Buy&Hold 단독한테 완패한 뒤 도입, MDD 최저점이 50% |
| `backtest.history_days` | 1700 (6.75년) | 2020 코로나 + 2022 하락장 포함하려고 늘림 |
| `broker.provider` | **kis** (이번 세션에서 mock→kis로 전환) | 실제 KIS 계좌 연동 완료 |
| `broker.kis.env` | **demo** (모의투자) | 실전(`real`)은 충분히 검증 후 사용자가 직접 전환해야 함 |

## 핵심 여정 (연대기 요약)

1. **전략 스켈레톤 구축**: 이평선크로스/볼린저/RSI/변동성돌파 4개 전략, 리스크관리, 브로커/데이터
   추상화 인터페이스로 시작.
2. **손익비 튜닝**: 손절/익절 비율, 비중 스윕 → 현재 값 확정.
3. **종목 교체**: SK하이닉스(226만원, 못 삼)·현대차(46.5만원, 못 삼) → 리노공업·만도로 교체 (100만원
   시드에 실제로 살 수 있는 가격대로).
4. **승률 극대화 시도 → 기각**: 익절을 0.1~0.5%까지 줄여봐도 승률 72%가 한계, 그마저도 수익률 마이너스.
5. **돈치안 채널 추가 → 볼린저 대체**: 새 전략은 "추가"보다 "교체"가 항상 나음(신호 과다 방지).
6. **MACD/모멘텀랭킹/거래량필터 검증**: 모멘텀랭킹 전부 기각, 거래량필터만 순수 개선으로 채택.
7. **외부 저장소(KIS 공식 샘플) 비교 → Buy&Hold 벤치마크 부재 발견**: 지금까지 전략끼리만 비교했지
   "그냥 사서 들고있기"와는 비교한 적이 없었음. 실제로 붙여보니 **전략이 Buy&Hold에 완패**
   (5년 기준 -13.8%p, 코로나 포함 6.75년 기준 -105%p).
8. **하이브리드 구조 도입**: 시드를 전략/Buy&Hold 두 슬리브로 분리해서 리스크 분산 효과 확인, 50%가
   MDD 최저점이라 채택.
9. **고래 추적(외국인/기관 순매수) 전략 추가**: KRX 정보데이터시스템(`data.krx.co.kr`) 계정 필요.
   실제 데이터로 검증한 결과 이평선/돈치안보다 확실히 나아서 **단독 채택** (추세추종 카테고리를
   whale_flow 하나로 완전 교체).
10. **KIS 실브로커 연동** (이번 세션): 공식 샘플 저장소에서 API 스펙 확인 (tr_id 코드가 파일마다
    다르게 적혀있는 걸 발견해서 더 신뢰할 수 있는 쪽 채택) → `broker/kis_broker.py`,
    `data/kis_data_feed.py`, `broker/kis_auth.py` 구현 → 실제 모의투자 계좌로 인증/잔고/시세/일봉
    조회 전부 확인 완료.
11. **KIS 모의투자 서버 불안정 발견 → 재시도 로직 추가**: 잔고조회가 거의 절반 확률로 일시적 500
    에러를 냄. GET은 안전하게 재시도, 주문(POST)은 "서버가 실제로 주문을 받았는지 알 수 없는
    5xx"는 재시도하지 않고 바로 실패 처리 (중복주문 방지).
12. **실전/모의 계좌번호 분리 버그 수정**: 처음엔 `KIS_ACCOUNT_NO` 하나로 실전/모의를 같이
    쓰려고 했는데, KIS는 이 둘이 아예 다른 계좌번호다 → `KIS_PAPER_ACCOUNT_NO` 별도 환경변수로 분리.

## 환경변수 (전부 사용자 PC에만 저장, Claude는 값을 모름)

```
KRX_ID / KRX_PW                                       - data.krx.co.kr (고래추적용)
KIS_APP_KEY / KIS_APP_SECRET                          - KIS 실전투자 앱키/시크릿
KIS_ACCOUNT_NO / KIS_ACCOUNT_PRODUCT_CD               - KIS 실전투자 계좌번호
KIS_PAPER_APP_KEY / KIS_PAPER_APP_SECRET              - KIS 모의투자 앱키/시크릿
KIS_PAPER_ACCOUNT_NO / KIS_PAPER_ACCOUNT_PRODUCT_CD   - KIS 모의투자 계좌번호
```
전부 설정 완료된 상태 (이번 세션에 확인함). Windows 환경변수 변경 후에는 **터미널/앱을
재시작해야** 새 프로세스가 값을 인식한다 (이 세션에서 두 번 겪었던 이슈).

## 지금까지 검증된 것 vs 안 된 것

✅ 검증됨:
- 백테스트 로직 전체 (49개 유닛테스트 통과)
- KIS 모의투자 인증/잔고조회/시세조회/일봉조회 (실제 API 호출로 확인)
- 재시도 로직이 실제 KIS 서버 불안정 상황에서 작동하는 것 확인
- 전체 파이프라인(신호생성→리스크관리→주문실행 준비까지) 1회 정상 실행

❌ 아직 안 됨 (다음 단계 후보):
- **실제 매수/매도 주문 체결** (지금까지 실행에서 신호가 안 나서 주문까지는 못 감 - 다음 논의 지점)
- 상태 영속성 없음 (매번 새로 실행하면 RiskManager 일일손실추적, 하이브리드 Buy&Hold 슬리브 상태
  전부 초기화됨) - 장중 상시 실행하려면 스케줄러 + 상태저장 로직 필요
- 호가단위(tick size)/상한가·하한가/부분체결/슬리피지 미반영
- tr_id 코드(TTTC0802U 등)를 KIS 최신 공식 문서로 재확인 안 함 (공식 샘플 저장소 안에서도
  파일마다 다르게 적혀 있던 걸 발견해서 더 신뢰되는 쪽으로 택했지만, 최종 확인 권장)
- `broker.provider: kis`가 지금 config.yaml에 켜진 상태 - 안 쓸 때는 `mock`으로 되돌리는 게 안전

## Git 상태

- 마지막 푸시된 커밋: `228c0f9` (고래추적 전략 추가)
- **이번 세션 작업(KIS 연동)은 아직 커밋/푸시 안 됨** - 변경파일: `.gitignore`, `README.md`,
  `config.yaml`, `main.py`, `requirements.txt` + 신규 `broker/kis_auth.py`, `broker/kis_broker.py`,
  `data/kis_data_feed.py`, `tests/test_kis_broker.py`
- 커밋 전 항상 확인: 앱키/시크릿/계좌번호가 코드에 하드코딩 안 됐는지 (지금까지는 전부 환경변수로만
  관리됨, 문제 없음)

## 다음에 이어서 할 만한 것

1. 실제 매수/매도 주문 체결까지 모의투자로 확인 (신호가 날 때까지 기다리거나, 강제로 한 번 테스트)
2. 이번 세션 변경사항 커밋/푸시
3. 상태 영속성 구현 (장중 상시 실행 대비)
4. 실전 전환 전 최종 체크리스트 재확인 (README "실제 증권사 API 연동" 섹션의 "아직 안 된 것" 참고)
