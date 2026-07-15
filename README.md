# stock_autotrading

주식 자동매매 프로그램의 파이썬 골격(스켈레톤)입니다. 증권사 API는 아직 연동하지 않았고,
`broker/base.py` 인터페이스만 구현하면 바로 실전 연동으로 교체할 수 있도록 설계했습니다.

## 구조

```
stock_autotrading/
├── config.yaml              # 시드, 리스크 파라미터, 전략 파라미터, 종목 리스트
├── main.py                  # 실행 진입점 (paper / backtest 모드)
├── broker/
│   ├── base.py               # 증권사 API가 구현해야 할 인터페이스 (BrokerBase)
│   └── mock_broker.py        # 지금 당장 쓸 수 있는 모의투자 브로커
├── data/
│   ├── base.py                # 시세 데이터 소스 인터페이스 (DataFeedBase)
│   └── yfinance_feed.py       # 임시 시세 데이터 (yfinance, API 키 불필요)
├── strategies/
│   ├── base.py                 # Strategy 인터페이스, Signal(BUY/SELL/HOLD)
│   ├── trend_following.py      # 이동평균 골든/데드크로스, 볼린저밴드 돌파
│   └── mean_reversion.py       # RSI 과매수/과매도, 변동성 돌파(Larry Williams)
├── risk/
│   └── risk_manager.py         # 손절/익절/종목당 비중/일일 손실한도
├── portfolio/
│   └── portfolio.py            # 현금/보유종목 장부 (MockBroker 내부에서 사용)
├── engine/
│   └── trading_engine.py       # 위 요소를 묶어 한 사이클(매매 판단→주문)을 실행
├── backtest/
│   └── backtest_runner.py      # 과거 데이터로 동일 엔진 로직을 재생
├── tests/
│   └── test_indicators.py      # 지표/전략 로직 sanity check
└── logs/trading.log            # 실행 로그
```

## 설치

