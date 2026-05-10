"""Build liquidity-filtered NSE watchlists from major Nifty indices.

Fetches index constituents via NSELive, then 20 days of bhavcopy data to
compute average daily volume per symbol. Writes one watchlist file per
index (liquid stocks only), a master `all_liquid.txt`, and a metadata CSV.

Run: python build_watchlists.py
"""
import csv
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from jugaad_data.nse import NSELive, bhavcopy_raw

from nse_scanner import SECTOR_MAP

# (file_key, NSE index name, display label, sector hint for fallback)
INDICES = [
    ("nifty50",            "NIFTY 50",           "Nifty 50",            None),
    ("nifty_next50",       "NIFTY NEXT 50",      "Nifty Next 50",       None),
    ("nifty_midcap150",    "NIFTY MIDCAP 150",   "Nifty Midcap 150",    None),
    ("nifty_smallcap250",  "NIFTY SMLCAP 250",   "Nifty Smallcap 250",  None),
    ("nifty_bank",         "NIFTY BANK",         "Nifty Bank",          "Banking"),
    ("nifty_it",           "NIFTY IT",           "Nifty IT",            "IT"),
    ("nifty_pharma",       "NIFTY PHARMA",       "Nifty Pharma",        "Pharma"),
    ("nifty_fmcg",         "NIFTY FMCG",         "Nifty FMCG",          "FMCG"),
    ("nifty_auto",         "NIFTY AUTO",         "Nifty Auto",          "Auto"),
    ("nifty_metal",        "NIFTY METAL",        "Nifty Metal",         "Metals"),
    ("nifty_energy",       "NIFTY ENERGY",       "Nifty Energy",        "Energy"),
    ("nifty_realty",       "NIFTY REALTY",       "Nifty Realty",        "Realty"),
    ("nifty_psu_bank",     "NIFTY PSU BANK",     "Nifty PSU Bank",      "PSU Bank"),
    ("nifty_media",        "NIFTY MEDIA",        "Nifty Media",         "Media"),
    ("nifty_infra",        "NIFTY INFRA",        "Nifty Infra",         "Infra"),
]

DELAY = 0.3
MIN_AVG_VOLUME = 100_000
LOOKBACK_TRADING_DAYS = 20
WATCHLIST_DIR = Path(__file__).parent / "watchlists"


def fetch_constituents(nse: NSELive, nse_name: str) -> list[str]:
    """Symbols in an index, excluding the index pseudo-row (which has the
    same `symbol` as the index name)."""
    payload = nse.live_index(nse_name)
    return [
        row["symbol"] for row in payload.get("data", [])
        if row.get("symbol") and row["symbol"] != nse_name
    ]


def extract_equity_volumes(df: pd.DataFrame) -> list[tuple[str, int]]:
    """Pull (symbol, volume) tuples for EQ-series stocks from a bhavcopy
    dataframe, handling both the new (TckrSymb/FinInstrmTp) and legacy
    (SYMBOL/ SERIES with leading spaces) schemas."""
    if "TckrSymb" in df.columns:
        eq = df[(df["FinInstrmTp"] == "STK") & (df["SctySrs"] == "EQ")]
        return [(s, int(v)) for s, v in zip(eq["TckrSymb"], eq["TtlTradgVol"])]
    if "SYMBOL" in df.columns:
        # Legacy schema — column names have leading whitespace
        cols = {c.strip(): c for c in df.columns}
        series_col, sym_col, vol_col = cols["SERIES"], cols["SYMBOL"], cols["TTL_TRD_QNTY"]
        eq = df[df[series_col].astype(str).str.strip() == "EQ"]
        return [(s, int(v)) for s, v in zip(eq[sym_col], eq[vol_col])]
    raise ValueError(f"Unknown bhavcopy schema: {list(df.columns)[:5]}")


