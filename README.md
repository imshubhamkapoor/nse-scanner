# NSE Bull-Flag Scanner 📈

Technical-analysis scanner for NSE-listed Indian stocks. Detects **Higher-High / Higher-Low** (bull-flag) structures with volume confirmation, scores each stock, and surfaces results through both a CLI scanner and an interactive Streamlit dashboard.

## Features at a glance

- **Two surfaces** — `nse_scanner.py` for batch CLI scans + `app.py` for an interactive Streamlit dashboard
- **15 pre-built watchlists** auto-generated from live Nifty index constituents (Nifty 50, Next 50, Midcap 150, Smallcap 250, plus 11 sector indices) — filtered to ~476 liquid stocks (`avg vol ≥ 100k`)
- **Bull-flag scoring** — 0–100 composite from HH/HL volume confirmation rates + DMA position + trend slope
- **Indicators** — 21/50/**200-DMA**, **RSI(14)** with overbought/oversold bands, volume bars, swing markers
- **Streamlit dashboard** with four tabs:
  - **Scan Results** — color-coded leaderboard with score + RSI badges, sector filter, symbol search, CSV export, full chart per stock with DMA Status row
  - **Sector Analysis** — average score per sector
  - **Screener** — live filter on score / RSI range / above-50DMA / sector / min volume
  - **Compare** — pick 2-3 stocks, see candlesticks side-by-side and a normalized % return chart

## Quick start

```bash
# 1. Install
pip install -r requirements.txt

# 2. (Optional) Rebuild watchlists from live NSE indices — ~30 seconds
python build_watchlists.py

# 3a. Streamlit dashboard
streamlit run app.py

# 3b. Or CLI scan
python nse_scanner.py --nifty50
```

CLI output lands in `output/`:
- 50 candlestick charts (PNG, with swing markers + DMAs)
- `results.json` — full scan data
- `dashboard.html` — standalone HTML dashboard

## CLI reference

```bash
# Preset watchlist
python nse_scanner.py --nifty50

# Specific stocks
python nse_scanner.py --stocks RELIANCE INFY TCS HDFCBANK

# Any watchlist file (one symbol per line)
python nse_scanner.py --file watchlists/nifty_pharma.txt

# Custom date range (defaults to last 180 days)
python nse_scanner.py --nifty50 --from 2025-06-01 --to 2026-03-28

# Score / volume threshold tuning
python nse_scanner.py --nifty50 --min-score 50 --hh-mult 1.3 --hl-mult 0.8

# Interactive mode (no flags) — falls back to embedded NIFTY50 constant
python nse_scanner.py
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--stocks` | — | Space-separated NSE symbols |
| `--file` | — | Path to a text file with one symbol per line |
| `--nifty50` | — | Shortcut for `--file watchlists/nifty50.txt` |
| `--from`, `--to` | last 180 days | YYYY-MM-DD date range |
| `--hh-mult` | `1.2` | HH volume threshold (× 10-day avg) |
| `--hl-mult` | `0.85` | HL volume threshold (× 10-day avg) |
| `--min-score` | `0` | Filter results below this score |
| `--output` | `output` | Output directory |

## How scoring works

The scanner identifies **bull-flag** structures by walking through swing pivots and grading each transition:

1. **Swing detection** — Local extrema with a 5-bar window on each side (`order=5`)
2. **Higher High (HH)** confirmation — current high > previous high **and** volume ≥ `1.2 ×` 10-day avg (indicates strong breakout conviction)
3. **Higher Low (HL)** confirmation — current low > previous low **and** volume ≤ `0.85 ×` 10-day avg (indicates quiet, healthy pullback — not panic selling)
4. **Composite score** out of 100:
   - HH confirmation rate (up to 30 pts)
   - HL confirmation rate (up to 30 pts)
   - Price above 21-DMA (15 pts)
   - Price above 50-DMA (10 pts)
   - 50-DMA slope (up to 15 pts)

| Score | Verdict | Meaning |
|-------|---------|---------|
| 70–100 | **Strong bull flag** | Clean HH/HL structure, well-confirmed by volume, price above rising 50-DMA |
| 50–69 | **Emerging bullish** | Pattern forming, needs more confirmation |
| 30–49 | **Weak / mixed** | Partial structure, divergent signals |
| 0–29 | **Bearish or sideways** | No meaningful bull-flag pattern |

### Chart conventions
- 🟢 **Green ▲** — Volume-confirmed higher low
- 🔴 **Red ▼** — Volume-confirmed higher high
- ⚪ **Grey ▲/▼** — Swing detected but volume didn't meet threshold
- **Blue line** — 21-DMA · **Orange** — 50-DMA · **Red dashed** — 200-DMA
- **Purple line** (subplot) — RSI(14) with 30 / 70 reference bands

## Streamlit dashboard

```bash
streamlit run app.py
```

### Sidebar
- Watchlist picker grouped into **Broad Market** (Nifty 50, Next 50, Midcap 150, Smallcap 250, All Liquid Stocks) and **Sectors** expanders, each radio showing live counts e.g. `Nifty 50 (50)`
- For lists with 100+ stocks, a **"Scan top N by volume"** slider appears (default 50) — ranks by 20-day average volume from `stock_metadata.csv`
- Custom symbols text-area + date range picker
- **Scan** button kicks off; results stay in session state across reruns

### Scan Results tab
- 4 metric cards (Total / Avg Score / Above 50-DMA / Top Scorer) above the leaderboard
- Color-coded leaderboard: Score (green ≥50, yellow ≥30, red <30) + RSI (red <30 oversold, green >70 overbought)
- Sector filter + symbol search input + CSV download (respects current filters)
- Click any stock → 3-row interactive Plotly chart (price+DMAs+swing markers · volume · RSI) + a **DMA Status** row showing % distance to each DMA with green/red arrows

### Other tabs
- **Sector Analysis** — bar chart of average score per sector
- **Screener** — live filter controls (min score, RSI range, above-50DMA, sectors, min avg-volume); `X of Y stocks match` count + filtered table with same color coding
- **Compare** — pick 2-3 stocks; renders candlesticks side-by-side via `st.columns` plus a normalized `% return from start of range` line chart underneath

## Watchlists

Run `python build_watchlists.py` to (re)generate. The script:

1. Fetches constituents of 15 Nifty indices via `NSELive.live_index()` (15 calls, 0.3s spacing)
2. Pulls 20 trading days of bhavcopy data and computes 20-day average volume per symbol
3. Filters to `avg_volume ≥ 100,000` shares
4. Writes one `*.txt` per index plus `all_liquid.txt` and a metadata CSV

**Generated files:**
```
watchlists/
├── nifty50.txt              ├── nifty_bank.txt
├── nifty_next50.txt         ├── nifty_it.txt
├── nifty_midcap150.txt      ├── nifty_pharma.txt
├── nifty_smallcap250.txt    ├── nifty_fmcg.txt
├── all_liquid.txt           ├── nifty_auto.txt
├── stock_metadata.csv       ├── nifty_metal.txt
                             ├── nifty_energy.txt
                             ├── nifty_realty.txt
                             ├── nifty_psu_bank.txt
                             ├── nifty_media.txt
                             └── nifty_infra.txt
```

`stock_metadata.csv` columns: `symbol, indices, avg_volume, sector` — used by the Streamlit "Scan top N by volume" slider.

## Project structure

```
nse-scanner/
├── README.md                 # This file
├── CLAUDE.md                 # Project guide for Claude Code
├── requirements.txt
├── nse_scanner.py            # Main scanner: detection, scoring, chart/JSON/HTML output
├── app.py                    # Streamlit dashboard (4 tabs)
├── build_watchlists.py       # Index-driven liquid-stock watchlist builder
├── watchlists/               # Generated watchlists + metadata
├── .streamlit/
│   └── config.toml           # runOnSave for auto-reload (no secrets here)
└── output/                   # Charts (PNG) + results.json + dashboard.html
```

## Data source

[jugaad-data](https://github.com/jugaad-py/jugaad-data) — free, no API key, scrapes NSE India directly. Per-call delays are baked in (`0.5s` for stocks, `0.3s` for index/bhavcopy fetches) to stay polite.

## Tech stack

`pandas`, `numpy`, `plotly`, `mplfinance`, `streamlit`, `jugaad-data`. No paid data sources, no API keys.

## Using with Claude Code

`CLAUDE.md` provides project conventions. Useful prompts:
- "Scan the Nifty 50 and show me the top 5 scorers"
- "Add MACD divergence to the scanner and surface it in the Screener tab"
- "Compare the top 3 IT stocks for the last 6 months"
- "Add a backtest of the bull-flag signal on Nifty Midcap 150"

## Disclaimer

For **educational and research purposes only**. Not investment advice. Past pattern detection ≠ future returns. Always do your own research.
