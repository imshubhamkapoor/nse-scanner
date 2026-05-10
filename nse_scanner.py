#!/usr/bin/env python3
"""
NSE Bull Flag Scanner - v2.0
Detects Higher-High / Higher-Low patterns with volume confirmation.
Generates candlestick charts + an HTML dashboard report.

Usage:
  python nse_scanner.py                              # default Nifty 50
  python nse_scanner.py --stocks RELIANCE INFY TCS
  python nse_scanner.py --file watchlist.txt
  python nse_scanner.py --from 2025-09-01 --to 2026-03-28 --min-score 40
"""

import argparse, os, sys, json, math, warnings, traceback
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# -- Nifty 50 constituents (March 2026) --
NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NTPC",
    "WIPRO", "TATAMOTORS", "M&M", "POWERGRID", "ADANIENT",
    "ADANIPORTS", "ONGC", "JSWSTEEL", "TATASTEEL", "TECHM",
    "INDUSINDBK", "NESTLEIND", "COALINDIA", "BAJAJFINSV", "BAJAJ-AUTO",
    "GRASIM", "CIPLA", "DRREDDY", "APOLLOHOSP", "EICHERMOT",
    "SBILIFE", "BRITANNIA", "DIVISLAB", "TATACONSUM", "HEROMOTOCO",
    "HINDALCO", "BPCL", "SHRIRAMFIN", "BEL", "TRENT",
]

SECTOR_MAP = {
    "RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Banking", "INFY": "IT",
    "ICICIBANK": "Banking", "HINDUNILVR": "FMCG", "ITC": "FMCG",
    "SBIN": "Banking", "BHARTIARTL": "Telecom", "KOTAKBANK": "Banking",
    "LT": "Infra", "AXISBANK": "Banking", "BAJFINANCE": "Finance",
    "ASIANPAINT": "Consumer", "MARUTI": "Auto", "HCLTECH": "IT",
    "SUNPHARMA": "Pharma", "TITAN": "Consumer", "ULTRACEMCO": "Cement",
    "NTPC": "Power", "WIPRO": "IT", "TATAMOTORS": "Auto",
    "M&M": "Auto", "POWERGRID": "Power", "ADANIENT": "Conglomerate",
    "ADANIPORTS": "Infra", "ONGC": "Energy", "JSWSTEEL": "Metals",
    "TATASTEEL": "Metals", "TECHM": "IT", "INDUSINDBK": "Banking",
    "NESTLEIND": "FMCG", "COALINDIA": "Mining", "BAJAJFINSV": "Finance",
    "BAJAJ-AUTO": "Auto", "GRASIM": "Cement", "CIPLA": "Pharma",
    "DRREDDY": "Pharma", "APOLLOHOSP": "Healthcare", "EICHERMOT": "Auto",
    "SBILIFE": "Insurance", "BRITANNIA": "FMCG", "DIVISLAB": "Pharma",
    "TATACONSUM": "FMCG", "HEROMOTOCO": "Auto", "HINDALCO": "Metals",
    "BPCL": "Energy", "SHRIRAMFIN": "Finance", "BEL": "Defence",
    "TRENT": "Retail", "AUROPHARMA": "Pharma", "TORNTPHARM": "Pharma",
    "LUPIN": "Pharma", "BIOCON": "Pharma", "ALKEM": "Pharma",
}

