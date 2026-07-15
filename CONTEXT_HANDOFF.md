# 컨텍스트 핸드오프 요약 (stock_autotrading)

이 문서는 새 대화/세션에 그대로 붙여넣어서 지금까지의 작업을 이어갈 수 있도록 정리한 것이다.
자세한 수치와 표는 `README.md`에 전부 남아있고, 여긴 "무엇을 왜 결정했는지"의 압축본이다.
(2026-07-15 세션 종료 시점에 정리·압축됨 - 그 이전 버전의 상세 항목별 기록은 git 이력의
이 파일 자체에서 찾을 수 있음.)

## 프로젝트 개요

- 목적: 100만원 시드로 국내주식 자동매매를 하는 파이썬 시스템. 백테스트 스켈레톤에서 시작해
  실제 한국투자증권(KIS) API 연동, 상태 영속성, Windows 스케줄러 무인 실행, 텔레그램 알림까지
  갖춘 상태.
- 저장소: https://github.com/hyeongchankim/stock_autotrading (main 브랜치)
- 실행: `python main.py --mode paper` (1회 실행) / `python main.py --mode backtest` (백테스트)
- 이 PC 특이사항: Windows 계정명에 한글(김형찬)이 포함되어 있어서 yfinance/pykrx/requests가
  SSL 인증서 경로를 못 찾는 문제가 있음 → `SSL_CERT_FILE`/`CURL_CA_BUNDLE` 환경변수를
  `C:\ca-certs\cacert.pem`으로 지정해야 정상 동작 (README "트러블슈팅" 섹션 참고).
- **장중 자동 실행 중**: Windows 작업 스케줄러 `StockAutoTradingPaper`가 평일 09:00~15:30
  15분 간격으로 `run_paper_cycle.bat` → `python main.py --mode paper`를 실행함 (git엔 등록
  안 됨, 로컬 머신 상태 - 재현 커맨드는 아래 "핵심 여정" 참고).

## 지금까지 채택된 최종 설정 (config.yaml)

| 항목 | 값 | 이유 (요약) |
|---|---|---|
| `seed_capital` | 1,000,000원 | 사용자 시드 |
| `risk.stop_loss_pct` | 9% | 백테스트로 검증한 최적점 |
| `risk.take_profit_pct` | 7% | 즉시청산이 트레일링 스탑보다 나음 |
| `risk.position_size_pct` | 30% | 스윕 검증 결과 최적점 |
| `risk.daily_max_loss_pct` | 30% | 실제 손실(-172,000원)로 서킷브레이커 작동 검증됨 |
| `strategies.trend_following.whale_flow` | **활성 (단독)** | 외국인+기관 순매수 추종. 이평선/돈치안보다 전부 나음 |
| 그 외 trend_following (ma_cross/bollinger/donchian/macd) | 전부 비활성 | whale_flow로 대체됨 |
| `strategies.mean_reversion.rsi`, `volatility_breakout` | 활성 | 유지 |
| `regime_filter` | 활성 (ADX 14, 25/20) | 추세/횡보 국면별로 맞는 전략만 진입 허용 |
| `volume_filter` | 활성 (20일 평균 대비 1.5배) | 순수 개선 확인됨 |
| `hybrid.enabled` | true, `strategy_allocation_pct: 0.5` | 시드를 전략 50%+Buy&Hold 50%로 분리, MDD 최저점 |
| `costs.commission_pct/sell_tax_pct` | 0.015% / 0.18% | 실제 거래비용 반영 |
| `costs.slippage_pct` | **0 (옵트인)** | 기본 미반영 - 켜면 튜닝된 결론들이 바뀔 수 있어 사용자가 직접 켜야 함 |
| `backtest.history_days` | 1700 (6.75년) | 2020 코로나 + 2022 하락장 포함 |
| `broker.provider` | **kis** | 실제 KIS 계좌 연동 완료 |
| `broker.kis.env` | **demo** (모의투자) | 실전(`real`)은 사용자가 직접 전환해야 함 - README "실전 전환 가이드" 참고 |

## 핵심 여정 (연대기 요약)

1. **전략 스켈레톤 구축 → 손익비 튜닝 → 종목 교체**: SK하이닉스/현대차(너무 비쌈) → 리노공업/
   만도로 교체. 승률 극대화(익절 축소)는 시도했지만 기각 (수익률 마이너스).
