# Architecture

## Layers

```text
main.py (application entry point)
  ├── gui.py ──> AppBTC and Tkinter/CustomTkinter interface
  │                 │
  │                 └── motor.py ──> MotorSimulador / orchestration
  ├── fetcher.py ──> CoinGecko / Binance / local quote cache
  ├── strategy.py ──> decision from Estado + current BRL/BTC price
  ├── simulator.py ──> applies decision to Estado and creates Trade/CashFlow
  ├── trading_analytics.py ──> FIFO P&L and risk metrics
  └── models.py ──> Estado, Trade, CashFlow data structures
                         │
                         └── JSON files under data/
```

`main.py` is the application entry point. `gui.py` owns the Tkinter UI, and
`motor.py` owns application orchestration and persistence through
`MotorSimulador`. The strategy is a decision layer; it does not mutate state or
perform I/O. The simulator applies manual and automatic operations to
in-memory models.

## Market and trade flow

1. The motor requests a quote from CoinGecko, then Binance. If both online
   sources fail, a cached quote can be displayed but is marked stale and cannot
   trigger operations.
2. For a current quote, `strategy.decidir_acao` evaluates stop-loss first,
   followed by take-profit and extra-buy conditions.
3. `simulator.py` applies a selected action to `Estado`, including the configured
   fee, updates balances/counters and returns a `Trade`. Manual deposits and
   withdrawals instead create `CashFlow` events and do not create trades.
4. The motor serializes modifications under its lock. Trade and cash-flow
   operations persist using the transaction journal described below; normal
   market iterations update the state and trade-history files and append an
   equity sample in memory.

## Transaction journal and recovery

Trade and cash-flow persistence first writes a complete snapshot to the
configured `trade_journal_file` (normally `data/trade_transaction.json`). It
then atomically replaces the state and relevant history files and removes the
journal only after all writes succeed. If a final write fails, the journal is
left in place and the motor reports a pending persistence error so further
operations are blocked. On startup, recovery validates the journal, atomically
rewrites the included state/history (and cash flow when present), then removes
the journal. Invalid saved data stops startup rather than silently replacing
the files with empty data.

Each individual JSON file is written to a temporary file in the destination
directory, flushed and synchronized, then installed with `os.replace`. The
equity history is saved separately and uses trailing debounce for ordinary
samples; forced samples are written immediately.

## Equity, NAV and benchmark

An equity sample contains timestamp, price (BRL/BTC), BRL cash, BTC balance,
total equity in BRL, NAV, units, benchmark BTC, benchmark value in BRL, and
external flow in BRL. The first sample initializes units to equity and NAV to
1. Later deposits/withdrawals adjust units by `flow / previous_NAV`, keeping
NAV returns independent of external capital changes. Portfolio equity is
`cash + BTC × price`.

The buy-and-hold benchmark tracks BTC units separately. At each sample it
adjusts those units by `flow / current_price`, then values the benchmark as
`benchmark_BTC × current_price`. The persisted series is capped at 15,000
samples. The UI may downsample larger series for plotting; it does not modify
the persisted data.

## Risk metrics

`trading_analytics.calcular_metricas_risco` computes maximum drawdown from the
recorded NAV peaks and annualized Sharpe from daily NAV returns (available when
at least two daily returns have nonzero variance). Win rate and profit factor
use realized, sell-side P&L matched to buy lots in FIFO order, including sale
fees; unmatched sales have no calculable FIFO result. Average holding time uses
quantity-weighted FIFO holding durations in hours. Average BTC exposure is the
BTC value divided by total equity, clipped to 0–100% and weighted by elapsed
time between samples.
