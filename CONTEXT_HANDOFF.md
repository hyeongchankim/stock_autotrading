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
13. **상태 영속성 구현** (이번 세션): `python main.py --mode paper`를 반복 실행해도(스케줄러 등)
    이전 실행의 상태가 이어지도록 `utils/state_store.py` (JSON 파일, `state.json`, gitignore됨) 추가.
    - `RiskManager.to_dict()/restore()`: 일일 손실 카운터/당일 거래정지 여부를 저장·복원 (매 프로세스
      실행마다 초기화되면 일일 손실한도 서킷브레이커가 사실상 작동 안 했을 것).
    - `portfolio/buy_and_hold.py` (`BuyAndHoldSleeve`): 하이브리드 모드의 Buy&Hold 슬리브를
      paper/live에서도 실제로 추적하도록 구현. `backtest/benchmark.py`와 같은 방식(워치리스트
      동일비중 1회 매수 후 보유)이지만 **실제 브로커에는 절대 주문을 넣지 않는 로컬 시뮬레이션**
      이다 - KIS 계좌는 현금/포지션이 하나로 묶여있어서 두 슬리브를 실제로 분리할 방법이 없기
      때문 (전략 슬리브와 같은 종목을 사면 평단가가 섞이고 재진입도 막힘).
14. **KisBroker 포지션 사이징 버그 발견 및 수정** (이번 세션): 상태 영속성 작업 중 실제 KIS
    모의투자 계좌로 첫 실행해보니 `strategy: cash=10000000`으로 찍힘 - config의 seed_capital(100만원)
    이 아니라 **KIS 모의투자 계좌의 실제 기본 잔고(1000만원)**를 그대로 쓰고 있었음.
    `RiskManager.calc_position_size()`는 이 값 기준으로 종목당 30%를 배분하므로, 의도한 예산(하이브리드
    전략 슬리브 50만원)의 6배(300만원)짜리 주문이 나갈 뻔한 것을 발견 → `KisBroker`에 `seed_capital`
    스코프의 로컬 현금 원장(ledger)을 추가해서 `get_cash_balance()/get_total_equity()`가 실제 계좌
    잔고 대신 이 로컬 원장을 쓰도록 수정 (포지션/평단가는 여전히 실제 계좌에서 읽음 - Buy&Hold
    슬리브가 실주문을 안 넣으므로 실제 계좌 포지션 = 항상 전략 슬리브 포지션이라는 전제가 성립).
    이 원장도 `state.json`에 영속화됨 (`kis_cash_ledger`). 수정 후 재확인: `strategy: cash=500000`으로
    정상 표시됨.
15. **실제 주문(POST) 체결 왕복 테스트** (이번 세션): 자연 신호가 안 나서, 임시 스크립트로
    `KisBroker.place_order()`를 직접 호출해 015760.KS(한국전력) 1주를 매수→즉시매도로 검증.
    매수/매도 둘 다 `filled=True`로 정상 체결 확인 (order_no 발급됨), 계좌는 원상복구(포지션 0)
    시켜둠. tr_id 코드(매수/매도 둘 다)가 실제로 맞다는 것이 이제 실계좌 호출로 확인됨.
16. **장중 반복 실행 스케줄러 등록** (이번 세션): 상태 영속성이 준비됐으니 실제로 반복 실행시켜주는
    Windows 작업 스케줄러 등록. [run_paper_cycle.bat](run_paper_cycle.bat) (SSL 인증서 env var 설정
    후 `python main.py --mode paper` 실행, stdout을 `logs/scheduler_stdout.log`로 리다이렉트 - Task
    Scheduler는 로그인 세션의 임시 export를 상속 안 받으므로 배치 파일 안에서 직접 설정)을
    `schtasks /create`로 등록 (PowerShell `Register-ScheduledTask`의 `.Repetition.Interval/.Duration`
    직접 대입 방식은 이 PowerShell 5.1에서 "property not found" 에러로 실패함 - CLI로 우회).
    - 작업 이름: `StockAutoTradingPaper` (git에는 등록 안 됨, 로컬 머신 상태) - 등록 커맨드:
      `schtasks /create /tn "StockAutoTradingPaper" /tr "<repo>\run_paper_cycle.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:00 /ri 15 /du 06:30 /f`
      (`/du` 형식은 반드시 `HH:MM` - `0630`처럼 콜론 없이 주면 "26일 6시간"으로 오해석됨, 직접 겪음)
    - 평일 09:00~15:30, 15분 간격. 등록 직후 자동 발동 1회 + 수동 트리거(`schtasks /run`) 1회 모두
      `Last Result: 0`으로 성공 확인, 로그에 `cycle complete` 정상 기록됨.
    - 제약: 로그인 세션에서만 실행됨(로그아웃 시 중단), 배터리 구동 중이면 기본적으로 건너뜀
      (`DisallowStartIfOnBatteries`), `MultipleInstancesPolicy=IgnoreNew`로 중복 실행은 자동 방지.
    - **주의**: `config.yaml`의 `broker.kis.env`를 나중에 `real`로 바꾸면 이 스케줄러가 그대로
      무인 실거래를 반복 실행하게 됨 - 전환 전 `schtasks /delete /tn "StockAutoTradingPaper" /f`로
      내리거나 신중히 재검토할 것.