2. **전략 구성 실험**: 돈치안 채널이 볼린저 대체 (새 전략은 "추가"보다 "교체"가 항상 나음),
   MACD/모멘텀랭킹 기각, 거래량필터만 순수 개선으로 채택.
3. **Buy & Hold 벤치마크 도입 → 전략이 완패한 걸 발견**: 그동안 전략끼리만 비교했지 "그냥 사서
   들고있기"와 비교한 적이 없었음. 실제 비교해보니 대폭 완패 → **하이브리드 구조**(시드를 전략/
   Buy&Hold 두 슬리브로 분리) 도입, 50%가 MDD 최저점.
4. **고래 추적(whale_flow) 전략 추가**: 외국인+기관 순매수 추종, KRX 정보데이터시스템 계정 필요.
   이평선/돈치안보다 확실히 나아서 추세추종 카테고리를 이걸로 완전 교체.
5. **KIS 실브로커 연동**: `broker/kis_broker.py`, `data/kis_data_feed.py`, `broker/kis_auth.py`
   구현. 모의투자 서버가 불안정해서(GET 요청 약 50% 확률로 일시적 500) 재시도 로직 추가 -
   단 주문(POST)은 "서버가 실제로 받았는지 알 수 없는 5xx"를 재시도 안 함(중복주문 방지).
   실전/모의 계좌번호를 완전히 분리된 환경변수로 관리(`KIS_ACCOUNT_NO` vs
   `KIS_PAPER_ACCOUNT_NO`) - 처음엔 하나로 합쳐뒀다가 버그로 발견하고 분리함.
6. **상태 영속성 구현**: `utils/state_store.py`(`state.json`, gitignore됨)로 `RiskManager`
   일일 손실 카운터, 하이브리드 Buy&Hold 슬리브(`portfolio/buy_and_hold.py`), `KisBroker`
   현금원장(`kis_cash_ledger`)이 프로세스 재실행 간에 유지되도록 함. **Buy&Hold 슬리브는
   paper/live에서도 절대 실제 주문을 넣지 않는 로컬 시뮬레이션**(KIS 단일 계좌가 슬리브별로
   현금/포지션을 못 나누기 때문) - 이건 사용자와 논의 후 "확정된 설계"로 결정됨(보류 아님).
7. **KisBroker 포지션 사이징 버그 발견·수정**: 첫 실계좌 실행에서 `KisBroker`가 config의
   `seed_capital` 대신 **계좌의 실제 잔고**를 그대로 써서, 의도한 예산의 6배짜리 주문이 나갈
   뻔했음 → `seed_capital` 스코프의 로컬 현금원장을 추가해서 해결.
8. **Windows 작업 스케줄러 등록**: `run_paper_cycle.bat` + `schtasks /create`로 평일
   09:00~15:30 15분 간격 자동 실행 등록 (재현: `schtasks /create /tn "StockAutoTradingPaper"
   /tr "<repo>\run_paper_cycle.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:00 /ri 15
   /du 06:30 /f` - `/du`는 반드시 `HH:MM` 형식, 콜론 없이 주면 오해석됨). 로그 로테이션도 추가
   (`utils/logger.py`의 `TimedRotatingFileHandler` + `scheduler_stdout_YYYY-MM-DD.log` 날짜별
   파일 + `forfiles` 정리 - `forfiles`가 삭제 대상 없을 때 종료코드를 오염시키는 버그도 같이 고침).
