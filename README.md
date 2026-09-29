# mexc-pump-fader

Бот для MEXC USDT-M фьючерсов: сканирует **низколиквидные токены**, ловит резкие
**пампы**, входит в **шорт**, при необходимости **усредняется** (до 2 раз) и
закрывает позицию на **небольшом плюсе**.

> ⚠️ **Стратегия рискованная.** Это «fade the pump» с усреднением в убыток.
> Профиль «много маленьких плюсов — редкий большой минус». Обязателен
> бэктест, paper-режим и жёсткий стоп. Никакой доходности не гарантируется.

## Что уже есть

| Модуль | Назначение |
|---|---|
| `mpf.config` | `StrategyParams` — все настройки, immutable, с валидацией |
| `mpf.core` | Чистая логика: индикаторы, детектор пампа, машина позиции, риск |
| `mpf.backtest` | Движок симуляции + метрики (winrate, PF, max DD, expectancy) |
| `mpf.scanner` | Скан рынка: фильтр ликвидности → детект пампа → скор |
| `mpf.exchange` | Адаптер MEXC через `ccxt` (ленивый импорт) |
| `mpf.data` | JSONL-хранилище свечей + генератор синтетики |
| `mpf.cli` | `scan` / `backtest` / `fetch` / `demo` |

Ядро не зависит от `ccxt`, сети и БД — тесты идут на чистом Python.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate

# только ядро + тесты
pip install -e ".[dev]"

# с биржей (ccxt)
pip install -e ".[exchange,dev]"
```

## Быстрый старт (без сети)

```bash
python -m mpf.cli demo
```

Прогоняет стратегию на синтетических «памп → откат» сериях и печатает метрики.

## Бэктест на истории

```bash
# 1. Скачать свечи с MEXC (публичные данные, ключи не нужны)
python -m mpf.cli fetch --symbol BTC/USDT:USDT --symbol ETH/USDT:USDT \
  --timeframe 1m --limit 1000 --out data/history.jsonl

# 2. Прогнать
python -m mpf.cli backtest --data data/history.jsonl --show 20
```

## Сканер пампов в реальном времени

```bash
python -m mpf.cli scan --top 60 --limit 20
```

## Сбор реальной истории

```bash
python -m mpf.cli collect --top 100 --days 2 --timeframe 1m --out data/history.jsonl
```

Скачивает историю по низколиквидным парам (по объёму/росту за 24ч) с
постраничной докачкой. **Важно:** MEXC отдаёт всего ~2 суток 1m-истории,
поэтому архив нужно накапливать регулярно (например, по cron).

## Оптимизация (walk-forward)

```bash
python -m mpf.cli optimize --data data/history.jsonl --min-trades 20 --top 10
```

Оптимизирует параметры на train (70% времени) и честно оценивает на test (30%).
Комбинации с малым числом сделок отбрасываются. Свою сетку можно задать
файлом: `--grid config/grid.example.json`.

Готовый оптимизированный набор лежит в `config/optimized.json`:

```bash
python -m mpf.cli backtest --data data/history.jsonl --config config/optimized.json
```

> **Результат первого прогона (100 пар, ~2 суток 1m):**
> 54 сделки, winrate 92.6%, profit factor 3.22, max drawdown 0.7%, +4.9% за период.
> Все 10 лучших комбинаций оказались положительными на out-of-sample.
> Но выборка маленькая — нужна проверка на длинной истории и в paper-режиме.

Интересный вывод: **тейк 4% бьёт «мелкий» 1.5%**. Слишком маленькая цель
съедается комиссиями и проскальзыванием. Это ровно та асимметрия, о которой
предупреждает раздел про риски.

## Плечо и ликвидация

При плече `L` ликвидация наступает примерно при движении против позиции
`(1/L − поддерживающая маржа)`:

| Плечо | Порог ликвидации | Что это значит |
|---|---|---|
| 3x | ~33% | усреднение до 15% помещается |
| 5x | ~20% | усреднение до 15% почти помещается |
| 10x | ~9.5% | 3 добора по 5% (+15%) не влезают |
| 20x | ~4.5% | даже один добор рискован |

Правила, которые проверяет `StrategyParams.validate`:

- стоп должен быть **меньше** порога ликвидации;
- все усреднения должны укладываться **до** стопа.

В бэктесте ликвидация моделируется и считается в метрике `ЛИКВИДАЦИЙ`.
Конфиги-примеры: `config/leveraged.json` (10x, стоп 6%, 1 добор),
`config/aggressive_20x.json` (демонстрация ликвидаций).

> **Ключевой вывод:** плечо не увеличивает прибыль — оно превращает
> «отложенные убытки» в безвозвратные ликвидации. На тех же данных
> режим без стопа дал +5.9% при 1x и всего +3.75% при 20x с 6 ликвидациями.

## Paper-trading (без денег)

```bash
python -m mpf.cli paper --config config/paper_3x.json --top 60 \
  --warmup 200 --poll 30 --out data/paper
