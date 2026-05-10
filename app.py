import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from nse_scanner import fetch_stock_data, detect_swings, score_stock, compute_rsi, SECTOR_MAP


def is_streamlit_cloud() -> bool:
    """Best-effort detection of Streamlit Community Cloud's runtime — used to
    pick conservative defaults (fewer workers, more aggressive retries) since
    NSE rate-limits cloud egress IPs harder than residential ones."""
    return (
        os.path.exists("/mount/src")
        or os.environ.get("HOSTNAME", "").startswith("streamlit")
        or "STREAMLIT_SHARING_MODE" in os.environ
    )


IS_CLOUD = is_streamlit_cloud()
SCAN_WORKERS = 2 if IS_CLOUD else 5
SCAN_CACHE_TTL_SECONDS = 3600
RETRY_DELAYS = (2, 4, 8)  # seconds; 3 attempts total

# (group, display label, watchlist filename relative to this script). Display
# order in the sidebar matches this list. Labels get a stock count appended at
# runtime from the file's contents, e.g. "Nifty 50 (50)".
WATCHLIST_FILES = [
    ("Broad Market", "Nifty 50",            "watchlists/nifty50.txt"),
    ("Broad Market", "Nifty Next 50",       "watchlists/nifty_next50.txt"),
    ("Broad Market", "Nifty Midcap 150",    "watchlists/nifty_midcap150.txt"),
    ("Broad Market", "Nifty Smallcap 250",  "watchlists/nifty_smallcap250.txt"),
    ("Broad Market", "All Liquid Stocks",   "watchlists/all_liquid.txt"),
    ("Sectors",      "Nifty Bank",          "watchlists/nifty_bank.txt"),
    ("Sectors",      "Nifty IT",            "watchlists/nifty_it.txt"),
    ("Sectors",      "Nifty Pharma",        "watchlists/nifty_pharma.txt"),
    ("Sectors",      "Nifty FMCG",          "watchlists/nifty_fmcg.txt"),
    ("Sectors",      "Nifty Auto",          "watchlists/nifty_auto.txt"),
    ("Sectors",      "Nifty Metal",         "watchlists/nifty_metal.txt"),
    ("Sectors",      "Nifty Energy",        "watchlists/nifty_energy.txt"),
    ("Sectors",      "Nifty Realty",        "watchlists/nifty_realty.txt"),
    ("Sectors",      "Nifty PSU Bank",      "watchlists/nifty_psu_bank.txt"),
    ("Sectors",      "Nifty Media",         "watchlists/nifty_media.txt"),
    ("Sectors",      "Nifty Infra",         "watchlists/nifty_infra.txt"),
]
WATCHLIST_GROUP_ORDER = ["Broad Market", "Sectors"]
NONE_OPT = "— none —"


def _build_watchlists() -> tuple[dict[str, str], dict[str, list[str]], dict[str, int]]:
    """Returns (label_to_path, group_to_labels, label_to_count)."""
    label_to_path: dict[str, str] = {}
    group_to_labels: dict[str, list[str]] = {g: [] for g in WATCHLIST_GROUP_ORDER}
    label_to_count: dict[str, int] = {}
    for group, label, rel_path in WATCHLIST_FILES:
        full = Path(__file__).parent / rel_path
        if not full.exists():
            continue
        n = sum(1 for line in full.read_text().splitlines() if line.strip())
        if n == 0:
            continue
        labelled = f"{label} ({n})"
        label_to_path[labelled] = rel_path
        group_to_labels.setdefault(group, []).append(labelled)
        label_to_count[labelled] = n
    return label_to_path, group_to_labels, label_to_count


WATCHLISTS, WATCHLIST_GROUPS, WATCHLIST_COUNTS = _build_watchlists()


@st.cache_data
def _load_avg_volumes() -> dict[str, int]:
    """Returns {symbol: avg_volume} from watchlists/stock_metadata.csv."""
    path = Path(__file__).parent / "watchlists" / "stock_metadata.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return dict(zip(df["symbol"].astype(str), df["avg_volume"].astype(int)))


@st.cache_data
def _load_master_symbol_list() -> list[str]:
    """Sorted union of all symbols across the configured watchlist files.
    Powers the sidebar autocomplete."""
    seen: set[str] = set()
    base = Path(__file__).parent
    for _, _, rel in WATCHLIST_FILES:
        full = base / rel
        if not full.exists():
            continue
        for line in full.read_text().splitlines():
            sym = line.strip().upper()
            if sym:
                seen.add(sym)
    return sorted(seen)