9. **실전 전환 체크리스트 전체 완료**: README "아직 안 된 것" 섹션 기준으로 하나씩 처리함 -
   수수료/세금을 `KisBroker` 로컬 원장에도 반영, 호가단위(`broker/krx_tick.py`, KRX 공개
   테이블로 주문가 반올림 - 상한가/하한가는 이 코드가 항상 실제 관측 시세로만 주문하므로 구조적
   으로 불필요하다고 판단해 의도적으로 생략), 체결확인 API(`KisBroker.get_daily_fills()`),
   daily_max_loss_pct 서킷브레이커를 실제 손실(-172,000원)로 검증, 부분체결 시 `realized_pnl`이
   요청수량이 아니라 **실제 체결수량**(`get_positions()` 디프)을 쓰도록 수정 - 이건 서킷브레이커
   정확도에 실질적으로 영향을 주는 진짜 버그였음. 슬리피지도 옵트인으로 추가(기본값 0, 켜면
   튜닝된 결론이 바뀔 수 있어서).
   - **교훈(재발 가능성 있음)**: tr_id를 여러 독립 출처(KIS 백테스터 참조모듈, 서드파티
     `Soju06/python-kis`)가 일치해도 실제 API로 검증하기 전엔 못 믿는다 - `get_daily_fills()`
     구현 때 두 출처가 똑같이 틀린 tr_id(`TTTC8001R`)를 쓰고 있었고, 실계좌 테스트로만
     발견됨(`TTTC0081R`이 맞음).
   - **발견한 별개 버그**: 시세/차트 조회(`get_current_price`/`get_ohlcv`)는 계좌가 모의투자여도
     **항상 실전 도메인**(`openapi.koreainvestment.com`)으로 나가야 함 - 모의투자 도메인에서
     500 에러가 훨씬 잦았음(실측 12.5% vs 0%). `KisSession.market_data_session()`으로 수정.
     이 때문에 모의투자만 쓰더라도 이제 실전용 앱키/시크릿이 필요해짐.
10. **실전 전환 가이드 문서화**: README에 9단계 가이드 저장 (스케줄러 끄기 → 시드/계좌 정합성
    확인 → **`state.json` 정리 필수**(모의투자로 누적된 로컬 원장이 실전 계좌 실제 잔고와
    안 맞게 됨) → env 변경 → 수동 1회 실행 확인 → 며칠 수동 운영 → 스케줄러 재활성화는 맨 마지막
    → 지속 모니터링 → 되돌리기 계획). **Claude는 전환 자체나 실주문 실행을 대신 하지 않는다는
    경계가 명시적으로 합의됨**.
11. **텔레그램 알림 구현 및 실제 테스트**: `utils/notify.py` - 진입/청산 체결(`[demo]`/`[real]`/
    `[mock]` 라벨), **청산 실패는 항상 통지**(포지션이 관리 안 된 채 열려있게 되는 가장 위험한
    상황 - 이 과정에서 원래 청산 실패 시 로그조차 안 남기던 버그도 같이 고침), 서킷브레이커
    최초 발동, 사이클 크래시를 전송. 크리덴셜 없으면 조용히 no-op, 실패해도 예외 안 던짐(알림이
    매매를 막으면 안 되므로).
    - **실제 테스트에서 발견한 이슈**: 이 PC의 평소 네트워크가 `api.telegram.org`를 SNI 기반으로
      차단하고 있었음 (TCP는 연결되는데 TLS 핸드셰이크에서 멈춤 - 코드 버그 아님). 모바일
      핫스팟으로 전환해서 실제 발송·수신까지 확인 완료. **미해결**: 평소 네트워크에서는 여전히
      안 될 가능성이 높음 - 알림 실패가 조용히 넘어가는 설계라 사용자가 직접 재확인해야 함
      (README "알림" 섹션에 경고 있음).

## 환경변수 (전부 사용자 PC에만 저장, Claude는 값을 모름)

```
KRX_ID / KRX_PW                                       - data.krx.co.kr (고래추적용)
KIS_APP_KEY / KIS_APP_SECRET                          - KIS 실전투자 앱키/시크릿
KIS_ACCOUNT_NO / KIS_ACCOUNT_PRODUCT_CD               - KIS 실전투자 계좌번호
KIS_PAPER_APP_KEY / KIS_PAPER_APP_SECRET              - KIS 모의투자 앱키/시크릿
KIS_PAPER_ACCOUNT_NO / KIS_PAPER_ACCOUNT_PRODUCT_CD   - KIS 모의투자 계좌번호
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID                 - 알림용 (선택 기능)
```
전부 설정 완료됨. **`KIS_APP_KEY`/`KIS_APP_SECRET`(실전용)는 모의투자만 쓰더라도 필수**
(시세/차트 조회가 항상 실전 도메인을 쓰기 때문 - 위 "핵심 여정" 9번 참고). Windows 환경변수
변경 후에는 터미널/앱을 재시작해야 새 프로세스가 값을 인식한다.

## 지금까지 검증된 것 vs 안 된 것