```powershell
cd C:\Users\김형찬(HyeongchanKim)\stock_autotrading
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 실행

**모의투자 1회 사이클** (실시간에 가까운 데이터로 매매 판단만 해보기):

```powershell
python main.py --mode paper
```

**백테스트** (과거 데이터로 전략 검증):

```powershell
python main.py --mode backtest
```

**테스트**:

```powershell
python -m unittest discover tests
```

## 설정 (`config.yaml`)

- `seed_capital`: 100만원
- `risk.stop_loss_pct`: 9% 손절 (아래 "손익비 검증 기록" 참고)
- `risk.take_profit_pct`: 7% 익절 (즉시 청산)
- `risk.position_size_pct`: 종목당 비중 30%
- `risk.daily_max_loss_pct`: 일일 최대 손실한도 30% (도달 시 당일 신규 진입만 중단, 보유 포지션 손절/익절은 계속 작동)
- `regime_filter`: ADX 기반 추세/횡보 국면 필터, 활성 국면에 맞는 전략만 신규 진입 허용
- `signal_combination.entry_mode`: 활성 국면 내 전략 신호 결합 방식 (`any`/`all`)
- `costs`: 매수/매도 수수료 및 매도세 (백테스트에 실제 비용 반영)
- `backtest.history_days`: 백테스트에 사용할 과거 거래일 수
- `hybrid`: 시드를 전략 슬리브 + Buy&Hold 슬리브로 분리 운용 (기본 50/50, 아래 "하이브리드 구조 검증 기록" 참고)
- `strategies`: 전략별 on/off 및 파라미터 (이평선 기간, 볼린저 window/표준편차, RSI 기간/기준선, 변동성 돌파 k값)
- `watchlist`: 감시 종목 (야후 파이낸스 형식, 코스피는 `.KS`, 코스닥은 `.KQ` 접미사) - 섹터를 다양하게 섞어서 상승장에만 편중된 검증이 되지 않도록 구성

## 손익비 검증 기록

10종목 · 5년 백테스트(거래비용 반영)로 실제 확인한 결과:

| 시도 | 총수익률 | MDD |
|---|---|---|
| 손절10%/익절7% 즉시청산 (원래값) | +64.9% | -28.9% |
| 트레일링 스탑 (7% 도달 후 고점대비 -4%) | +43.2% | -33.0% |
| 익절 목표를 12%로 상향 | -0.7% | -33.6% |
| **손절9%/익절7% 즉시청산 (현재값)** | **+72.0%** | **-29.0%** |
| 손절8%/익절7% | +71.3% | -35.7% |

트레일링 스탑과 익절 목표 상향은 모두 성능을 악화시켰다 - 이 전략 조합은 종목당 30% 비중 제한 때문에
자본을 빨리 회수해서 다음 신호에 재투자하는 속도가 한 종목에서 더 버티는 것보다 중요했다. 손절을
8%까지 조이면 수익은 오르지만 MDD가 크게 나빠져 기각, 9%가 수익·MDD 모두 원래값보다 낫거나 비슷한
지점이라 채택했다. 파라미터를 다시 바꿀 때는 반드시 `python main.py --mode backtest`로 총수익률뿐
아니라 MDD·승률까지 함께 비교할 것.

## 승률 극대화 시도 기록 (기각)

승률 80%를 목표로 익절폭을 0.1~0.5%까지 줄이고 손절폭을 최대 30%까지 넓혀봤지만, 승률은
최대 72%(익절0.2%/손절30%)에서 막혔고 그 지점 수익률은 -16.8%로 마이너스였다. 익절을 줄일수록
"자주 이기지만 크게 지는" 구조가 되어 기대값이 무너진다 - 승률 자체를 목표로 삼지 말 것.
승률보다 총수익률·MDD를 기준으로 판단해야 한다.

## 전략 구성 검증 기록: 돈치안 채널 브레이크아웃 추가

새 전략 후보로 `DonchianBreakoutStrategy`(20일 신고가 매수 / 10일 신저가 매도, 터틀 트레이딩 스타일)를
`strategies/trend_following.py`에 추가하고 조합을 비교했다:

| 구성 | 수익률 | MDD | 승률 |
|---|---|---|---|
| 기존 4개 전략 (이평선+볼린저+RSI+변동성돌파) | +23.5% | -28.3% | 44.3% |
| 돈치안 추가 (5개 전략, 추세 3개) | +28.4% | -32.4% | 41.5% |
| 돈치안이 이평선크로스 대체 | +13.9% | -36.7% | 39.7% |
| **돈치안이 볼린저 대체 (현재값)** | **+34.3%** | -30.5% | 41.4% |
| 돈치안 단독 (이평선+볼린저 모두 제거) | +24.9% | -29.5% | 43.7% |

"볼린저를 돈치안으로 교체"가 가장 나아서 채택했다 (`bollinger_breakout.enabled: false`,
`donchian_breakout.enabled: true`). 추세추종 카테고리에 전략을 그냥 추가하면(entry_mode=any 특성상)
신호가 더 잦아져 MDD만 나빠지므로, 새 전략은 항상 "추가"가 아니라 "교체" 여부부터 비교할 것.
돈치안 자체 파라미터도 10/5(더 민감)와 40/20(더 둔감) 모두 20/10보다 나빴다.

## 다음 방향 검증 기록: MACD / 모멘텀 랭킹 / 거래량 필터

수익률을 더 올릴 방법 세 가지를 실제로 구현해서 검증했다.

**엔진 구조 변경에 따른 중요한 부작용**: 모멘텀 랭킹을 지원하려면 "종목별로 청산 후 바로 진입"
하던 방식을, "전체 종목 청산 먼저 처리 → 전체 진입은 그 다음에 일괄 처리"하는 2-pass 구조로
바꿔야 했다 (`engine/trading_engine.py`). 그런데 **모멘텀 랭킹을 꺼둔 채로 이 구조 변경만
적용해도 수익률이 +34.3% → +6.1%로 떨어졌다** - 자본 배분 순서가 바뀌면서 정수 주식 수 반올림에
따른 나비효과가 또 발생한 것이다. 이 프로젝트의 백테스트 결과가 구현 세부사항(순서, 반올림)에
이 정도로 민감하다는 걸 다시 확인했다.

| 구성 (전부 2-pass 엔진 기준) | 수익률 | MDD | 승률 | 거래횟수 |
|---|---|---|---|---|
| 2-pass 엔진 기본값 (모멘텀랭킹/거래량필터 모두 끔) | +6.1% | -34.9% | 41.6% | 644 |
| + 모멘텀 랭킹 켬 | -1.2% | -39.0% | 41.7% | 630 |
| **+ 거래량 필터 켬 (채택)** | **+34.1%** | **-25.9%** | **45.0%** | **351** |
| + 거래량필터 + 모멘텀랭킹 둘 다 | +5.6% | -28.6% | 44.6% | 345 |
| + 거래량필터 + MACD 추가 (5개 전략) | +0.8% | -31.7% | 42.5% | 379 |
| + 거래량필터 + MACD가 이평선크로스 대체 | +4.1% | -32.3% | 42.9% | 368 |
| + 거래량필터 + MACD가 돈치안 대체 | +26.5% | **-17.8%** | **45.7%** | 256 |

**결론**:
- **모멘텀 랭킹은 모든 조합에서 손해였다.** 채택하지 않음 (`rank_entries_by_momentum: false` 유지).
- **거래량 필터(최근 20일 평균 대비 1.5배 이상)는 순수 개선이었다.** 수익률·MDD·승률·거래횟수
  전부 좋아졌다. 채택함 (`volume_filter.enabled: true`).
- **MACD는 수익률 기준으로는 도움이 안 됐다** (거래량필터 단독보다 항상 낮음). 다만 "MACD가
  돈치안을 대체"하는 조합은 MDD를 -25.9%→-17.8%까지 크게 낮춘다 - 수익률보다 안정성이
  우선이면 이 조합(`donchian_breakout: false`, `macd: true` + 거래량필터)을 고려할 만하다.
  현재는 수익률을 우선해 MACD는 비활성 상태로 둠.

새로운 강화 아이디어를 테스트할 때는 반드시 이 표처럼 "켬/끔"과 "추가/교체" 조합을 전부
비교하고, 엔진 구조 자체를 바꿨다면 그 변경만으로 결과가 얼마나 달라지는지부터 별도로 확인할 것.

## 신호 결합 정책 (현재 단순화된 버전)

한 종목에 대해 여러 전략이 동시에 신호를 낼 수 있습니다. 지금은:

- 활성화된 전략 중 하나라도 BUY → (미보유 상태이고 일일 손실한도 미도달 시) 신규 진입
- 활성화된 전략 중 하나라도 SELL → (보유 중이면) 청산
- 손절/익절은 전략 신호보다 항상 우선 적용
- 종목당 포지션은 한 번에 하나만 추적 (분할매수/피라미딩 없음)

전략 간 확인(confirmation) 로직이나 가중치 투표 방식으로 바꾸고 싶다면
`engine/trading_engine.py`의 `_evaluate_strategies`만 수정하면 됩니다.

## 실제 증권사 API 연동: 한국투자증권(KIS) Open API

`broker/kis_broker.py`(`KisBroker`)와 `data/kis_data_feed.py`(`KisDataFeed`)로 실제 KIS 계좌
연동을 구현했다. 참조 스펙은 KIS 공식 샘플 저장소
([koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api))에서
확인했다 - 특히 tr_id는 그 저장소 안에서도 파일마다 다르게 적혀 있는 걸 발견해서
(`examples_user/domestic_stock/domestic_stock_functions.py`는 `TTTC0011U/0012U`,
`backtester/kis_backtest/providers/kis/constants.py`는 `TTTC0801U/0802U`) 더 신뢰할 수 있는
후자(백테스터 모듈 + 도큐먼트 문구와 일치)를 채택했다. **실전 투입 전 최신 KIS 공식 문서로
한 번 더 대조 확인 권장.**

### 설정

1. KIS 홈페이지에서 OpenAPI 서비스 신청 → **실전투자용**과 **모의투자용** 앱키/시크릿을
   각각 발급 (KIS는 이 둘을 별도로 발급한다). **모의투자만 쓸 계획이어도 실전투자용 앱키/시크릿이
   필요하다** - 시세/차트 조회가 계좌 env와 무관하게 항상 실전 도메인을 쓰기 때문 (아래
   "안전장치" 참고).
2. **계좌번호도 실전/모의가 서로 다르다** - 모의투자를 신청하면 KIS가 별도의 가상
   계좌번호를 발급해준다. 실전계좌번호를 모의투자에 쓰면 안 된다.
3. 아래 환경변수를 본인 PC에 설정 (Claude에게 값 자체를 알려줄 필요 없음):
   ```powershell
   [System.Environment]::SetEnvironmentVariable("KIS_APP_KEY", "실전 앱키", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_APP_SECRET", "실전 앱시크릿", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_ACCOUNT_NO", "실전 계좌번호 앞 8자리", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_ACCOUNT_PRODUCT_CD", "01", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_PAPER_APP_KEY", "모의 앱키", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_PAPER_APP_SECRET", "모의 앱시크릿", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_PAPER_ACCOUNT_NO", "모의 계좌번호 앞 8자리", "User")
   [System.Environment]::SetEnvironmentVariable("KIS_PAPER_ACCOUNT_PRODUCT_CD", "01", "User")
   ```
4. 새 터미널에서 `config.yaml`의 `broker.provider: kis`로 변경 (기본값은 `mock`)
5. `broker.kis.env`는 반드시 `demo`(모의투자)로 시작 - `real`은 충분히 검증 후 본인이 직접 전환

### 안전장치

- **`broker.kis.env`의 기본값은 `demo`.** `real`로 바꾸는 건 사용자 본인의 명시적 결정이어야 함.
- **백테스트는 `broker.provider` 값과 무관하게 항상 `MockBroker`+`yfinance`로 돈다** (`run_backtest`에
  하드코딩됨) - 실계좌 API에 수년치 데이터를 반복 조회하는 건 rate limit도 걸리고 부적절함.
  KIS 연동은 `run_paper`(모의/실전 주문 실행)에만 적용됨.
- 토큰은 `.kis_cache/`에 캐시되어 재발급을 최소화함 (KIS는 토큰 재발급마다 알림톡을 보냄) -
  이 디렉터리는 `.gitignore`에 등록됨.
- `env="real"`로 주문을 낼 때마다 `PLACING REAL-MONEY ORDER` 경고 로그가 남음.
- 실전/모의 계좌번호를 완전히 분리된 환경변수(`KIS_ACCOUNT_NO` vs `KIS_PAPER_ACCOUNT_NO`)로
  관리해서 서로 섞여 들어갈 수 없게 만듦 - 초기 구현에서 이 둘을 하나로 합쳐뒀다가
  발견하고 수정함 (`tests/test_kis_broker.py`에 회귀 테스트 추가).
- 계좌번호나 앱키/시크릿이 없으면 `KisCredentialsError`로 즉시 실패 (조용히 잘못된
  계좌로 주문 나가는 일 방지).
- **시세/차트 조회는 계좌가 모의투자여도 항상 실전 도메인(`openapi.koreainvestment.com`)으로
  나간다** (`KisSession.market_data_session()`) - 실계좌로 8회씩 비교 테스트해본 결과 모의투자
  도메인에서만 500 에러가 발생했음(12.5% vs 0%), 활발히 유지되는 서드파티 라이브러리
  (`Soju06/python-kis`)도 모든 시세/차트 호출에 항상 실전 도메인을 씀. 주문/잔고/체결조회는
  그대로 계좌의 실제 env(모의/실전)를 따른다 - 시세만 예외. **이 때문에 모의투자만 쓰더라도
  `KIS_APP_KEY`/`KIS_APP_SECRET`(실전용) 환경변수가 필요해졌다** (아래 "설정" 참고).

### 아직 안 된 것 (실전 투입 전 필수)

- ~~KIS 모의투자 계좌로 실제 검증 안 함~~ → 완료: 인증/잔고/시세/일봉조회 + 매수/매도 주문(POST)
  왕복까지 실제 모의투자 API 호출로 확인함.
- ~~상태 영속성 없음~~ → 완료: `utils/state_store.py` (`state.json`)로 `RiskManager` 일일 손실
  카운터, 하이브리드 Buy&Hold 슬리브, `KisBroker` 현금 원장이 프로세스 재실행 간에 유지됨.
  평일 09:00~15:30 15분 간격으로 반복 실행하는 Windows 작업 스케줄러(`StockAutoTradingPaper`,
  `run_paper_cycle.bat`)도 등록·검증 완료.
- ~~호가단위(tick size) 미반영~~ → 완료: `broker/krx_tick.py`가 KRX의 공개된 고정 호가단위
  테이블(2023년 코스피/코스닥 통합 기준)로 주문가를 가까운 유효 틱으로 반올림함 -
  `KisBroker.place_order()`가 KIS로 보내기 전에 항상 이걸 거침 (지표 계산을 거친 가격은 대개
  틱에 안 맞아서, 안 맞으면 KIS가 그냥 주문을 거부함).
  - **상한가/하한가는 별도 구현 안 함** - 의도적 판단: 이 코드베이스가 브로커에 보내는 주문가는
    전부 실제 관측된 시세(체결된 종가/현재가)에서만 나온다 (`avg_price * 1.05` 같은 합성 목표가를
    직접 계산해서 주문하는 로직이 없음) - 즉 거래소가 실제로 그 가격에 체결을 허용했다는 뜻이라
    구조적으로 항상 그날의 밴드 안에 있다. 밴드를 벗어나는 시나리오 자체가 이 코드에는 없어서
    별도 클리핑 로직을 추가하지 않았다 (혹시 발생하면 KIS가 거부 응답을 주고, 이미 `msg1`로
    실패 처리됨 - 조용히 잘못 성공 처리되는 일은 없음).
  - ~~부분체결/체결확인 안 됨~~ → API 연동은 완료: `KisBroker.get_daily_fills()`가
    주식일별주문체결조회(`inquire-daily-ccld`)로 실제 체결수량(`filled_qty`)/미체결수량
    (`pending_qty`)/체결평균가를 조회함. **tr_id 발견 과정 자체가 이 프로젝트의 "tr_id 재검증
    필요" 경고가 왜 있는지 보여주는 사례** - KIS 백테스터 참조모듈(`constants.py`)과 활발히
    유지되는 서드파티 라이브러리(`Soju06/python-kis`) 둘 다 `TTTC8001R`/`VTTC8001R`을 썼지만,
    실제 모의투자 계좌로 테스트해보니 `rt_cd="0"`(성공)인데 `output1`(개별 주문 내역)이 항상
    빈 배열로 돌아옴 - 반면 `examples_user`의 `inquire_daily_ccld` 함수가 쓰는
    `TTTC0081R`/`VTTC0081R`은 실제 데이터를 정확히 반환함(직접 매수→조회→매도로 검증).
    ~~다만 이 결과를 엔진 로직에 반영하는 건 아직 안 함~~ → **가장 위험한 부분은 해결됨**:
    `KisBroker.place_order()`의 SELL 경로가 부분체결을 정확히 반영하도록 수정 - 이전엔 실제
    체결수량이 아니라 **요청한 수량**으로 `realized_pnl`을 계산해서, 손절 매도가 부분체결되면
    `RiskManager`에 실제보다 부풀려진 손실이 기록되고 `daily_max_loss_pct` 서킷브레이커가
    왜곡될 수 있었음. `_actual_sold_qty()`가 주문 전후 `get_positions()`를 비교해서 실제
    체결수량을 구함 (계좌 반영이 몇 초 지연될 수 있어서 짧게 재시도, 끝까지 안 바뀌면 원래
    동작대로 요청수량 전체가 체결된 걸로 간주 - "0체결"로 잘못 보고하는 것보다 안전한 방향).
    BUY 쪽은 그대로 둠 (부분체결돼도 다음 사이클 `get_positions()`로 자연 정정되고, 리스크
    서킷브레이커처럼 즉시 정확해야 하는 값에 안 쓰이기 때문). `get_daily_fills()` 자체를
    엔진에 직접 연결하는 건 여전히 안 함 - `get_positions()` 디프만으로 실제 위험(realized_pnl
    부정확)을 해결할 수 있어서 더 무거운 방식은 불필요하다고 판단. 테스트 3개 추가.
    슬리피지는 여전히 미반영.
- **엔진의 자연 신호를 통한 실주문 체결은 아직 못 봄** - `place_order()`를 직접 호출한 수동
  테스트로 주문 경로 자체는 확인했지만, 신호생성→리스크관리→주문실행 전체 파이프라인을 통해
  실제로 진입하는 걸 본 적은 없다. 지금 스케줄러가 돌고 있으니 며칠~몇 주 지켜보면서 확인 필요.
- ~~KIS 실주문 수수료/세금이 로컬 현금 원장에는 반영 안 됨~~ → 완료: `KisBroker`도 `MockBroker`와
  같은 방식으로 `costs.commission_pct`/`sell_tax_pct`를 로컬 원장(`kis_cash_ledger`)과
  `realized_pnl` 계산에 적용함 (실제 KIS로 보내는 주문가는 그대로 raw fill_price - 이건 어디까지나
  로컬 장부 근사치용). KIS의 실제 수수료 스케줄과 완전히 일치하진 않겠지만 드리프트를 크게 줄임.
- **[결정됨] 하이브리드 Buy&Hold 슬리브는 실전에서도 "가상 추적"으로 유지하기로 함 - 실제 매수 안 함**
  (사용자와 논의 후 확정, 미해결 항목 아님). paper/live 모드의 `BuyAndHoldSleeve`는 브로커에 절대
  주문을 넣지 않는다 (KIS 계좌가 현금/포지션을 슬리브별로 분리 못 하기 때문 - 실주문을 넣으면
  전략 슬리브와 같은 종목일 때 평단가가 섞이고 재진입도 막힘). 즉 **계좌에 시드 전체(예: 100만원)를
  입금해도 실제로 매수되는 건 전략 슬리브 몫(예: 50만원)뿐이고, 나머지는 그냥 현금으로 남는다.**
  `combined equity` 로그에 찍히는 Buy&Hold 가치는 어디까지나 비교용 가상 수치이지 실제 계좌 자산이
  아니다 - 매 사이클 로그에 `buy_and_hold[virtual, not real capital]`로 명시해뒀다
  (`main.py`의 `run_paper`). 실전에서 정말 그 절반을 워치리스트에 분산 투자하고 싶다면
  **사용자가 직접 수동으로 매수**해야 한다 - config.yaml `hybrid` 섹션에도 같은 경고가 있다.
- tr_id 코드가 KIS 모의투자 실계좌 호출로는 검증됐지만(매수/매도 둘 다 정상 체결), 최신 KIS
  공식 문서와의 재대조는 아직 권장 수준으로 남아있음.
- ~~`daily_max_loss_pct` 서킷브레이커가 실제 손실로 검증된 적 없음~~ → 완료: KIS 모의투자
  계좌에서 실제로 20주를 사서 시장가보다 25% 낮은 가격(밴드 내)에 팔아 실손실(-172,000원)을
  발생시키고, config.yaml과 동일하게 스코프된 `RiskManager`(seed=50만원,
  daily_max_loss_pct=30% → 한도 15만원)에 넣어 정상적으로 거래정지되는 것 확인. `to_dict()`/
  `restore()` 왕복으로 프로세스 재시작 시나리오까지 통과 (halt 상태 유지됨) - `main.py`가
  실제로 쓰는 영속화 경로와 동일. 보호성 청산(`_check_protective_exit`/`_check_strategy_exit`)은
  `can_open_new_position()`을 호출하지 않으므로 거래정지 중에도 청산은 계속 허용됨(엔진 코드
  확인). 여전히 남은 리스크: `state.json`이 삭제/손상되면 당일 손실 누적이 초기화되어
  서킷브레이커가 무력화될 수 있음.
- ~~로그 로테이션이 없음~~ → 완료: `utils/logger.py`가 `trading.log`에 `TimedRotatingFileHandler`
  (자정 회전, 30일 보관)를 적용. `run_paper_cycle.bat`가 캡처하는 stdout(pykrx 등 라이브러리의
  `print()` 기반 메시지 - 파이썬 logging으로는 못 잡음)은 날짜별 파일(`scheduler_stdout_YYYY-MM-DD.log`)로
  나눠 쓰고, 실행할 때마다 `forfiles`로 30일 지난 파일을 정리함. 예전 누적 파일
  `logs/scheduler_stdout.log`는 그대로 남아있지만 더 이상 커지지 않음.

`strategies/`, `risk/`, `engine/`, `backtest/`는 브로커가 무엇이든 그대로 재사용된다.

## 트러블슈팅: SSL 인증서 오류 (Windows + 한글 계정명)

Windows 사용자 계정명에 한글이 포함되어 있으면(`C:\Users\김형찬(HyeongchanKim)\...`),
`yfinance`가 내부적으로 쓰는 curl 라이브러리가 인증서 경로(`certifi`의 `cacert.pem`)를
못 찾아 다음과 같은 에러가 날 수 있습니다.

```
Failed to perform, curl: (77) error setting certificate verify locations
```

해결: 인증서 파일을 한글이 없는 경로로 복사하고, 환경변수로 그 경로를 지정합니다.

```powershell
mkdir C:\ca-certs -Force
python -c "import certifi, shutil; shutil.copy(certifi.where(), r'C:\ca-certs\cacert.pem')"