```

Раннер опрашивает MEXC, кормит **закрытые** свечи в ту же машину состояний
`LiveState`, что использует бэктест, и ведёт виртуальные сделки:

- `data/paper/trades.jsonl` — закрытые сделки;
- `data/paper/state.json` — equity, открытые позиции, «взведённые» сигналы.

`--iterations N` останавливает после N циклов (удобно для проверки), без него
работает бесконечно. Ключи API для paper-режима **не нужны**.

Логика live и бэктеста — один и тот же код, единство покрыто тестом
`test_live_state_matches_backtester`.

## Формат сигналов (как в канале Optimac)

Детектор повторяет набор фич из сигналов канала:

| Поле канала | В боте |
|---|---|
| `PUMP +X%` | `pump_pct` (окно `pump_lookback`) |
| `RSI_5m` | `rsi` (таймфрейм 5m) |
| `X30` | `volume_mult` = объём / медиана за `volume_lookback=30` |
| `ATR_K` | `atr_pct` — ATR в % от цены, фильтр `min_atr_pct` |
| `ADX` | `adx` — сила тренда, фильтр `min_adx` |
| `Вероятность отката` | `score` (эвристика 0..100, НЕ обученная модель) |
| `Плотность в стакане` | ⏳ пока нет (нужен order book) |
| `OI` (open interest) | ⏳ пока нет |

Сканер печатает сигналы в том же виде:

```bash
python -m mpf.cli scan --timeframe 5m --top 60
#   GP/USDT   score= 62.10 PUMP= 14.07% RSI= 71.8 ATR_K=  3.53 ADX= 17.8 X= 12.4
```

Рабочий конфиг под 5m — `config/optimized_5m.json`
(34 сделки, WR 94%, PF 5.7, max DD 0.37% на 2 сутках истории).

> **Ограничение:** MEXC хранит лишь ~2 суток внутридневной истории, поэтому
> полноценный walk-forward возможен только после накопления архива.

## Сигналы из Telegram-канала

Бот умеет читать публичное превью канала Optimac и обогащать сигналы данными
MEXC (объём, open interest, funding):

```bash
python -m mpf.cli signals --pumps-only --limit 12
python -m mpf.cli signals --min-fp 30      # только с высокой вероятностью отката
```

```
     # token          move    RSI  ATR_K   ADX   FP  OI%    density    MEXC OI     vol24    fund%
 41909 Q          P +12.7%     82   9.24    22    3   75       0.04      46104      604K  0.0251
 41916 GP         P +13.8%  59.91   2.37 11.80   75   95       0.01   12937326      235K -0.0135
```

Парсер работает на stdlib (`urllib` + регулярки), без API-ключей и без доступа
к аккаунту. Open interest берётся из `holdVol` тикера MEXC.

## Сигнальный paper-раннер

Торгует **по сигналам канала** (виртуально): читает новые сигналы, фильтрует
и открывает шорт по текущей цене, управляя позицией той же логикой, что и
бэктест (усреднение/тейк/стоп/ликвидация).

```bash
python -m mpf.cli follow --config config/optimized_5m.json \
  --min-fp 0 --poll 30 --out data/follow

# строже: только сигналы, где RSI/ATR/ADX прошли, и высокая вероятность отката
python -m mpf.cli follow --min-fp 50 --min-oi 80 --require-ok
```

На старте старые сигналы только запоминаются — бот не «догоняет» прошлое.
Лог: `data/follow/trades.jsonl`, состояние: `data/follow/state.json`.

## Автосбор архива и запуск как сервис

MEXC хранит ~2 суток истории, поэтому архив надо копить. Скрипт дописывает
данные с дедупликацией:

```bash
scripts/collect_archive.sh          # TOP=150 DAYS=2 TIMEFRAME=1m
```

Готовые systemd-юниты в `deploy/`:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mpf-collect.timer   # сбор архива каждые 2ч
systemctl --user enable --now mpf-follow.service  # сигнальный paper-раннер
systemctl --user status mpf-follow.service
```