def fetch_bhavcopy_volumes(days: int) -> pd.DataFrame:
    """Walk back from yesterday, collect `days` trading sessions of bhavcopy
    equity volume data. Returns long-form DataFrame [date, symbol, volume]."""
    rows: list[tuple] = []
    d = date.today() - timedelta(days=1)
    cutoff = date.today() - timedelta(days=days * 3)  # safety bound
    fetched = 0
    while fetched < days and d > cutoff:
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue
        try:
            time.sleep(DELAY)
            raw = bhavcopy_raw(d)
            df = pd.read_csv(io.StringIO(raw))
            pairs = extract_equity_volumes(df)
            for sym, vol in pairs:
                rows.append((d, sym, vol))
            fetched += 1
            print(f"  [{fetched:>2}/{days}] {d} -> {len(pairs)} equity rows", flush=True)
        except Exception as exc:
            print(f"  skip {d}: {type(exc).__name__}: {str(exc)[:60]}", flush=True)
        d -= timedelta(days=1)
    return pd.DataFrame(rows, columns=["date", "symbol", "volume"])


def derive_sector(symbol: str, indices_for_symbol: set[str],
                  sector_hints: dict[str, str | None]) -> str:
    if symbol in SECTOR_MAP:
        return SECTOR_MAP[symbol]
    for key in indices_for_symbol:
        sector = sector_hints.get(key)
        if sector:
            return sector
    return "Other"


def main() -> None:
    WATCHLIST_DIR.mkdir(exist_ok=True)
    nse = NSELive()

    print(f"Fetching constituents from {len(INDICES)} indices...")
    index_members: dict[str, list[str]] = {}
    for key, nse_name, label, _ in INDICES:
        try:
            time.sleep(DELAY)
            members = fetch_constituents(nse, nse_name)
            index_members[key] = members
            print(f"  {label:22s} {len(members):>4}", flush=True)
        except Exception as exc:
            print(f"  {label:22s} FAILED ({type(exc).__name__}: {str(exc)[:60]})",
                  flush=True)
            index_members[key] = []

    universe = sorted({s for lst in index_members.values() for s in lst})
    print(f"\nTotal unique symbols across indices: {len(universe)}\n")

    print(f"Fetching {LOOKBACK_TRADING_DAYS} days of bhavcopy data...")
    vol_df = fetch_bhavcopy_volumes(LOOKBACK_TRADING_DAYS)
    if vol_df.empty:
        print("ERROR: No bhavcopy data fetched. Aborting.", file=sys.stderr)
        sys.exit(1)

    avg_vol = vol_df.groupby("symbol")["volume"].mean().round().astype(int).to_dict()
    universe_vol = {s: avg_vol.get(s, 0) for s in universe}
    liquid = {s: v for s, v in universe_vol.items() if v >= MIN_AVG_VOLUME}
    print(f"\nLiquid stocks (avg vol >= {MIN_AVG_VOLUME:,}): "
          f"{len(liquid)}/{len(universe)}\n")

    symbol_indices: dict[str, set[str]] = {}
    for key, members in index_members.items():
        for s in members:
            symbol_indices.setdefault(s, set()).add(key)

    sector_hints = {key: hint for key, _, _, hint in INDICES}
    counts: dict[str, int] = {}

    print("Writing per-index watchlists...")
    for key, _, label, _ in INDICES:
        liquid_for_index = [s for s in index_members.get(key, []) if s in liquid]
        path = WATCHLIST_DIR / f"{key}.txt"
        path.write_text("\n".join(liquid_for_index) +
                        ("\n" if liquid_for_index else ""))
        counts[key] = len(liquid_for_index)
        print(f"  {path.name:30s} {counts[key]:>4}")

    master = sorted(liquid.keys())
    (WATCHLIST_DIR / "all_liquid.txt").write_text("\n".join(master) + "\n")
    counts["all_liquid"] = len(master)
    print(f"  all_liquid.txt                 {len(master):>4}")

    metadata_path = WATCHLIST_DIR / "stock_metadata.csv"
    with metadata_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "indices", "avg_volume", "sector"])
        for s in master:
            idxs = symbol_indices.get(s, set())
            w.writerow([
                s,
                ",".join(sorted(idxs)),
                liquid[s],
                derive_sector(s, idxs, sector_hints),
            ])
    print(f"  {metadata_path.name}")

    print("\nDone.")


if __name__ == "__main__":
    main()
