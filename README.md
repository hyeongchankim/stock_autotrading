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

## 실제 증권사 API 연동하는 법

1. `broker/base.py`의 `BrokerBase`를 상속하는 새 클래스를 만듭니다. (예: `broker/kis_broker.py`)
2. `get_cash_balance`, `get_positions`, `get_current_price`, `place_order` 4개 메서드를 실제 API 호출로 구현합니다.
3. `data/base.py`의 `DataFeedBase`도 마찬가지로 실제 시세 API로 구현하는 새 클래스를 만듭니다. (예: `data/kis_data_feed.py`)
4. `main.py`의 `MockBroker(...)` / `YFinanceDataFeed()` 부분만 새로 만든 클래스로 교체합니다.
5. `config.yaml`의 `broker.provider`, `data_feed.provider` 값을 갱신하고, API 키는 코드에 하드코딩하지 말고 환경변수나 `.env`로 관리하세요.

`strategies/`, `risk/`, `engine/`, `backtest/`는 브로커가 무엇이든 그대로 재사용됩니다.

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

## 유의사항

- 이 코드는 실거래 검증 없는 스켈레톤입니다. 실제 자금 투입 전 충분한 백테스트와 모의투자 기간을 거치세요.
- 일일 손실한도 30%는 사용자가 지정한 값을 그대로 반영한 것으로, 개별 손절 10%보다 커서 실질적으로는 "한 종목 손절만으로는 잘 발동하지 않는 한도"입니다. 여러 종목 동시 손실 시나리오에서 안전장치로 작동합니다. 값 조정이 필요하면 `config.yaml`의 `risk.daily_max_loss_pct`만 바꾸면 됩니다.