> В юнитах путь `%h/Документы/mexc-pump-fader` — поправь под своё расположение.

## Telegram-бот статистики

Бот показывает статистику прогонов, свежие сигналы канала и уведомляет о
закрытых сделках. Токен берётся из `.env` (`MPF_TELEGRAM_TOKEN`).

```bash
python -m mpf.cli bot --data-dir data --run follow
```

Команды и кнопки меню:

| Команда | Что делает |
|---|---|
| `/menu` | Inline-меню |
| `/stats` | Equity, winrate, PnL, просадка, ликвидации |
| `/positions` | Открытые виртуальные позиции |
| `/trades` | Последние закрытые сделки |
| `/signals` | Свежие сигналы канала (PUMP) |
| `/status` | Какие прогоны и файлы есть |

Уведомления о новых сделках приходят автоматически. Chat id можно не задавать:
бот запомнит чат после первой команды `/start`.

Запуск как сервис — `deploy/mpf-bot.service` (использует `.env`).

## CI

`.github/workflows/ci.yml` гоняет `ruff check` и `pytest` на Python 3.11 и 3.12
при push и pull request.

## Деплой на VPS (Docker Compose)

Для работы 24/7 (без включённого ПК) нужен VPS с Ubuntu/Debian и Docker.

```bash
# на сервере
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"   # перелогиниться

git clone <repo-url> mpf && cd mpf    # либо скопировать проект на сервер
cp .env.example .env                  # вписать MPF_TELEGRAM_TOKEN
mkdir -p data
docker compose up -d --build
docker compose ps
docker compose logs -f follow
```

Поднимаются три сервиса:

| Сервис | Роль |
|---|---|
| `collect` | история MEXC (5m + 1m) раз в 2 часа |
| `follow` | paper-торговля по сигналам канала |
| `bot` | Telegram-бот статистики |

Данные и архив — в `./data` (том, переживает пересборку). Обновление:
`git pull && docker compose up -d --build`.

### Авто-деплой через GitHub

Сервер раз в 5 минут проверяет репозиторий и сам пересобирается при изменениях
(cron-задача `mpf_autoupdate`). Чтобы выложить обновление, достаточно запушить:

```bash
scripts/deploy_push.sh
```

Скрипт делает «снимок» текущего кода и пушит в публичный репозиторий
`abbazobilvir23-bit/mexc-pump-fader`. Через ≤5 минут сервер подтянет изменения
(`git fetch` + `reset --hard` + `docker-compose up -d --build`), сохранив `data/`,
`.env` и `backups/`.

## Параметры

Правятся в `config/default.json` или через `--config my.json`.
Ключевые:

- `pump_threshold_pct` — минимальный рост за окно, %.
- `volume_mult` — во сколько раз объём должен превысить медиану.
- `rsi_overbought` — порог перегрева.
- `min/max_quote_volume_24h` — коридор «низкой ликвидности».
- `max_averages`, `avg_step_pct`, `avg_size_mult` — логика усреднения.
- `take_profit_pct` — маленький тейк.
- `stop_loss_pct` — жёсткий стоп (обязательно больше `avg_step_pct * max_averages`).
- `risk_per_trade_pct` — риск на сделку от equity.

## Тесты

```bash
pytest -q
ruff check src tests
mypy src
```

## Ограничения текущей версии

- Нет стакана — проскальзывание задаётся константой `slippage_pct`.
- Нет внутрибарного порядка — при неоднозначности приоритет у стопа.
- Funding-ставки MEXC не учтены.
- MEXC отдаёт лишь ~2 суток 1m-истории — длинный бэктест требует накопления архива.
- Нет live-исполнения: следующий этап — paper-trading, затем live с ключами.

## Roadmap

1. ✅ Core + бэктест + сканер.
2. ✅ Сбор истории + walk-forward оптимизация.
3. ✅ Плечо и симуляция ликвидации.
4. ✅ Paper-trading на живых данных (общая логика `LiveState`).
5. ✅ Фичи канала (RSI/ATR_K/ADX/X30, 5m) + чтение сигналов + OI.
6. ✅ Сигнальный paper-раннер + автосбор архива (systemd).
7. ⏳ Накопить архив, честный walk-forward, затем live с реальными ключами.