17. **실전 전환 체크리스트 점검 시작** (이번 세션): README/핸드오프의 "아직 안 된 것"을 최신화하고
    사용자와 하나씩 정리. 가장 먼저 짚은 건 **하이브리드 Buy&Hold 슬리브가 실전에서도 실제 매수가
    아니라는 점** (13번 항목 참고) - 사용자에게 명시적으로 알렸고, 지금은 보류하고 다른 항목부터
    처리하기로 함(아직 미해결로 남아있음, 밑에 표시).
18. **KisBroker 로컬 원장에 수수료/세금 반영** (이번 세션): 체크리스트에서 두 번째로 처리한 항목.
    `KisBroker`가 `commission_pct`/`sell_tax_pct`(생성자 인자, `main.py`가 config의 `costs` 섹션에서
    전달)를 `MockBroker`/`Portfolio`와 같은 방식으로 로컬 현금원장(`kis_cash_ledger`)과
    `realized_pnl` 계산에 적용하도록 수정 (`KisBroker._effective_price()`). **실제 KIS로 보내는
    주문가(`ORD_UNPR`)는 그대로 raw fill_price** - 이 조정은 어디까지나 로컬 장부용, KIS 실제 계좌
    수수료는 KIS가 자체적으로 처리함. 테스트 3개 추가 (72개 전체 통과).
19. **호가단위(tick size) 반영** (이번 세션): 체크리스트 세 번째 항목. `broker/krx_tick.py`에 KRX의
    공개 고정 호가단위 테이블(2023년 코스피/코스닥 통합 기준) 구현 → `KisBroker.place_order()`가
    KIS로 보내기 전에 주문가를 가장 가까운 유효 틱으로 반올림하도록 수정 (지표 계산을 거친 가격은
    대개 틱에 안 맞아서, 안 맞으면 KIS가 그냥 주문 거부함).
    **상한가/하한가 클리핑은 의도적으로 구현 안 함** - 이 코드베이스가 브로커에 보내는 주문가는
    전부 실제 관측된 시세(체결된 종가/현재가)에서만 나오고, 합성 목표가(`avg_price * 1.05` 같은
    직접 계산)를 주문가로 쓰는 로직이 없음 → 구조적으로 항상 그날의 ±30% 밴드 안에 있어서, 밴드를
    벗어나는 시나리오 자체가 이 코드엔 없다고 판단함 (혹시 발생해도 KIS 거부 응답이 `msg1`로 이미
    실패 처리됨). 부분체결/슬리피지는 여전히 미반영 (`filled=True`는 "접수됨"이지 "전량 체결"
    보장이 아님 - 실제 체결 확인하려면 체결내역조회 API를 별도로 붙여야 함, 아직 안 함).
    테스트 6개 추가 (78개 전체 통과).
20. **로그 로테이션 추가** (이번 세션): 체크리스트 네 번째 항목. `utils/logger.py`가 `trading.log`에
    `TimedRotatingFileHandler`(자정 회전, 30일 보관) 적용. `run_paper_cycle.bat`가 캡처하는 stdout은
    (pykrx 등 라이브러리의 `print()` 메시지 - 파이썬 logging으로는 못 잡음) 날짜별 파일
    (`scheduler_stdout_YYYY-MM-DD.log`)로 나눠 쓰고, 매 실행마다 `forfiles`로 30일 지난 파일을 정리.
    구현 중 발견한 버그: **`forfiles`는 삭제 대상이 없으면(=거의 매일) 종료코드가 0이 아니게 되는데,
    이게 그대로 배치 스크립트의 종료코드로 전파돼서 파이썬이 실제로는 성공했는데도 Task Scheduler엔
    "실패"로 찍힐 뻔했다** - `set PYEXIT=%ERRORLEVEL%`로 파이썬 종료코드를 forfiles 실행 전에 미리
    저장해뒀다가 `exit /b %PYEXIT%`로 그걸 최종 종료코드로 쓰도록 수정. 실제로 재현해서 고침 전/후
    둘 다 직접 확인함 (`.gitignore`의 `logs/*.log` → `logs/*`로 확장, 로테이션 백업 파일명이
    `.log` 확장자로 안 끝나서 기존 패턴에 안 걸렸었음).

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
- 백테스트 로직 전체 (78개 유닛테스트 통과, 이번 세션에 29개 추가됨)
- KIS 모의투자 인증/잔고조회/시세조회/일봉조회 (실제 API 호출로 확인)
- 재시도 로직이 실제 KIS 서버 불안정 상황에서 작동하는 것 확인
- 전체 파이프라인(신호생성→리스크관리→주문실행 준비까지) 정상 실행 (여러 번 반복 실행해도
  상태가 올바르게 이어짐 - 일일손실카운터, Buy&Hold 슬리브, KIS 현금원장 전부 `state.json` 확인함)