✅ 검증됨:
- 백테스트 로직 전체 (103개 유닛테스트 통과)
- KIS 모의투자 인증/잔고조회/시세조회/일봉조회/매수·매도 주문(POST) 전부 실계좌 호출로 확인
- 재시도 로직이 실제 KIS 서버 불안정 상황에서 작동
- 포지션 사이징이 config의 seed_capital 기준으로 정확히 계산됨 (계좌 실제 잔고 아님)
- 상태 영속성 (일일손실카운터/Buy&Hold슬리브/KIS현금원장) - 반복 실행해도 올바르게 이어짐
- 장중 반복 실행 스케줄러 등록·검증 완료 (평일 09:00~15:30, 15분 간격)
- 수수료/세금/호가단위 반영, 로그 로테이션, 부분체결 시 realized_pnl 정확도
- daily_max_loss_pct 서킷브레이커가 실제 손실(-172,000원)로 정상 작동
- 주문 관련 tr_id 3개 독립 출처 + 실계좌로 재검증 완료
- 텔레그램 알림 구현 및 실제 발송·수신 확인 (단, 모바일 핫스팟에서만 - 아래 참고)

❌ 아직 안 됨:
- **엔진의 자연 신호를 통한 실주문 체결 미확인** - 지금까지 실행에서 신호가 자연 발생한 적이
  없어서(일봉 기반 전략이라 하루 안에서는 신호가 잘 안 바뀜), 신호생성→리스크관리→주문실행
  전체 파이프라인이 실제로 진입까지 가는 걸 본 적 없음. `place_order()` 직접 호출로 주문 경로
  자체는 검증됨. 스케줄러가 계속 도니 며칠~몇 주 지켜보면 자연스럽게 확인될 것 - 능동적으로
  더 할 일 없음, 관찰 대기.
- **텔레그램 알림이 스케줄러의 평소 네트워크에서도 되는지 미확인** - 모바일 핫스팟 테스트만
  성공함, 평소 네트워크는 `api.telegram.org` SNI 차단 의심 상황에서 재확인 안 됨. 안 되면
  VPN/네트워크 상시 전환/다른 알림 채널(이메일, Windows 토스트) 병행 검토.
- `broker.provider: kis`가 지금 config.yaml에 켜진 상태 - 안 쓸 때는 `mock`으로 되돌리는 게 안전
- 부분체결/체결확인(`get_daily_fills()`)은 엔진 로직에 연결 안 함 - 포지션 디프 방식으로 더
  가볍게 실제 위험(realized_pnl 정확도)을 해결해서 필요성이 낮아짐, 의도적 보류
- 슬리피지는 기본값 0(옵트인) - 켜서 재백테스트하고 싶으면 사용자가 원할 때

**실전 전환 체크리스트(README "아직 안 된 것" 섹션과 1:1 대응)는 완료됨.**

## Git 상태

- 전부 커밋/푸시됨, 최신 커밋은 `git log --oneline -1`로 확인. `state.json`과
  `StockAutoTradingPaper` 작업 스케줄러 등록은 로컬 머신 상태라 git에는 없음 (재현 커맨드는
  위 "핵심 여정" 8번 참고).
- 커밋 전 항상 확인: 앱키/시크릿/계좌번호가 코드에 하드코딩 안 됐는지 (지금까지는 전부
  환경변수로만 관리됨, 문제 없음). `state.json`은 gitignore 되어 있어 커밋 대상 아님.
- **작업 순서 규칙**: 사용자가 명시적으로 요청함 - CONTEXT_HANDOFF.md 갱신은 항상 코드 변경을
  커밋/푸시한 *다음*에, 별도 커밋으로 진행할 것 (같은 커밋에 묶지 말 것).

## 다음에 이어서 할 만한 것

1. **텔레그램 알림이 스케줄러의 평소 네트워크에서도 정상 발송되는지 확인** - 안 되면 VPN/
   네트워크 상시 전환/다른 알림 채널 병행 검토
2. 스케줄러가 며칠 돌면서 자연 신호로 실주문까지 가는지 관찰 (로그: `logs/trading.log`,
   `logs/scheduler_stdout_YYYY-MM-DD.log`) - 능동적으로 더 할 건 없고 시간이 필요한 항목
3. (선택) `costs.slippage_pct`를 실제 값으로 켜고 재백테스트해서 손절/비중/하이브리드 비율
   결론이 슬리피지 하에서도 여전히 유효한지 확인
4. 정말 실전(`broker.kis.env: real`) 전환을 고려한다면, README "실전 전환 가이드"를 따라
   사용자 본인이 직접 진행 (Claude는 전환/실주문을 대신 실행하지 않음 - 명시적으로 합의된 경계)