def load_watchlist(name: str, top_n: int | None = None) -> list[str]:
    rel = WATCHLISTS.get(name, "")
    if not rel:
        return []
    path = Path(__file__).parent / rel
    if not path.exists():
        return []
    symbols = [line.strip().upper() for line in path.read_text().splitlines() if line.strip()]
    if top_n is None or top_n >= len(symbols):
        return symbols
    avg_vol = _load_avg_volumes()
    # Rank by avg volume desc; symbols missing from metadata fall to the bottom.
    return sorted(symbols, key=lambda s: -avg_vol.get(s, 0))[:top_n]


_CELL_BASE = "color: #000; font-weight: bold; border-radius: 4px; padding: 4px; text-align: center;"


def color_score(val) -> str:
    try:
        score = float(val) if isinstance(val, str) else val
    except (ValueError, TypeError):
        return ""
    if pd.isna(score):
        return ""
    if score >= 50:
        return f"background-color: #C8E6C9; {_CELL_BASE}"
    if score >= 30:
        return f"background-color: #FFF9C4; {_CELL_BASE}"
    return f"background-color: #FFCDD2; {_CELL_BASE}"


def render_dma_status(close: float, dma21, dma50, dma200) -> None:
    """Render a 3-column row of st.metric cards comparing the latest close to
    each moving average. dma values may be None when there's insufficient
    history (e.g. DMA200 for a stock with <200 trading days)."""
    st.markdown("**DMA Status**")
    cols = st.columns(3)
    for col, name, dma in zip(cols, ("21-DMA", "50-DMA", "200-DMA"), (dma21, dma50, dma200)):
        with col:
            if dma is None or pd.isna(dma):
                st.metric(f"vs {name}", "—",
                          delta="insufficient history",
                          delta_color="off")
            else:
                pct = (close / dma - 1) * 100
                st.metric(f"vs {name}", f"Rs {dma:,.2f}",
                          delta=f"{pct:+.2f}%",
                          delta_color="normal")