# -- Data fetching --
def fetch_stock_data(symbol, start_date, end_date):
    from jugaad_data.nse import stock_df
    df = stock_df(symbol=symbol, from_date=start_date, to_date=end_date, series="EQ")
    df = df.rename(columns={
        "DATE": "Date", "OPEN": "Open", "HIGH": "High",
        "LOW": "Low", "CLOSE": "Close", "VOLUME": "Volume",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    return df


# -- Swing detection --
def detect_swings(df, lookback=5):
    highs, lows = [], []
    prices_high = df["High"].values
    prices_low = df["Low"].values
    for i in range(lookback, len(df) - lookback):
        if prices_high[i] == max(prices_high[i - lookback : i + lookback + 1]):
            highs.append(i)
        if prices_low[i] == min(prices_low[i - lookback : i + lookback + 1]):
            lows.append(i)
    return highs, lows


# -- Volume confirmation --
def confirm_volume(df, idx, vol_avg_period=20, hh_mult=1.2, hl_mult=0.85, is_high=True):
    start = max(0, idx - vol_avg_period)
    avg_vol = df["Volume"].iloc[start:idx].mean()
    if avg_vol == 0:
        return False
    current_vol = df["Volume"].iloc[idx]
    if is_high:
        return current_vol >= hh_mult * avg_vol
    else:
        return current_vol <= hl_mult * avg_vol


# -- Pattern scoring --
def score_stock(df, swing_highs, swing_lows, hh_mult=1.2, hl_mult=0.85):
    score = 0
    details = {}

    # HH analysis
    hh_pairs = []
    for i in range(1, len(swing_highs)):
        prev_i, curr_i = swing_highs[i - 1], swing_highs[i]
        is_hh = df["High"].iloc[curr_i] > df["High"].iloc[prev_i]
        vol_ok = confirm_volume(df, curr_i, hh_mult=hh_mult, hl_mult=hl_mult, is_high=True)
        hh_pairs.append({"idx": curr_i, "is_hh": is_hh, "vol_confirmed": vol_ok, "valid": is_hh and vol_ok})

    valid_hh = sum(1 for h in hh_pairs if h["valid"])
    total_hh = sum(1 for h in hh_pairs if h["is_hh"])
    hh_score = min(30, (valid_hh / max(1, len(hh_pairs))) * 30) if hh_pairs else 0
    score += hh_score
    details["hh_valid"] = valid_hh
    details["hh_total"] = total_hh
    details["hh_pairs"] = hh_pairs

    # HL analysis
    hl_pairs = []
    for i in range(1, len(swing_lows)):
        prev_i, curr_i = swing_lows[i - 1], swing_lows[i]
        is_hl = df["Low"].iloc[curr_i] > df["Low"].iloc[prev_i]
        vol_ok = confirm_volume(df, curr_i, hh_mult=hh_mult, hl_mult=hl_mult, is_high=False)
        hl_pairs.append({"idx": curr_i, "is_hl": is_hl, "vol_confirmed": vol_ok, "valid": is_hl and vol_ok})

    valid_hl = sum(1 for h in hl_pairs if h["valid"])
    total_hl = sum(1 for h in hl_pairs if h["is_hl"])
    hl_score = min(30, (valid_hl / max(1, len(hl_pairs))) * 30) if hl_pairs else 0
    score += hl_score
    details["hl_valid"] = valid_hl
    details["hl_total"] = total_hl
    details["hl_pairs"] = hl_pairs

    # Moving averages
    df["DMA21"] = df["Close"].rolling(21).mean()
    df["DMA50"] = df["Close"].rolling(50).mean()
    df["VOL_DMA10"] = df["Volume"].rolling(10).mean()

    last = df.iloc[-1]
    above_21 = last["Close"] > last["DMA21"] if pd.notna(last["DMA21"]) else False
    above_50 = last["Close"] > last["DMA50"] if pd.notna(last["DMA50"]) else False

    if above_21:
        score += 15
    if above_50:
        score += 10

    details["above_21dma"] = bool(above_21)
    details["above_50dma"] = bool(above_50)
    details["last_close"] = round(float(last["Close"]), 2)
    details["dma21"] = round(float(last["DMA21"]), 2) if pd.notna(last["DMA21"]) else None
    details["dma50"] = round(float(last["DMA50"]), 2) if pd.notna(last["DMA50"]) else None

    # Trend slope
    if pd.notna(last["DMA50"]):
        dma50_vals = df["DMA50"].dropna().tail(20)
        if len(dma50_vals) >= 10:
            slope = (dma50_vals.iloc[-1] - dma50_vals.iloc[0]) / dma50_vals.iloc[0] * 100
            trend_pts = min(15, max(0, slope * 3))
            score += trend_pts
            details["dma50_slope_pct"] = round(slope, 2)
        else:
            details["dma50_slope_pct"] = None
    else:
        details["dma50_slope_pct"] = None

    details["score"] = round(score, 1)

    # Qualitative comment
    if score >= 70:
        details["comment"] = "Strong bull flag - HH/HL intact with volume"
    elif score >= 50:
        details["comment"] = "Emerging bullish - needs more confirmation"
    elif score >= 30:
        details["comment"] = "Weak / mixed signals"
    else:
        details["comment"] = "Bearish or sideways - no bull flag"

    # Price change
    if len(df) >= 2:
        details["price_chg_1d"] = round((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100, 2)
    else:
        details["price_chg_1d"] = 0.0
    if len(df) >= 22:
        details["price_chg_1m"] = round((df["Close"].iloc[-1] / df["Close"].iloc[-22] - 1) * 100, 2)
    else:
        details["price_chg_1m"] = 0.0
    if len(df) >= 66:
        details["price_chg_3m"] = round((df["Close"].iloc[-1] / df["Close"].iloc[-66] - 1) * 100, 2)
    else:
        details["price_chg_3m"] = 0.0

    return details


# -- Chart generation --
def generate_chart(df, symbol, details, output_dir):
    import mplfinance as mpf
    import matplotlib
    matplotlib.use("Agg")

    df_plot = df.copy()
    df_plot.index = pd.DatetimeIndex(df_plot["Date"])

    hh_markers = pd.Series(np.nan, index=df_plot.index)
    hl_markers = pd.Series(np.nan, index=df_plot.index)
    weak_hh = pd.Series(np.nan, index=df_plot.index)
    weak_hl = pd.Series(np.nan, index=df_plot.index)

    for h in details.get("hh_pairs", []):
        idx = h["idx"]
        if idx < len(df_plot):
            if h["valid"]:
                hh_markers.iloc[idx] = df_plot["High"].iloc[idx] * 1.01
            elif h["is_hh"]:
                weak_hh.iloc[idx] = df_plot["High"].iloc[idx] * 1.01

    for h in details.get("hl_pairs", []):
        idx = h["idx"]
        if idx < len(df_plot):
            if h["valid"]:
                hl_markers.iloc[idx] = df_plot["Low"].iloc[idx] * 0.99
            elif h["is_hl"]:
                weak_hl.iloc[idx] = df_plot["Low"].iloc[idx] * 0.99

    add_plots = []
    if hh_markers.notna().any():
        add_plots.append(mpf.make_addplot(hh_markers, type="scatter", markersize=80, marker="v",
                         color="#1D9E75", panel=0))
    if hl_markers.notna().any():
        add_plots.append(mpf.make_addplot(hl_markers, type="scatter", markersize=80, marker="^",
                         color="#378ADD", panel=0))
    if weak_hh.notna().any():
        add_plots.append(mpf.make_addplot(weak_hh, type="scatter", markersize=40, marker="v",
                         color="#1D9E75", alpha=0.3, panel=0))
    if weak_hl.notna().any():
        add_plots.append(mpf.make_addplot(weak_hl, type="scatter", markersize=40, marker="^",
                         color="#378ADD", alpha=0.3, panel=0))

    if "VOL_DMA10" in df_plot.columns and df_plot["VOL_DMA10"].notna().any():
        add_plots.append(
            mpf.make_addplot(df_plot["VOL_DMA10"], panel=1, color="#BA7517",
                             width=1.2, linestyle="--")
        )

    mc = mpf.make_marketcolors(up="#1D9E75", down="#E24B4A", edge="inherit",
                                wick="inherit", volume="in")
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", gridcolor="#cccccc",
                           figcolor="white", facecolor="white")

    score = details.get("score", 0)
    title = f"{symbol}  |  Score: {score}/100  |  {details.get('comment', '')}"

    filepath = os.path.join(output_dir, f"{symbol}_chart.png")
    plot_kwargs = dict(
        type="candle", style=s, mav=(21, 50), volume=True, title=title,
        figsize=(14, 7), savefig=dict(fname=filepath, dpi=120, bbox_inches="tight"),
    )
    if add_plots:
        plot_kwargs["addplot"] = add_plots
    mpf.plot(df_plot, **plot_kwargs)
    import matplotlib.pyplot as plt
    plt.close("all")
    return filepath


# -- Console output --
def progress_bar(current, total, width=30):
    pct = current / total
    filled = int(width * pct)
    bar = "=" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{total}"


def print_stock_summary(symbol, details):
    score = details.get("score", 0)
    bar_len = int(score / 100 * 20)
    bar = "#" * bar_len + "." * (20 - bar_len)
    print(f"\n  {'=' * 56}")
    print(f"  {symbol:<15} Score: {score:>5.1f}/100  [{bar}]")
    print(f"  {'=' * 56}")
    print(f"    HH valid/total : {details['hh_valid']}/{details['hh_total']}   |   HL valid/total : {details['hl_valid']}/{details['hl_total']}")
    dma21_str = f"{details['dma21']}" if details.get('dma21') else "N/A"
    dma50_str = f"{details['dma50']}" if details.get('dma50') else "N/A"
    print(f"    Close: Rs{details['last_close']:>10}   21-DMA: Rs{dma21_str:>10}   50-DMA: Rs{dma50_str:>10}")
    above21 = "Y" if details["above_21dma"] else "N"
    above50 = "Y" if details["above_50dma"] else "N"
    slope_str = f"{details['dma50_slope_pct']}%" if details.get('dma50_slope_pct') is not None else "N/A"
    print(f"    Above 21-DMA: {above21}   |   Above 50-DMA: {above50}   |   50-DMA slope: {slope_str}")
    print(f"    chg 1d: {details.get('price_chg_1d', 0):+.2f}%   1m: {details.get('price_chg_1m', 0):+.2f}%   3m: {details.get('price_chg_3m', 0):+.2f}%")
    print(f"    -> {details['comment']}")


# -- Dashboard HTML generation --
def generate_dashboard(results, output_dir, start_date, end_date):
    sorted_results = sorted(results, key=lambda x: x["details"]["score"], reverse=True)

    # Sector aggregation
    sector_scores = {}
    for r in sorted_results:
        sec = r.get("sector", "Other")
        if sec not in sector_scores:
            sector_scores[sec] = {"total": 0, "count": 0, "stocks": []}
        sector_scores[sec]["total"] += r["details"]["score"]
        sector_scores[sec]["count"] += 1
        sector_scores[sec]["stocks"].append(r["symbol"])

    sector_data = []
    for sec, d in sorted(sector_scores.items(), key=lambda x: x[1]["total"] / x[1]["count"], reverse=True):
        sector_data.append({
            "name": sec,
            "avg_score": round(d["total"] / d["count"], 1),
            "count": d["count"],
            "stocks": d["stocks"][:5],
        })

    # Score distribution
    buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    for r in sorted_results:
        s = r["details"]["score"]
        if s < 20: buckets["0-20"] += 1
        elif s < 40: buckets["20-40"] += 1
        elif s < 60: buckets["40-60"] += 1
        elif s < 80: buckets["60-80"] += 1
        else: buckets["80-100"] += 1

    max_bucket = max(buckets.values()) if max(buckets.values()) > 0 else 1

    # Summary stats
    scores = [r["details"]["score"] for r in sorted_results]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    max_score = max(scores) if scores else 0
    bullish_count = sum(1 for s in scores if s >= 50)
    bearish_count = sum(1 for s in scores if s < 30)

    # Build table rows
    table_rows = ""
    for rank, r in enumerate(sorted_results, 1):
        d = r["details"]
        s = d["score"]
        if s >= 70:
            badge = '<span class="badge bullish">Bullish</span>'
        elif s >= 50:
            badge = '<span class="badge emerging">Emerging</span>'
        elif s >= 30:
            badge = '<span class="badge neutral">Neutral</span>'
        else:
            badge = '<span class="badge bearish">Bearish</span>'

        chg1d = d.get("price_chg_1d", 0)
        chg1m = d.get("price_chg_1m", 0)
        chg3m = d.get("price_chg_3m", 0)
        chg1d_cls = "pos" if chg1d >= 0 else "neg"
        chg1m_cls = "pos" if chg1m >= 0 else "neg"
        chg3m_cls = "pos" if chg3m >= 0 else "neg"

        bar_color = "#1D9E75" if s >= 50 else "#BA7517" if s >= 30 else "#E24B4A"

        table_rows += f"""<tr>
            <td>{rank}</td>
            <td><strong>{r['symbol']}</strong><br><small class="muted">{r.get('sector','')}</small></td>
            <td>Rs {d['last_close']:,.2f}</td>
            <td class="{chg1d_cls}">{chg1d:+.2f}%</td>
            <td class="{chg1m_cls}">{chg1m:+.2f}%</td>
            <td class="{chg3m_cls}">{chg3m:+.2f}%</td>
            <td>{d['hh_valid']}/{d['hh_total']}</td>
            <td>{d['hl_valid']}/{d['hl_total']}</td>
            <td>{'Y' if d['above_21dma'] else 'N'}</td>
            <td>{'Y' if d['above_50dma'] else 'N'}</td>
            <td><div class="score-bar"><div class="score-fill" style="width:{s}%;background:{bar_color}"></div><span class="score-label">{s:.0f}</span></div></td>
            <td>{badge}</td>
        </tr>\n"""

    # Chart cards for top 10
    chart_cards = ""
    for r in sorted_results[:10]:
        chart_file = f"{r['symbol']}_chart.png"
        if os.path.exists(os.path.join(output_dir, chart_file)):
            chart_cards += f'<div class="chart-card"><img src="{chart_file}" alt="{r["symbol"]}"></div>\n'

    # Sector cards
    sector_cards = ""
    for s in sector_data:
        sc = "#1D9E75" if s["avg_score"] >= 50 else "#BA7517" if s["avg_score"] >= 30 else "#E24B4A"
        sector_cards += f'''<div class="sector-card">
            <div class="sec-name">{s["name"]}</div>
            <div class="sec-score" style="color:{sc}">{s["avg_score"]}</div>
            <div class="sec-detail">{s["count"]} stocks</div>
            <div class="sec-stocks">{", ".join(s["stocks"])}</div>
        </div>\n'''

    # Distribution bars
    dist_colors = ["#E24B4A", "#D85A30", "#BA7517", "#378ADD", "#1D9E75"]
    dist_bars = ""
    for (k, v), c in zip(buckets.items(), dist_colors):
        h = max(8, int(v / max_bucket * 80))
        dist_bars += f'''<div style="flex:1;text-align:center;">
            <div style="background:{c};height:{h}px;border-radius:4px 4px 0 0;position:relative;margin:0 2px;">
                <span style="position:absolute;top:-18px;left:0;right:0;font-size:12px;font-weight:500;">{v}</span>
            </div>
            <div style="font-size:11px;color:#888780;margin-top:4px;">{k}</div>
        </div>\n'''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NSE Bull Flag Scanner - Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f0; color: #2c2c2a; line-height: 1.6; }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; font-weight: 500; margin-bottom: 4px; }}
  .subtitle {{ color: #888780; font-size: 14px; margin-bottom: 24px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .metric {{ background: #fff; border-radius: 10px; padding: 18px 20px; border: 0.5px solid #e0dfd8; }}
  .metric .label {{ font-size: 12px; color: #888780; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric .value {{ font-size: 26px; font-weight: 500; margin-top: 4px; }}
  .metric .value.green {{ color: #1D9E75; }}
  .metric .value.red {{ color: #E24B4A; }}
  .metric .value.amber {{ color: #BA7517; }}
  .section {{ background: #fff; border-radius: 10px; border: 0.5px solid #e0dfd8; padding: 20px; margin-bottom: 20px; }}
  .section h2 {{ font-size: 16px; font-weight: 500; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 10px 8px; border-bottom: 1.5px solid #e0dfd8; color: #888780; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }}
  td {{ padding: 10px 8px; border-bottom: 0.5px solid #f0efe8; vertical-align: middle; }}
  tr:hover {{ background: #fafaf7; }}
  .pos {{ color: #1D9E75; font-weight: 500; }}
  .neg {{ color: #E24B4A; font-weight: 500; }}
  .muted {{ color: #888780; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; }}
  .badge.bullish {{ background: #E1F5EE; color: #0F6E56; }}
  .badge.emerging {{ background: #E6F1FB; color: #185FA5; }}
  .badge.neutral {{ background: #FAEEDA; color: #854F0B; }}
  .badge.bearish {{ background: #FCEBEB; color: #A32D2D; }}
  .score-bar {{ width: 80px; height: 18px; background: #f0efe8; border-radius: 9px; position: relative; overflow: hidden; display: inline-block; }}
  .score-fill {{ height: 100%; border-radius: 9px; }}
  .score-label {{ position: absolute; top: 0; left: 0; right: 0; text-align: center; font-size: 11px; font-weight: 500; line-height: 18px; color: #2c2c2a; }}
  .sector-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }}
  .sector-card {{ padding: 14px; border-radius: 8px; border: 0.5px solid #e0dfd8; }}
  .sector-card .sec-name {{ font-weight: 500; font-size: 14px; }}
  .sector-card .sec-score {{ font-size: 22px; font-weight: 500; margin: 4px 0; }}
  .sector-card .sec-detail {{ font-size: 12px; color: #888780; }}
  .sector-card .sec-stocks {{ font-size: 11px; color: #888780; margin-top: 2px; }}
  .chart-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr)); gap: 16px; margin-top: 16px; }}
  .chart-card {{ border: 0.5px solid #e0dfd8; border-radius: 8px; overflow: hidden; }}
  .chart-card img {{ width: 100%; display: block; }}
  .footer {{ text-align: center; color: #b4b2a9; font-size: 12px; margin-top: 32px; padding-top: 16px; border-top: 0.5px solid #e0dfd8; }}
</style>
</head>
<body>
<div class="container">
  <h1>NSE bull flag scanner - dashboard</h1>
  <p class="subtitle">Scan period: {start_date} to {end_date} | {len(sorted_results)} stocks analysed | Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

  <div class="metrics">
    <div class="metric"><div class="label">Stocks scanned</div><div class="value">{len(sorted_results)}</div></div>
    <div class="metric"><div class="label">Average score</div><div class="value amber">{avg_score}</div></div>
    <div class="metric"><div class="label">Highest score</div><div class="value green">{max_score:.0f}</div></div>
    <div class="metric"><div class="label">Bullish (score 50+)</div><div class="value green">{bullish_count}</div></div>
    <div class="metric"><div class="label">Bearish (score &lt;30)</div><div class="value red">{bearish_count}</div></div>
  </div>

  <div class="section">
    <h2>Score distribution</h2>
    <div style="display:flex;gap:6px;align-items:flex-end;height:110px;margin-top:12px;padding-top:20px;">
      {dist_bars}
    </div>
  </div>

  <div class="section">
    <h2>Sector breakdown</h2>
    <div class="sector-grid">
      {sector_cards}
    </div>
  </div>

  <div class="section">
    <h2>Full leaderboard</h2>
    <div style="overflow-x: auto;">
    <table>
      <thead><tr>
        <th>#</th><th>Stock</th><th>Close</th><th>chg 1D</th><th>chg 1M</th><th>chg 3M</th>
        <th>HH</th><th>HL</th><th>21D</th><th>50D</th><th>Score</th><th>Signal</th>
      </tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
    </div>
  </div>

  <div class="section">
    <h2>Candlestick charts - top 10 by score</h2>
    <p class="muted" style="margin-bottom:8px; font-size:12px;">green arrow = valid HH | blue arrow = valid HL | faded = unconfirmed swings</p>
    <div class="chart-grid">
      {chart_cards}
    </div>
  </div>

  <div class="footer">
    NSE Bull Flag Scanner v2.0 | Data source: jugaad_data (NSE) | For educational purposes only - not financial advice
  </div>
</div>
</body>
</html>"""

    filepath = os.path.join(output_dir, "dashboard.html")
    with open(filepath, "w") as f:
        f.write(html)
    return filepath


# -- Main runner --
def main():
    parser = argparse.ArgumentParser(description="NSE Bull Flag Scanner v2.0")
    parser.add_argument("--stocks", nargs="+", help="Stock symbols to scan")
    parser.add_argument("--file", help="Text file with one symbol per line")
    parser.add_argument("--nifty50", action="store_true",
                        help="Shortcut for --file watchlists/nifty50.txt")
    parser.add_argument("--from", dest="start", help="Start date (YYYY-MM-DD)", default=None)
    parser.add_argument("--to", dest="end", help="End date (YYYY-MM-DD)", default=None)
    parser.add_argument("--hh-mult", type=float, default=1.2, help="HH volume multiplier")
    parser.add_argument("--hl-mult", type=float, default=0.85, help="HL volume multiplier")
    parser.add_argument("--min-score", type=float, default=0, help="Min score to include")
    parser.add_argument("--output", default="output", help="Output directory")
    args = parser.parse_args()

    # Stock list
    if args.stocks:
        symbols = [s.upper() for s in args.stocks]
    elif args.nifty50:
        nifty_path = os.path.join(os.path.dirname(__file__), "watchlists", "nifty50.txt")
        with open(nifty_path) as f:
            symbols = [line.strip().upper() for line in f if line.strip()]
    elif args.file:
        with open(args.file) as f:
            symbols = [line.strip().upper() for line in f if line.strip()]
    else:
        symbols = NIFTY50

    # Date range
    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = date.fromisoformat(args.start) if args.start else end_date - timedelta(days=180)

    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # Pre-create jugaad-data cache dir to dodge a TOCTOU race in util.py:106-108 (it fetches monthly chunks via 2 threads).
    from appdirs import user_cache_dir
    os.makedirs(user_cache_dir("nsehistory-stock", "nsehistory-stock"), exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  NSE BULL FLAG SCANNER v2.0")
    print(f"  Period : {start_date} -> {end_date}")
    print(f"  Stocks : {len(symbols)}")
    print(f"  HH mult: {args.hh_mult}x  |  HL mult: {args.hl_mult}x")
    print(f"  Min score filter: {args.min_score}")
    print(f"{'=' * 60}\n")

    results = []
    failed = []

    for i, symbol in enumerate(symbols):
        print(f"  {progress_bar(i + 1, len(symbols))}  {symbol:<15}", end="", flush=True)
        try:
            df = fetch_stock_data(symbol, start_date, end_date)
            if len(df) < 60:
                print(f" ! only {len(df)} rows, skipping")
                failed.append((symbol, "insufficient data"))
                continue

            swing_highs, swing_lows = detect_swings(df, lookback=5)
            details = score_stock(df, swing_highs, swing_lows,
                                  hh_mult=args.hh_mult, hl_mult=args.hl_mult)

            chart_path = generate_chart(df, symbol, details, output_dir)

            sector = SECTOR_MAP.get(symbol, "Other")
            results.append({
                "symbol": symbol,
                "sector": sector,
                "details": details,
                "chart": chart_path,
            })
            print(f" -> Score: {details['score']:5.1f}  {'*' if details['score'] >= 50 else '.'}")
        except Exception as e:
            print(f" X Error: {str(e)[:50]}")
            failed.append((symbol, str(e)[:60]))

    # Filter
    if args.min_score > 0:
        results = [r for r in results if r["details"]["score"] >= args.min_score]

    results_sorted = sorted(results, key=lambda x: x["details"]["score"], reverse=True)

    print(f"\n{'=' * 60}")
    print(f"  LEADERBOARD - Top 10")
    print(f"{'=' * 60}")
    for r in results_sorted[:10]:
        print_stock_summary(r["symbol"], r["details"])

    if failed:
        print(f"\n  ! Failed ({len(failed)}): {', '.join(f[0] for f in failed)}")

    # Dashboard
    print(f"\n  Generating HTML dashboard...")
    dash_path = generate_dashboard(results_sorted, output_dir, start_date, end_date)
    print(f"  OK Dashboard saved: {dash_path}")

    # JSON
    json_path = os.path.join(output_dir, "results.json")
    json_data = []
    for r in results_sorted:
        d = r["details"].copy()
        d.pop("hh_pairs", None)
        d.pop("hl_pairs", None)
        json_data.append({"symbol": r["symbol"], "sector": r["sector"], **d})
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.bool_,)): return bool(obj)
            if isinstance(obj, (np.integer,)): return int(obj)
            if isinstance(obj, (np.floating,)): return float(obj)
            return super().default(obj)
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, cls=NpEncoder)
    print(f"  OK JSON results: {json_path}")

    print(f"\n{'=' * 60}")
    print(f"  Scan complete. {len(results)} stocks analysed.")
    print(f"  Charts + dashboard in: {output_dir}/")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