# 매번 실행 전에 아래 두 줄로 환경변수 지정 (PowerShell 세션 기준)
$env:SSL_CERT_FILE = "C:\ca-certs\cacert.pem"
$env:CURL_CA_BUNDLE = "C:\ca-certs\cacert.pem"

python main.py --mode paper
```

매번 수동 지정이 번거로우면 Windows 시스템 환경변수(제어판 > 시스템 > 고급 시스템 설정 >
환경 변수)에 `SSL_CERT_FILE`, `CURL_CA_BUNDLE`을 위 경로로 영구 등록하면 됩니다.
증권사 API로 교체한 뒤에는 대부분 이 문제와 무관해집니다(브로커사 SDK가 다른 HTTP
클라이언트를 쓰는 경우가 많음).

## 외부 프로젝트(KIS Open Trading API) 코드 비교 후 발견/수정한 문제

다른 오픈소스 자동매매 저장소(`koreainvestment/open-trading-api`) 분석 결과와 이 프로젝트를
비교해서 다음을 확인했다:

- **죽은 파라미터 발견 및 제거**: `config.yaml`에 `data_feed.provider`, `broker.provider` 값이
  있었지만 `main.py`는 항상 `YFinanceDataFeed()`/`MockBroker()`를 하드코딩으로 생성해서 이
  값들을 한 번도 읽지 않았다. 저 저장소가 지적한 `.kis.yaml`의 `buy_ratio`/`threshold_pct`
  미사용 문제와 같은 유형. 두 필드 모두 제거하고, 실브로커/데이터소스 교체는 `main.py`를
  직접 수정해야 한다는 안내만 주석으로 남김.
- **체결 모델 한계는 동일하게 있음**: 수수료·세금은 반영하지만 슬리피지, KRX 호가단위
  (tick size) 반올림은 아직 미반영. 저 저장소의 경량 시뮬레이션과 같은 한계.
- **Buy & Hold 벤치마크가 없었던 게 가장 큰 문제였다** — 아래 항목 참고.

## Buy & Hold 벤치마크 검증: 우리 전략도 완패했다

`backtest/benchmark.py`의 `run_buy_and_hold()`로 "워치리스트 10종목 동일비중 매수 후 그냥
보유"와 우리 전략을 같은 기간·같은 시드로 직접 비교했다 (`main.py --mode backtest` 실행 시
자동으로 같이 출력됨).

5년 구간(2021-06~2026-07)에서:

| | 수익률 | MDD |
|---|---|---|
| 전략 | +31.6% | -25.4% |
| Buy & Hold | **+45.4%** | **-13.2%** |

Buy & Hold가 수익률·MDD 둘 다 이겼다. 2020년 코로나 급락(회복 랠리 포함)까지 넣은 6.75년
구간(2019-08~2026-07)으로 확장하면 격차가 더 벌어진다:

| | 수익률 | MDD |
|---|---|---|
| 전략 | +42.1% | **-25.8%** |
| Buy & Hold | **+147.6%** | -33.7% |

이 구간은 MDD는 전략이 더 낫지만(리스크 관리가 실제로 작동한다는 증거), 수익률 격차가
105%p까지 벌어진다. 원인은 설계 자체에 있다: 종목당 비중 30% 상한 + 익절 7%마다 청산하는
구조는 동시 보유 종목 수를 3개 안팎으로 제한하고, V자 반등장에서 한 종목이 100%+ 오르는
동안 여러 번 들어갔다 나갔다 하며 상승분의 일부만 잘라먹는다. Buy & Hold는 팔지 않으니
전체 상승을 그대로 가져간다. 버그가 아니라 "손실은 짧게, 수익은 규칙적으로 실현"하는
리스크관리형 설계의 구조적 트레이드오프다.

`backtest.history_days`를 바꿔서 백테스트 구간을 조정할 수 있다 (`data/yfinance_feed.py`가
`period="max"`로 데이터를 받아온 뒤 `.tail(lookback)`으로 자르는 방식이라, 값을 늘리면 그만큼
더 먼 과거까지 포함됨).

## 하이브리드 구조 검증 기록: 시드를 전략/Buy&Hold 두 슬리브로 분리

Buy & Hold 완패 결과를 받아들여, 시드를 "전략 슬리브"와 "Buy&Hold 슬리브"로 나누고 결합
성과를 계산하는 하이브리드 모드를 추가했다 (`config.yaml`의 `hybrid.*`,
`main.py`의 `run_backtest`가 두 슬리브를 각각 돌리고 날짜별로 합산). 전략 슬리브는
`build_broker`/`build_risk_manager`/`build_engine`에 `seed_capital` 파라미터를 추가해서
전체 시드가 아닌 배분된 몫만 갖고 돌아가게 만들었다.

6.75년 구간에서 전략 비중별 결합 성과:

| 전략 비중 | 수익률 | MDD |
|---|---|---|
| 0% (순수 Buy&Hold) | +145.6% | -33.7% |
| 20% | +99.2% | -28.0% |
| 30% | +85.9% | -26.0% |
| **50% (채택)** | +75.9% | **-20.1% (전 구간 최저)** |
| 70% | +62.1% | -21.1% |
| 80% | +58.3% | -20.3% |
| 100% (순수 전략) | +42.1% | -25.8% |

전략 50~80% 구간의 MDD가 양쪽 순수 방식보다 전부 낮다 - 서로 다른 두 접근(능동 리스크관리 vs
정적 보유)의 낙폭 시점이 완전히 겹치지 않아서 나오는 진짜 분산 효과. 수익률은 전략 비중이
낮을수록 오르지만 MDD도 20% 지점부터 다시 나빠지기 시작해서, MDD 최저점인 50%를 기본값으로
채택했다 (`hybrid.enabled: true`, `hybrid.strategy_allocation_pct: 0.5`).

**주의**: `0.2`~`0.8` 사이 다른 비율도 config 값만 바꾸면 바로 지원되며, `hybrid.enabled: false`로
끄면 기존처럼 순수 전략 100%로 되돌아간다. 다만 **paper/live 모드는 아직 Buy&Hold 슬리브
상태를 세션 간 유지하지 못한다** (영속 저장소 미구현, `run_paper`가 경고 로그만 남기고 전략
슬리브만 실행함) - 백테스트에서만 결합 성과를 계산할 수 있다. 나중에 실거래로 넘어갈 때
Buy&Hold 슬리브도 살리려면 매수 시점/수량을 파일이나 DB에 저장하고 재시작 시 복원하는
로직을 `broker/` 또는 별도 상태 저장 모듈에 추가해야 한다.

## 고래 추적 지표: 외국인/기관 순매수 추종 전략 (`whale_flow`)

"고래 추적"(외국인+기관 대량 순매수 추종)을 `strategies/whale_flow.py`의 `WhaleFlowStrategy`로
구현했다. 최근 N거래일(기본 5일)의 (외국인 순매수 + 기관 순매수) 합계를 같은 기간 거래대금으로
나눈 비율이 임계값(기본 ±5%)을 넘으면 매수/매도 신호를 낸다 - 종목마다 시가총액이 크게
달라서 원화 절대금액 대신 거래대금 대비 비율로 정규화했다.

**데이터 소스 제약**: 이 데이터는 yfinance에 없고, KRX 정보데이터시스템(`data.krx.co.kr`)
로그인이 필요하다 (`pykrx` 라이브러리가 내부적으로 `KRX_ID`/`KRX_PW` 환경변수로 로그인 시도).
계정 생성은 사용자 본인이 직접 해야 하는 작업이라 Claude가 대신 만들 수 없다.

**설정 방법**:
1. https://data.krx.co.kr 에서 무료 회원가입
2. Windows 환경변수에 `KRX_ID`, `KRX_PW`를 본인 계정 정보로 설정 (제어판 > 시스템 > 고급 시스템
   설정 > 환경 변수, 또는 PowerShell: `[System.Environment]::SetEnvironmentVariable("KRX_ID", "아이디", "User")`)
3. `config.yaml`의 `strategies.trend_following.whale_flow.enabled: true`로 변경
4. 새 터미널(환경변수 반영)에서 `python main.py --mode backtest` 실행

**계정 없이 그냥 켜면 어떻게 되나**: 에러 없이 자동으로 조용히 비활성 상태로 동작한다.
`data/krx_investor_feed.py`의 `WhaleEnrichedDataFeed`가 KRX 조회 실패를 심볼별로 개별
포착해서 경고 로그만 남기고(`skipping whale flow enrichment for ...`) 일반 OHLCV로
계속 진행하며, `WhaleFlowStrategy`는 `institutional_net`/`foreign_net` 컬럼이 없으면
그냥 HOLD를 반환한다. 단, pykrx 라이브러리 자체의 로깅 버그로 실패 시 콘솔에 지저분한
트레이스백이 여러 번 출력되는데 (`logging.info(args, kwargs)` 인자 오류, pykrx 쪽 버그) -
무시해도 되고, 실행 결과 자체(`cycle complete...`)에는 영향이 없다.

**검증 완료 - 채택함.** 계정 설정 후 실제 KRX 데이터로 10종목·6.75년 백테스트(순수 전략 100%
기준, 하이브리드 끔)를 돌려 다른 전략들과 같은 방식으로 "추가 vs 교체" 비교했다:

| 구성 | 수익률 | MDD | 승률 |
|---|---|---|---|
| 베이스라인 (이평선+돈치안+RSI+변동성돌파, whale_flow 없음) | +38.3% | -26.8% | 46.6% |
| whale_flow 추가 (5개 전략) | +18.7% | -23.4% | 45.2% |
| whale_flow이 돈치안 대체 | +45.2% | -17.9% | 46.5% |
| whale_flow이 이평선크로스 대체 | +17.4% | -26.3% | 45.4% |
| **whale_flow 단독 (이평선+돈치안 모두 제거, 채택)** | **+52.8%** | **-17.9%** | **47.3%** |

외국인+기관 순매수 추종이 이평선/돈치안 같은 가격 기반 기술적 지표보다 수익률·MDD·승률
전부 더 나은 신호였다 - 이 프로젝트에서 지금까지 나온 단일 전략 조합 중 최고 결과다. 그래서
`config.yaml`에서 `ma_cross`와 `donchian_breakout`을 비활성화하고 추세추종 카테고리를
`whale_flow` 하나로 완전히 대체했다 (`mean_reversion`의 RSI/변동성돌파는 그대로 유지).

하이브리드 50/50 구조와 다시 결합한 최종 결과(전략 슬리브에 whale_flow 반영):

| | 수익률 | MDD |
|---|---|---|
| 이전 최선 (하이브리드 50/50, whale_flow 없음) | +75.9% | -20.1% |
| **현재 (하이브리드 50/50 + whale_flow, 채택)** | **+87.9%** | **-19.0%** |

수익률·MDD 둘 다 개선되어 현재 기본값으로 채택했다.

## 유의사항

- 이 코드는 실거래 검증 없는 스켈레톤입니다. 실제 자금 투입 전 충분한 백테스트와 모의투자 기간을 거치세요.
- 일일 손실한도 30%는 사용자가 지정한 값을 그대로 반영한 것으로, 개별 손절 10%보다 커서 실질적으로는 "한 종목 손절만으로는 잘 발동하지 않는 한도"입니다. 여러 종목 동시 손실 시나리오에서 안전장치로 작동합니다. 값 조정이 필요하면 `config.yaml`의 `risk.daily_max_loss_pct`만 바꾸면 됩니다.