@st.cache_data(show_spinner=False)
def _fetch_for_compare(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Cached fetch for the Compare tab so reselection doesn't re-hit NSE."""
    return fetch_stock_data(symbol, start, end)


def create_compare_candlestick(symbol: str, df: pd.DataFrame) -> go.Figure:
    """Compact single-pane candlestick + 21/50/200 DMAs for side-by-side view."""
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["DMA21"] = df["Close"].rolling(21).mean()
    df["DMA50"] = df["Close"].rolling(50).mean()
    df["DMA200"] = df["Close"].rolling(200).mean()

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["Date"], open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="OHLC",
        increasing_line_color="#1D9E75", decreasing_line_color="#E24B4A",
    ))
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA21"], mode="lines", name="21-DMA",
        line=dict(color="#378ADD", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA50"], mode="lines", name="50-DMA",
        line=dict(color="#BA7517", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA200"], mode="lines", name="200-DMA",
        line=dict(color="#E24B4A", width=1.5, dash="dash"),
    ))
    fig.update_layout(
        title=symbol, template="plotly_white", height=380,
        xaxis_rangeslider_visible=False, showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


def create_normalized_perf_chart(symbol_data: dict[str, pd.DataFrame]) -> go.Figure:
    """Multi-line % return from the first session, rebased to 0%."""
    fig = go.Figure()
    palette = ["#378ADD", "#1D9E75", "#BA7517", "#E24B4A", "#7B3FBF"]
    for i, (sym, df) in enumerate(symbol_data.items()):
        d = df.sort_values("Date").reset_index(drop=True)
        if d.empty:
            continue
        first = d["Close"].iloc[0]
        if first == 0 or pd.isna(first):
            continue
        pct = (d["Close"] / first - 1) * 100
        fig.add_trace(go.Scatter(
            x=d["Date"], y=pct, mode="lines", name=sym,
            line=dict(color=palette[i % len(palette)], width=2),
            hovertemplate=f"<b>{sym}</b><br>%{{x|%b %d, %Y}}<br>%{{y:+.2f}}%<extra></extra>",
        ))
    fig.add_hline(y=0, line=dict(color="#888", width=1, dash="dot"))
    fig.update_layout(
        title="Normalized performance (% return from start of selected range)",
        template="plotly_white", height=420,
        yaxis_title="% return", xaxis_title="Date",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def color_rsi(val) -> str:
    try:
        rsi = float(val) if isinstance(val, str) else val
    except (ValueError, TypeError):
        return ""
    if pd.isna(rsi):
        return ""
    if rsi < 30:
        return f"background-color: #FFCDD2; {_CELL_BASE}"
    if rsi > 70:
        return f"background-color: #C8E6C9; {_CELL_BASE}"
    return ""


def _fetch_with_retry(symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
    """fetch_stock_data with exponential-backoff retry. NSE rate-limits will
    surface as JSONDecodeError ('Expecting value: line 1 column 1') — those
    are exactly what the retries catch. After all attempts fail, raises a
    RuntimeError summarising the attempt count so cache_data does NOT cache
    the error result."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            return fetch_stock_data(symbol, start_date, end_date)
        except Exception as exc:
            last_exc = exc
            if attempt < len(RETRY_DELAYS):
                time.sleep(delay)
    raise RuntimeError(
        f"rate-limited or unreachable after {len(RETRY_DELAYS)} attempts: "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


@st.cache_data(ttl=SCAN_CACHE_TTL_SECONDS, show_spinner=False)
def _scan_one_symbol(symbol: str, start_date: date, end_date: date) -> dict:
    """Fetch + score one symbol. Successful results and 'insufficient data'
    rows are cached for 1 hour. Fetch failures (after retries) propagate as
    exceptions so they're NOT cached — `scan_symbols` catches them and
    surfaces an error row, while leaving the cache available for retry."""
    df = _fetch_with_retry(symbol, start_date, end_date)
    if len(df) < 60:
        return {
            "symbol": symbol,
            "score": None,
            "comment": None,
            "status": "insufficient data",
        }

    swing_highs, swing_lows = detect_swings(df, lookback=5)
    details = score_stock(df, swing_highs, swing_lows)

    dma50 = df["Close"].rolling(50).mean().iloc[-1]
    last_close = details.get("last_close", df["Close"].iloc[-1])
    above_50dma = bool(pd.notna(dma50) and last_close > dma50)

    return {
        "symbol": symbol,
        "score": details.get("score", 0),
        "comment": details.get("comment", ""),
        "hh_valid": details.get("hh_valid", 0),
        "hh_total": details.get("hh_total", 0),
        "hl_valid": details.get("hl_valid", 0),
        "hl_total": details.get("hl_total", 0),
        "last_close": last_close,
        "above_50dma": above_50dma,
        "rsi14": details.get("rsi14"),
        "sector": SECTOR_MAP.get(symbol, "Other"),
        "status": "ok",
    }


def scan_symbols(
    symbols: list[str],
    start_date: date,
    end_date: date,
    progress_callback: Callable[[int, int, float], None] | None = None,
) -> list[dict]:
    """Fetch + score `symbols` in parallel, preserving input order. Worker
    count is environment-aware: 5 locally, 2 on Streamlit Cloud (gentler on
    NSE's rate limits which hit cloud egress IPs harder).
    progress_callback is invoked on the main thread after each completion with
    (completed_count, total, elapsed_seconds)."""
    results: list[dict | None] = [None] * len(symbols)
    started_at = time.monotonic()

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_scan_one_symbol, sym, start_date, end_date): i
            for i, sym in enumerate(symbols)
        }
        for completed, future in enumerate(as_completed(future_to_idx), start=1):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "symbol": symbols[idx],
                    "score": None,
                    "comment": None,
                    "status": f"error: {exc}",
                }
            if progress_callback is not None:
                progress_callback(completed, len(symbols), time.monotonic() - started_at)

    return [r for r in results if r is not None]


def create_candlestick_chart(
    symbol: str,
    df: pd.DataFrame,
    swing_highs: list | None = None,
    swing_lows: list | None = None,
    hh_pairs: list | None = None,
    hl_pairs: list | None = None,
) -> go.Figure:
    """3-row chart: candlestick + DMAs + swing markers (top), volume bars
    (middle), RSI(14) with 30/70 reference lines (bottom)."""
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["DMA21"] = df["Close"].rolling(21).mean()
    df["DMA50"] = df["Close"].rolling(50).mean()
    df["DMA200"] = df["Close"].rolling(200).mean()
    df["RSI14"] = compute_rsi(df["Close"], period=14)

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.6, 0.18, 0.22],
        subplot_titles=("Price", "Volume", "RSI(14)"),
    )

    fig.add_trace(go.Candlestick(
        x=df["Date"], open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="OHLC",
        increasing_line_color="#1D9E75", decreasing_line_color="#E24B4A",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA21"], mode="lines", name="21-DMA",
        line=dict(color="#378ADD", width=2),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA50"], mode="lines", name="50-DMA",
        line=dict(color="#BA7517", width=2),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["DMA200"], mode="lines", name="200-DMA",
        line=dict(color="#E24B4A", width=2, dash="dash"),
    ), row=1, col=1)

    # Swing markers (row 1)
    def _split_swings(indices, pairs, price_col):
        valid_idx = {p["idx"] for p in (pairs or []) if p["valid"]}
        conf_x, conf_y, unconf_x, unconf_y = [], [], [], []
        for idx in indices or []:
            if idx >= len(df):
                continue
            d, p = df["Date"].iloc[idx], df[price_col].iloc[idx]
            if idx in valid_idx:
                conf_x.append(d); conf_y.append(p)
            else:
                unconf_x.append(d); unconf_y.append(p)
        return conf_x, conf_y, unconf_x, unconf_y

    hh_conf_x, hh_conf_y, hh_un_x, hh_un_y = _split_swings(swing_highs, hh_pairs, "High")
    hl_conf_x, hl_conf_y, hl_un_x, hl_un_y = _split_swings(swing_lows, hl_pairs, "Low")

    if hh_conf_x:
        fig.add_trace(go.Scatter(
            x=hh_conf_x, y=hh_conf_y, mode="markers", name="Confirmed HH",
            marker=dict(symbol="triangle-down", color="#E24B4A", size=12,
                        line=dict(color="#A53634", width=1)),
            hovertemplate="Confirmed HH<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ), row=1, col=1)
    if hl_conf_x:
        fig.add_trace(go.Scatter(
            x=hl_conf_x, y=hl_conf_y, mode="markers", name="Confirmed HL",
            marker=dict(symbol="triangle-up", color="#1D9E75", size=12,
                        line=dict(color="#15734F", width=1)),
            hovertemplate="Confirmed HL<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ), row=1, col=1)
    if hh_un_x:
        fig.add_trace(go.Scatter(
            x=hh_un_x, y=hh_un_y, mode="markers", name="Unconfirmed high",
            marker=dict(symbol="triangle-down", color="#9AA0A6", size=9, opacity=0.6),
            hovertemplate="Unconfirmed swing high<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ), row=1, col=1)
    if hl_un_x:
        fig.add_trace(go.Scatter(
            x=hl_un_x, y=hl_un_y, mode="markers", name="Unconfirmed low",
            marker=dict(symbol="triangle-up", color="#9AA0A6", size=9, opacity=0.6),
            hovertemplate="Unconfirmed swing low<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ), row=1, col=1)

    # Volume bars (row 2) — green on up days, red on down days
    vol_colors = ["#1D9E75" if c >= o else "#E24B4A"
                  for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(
        x=df["Date"], y=df["Volume"], name="Volume",
        marker=dict(color=vol_colors), showlegend=False,
        hovertemplate="%{x|%b %d, %Y}<br>Vol %{y:,.0f}<extra></extra>",
    ), row=2, col=1)

    # RSI(14) (row 3)
    fig.add_trace(go.Scatter(
        x=df["Date"], y=df["RSI14"], mode="lines", name="RSI(14)",
        line=dict(color="#7B3FBF", width=2), showlegend=False,
        hovertemplate="%{x|%b %d, %Y}<br>RSI %{y:.1f}<extra></extra>",
    ), row=3, col=1)
    # Overbought / oversold reference bands
    fig.add_hline(y=70, line=dict(color="#1D9E75", width=1, dash="dash"),
                  annotation_text="70", annotation_position="right",
                  row=3, col=1)
    fig.add_hline(y=30, line=dict(color="#E24B4A", width=1, dash="dash"),
                  annotation_text="30", annotation_position="right",
                  row=3, col=1)

    fig.update_yaxes(title_text="Price (Rs)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
    fig.update_xaxes(title_text="Date", row=3, col=1)

    fig.update_layout(
        title=f"{symbol} — price, volume, and RSI(14)",
        template="plotly_white",
        hovermode="x unified",
        height=750,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig



st.set_page_config(page_title="NSE Bull Flag Scanner", layout="wide")

# Initialize session state
if "scan_results_df" not in st.session_state:
    st.session_state.scan_results_df = None
if "scan_params" not in st.session_state:
    st.session_state.scan_params = None

st.sidebar.title("NSE Scanner")
st.sidebar.markdown("Scan a custom symbol list or choose a preset watchlist.")


def _on_broad_change():
    if st.session_state.broad_radio != NONE_OPT:
        st.session_state.sector_radio = NONE_OPT


def _on_sector_change():
    if st.session_state.sector_radio != NONE_OPT:
        st.session_state.broad_radio = NONE_OPT


with st.sidebar.expander("Broad Market", expanded=False):
    st.radio(
        "Broad market watchlist",
        [NONE_OPT, *WATCHLIST_GROUPS.get("Broad Market", [])],
        key="broad_radio",
        label_visibility="collapsed",
        on_change=_on_broad_change,
    )

with st.sidebar.expander("Sectors", expanded=False):
    st.radio(
        "Sector watchlist",
        [NONE_OPT, *WATCHLIST_GROUPS.get("Sectors", [])],
        key="sector_radio",
        label_visibility="collapsed",
        on_change=_on_sector_change,
    )

# Resolve selection from whichever group has a non-none pick
broad_pick = st.session_state.get("broad_radio", NONE_OPT)
sector_pick = st.session_state.get("sector_radio", NONE_OPT)
selected_watchlist = next(
    (p for p in (broad_pick, sector_pick) if p != NONE_OPT), None
)

# Per-watchlist scan-size controls
top_n_limit: int | None = None
if selected_watchlist:
    full_count = WATCHLIST_COUNTS.get(selected_watchlist, 0)
    if full_count >= 100:
        approx_minutes = full_count * 0.6 / 60  # ~0.6s per stock incl. fetch + parse
        st.sidebar.warning(
            f"{full_count} stocks — a full scan takes ~{approx_minutes:.1f} min."
        )
        top_n_limit = st.sidebar.slider(
            "Scan top N by volume",
            min_value=10,
            max_value=full_count,
            value=min(50, full_count),
            step=10,
            help="Ranks symbols by 20-day average volume from stock_metadata.csv.",
        )

end_date = date.today()
start_date = end_date - timedelta(days=365)
selected_range = st.sidebar.date_input(
    "Date range",
    value=(start_date, end_date),
    max_value=end_date,
)

if isinstance(selected_range, tuple) or isinstance(selected_range, list):
    selected_start, selected_end = selected_range
else:
    selected_start = selected_range
    selected_end = selected_range

if selected_start > selected_end:
    st.sidebar.error("Start date must be before end date.")

sidebar_scan = st.sidebar.button(
    "Scan", type="primary", width="stretch", key="scan_sidebar"
)

st.title("NSE Bull Flag Scanner")

# --- Stock selector (full-width, above the tabs) ---
master_symbols = _load_master_symbol_list()

# Align all column children to the bottom edge so labelled inputs and the
# unlabeled button line up at the field baseline. Note: this rule is global
# and affects every horizontal block in the app — most other column rows in
# this app contain widgets of similar heights, so the visual impact elsewhere
# is minimal, but worth knowing.
st.markdown(
    '<style>[data-testid="stHorizontalBlock"] {align-items: end;}</style>',
    unsafe_allow_html=True,
)

picker_col, unlisted_col, scan_col = st.columns([5, 2, 1])
with picker_col:
    custom_picks = st.multiselect(
        "Search or type stocks",
        options=master_symbols,
        default=[],
        placeholder="e.g. RELIANCE, INFY, TCS",
        key="custom_picks",
    )
with unlisted_col:
    custom_text = st.text_input(
        "Add unlisted symbol",
        value="",
        placeholder="e.g. NEWLISTING",
        key="custom_text",
        help="For NSE symbols not present in any watchlist file.",
    )
with scan_col:
    main_scan = st.button(
        "Scan", type="primary", width="stretch", key="scan_main"
    )

# Either button triggers the same scan
scan_clicked = main_scan or sidebar_scan

st.caption(
    f":grey[Search from {len(master_symbols)} indexed stocks "
    "or type any NSE symbol]"
)

typed_symbols = [
    s.strip().upper()
    for s in custom_text.replace(",", " ").split()
    if s.strip()
]
custom_combined = list(dict.fromkeys([*custom_picks, *typed_symbols]))

if custom_combined:
    n = len(custom_combined)
    st.caption(
        f"**{n} stock{'s' if n != 1 else ''} selected** — overriding watchlist"
    )

st.divider()

if scan_clicked:
    # Either custom-input source (autocomplete + typed) overrides the watchlist.
    if custom_combined:
        symbols = custom_combined
    elif selected_watchlist:
        symbols = load_watchlist(selected_watchlist, top_n=top_n_limit)
    else:
        symbols = []

    if not symbols:
        st.warning("Pick a watchlist or search for at least one stock.")
    elif selected_start > selected_end:
        st.warning("Please choose a valid date range.")
    else:
        progress_bar = st.progress(0.0, text=f"Scanning {len(symbols)} symbols...")
        eta_text = st.empty()

        def _on_progress(completed: int, total: int, elapsed: float) -> None:
            fraction = completed / total
            rate = completed / elapsed if elapsed > 0 else 0
            remaining = (total - completed) / rate if rate > 0 else 0
            progress_bar.progress(
                fraction,
                text=f"Scanning {completed}/{total} symbols ({fraction:.0%})",
            )
            eta_text.caption(
                f"Elapsed {elapsed:.1f}s · ~{remaining:.0f}s remaining "
                f"({rate:.1f} symbols/sec across {SCAN_WORKERS} worker"
                f"{'s' if SCAN_WORKERS != 1 else ''}"
                f"{' · cloud mode' if IS_CLOUD else ''})"
            )

        scan_results = scan_symbols(
            symbols, selected_start, selected_end,
            progress_callback=_on_progress,
        )
        progress_bar.empty()
        eta_text.empty()

        results_df = pd.DataFrame(scan_results)
        st.session_state.scan_results_df = results_df
        st.session_state.scan_params = {
            "start_date": selected_start,
            "end_date": selected_end,
        }
        st.success(f"Scan completed for {len(symbols)} symbols.")

if st.session_state.scan_results_df is not None:
    results_df = st.session_state.scan_results_df
    scan_params = st.session_state.scan_params
    selected_start = scan_params["start_date"]
    selected_end = scan_params["end_date"]

    ok_df = results_df[results_df["status"] == "ok"].copy()
    if not ok_df.empty:
        # Create tabs
        tab1, tab2, tab3, tab4 = st.tabs(
            ["Scan Results", "Sector Analysis", "Screener", "Compare"]
        )

        with tab1:
            st.subheader("Scan results")

            # Summary metrics
            total_stocks = len(ok_df)
            avg_score = ok_df["score"].mean()
            above_50dma_count = int(ok_df["above_50dma"].sum()) if "above_50dma" in ok_df.columns else 0
            if not ok_df.empty:
                top_row = ok_df.loc[ok_df["score"].idxmax()]
                top_scorer_label = top_row["symbol"]
                top_scorer_delta = f"{top_row['score']:.1f}"
            else:
                top_scorer_label = "N/A"
                top_scorer_delta = None

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Stocks Scanned", total_stocks)
            with col2:
                st.metric("Average Score", f"{avg_score:.1f}")
            with col3:
                st.metric("Above 50-DMA", above_50dma_count)
            with col4:
                st.metric("Top Scorer", top_scorer_label, delta=top_scorer_delta, delta_color="off")

            # Sector filter
            all_sectors = sorted(ok_df["sector"].unique().tolist())
            selected_sectors = st.multiselect(
                "Filter by sector",
                all_sectors,
                default=all_sectors,
                key="sector_filter",
            )

            search_query = st.text_input(
                "Filter by symbol",
                value="",
                placeholder="Type to filter — e.g. RELIANCE, INFY, TATA",
                key="symbol_search",
            )

            # Filter results by sector + search query
            filtered_df = ok_df[ok_df["sector"].isin(selected_sectors)].copy()
            if search_query.strip():
                q = search_query.strip().upper()
                filtered_df = filtered_df[filtered_df["symbol"].str.contains(q, case=False, na=False)]

            if filtered_df.empty:
                st.warning("No stocks match the current filters.")
            else:
                # Sort by score descending
                filtered_df = filtered_df.sort_values(by="score", ascending=False).reset_index(drop=True)

                styled_df = filtered_df[
                    ["symbol", "sector", "score", "rsi14", "comment", "hh_valid", "hh_total", "hl_valid", "hl_total", "last_close"]
                ].copy()
                styled_df.columns = ["Symbol", "Sector", "Score", "RSI", "Comment", "HH Valid", "HH Total", "HL Valid", "HL Total", "Close"]

                # Format score, RSI, and close price
                styled_df["Score"] = styled_df["Score"].apply(lambda x: f"{x:.1f}".rstrip('0').rstrip('.') if isinstance(x, (int, float)) else x)
                styled_df["RSI"] = styled_df["RSI"].apply(lambda x: f"{x:.1f}" if isinstance(x, (int, float)) and pd.notna(x) else "—")
                styled_df["Close"] = styled_df["Close"].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x)

                styled_df_display = (
                    styled_df.style
                    .map(color_score, subset=["Score"])
                    .map(color_rsi, subset=["RSI"])
                )

                st.dataframe(styled_df_display, width="stretch")

                csv_export = filtered_df[
                    ["symbol", "sector", "score", "rsi14", "comment",
                     "hh_valid", "hh_total", "hl_valid", "hl_total", "last_close"]
                ].rename(columns={
                    "symbol": "Symbol", "sector": "Sector", "score": "Score", "rsi14": "RSI",
                    "comment": "Comment", "hh_valid": "HH Valid", "hh_total": "HH Total",
                    "hl_valid": "HL Valid", "hl_total": "HL Total", "last_close": "Close",
                })
                st.download_button(
                    label="Download CSV",
                    data=csv_export.to_csv(index=False).encode("utf-8"),
                    file_name=f"nse_scan_{date.today().isoformat()}.csv",
                    mime="text/csv",
                )

                # Stock selection for chart
                st.markdown("---")
                st.subheader("View chart")
                selected_stock = st.selectbox(
                    "Select a stock to view its candlestick chart",
                    options=filtered_df["symbol"].tolist(),
                    index=0 if len(filtered_df) > 0 else None,
                    key="chart_selector",
                )

                if selected_stock:
                    with st.spinner(f"Loading chart for {selected_stock}..."):
                        try:
                            df_chart = fetch_stock_data(selected_stock, selected_start, selected_end)
                            df_chart = df_chart.sort_values("Date").reset_index(drop=True)
                            chart_highs, chart_lows = detect_swings(df_chart, lookback=5)
                            chart_details = score_stock(df_chart, chart_highs, chart_lows)
                            fig = create_candlestick_chart(
                                selected_stock, df_chart,
                                swing_highs=chart_highs,
                                swing_lows=chart_lows,
                                hh_pairs=chart_details.get("hh_pairs"),
                                hl_pairs=chart_details.get("hl_pairs"),
                            )
                            st.plotly_chart(fig, width="stretch")
                            render_dma_status(
                                close=chart_details.get("last_close", df_chart["Close"].iloc[-1]),
                                dma21=chart_details.get("dma21"),
                                dma50=chart_details.get("dma50"),
                                dma200=chart_details.get("dma200"),
                            )
                        except Exception as e:
                            st.error(f"Error loading chart: {e}")

        with tab2:
            st.subheader("Sector Analysis")

            # Calculate average score by sector
            sector_avg = ok_df.groupby("sector")["score"].agg(["mean", "count"]).reset_index()
            sector_avg = sector_avg.sort_values("mean", ascending=False)
            sector_avg["mean"] = sector_avg["mean"].round(1)

            # Create bar chart
            fig_sector = go.Figure()

            fig_sector.add_trace(
                go.Bar(
                    x=sector_avg["sector"],
                    y=sector_avg["mean"],
                    text=sector_avg["mean"],
                    textposition="auto",
                    marker_color="#378ADD",
                    hovertemplate="<b>%{x}</b><br>Average Score: %{y:.1f}<br>Stocks: %{customdata}<extra></extra>",
                    customdata=sector_avg["count"],
                )
            )

            fig_sector.update_layout(
                title="Average Bull Flag Score by Sector",
                xaxis_title="Sector",
                yaxis_title="Average Score",
                template="plotly_white",
                height=500,
                xaxis_tickangle=-45,
            )

            st.plotly_chart(fig_sector, width="stretch")

            # Show sector details table
            sector_avg_display = sector_avg.copy()
            sector_avg_display.columns = ["Sector", "Avg Score", "Stock Count"]
            st.dataframe(sector_avg_display, width="stretch")

        with tab3:
            st.subheader("Screener")
            st.caption("Filter scan results live. Updates as you change any control.")

            # Join 20-day avg volume from stock_metadata.csv onto the scan rows
            avg_vol_map = _load_avg_volumes()
            screener_df = ok_df.copy()
            screener_df["avg_volume"] = (
                screener_df["symbol"].map(avg_vol_map).fillna(0).astype(int)
            )

            sc_col1, sc_col2 = st.columns(2)
            with sc_col1:
                min_score = st.slider("Min score", 0, 100, 0, 1, key="scr_min_score")
            with sc_col2:
                rsi_range = st.slider(
                    "RSI(14) range", 0, 100, (0, 100), 1, key="scr_rsi_range"
                )

            scr_all_sectors = sorted(ok_df["sector"].unique().tolist())
            scr_sectors = st.multiselect(
                "Sectors", scr_all_sectors, default=scr_all_sectors, key="scr_sectors"
            )

            sc_col3, sc_col4 = st.columns([1, 2])
            with sc_col3:
                above_50_only = st.checkbox(
                    "Above 50-DMA only", value=False, key="scr_above_50"
                )
            with sc_col4:
                min_volume = st.number_input(
                    "Min 20-day avg volume",
                    min_value=0,
                    value=0,
                    step=10000,
                    key="scr_min_volume",
                    help=(
                        "Filters by 20-day average volume from "
                        "watchlists/stock_metadata.csv. Symbols not in the "
                        "metadata file are treated as 0 and excluded by any "
                        "non-zero threshold."
                    ),
                )

            mask = (
                (screener_df["score"] >= min_score)
                & (screener_df["sector"].isin(scr_sectors))
                & (screener_df["avg_volume"] >= min_volume)
            )
            # Apply RSI range only when narrowed; otherwise NaN RSIs are kept
            if rsi_range != (0, 100):
                mask &= screener_df["rsi14"].between(rsi_range[0], rsi_range[1])
            if above_50_only and "above_50dma" in screener_df.columns:
                mask &= screener_df["above_50dma"].fillna(False)

            matched = (
                screener_df[mask]
                .sort_values("score", ascending=False)
                .reset_index(drop=True)
            )

            st.markdown(f"**{len(matched)} of {len(screener_df)} stocks match**")

            if matched.empty:
                st.info("No stocks match the current filters. Try widening the criteria.")
            else:
                disp = matched[
                    ["symbol", "sector", "score", "rsi14",
                     "above_50dma", "avg_volume", "last_close"]
                ].copy()
                disp.columns = [
                    "Symbol", "Sector", "Score", "RSI",
                    "Above 50-DMA", "Avg Volume", "Close",
                ]
                disp["Score"] = disp["Score"].apply(
                    lambda x: f"{x:.1f}".rstrip("0").rstrip(".")
                    if isinstance(x, (int, float)) and pd.notna(x) else x
                )
                disp["RSI"] = disp["RSI"].apply(
                    lambda x: f"{x:.1f}"
                    if isinstance(x, (int, float)) and pd.notna(x) else "—"
                )
                disp["Avg Volume"] = disp["Avg Volume"].apply(
                    lambda x: f"{x:,}" if isinstance(x, (int, float)) else x
                )
                disp["Close"] = disp["Close"].apply(
                    lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x
                )
                disp["Above 50-DMA"] = disp["Above 50-DMA"].map(
                    {True: "Yes", False: "No"}
                ).fillna("—")

                styled = (
                    disp.style
                    .map(color_score, subset=["Score"])
                    .map(color_rsi, subset=["RSI"])
                )
                st.dataframe(styled, width="stretch")

        with tab4:
            st.subheader("Compare stocks")
            st.caption("Pick 2-3 stocks to view their charts side-by-side, "
                       "with normalized % returns below.")

            available = sorted(ok_df["symbol"].unique().tolist())
            top_two = (
                ok_df.sort_values("score", ascending=False)["symbol"].head(2).tolist()
                if not ok_df.empty else []
            )
            selected_compare = st.multiselect(
                "Select stocks (2-3)",
                options=available,
                default=top_two,
                max_selections=3,
                key="compare_picker",
            )

            if len(selected_compare) < 2:
                st.info("Select at least 2 stocks to compare.")
            else:
                with st.spinner(f"Fetching data for {len(selected_compare)} stocks..."):
                    symbol_data: dict[str, pd.DataFrame] = {}
                    fetch_errors: dict[str, str] = {}
                    for sym in selected_compare:
                        try:
                            symbol_data[sym] = _fetch_for_compare(
                                sym, selected_start, selected_end
                            )
                        except Exception as exc:
                            fetch_errors[sym] = str(exc)

                for sym, err in fetch_errors.items():
                    st.warning(f"Could not fetch {sym}: {err}")

                if symbol_data:
                    cols = st.columns(len(symbol_data))
                    for col, (sym, df_cmp) in zip(cols, symbol_data.items()):
                        with col:
                            st.plotly_chart(
                                create_compare_candlestick(sym, df_cmp),
                                width="stretch",
                            )

                    st.plotly_chart(
                        create_normalized_perf_chart(symbol_data),
                        width="stretch",
                    )

    failed_df = results_df[results_df["status"] != "ok"]
    if not failed_df.empty:
        st.subheader("Failed or skipped symbols")
        st.dataframe(failed_df[["symbol", "status"]], width="stretch")
else:
    st.info("Enter stocks and click Scan")
