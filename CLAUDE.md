# NSE Bull-Flag Scanner — Project Instructions

## What this project does
A Python-based technical analysis scanner for NSE (National Stock Exchange of India) stocks.
It detects Higher-High / Higher-Low (bull flag) structures with volume confirmation,
scores each stock, generates candlestick charts, and produces an interactive HTML dashboard.

## Key files
- `nse_scanner.py` — Main scanner script (CLI + interactive modes)
- `dashboard.py` — Standalone dashboard generator (reads scan_results.json)
- `watchlists/` — Text files with stock symbols (one per line)
- `output/` — Charts (.png), dashboard (.html), and results (.json) go here

## How to run
```bash
# Full Nifty 50 scan (defaults to last 180 days)
python nse_scanner.py --nifty50

# Specific stocks
python nse_scanner.py --stocks RELIANCE INFY TCS HDFCBANK

# From a watchlist file
python nse_scanner.py --file watchlists/pharma.txt

# Custom date range + score filter + volume thresholds + output dir
python nse_scanner.py --nifty50 \
    --from 2025-06-01 --to 2026-03-28 \
    --min-score 50 --hh-mult 1.2 --hl-mult 0.85 \
    --output output

# Regenerate dashboard from existing results
python dashboard.py output/scan_results.json
```

CLI flags: `--stocks`, `--file`, `--nifty50`, `--from`, `--to`,
`--hh-mult` (default 1.2), `--hl-mult` (default 0.85),
`--min-score` (default 0), `--output` (default `output`).
Default lookback is 180 days when `--from` is omitted.
With no input flags, falls back to the embedded `NIFTY50` constant.

## Dependencies
```bash
pip install jugaad-data mplfinance pandas numpy
```

## Technical details
- **Data source**: jugaad_data (free, no API key needed, scrapes NSE)
- **Swing detection**: Local extrema with order=5 (5 bars each side)
- **HH confirmation**: Volume >= 1.2× rolling 10-day average (strong breakout)
- **HL confirmation**: Volume <= 0.85× rolling 10-day average (quiet pullback)
- **Scoring**: 0–100 composite (50% HH confirmation rate + 50% HL confirmation rate)
- **Indicators**: 21-DMA, 50-DMA, 200-DMA, RSI(14), Bollinger Bands(20,2)

## Conventions
- All stock symbols are UPPERCASE NSE symbols (e.g., RELIANCE, not reliance.ns)
- Dates are YYYY-MM-DD format
- Charts saved as {SYMBOL}.png in the output directory
- Be polite to NSE servers — 0.5s delay between fetches
- Use `matplotlib Agg` backend (no display needed)

## When adding new patterns
1. Add detection function in the `# PATTERN DETECTION` section
2. Return a dict with `{name, signals[], score}`
3. Register it in `scan_stock()` so it runs alongside bull-flag detection
4. Add markers to the chart in `save_chart()`
5. Update the dashboard to show the new pattern column
