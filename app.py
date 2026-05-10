import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nse_scanner import fetch_stock_data, detect_swings, score_stock, SECTOR_MAP

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


def parse_symbols(symbols_text: str) -> list[str]:
    symbols = re.split(r"[,\s]+", symbols_text or "")
    return [symbol.upper() for symbol in symbols if symbol.strip()]


def scan_symbols(symbols: list[str], start_date: date, end_date: date) -> list[dict]:
    results = []
    for symbol in symbols:
        item = {"symbol": symbol, "score": None, "comment": None, "status": "ok"}
        try:
            df = fetch_stock_data(symbol, start_date, end_date)
            if len(df) < 60:
                item["status"] = "insufficient data"
                results.append(item)
                continue

            swing_highs, swing_lows = detect_swings(df, lookback=5)
            details = score_stock(df, swing_highs, swing_lows)

            dma50 = df["Close"].rolling(50).mean().iloc[-1]
            last_close = details.get("last_close", df["Close"].iloc[-1])
            above_50dma = bool(pd.notna(dma50) and last_close > dma50)

            item.update(
                score=details.get("score", 0),
                comment=details.get("comment", ""),
                hh_valid=details.get("hh_valid", 0),
                hh_total=details.get("hh_total", 0),
                hl_valid=details.get("hl_valid", 0),
                hl_total=details.get("hl_total", 0),
                last_close=last_close,
                above_50dma=above_50dma,
                sector=SECTOR_MAP.get(symbol, "Other"),
                status="ok",
            )
        except Exception as exc:
            item["status"] = f"error: {exc}"
        results.append(item)
    return results


def create_candlestick_chart(
    symbol: str,
    df: pd.DataFrame,
    swing_highs: list | None = None,
    swing_lows: list | None = None,
    hh_pairs: list | None = None,
    hl_pairs: list | None = None,
) -> go.Figure:
    """Create an interactive Plotly candlestick chart with DMAs and swing markers."""
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["DMA21"] = df["Close"].rolling(21).mean()
    df["DMA50"] = df["Close"].rolling(50).mean()

    fig = go.Figure()

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["Date"],
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="OHLC",
            increasing_line_color="#1D9E75",
            decreasing_line_color="#E24B4A",
        )
    )

    # 21-DMA
    fig.add_trace(
        go.Scatter(
            x=df["Date"],
            y=df["DMA21"],
            mode="lines",
            name="21-DMA",
            line=dict(color="#378ADD", width=2),
        )
    )

    # 50-DMA
    fig.add_trace(
        go.Scatter(
            x=df["Date"],
            y=df["DMA50"],
            mode="lines",
            name="50-DMA",
            line=dict(color="#BA7517", width=2),
        )
    )

    # Swing markers
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
        ))
    if hl_conf_x:
        fig.add_trace(go.Scatter(
            x=hl_conf_x, y=hl_conf_y, mode="markers", name="Confirmed HL",
            marker=dict(symbol="triangle-up", color="#1D9E75", size=12,
                        line=dict(color="#15734F", width=1)),
            hovertemplate="Confirmed HL<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ))
    if hh_un_x:
        fig.add_trace(go.Scatter(
            x=hh_un_x, y=hh_un_y, mode="markers", name="Unconfirmed high",
            marker=dict(symbol="triangle-down", color="#9AA0A6", size=9, opacity=0.6),
            hovertemplate="Unconfirmed swing high<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ))
    if hl_un_x:
        fig.add_trace(go.Scatter(
            x=hl_un_x, y=hl_un_y, mode="markers", name="Unconfirmed low",
            marker=dict(symbol="triangle-up", color="#9AA0A6", size=9, opacity=0.6),
            hovertemplate="Unconfirmed swing low<br>%{x|%b %d, %Y}<br>Rs %{y:.2f}<extra></extra>",
        ))

    fig.update_layout(
        title=f"{symbol} - Candlestick Chart with Moving Averages",
        yaxis_title="Price (Rs)",
        xaxis_title="Date",
        template="plotly_white",
        hovermode="x unified",
        height=500,
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


with st.sidebar.expander("Broad Market", expanded=True):
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

custom_symbols = st.sidebar.text_area(
    "Stock symbols (comma-separated)",
    value="",
    help="Type NSE symbols like RELIANCE, INFY, TCS separated by commas or spaces.",
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

scan_clicked = st.sidebar.button("Scan")

st.title("NSE Bull Flag Scanner")

if scan_clicked:
    symbols = []
    if selected_watchlist:
        symbols.extend(load_watchlist(selected_watchlist, top_n=top_n_limit))

    symbols.extend(parse_symbols(custom_symbols))
    symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]

    if not symbols:
        st.warning("Please enter at least one stock symbol or select a watchlist.")
    elif selected_start > selected_end:
        st.warning("Please choose a valid date range.")
    else:
        with st.spinner(f"Scanning {len(symbols)} symbols..."):
            scan_results = scan_symbols(symbols, selected_start, selected_end)

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
        tab1, tab2 = st.tabs(["Scan Results", "Sector Analysis"])

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

                # Color-code scores
                def color_score(val):
                    try:
                        score = float(val) if isinstance(val, str) else val
                    except (ValueError, TypeError):
                        return ""
                    
                    if score >= 50:
                        return "background-color: #C8E6C9; color: #000; font-weight: bold; border-radius: 4px; padding: 4px; text-align: center;"
                    elif score >= 30:
                        return "background-color: #FFF9C4; color: #000; font-weight: bold; border-radius: 4px; padding: 4px; text-align: center;"
                    else:
                        return "background-color: #FFCDD2; color: #000; font-weight: bold; border-radius: 4px; padding: 4px; text-align: center;"

                styled_df = filtered_df[
                    ["symbol", "sector", "score", "comment", "hh_valid", "hh_total", "hl_valid", "hl_total", "last_close"]
                ].copy()
                styled_df.columns = ["Symbol", "Sector", "Score", "Comment", "HH Valid", "HH Total", "HL Valid", "HL Total", "Close"]
                
                # Format score and close price
                styled_df["Score"] = styled_df["Score"].apply(lambda x: f"{x:.1f}".rstrip('0').rstrip('.') if isinstance(x, (int, float)) else x)
                styled_df["Close"] = styled_df["Close"].apply(lambda x: f"{x:.2f}" if isinstance(x, (int, float)) else x)

                styled_df_display = styled_df.style.map(
                    color_score, subset=["Score"]
                )

                st.dataframe(styled_df_display, width="stretch")

                csv_export = filtered_df[
                    ["symbol", "sector", "score", "comment",
                     "hh_valid", "hh_total", "hl_valid", "hl_total", "last_close"]
                ].rename(columns={
                    "symbol": "Symbol", "sector": "Sector", "score": "Score",
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

    failed_df = results_df[results_df["status"] != "ok"]
    if not failed_df.empty:
        st.subheader("Failed or skipped symbols")
        st.dataframe(failed_df[["symbol", "status"]], width="stretch")
else:
    st.info("Enter stocks and click Scan")
