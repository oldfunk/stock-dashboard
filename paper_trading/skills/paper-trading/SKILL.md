---
name: paper-trading
description: "Use when operating the Paper Trading Framework. Drives local backtesting, live paper trading, order placement, and account inspection through the Hermes agent."
version: 0.1.0
author: oldfunk
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [paper-trading, trading, backtesting, broker, strategy]
    homepage: https://github.com/oldfunk/paper-trading
---

# Paper Trading Framework

Local event-driven paper trading framework for A-share markets. This skill defines how the Hermes agent interacts with the trading system.

## When to Use

- User asks to run a trading session, check account status, place orders, or inspect history
- Cron job triggers a daily settlement run
- Agent needs to analyze portfolio performance or risk

## Core Invariants

- **Never fabricate market data** — always fetch via `AkshareFetcher` or read from local SQLite
- **Never bypass risk checks** — all orders must go through `RiskManager`
- **T+1 is mandatory** — `available_volume` must be 0 for same-day buys; `unfreeze_t1()` only runs on the next settlement
- **Costs are real** — commission (0.025%, min 5 CNY), stamp duty (0.05% sell-side), transfer fee (0.001%) must be deducted
- **Slippage is configurable** — default 0.01 CNY fixed or 0.1% percentage

## CLI Interface

All commands are invoked via `python -m paper_trading.hermes_bridge` from the project root.

### Status

```bash
python -m paper_trading.hermes_bridge status --json
```

Returns: cash, market_value, total_value, pnl, pnl_pct, positions[]

### Run Daily Settlement

```bash
python -m paper_trading.hermes_bridge run --symbols 600519 000858 --json
```

Executes: data update → signal generation → risk check → order execution → T+1 unfreeze → NAV record

### Place Order

```bash
# Buy at market price (uses latest close)
python -m paper_trading.hermes_bridge buy --symbol 600519 --volume 100 --json

# Sell at limit price
python -m paper_trading.hermes_bridge sell --symbol 600519 --volume 100 --price 1500.00 --json
```

### LLM (ask-only, never trades)

```bash
# Save provider (Key goes to local secrets.local.json only, 0600)
python -m paper_trading.hermes_bridge llm config --preset deepseek --api-key xxx --model deepseek-chat

# List remote models / ask once (logged to op_log, Key never logged)
python -m paper_trading.hermes_bridge llm models --json
python -m paper_trading.hermes_bridge llm ask --prompt "..." --json
```

Presets: deepseek / qwen / moonshot / glm / doubao / openai / custom (any OpenAI-compatible base-url; model name can always be typed manually).

### AI trader (one decision per day; mutually exclusive with `run` on the same account)

```bash
python -m paper_trading.hermes_bridge agent run --dry-run --json
python -m paper_trading.hermes_bridge agent run --json
# plan during closed market, auto-executed at open (no ledger touch)
python -m paper_trading.hermes_bridge agent run --plan-only --json
```

### View History

```bash
# NAV history
python -m paper_trading.hermes_bridge nav --json

# Order history
python -m paper_trading.hermes_bridge history --type orders --limit 20 --json

# Fill history
python -m paper_trading.hermes_bridge history --type fills --limit 20 --json
```

### Cron Mode

```bash
python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858 --json
```

Same as `run` but designed for Hermes cronjob invocation.

## Hermes Cron Integration

To schedule daily settlement via Hermes cron:

```bash
hermes cron add \
  --name "paper-trading-daily" \
  --schedule "0 16 * * 1-5" \
  --command "cd /path/to/paper-trading && python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858 --json" \
  --no-agent
```

Or with agent analysis:

```bash
hermes cron add \
  --name "paper-trading-analysis" \
  --schedule "30 16 * * 1-5" \
  --command "cd /path/to/paper-trading && python -m paper_trading.hermes_bridge cron-run --symbols 600519 000858 --json" \
  --context-from "paper-trading-daily"
```

## Database Schema

### data.db (Market Data)

- `daily_bars(symbol, timestamp, open, high, low, close, volume, turn)` — OHLCV + turnover
- `stock_pool(symbol, name, added_at)` — tracked stocks

### paper_account.db (Account Ledger)

- `account(id, cash, initial_cash, created_at)` — account balance
- `positions(symbol, total_volume, available_volume, avg_cost, last_update)` — holdings with T+1 freeze
- `orders(order_id, symbol, direction, volume, order_type, limit_price, status, ...)` — order history
- `fills(fill_id, order_id, symbol, direction, volume, price, commission, stamp_duty, transfer_fee, timestamp)` — execution history
- `nav_history(id, timestamp, cash, market_value, total_value, pnl, pnl_pct)` — NAV snapshots
- `t1_freeze(id, symbol, volume, freeze_date, unfreeze_date, is_unfrozen)` — T+1 freeze records

## Trading Rules

| Rule | Value |
|------|-------|
| Commission rate | 0.025% |
| Commission minimum | 5 CNY per order |
| Stamp duty | 0.05% (sell-side only) |
| Transfer fee | 0.001% |
| Slippage (fixed) | 0.01 CNY |
| Slippage (percentage) | 0.1% |
| T+1 freeze | Same-day buys unfrozen next day |

## Risk Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| max_single_order_value | 200,000 CNY | Max value per single order |
| max_position_pct | 30% | Max single-stock position |
| max_total_position_pct | 95% | Max total portfolio exposure |
| max_drawdown_pct | 20% | Max drawdown before halt |

## Strategy: MA Cross (MA5/MA20)

- **Golden cross** (MA5 crosses above MA20): BUY
- **Death cross** (MA5 crosses below MA20): SELL
- Default volume: 100 shares per signal

## File Structure

```
paper_trading/
├── hermes_bridge.py           # Hermes Agent adapter (CLI + API)
├── main.py                    # Standalone entry point
├── config.yaml                # Configuration
├── models/types.py            # Data types
├── data/                      # Data module
│   ├── akshare_fetcher.py     # Market data fetcher
│   └── db_manager.py          # SQLite manager
├── strategy/                  # Strategy module
│   ├── base_strategy.py       # Abstract base
│   └── ma_cross_strategy.py   # MA cross strategy
├── broker/                    # Broker module
│   └── paper_broker.py        # Paper matching engine
├── portfolio/                 # Portfolio module
│   └── portfolio.py           # NAV & positions
├── risk/                      # Risk module
│   └── risk_manager.py        # Risk checks
└── utils/                     # Utilities
    └── logger.py              # Logging
```

## Verification

After any trading operation, verify:

1. Order status is `filled` or `rejected` (not `pending`)
2. `available_volume` is 0 for same-day buys
3. Commission >= 5 CNY when amount * 0.025% < 5
4. Stamp duty is only on sells
5. NAV history is recorded
6. All state changes are persisted to SQLite
