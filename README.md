# NSE Bull-Flag Scanner 📈

A Python-based technical analysis scanner for NSE-listed Indian stocks. Detects **Higher-High / Higher-Low** bull flag patterns with volume confirmation, generates candlestick charts, and produces an interactive HTML dashboard.

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run a full Nifty 50 scan
python nse_scanner.py --nifty50

# 3. Check output/
#    - 50 candlestick charts (PNG)
#    - scan_results.json
#    - dashboard.html
```

## Usage

```bash
# Scan specific stocks
python nse_scanner.py --stocks RELIANCE INFY TCS HDFCBANK

# Scan from a watchlist file
python nse_scanner.py --file watchlists/pharma.txt

# Custom date range
python nse_scanner.py --nifty50 --from 2025-06-01 --to 2026-03-28

# Filter by minimum score
python nse_scanner.py --nifty50 --min-score 50

# Adjust volume thresholds
python nse_scanner.py --nifty50 --hh-mult 1.3 --hl-mult 0.8

# Interactive mode (no arguments)
python nse_scanner.py
```

## How it works

### Pattern detection
The scanner identifies **bull flag** patterns by looking for sequences of Higher Highs (HH) and Higher Lows (HL) in price swings:

1. **Swing detection** — Finds local peaks and troughs using a 5-bar window on each side
2. **HH confirmation** — A higher high is *confirmed* if its volume ≥ 1.2× the 10-day rolling average (indicates strong breakout conviction)
3. **HL confirmation** — A higher low is *confirmed* if its volume ≤ 0.85× the 10-day rolling average (indicates quiet, healthy pullback — not panic selling)
4. **Scoring** — Composite score from 0–100:
   - 50% from HH confirmation rate
   - 50% from HL confirmation rate

### Score interpretation
| Score    | Verdict              | Meaning |
|----------|----------------------|---------|
| 70–100   | Strong bull flag      | Clean HH/HL structure, well-confirmed by volume |
| 45–69    | Moderate bullish      | Pattern exists but volume signals are mixed |
| 20–44    | Weak / forming        | Some structure visible, not yet confirmed |
| 0–19     | No bull flag           | No meaningful HH/HL pattern detected |

### Technical indicators
- **21-DMA** (blue) — Short-term trend
- **50-DMA** (orange) — Medium-term trend
- **200-DMA** — Long-term trend
- **RSI(14)** — Momentum oscillator
- **Bollinger Bands(20,2)** — Volatility envelope
- **Volume 10-DMA** — Volume trend baseline

### Chart markers
- 🟢 **Green ▲** — Volume-confirmed higher low
- 🟢 **Green ▼** — Volume-confirmed higher high
- ⚪ **Grey ▲/▼** — Unconfirmed swing (volume didn't meet threshold)

## Project structure

```
nse-scanner/
├── CLAUDE.md              # Instructions for Claude Code
├── README.md              # This file
├── requirements.txt       # Python dependencies
├── nse_scanner.py         # Main scanner script
├── watchlists/
│   ├── nifty50.txt        # Nifty 50 constituents
│   ├── pharma.txt         # Pharma sector stocks
│   ├── it.txt             # IT sector stocks
│   └── banking.txt        # Banking sector stocks
└── output/                # Generated charts, JSON, and dashboard
```

## Using with Claude Code

This project includes a `CLAUDE.md` file with persistent instructions. When you open the project folder in Claude Code, it will automatically understand the project structure and conventions.

**Example prompts for Claude Code:**
- "Scan the Nifty 50 and show me the top 10 scorers"
- "Add head-and-shoulders pattern detection"
- "Scan the pharma watchlist and compare results"
- "Add RSI divergence detection to the scanner"
- "Create a weekly scheduled scan that emails results"

## Data source

Uses [jugaad-data](https://github.com/jugaad-py/jugaad-data) — a free, no-API-key library that fetches OHLCV data directly from NSE India. No registration or API keys needed.

## Disclaimer

This tool is for **educational and research purposes only**. It is not investment advice. Always do your own research and consult a qualified financial advisor before making investment decisions.