- **실제 매수/매도 주문 체결** (이번 세션에 임시 스크립트로 KIS 모의투자 계좌에 실제 왕복 검증 완료 -
  015760.KS 1주 매수→매도, 둘 다 order_no 발급되며 정상 체결)
- 포지션 사이징이 실제 KIS 계좌 잔고가 아니라 config의 seed_capital 기준으로 정확히 계산됨
  (이전엔 계좌 기본잔고 1000만원 기준으로 계산되는 버그가 있었음 - 위 14번 항목 참고)

✅ (이어서) 장중 반복 실행 스케줄러도 등록·검증 완료 (위 16번 참고) - `python main.py --mode paper`가
  평일 09:00~15:30 15분 간격으로 자동 실행 중.
✅ (이어서) KisBroker 로컬 원장에 수수료/세금 반영 완료 (위 18번 참고).
✅ (이어서) 호가단위(tick size) 반영 완료 (위 19번 참고) - 상한가/하한가는 구조적으로 발생 안 하는
  시나리오라 의도적으로 생략, 부분체결/슬리피지는 여전히 미반영.
✅ (이어서) 로그 로테이션 추가 완료 (위 20번 참고) - trading.log는 자정 회전/30일 보관,
  scheduler_stdout은 날짜별 파일 + forfiles로 30일 지난 파일 정리.

❌ 아직 안 됨 (다음 단계 후보, 실전 전환 체크리스트):
- **하이브리드 Buy&Hold 슬리브는 실전에서도 실제 매수가 아니라 로컬 가상 계산** - 사용자가 알고
  있어야 할 가장 중요한 항목 (위 17번, README "아직 안 된 것" 참고). 처리 방향은 아직 미정으로
  보류 상태 (다른 항목부터 먼저 처리하기로 함) - 다음 세션에서 다시 논의 필요.
- **엔진의 자연 신호를 통한 실주문 체결은 아직 미확인** (지금까지 실행에서 신호가 안 나서 -
  임시 스크립트로 place_order()를 직접 호출해 경로 자체는 검증했지만, 신호생성→리스크관리→주문실행
  전체 파이프라인을 통해 실제로 진입하는 건 아직 못 봄 - 스케줄러가 이제 돌고 있으니 며칠 지켜보면
  자연스럽게 확인될 가능성 높음)
- 부분체결/슬리피지 미반영 (`filled=True`가 "전량 체결" 보장이 아님 - 위 19번 참고)
- tr_id 코드(TTTC0802U 등)가 실계좌 매수/매도/POST 호출로 이제 검증됨 (위 15번) - 다만 KIS 공식
  문서 최신본과 재대조는 안 했으니 최종 확인은 권장
- `daily_max_loss_pct` 서킷브레이커가 자연 발생 손실로 실제 작동하는 걸 본 적 없음 (유닛
  테스트로만 검증)
- `broker.provider: kis`가 지금 config.yaml에 켜진 상태 - 안 쓸 때는 `mock`으로 되돌리는 게 안전

## Git 상태

- 마지막 푸시된 커밋: `b6bfb62` (로그 로테이션 추가, 이번 세션)
- 이번 세션 작업은 전부 커밋/푸시됨 (`94bc468`: 상태 영속성 + 사이징 버그 수정, `6dde3e8`:
  `run_paper_cycle.bat`, `d1bbe07`/`59a6a31`: 핸드오프/README 최신화, `0e6ce09`: 수수료/세금 반영,
  `9cc48f6`: 핸드오프 최신화, `e4ff8d2`: 호가단위 반영, `c656144`: 핸드오프 최신화, `b6bfb62`:
  로그 로테이션). `state.json`과 `StockAutoTradingPaper` 작업 스케줄러 등록은 로컬 머신 상태라
  git에는 없음 (재현 커맨드는 위 16번 참고).
- 커밋 전 항상 확인: 앱키/시크릿/계좌번호가 코드에 하드코딩 안 됐는지 (지금까지는 전부 환경변수로만
  관리됨, 문제 없음). `state.json`은 gitignore 되어 있어 커밋 대상 아님.
- **작업 순서 규칙**: 사용자가 명시적으로 요청함 - CONTEXT_HANDOFF.md 갱신은 항상 코드 변경을
  커밋/푸시한 *다음*에, 별도 커밋으로 진행할 것 (같은 커밋에 묶지 말 것).

## 다음에 이어서 할 만한 것

1. **하이브리드 Buy&Hold 슬리브를 실전에서 어떻게 처리할지 결정** (보류 중, 위 17번 참고) -
   옵션: (a) 문서화만 하고 그대로 둔다, (b) `hybrid.enabled: false`로 순수 전략만 쓴다,
   (c) 다른 방향 (사용자가 직접 지정 예정)
2. 스케줄러가 며칠 돌면서 자연 신호로 실주문까지 가는지 관찰 (로그: `logs/trading.log`,
   `logs/scheduler_stdout.log`)
3. 부분체결/슬리피지 반영 여부 검토 (체결내역조회 API 연동 필요)
4. `daily_max_loss_pct` 서킷브레이커 실제 손실 시나리오로 검증
5. tr_id 코드 최신 KIS 공식 문서와 재대조
