"""
build_data_pkl.py
-----------------
Reads the LTGG Excel workbook (with `Trades`, `Performance`, and optionally
`Current Portfolio` sheets) and writes a single pickle file (`data.pkl`) that
the Streamlit app consumes.

Run this in Google Colab (see build_data_pkl.ipynb) or locally:

    python build_data_pkl.py LTGG_Trades_and_Performance.xlsx data.pkl
"""

from __future__ import annotations

import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


# Companies that have rebranded. These names are normalised throughout the
# dashboard, including trades, prices, dropdowns, charts, tables, KPIs, and the
# current-portfolio filter.
BEONE_CANONICAL_NAME = "BeOne Medicine"

RENAMES = {
    "Beigene Ltd": BEONE_CANONICAL_NAME,
    "BeiGene Ltd": BEONE_CANONICAL_NAME,
    "Beigene": BEONE_CANONICAL_NAME,
    "BeiGene": BEONE_CANONICAL_NAME,
    "BeOne Medicines": BEONE_CANONICAL_NAME,
    "Beone Medicines": BEONE_CANONICAL_NAME,
    "BeOne Medicine HK Line": BEONE_CANONICAL_NAME,
    "BeOne Medicines HK Line": BEONE_CANONICAL_NAME,
    "Beone Medicines HK Line": BEONE_CANONICAL_NAME,
}

# Alias map: Current-Portfolio name -> canonical dashboard name.
# Matching is case-insensitive and whitespace-insensitive.
NAME_ALIASES = {
    "beone medicine hk line": BEONE_CANONICAL_NAME,
    "beone medicines hk line": BEONE_CANONICAL_NAME,
    "beone medicine": BEONE_CANONICAL_NAME,
    "beone medicines": BEONE_CANONICAL_NAME,
    "beigene ltd": BEONE_CANONICAL_NAME,
    "beigene": BEONE_CANONICAL_NAME,
}


def _clean_name(value):
    if not isinstance(value, str):
        return value
    return " ".join(value.replace("\xa0", " ").split())


def _name_lookup():
    lookup = {}
    for old, new in RENAMES.items():
        lookup[_clean_name(old).lower()] = new
    for old, new in NAME_ALIASES.items():
        lookup[_clean_name(old).lower()] = new
    return lookup


_NAME_LOOKUP = _name_lookup()


def canonicalise_name(value):
    """Return the dashboard display name for a workbook/security label."""
    if not isinstance(value, str):
        return value
    clean = _clean_name(value)
    return _NAME_LOOKUP.get(clean.lower(), clean)


def _dedupe_price_columns(prices: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate price columns created by a rename, keeping first values."""
    if not prices.columns.has_duplicates:
        return prices
    return prices.T.groupby(level=0, sort=False).first().T


def build_pickle(input_xlsx: str | Path, output_pkl: str | Path = "data.pkl") -> dict:
    input_xlsx = Path(input_xlsx)
    output_pkl = Path(output_pkl)

    # Read sheets.
    trades = pd.read_excel(input_xlsx, sheet_name="Trades")
    perf = pd.read_excel(input_xlsx, sheet_name="Performance")
    try:
        current_pf_raw = pd.read_excel(input_xlsx, sheet_name="Current Portfolio")
        current_raw = current_pf_raw["Instrument Name"].dropna().unique().tolist()
    except (ValueError, KeyError):
        current_raw = []
        print("No 'Current Portfolio' sheet found - the toggle will be disabled.")

    # Tidy trades.
    trades["Earliest Trade Date"] = pd.to_datetime(trades["Earliest Trade Date"])
    trades = trades.dropna(subset=["Earliest Trade Date", "Instrument Name"]).reset_index(drop=True)
    trades["Instrument Name"] = trades["Instrument Name"].map(canonicalise_name)

    # Build friendly-name mapping, column A to price columns.
    friendly_names = perf["Instrument Name"].dropna().map(canonicalise_name).tolist()
    price_cols = perf.columns[3:].tolist()

    if len(friendly_names) != len(price_cols):
        raise ValueError(
            f"Mapping length mismatch: {len(friendly_names)} friendly names vs "
            f"{len(price_cols)} price columns. Check the workbook layout."
        )
    name_to_col = dict(zip(friendly_names, price_cols))

    # Build prices DataFrame indexed by date, columns = friendly names.
    raw = perf[["Name"] + price_cols].rename(columns={"Name": "Date"})
    raw["Date"] = pd.to_datetime(raw["Date"])
    raw = raw.dropna(subset=["Date"]).set_index("Date").sort_index()

    col_to_name = {v: k for k, v in name_to_col.items()}
    prices = raw.rename(columns=col_to_name)
    prices = prices.rename(columns={c: canonicalise_name(c) for c in prices.columns})
    prices = _dedupe_price_columns(prices)

    # Manual proxy: HK-listed Alibaba uses the ADR series.
    proxy_target = "Alibaba Group Holding Sponsored ADR"
    if proxy_target in prices.columns:
        prices["Alibaba (HK Line)"] = prices[proxy_target]

    prices = prices.apply(pd.to_numeric, errors="coerce")
    prices.index = pd.DatetimeIndex(prices.index.values.astype("datetime64[ns]"), name=prices.index.name)

    # Coverage check.
    trade_companies = set(trades["Instrument Name"].unique())
    missing = sorted(trade_companies - set(prices.columns))
    if missing:
        print(f"{len(missing)} trade companies have no price column: {missing}")
    else:
        print("Every trade has a matching price column.")

    # Current portfolio matching. Store both the matched list and the raw/display
    # list in canonical dashboard names, because some app versions read the raw
    # field directly for the current-portfolio filter.
    trade_names_lower = {n.lower(): n for n in trade_companies if isinstance(n, str)}
    current_portfolio_names = set()
    current_portfolio_display = []
    unmatched_current = []

    for raw_name in current_raw:
        display_name = canonicalise_name(raw_name)
        canonical = None
        if isinstance(display_name, str):
            canonical = trade_names_lower.get(display_name.lower())
        if canonical is not None:
            current_portfolio_names.add(canonical)
            current_portfolio_display.append(canonical)
        else:
            current_portfolio_display.append(display_name)
            unmatched_current.append(raw_name)

    if current_raw:
        print(
            f"Current portfolio: {len(current_portfolio_names)}/{len(current_raw)} "
            "holdings have trade history."
        )
        if unmatched_current:
            print(
                f"({len(unmatched_current)} held but never traded in this dataset: "
                f"{unmatched_current})"
            )

    current_portfolio_names = sorted(current_portfolio_names)
    current_portfolio_display = sorted(set(current_portfolio_display))

    # Explicit guard for the Beigene/BeOne rebrand and current holding.
    if BEONE_CANONICAL_NAME in trade_companies and BEONE_CANONICAL_NAME in prices.columns:
        current_portfolio_names = sorted(set(current_portfolio_names) | {BEONE_CANONICAL_NAME})
        current_portfolio_display = sorted(set(current_portfolio_display) | {BEONE_CANONICAL_NAME})

    payload = {
        "trades": trades,
        "prices": prices,
        "alibaba_hk_proxy": proxy_target,
        "current_portfolio_names": current_portfolio_names,
        "current_portfolio_raw": current_portfolio_display,
        "current_portfolio_display_names": current_portfolio_display,
        "current_portfolio_companies": current_portfolio_names,
        "generated_at_utc": datetime.now(timezone.utc),
        "source_filename": input_xlsx.name,
        "n_trades": int(len(trades)),
        "n_companies": int(prices.shape[1]),
        "n_price_dates": int(len(prices)),
        "n_current_holdings": int(len(current_portfolio_display)),
        "trade_date_range": (
            trades["Earliest Trade Date"].min().to_pydatetime(),
            trades["Earliest Trade Date"].max().to_pydatetime(),
        ),
        "price_date_range": (
            prices.index.min().to_pydatetime(),
            prices.index.max().to_pydatetime(),
        ),
        "schema_version": 2,
    }

    with open(output_pkl, "wb") as f:
        pickle.dump(payload, f, protocol=4)

    size_kb = output_pkl.stat().st_size / 1024
    print(f"
Wrote {output_pkl}  ({size_kb:,.0f} KB)")
    print(f"   Trades             : {payload['n_trades']:>6,}")
    print(f"   Companies          : {payload['n_companies']:>6,}")
    print(
        f"   Current holdings   : {payload['n_current_holdings']:>6,}  "
        f"({len(current_portfolio_names)} matched to trades)"
    )
    print(f"   Price-data rows    : {payload['n_price_dates']:>6,}")
    print(f"   Trade date range   : {payload['trade_date_range'][0].date()} to {payload['trade_date_range'][1].date()}")
    print(f"   Price date range   : {payload['price_date_range'][0].date()} to {payload['price_date_range'][1].date()}")
    print(f"   Generated (UTC)    : {payload['generated_at_utc'].strftime('%Y-%m-%d %H:%M:%S')}")

    return payload


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "LTGG_Trades_and_Performance.xlsx"
    dst = sys.argv[2] if len(sys.argv) > 2 else "data.pkl"
    build_pickle(src, dst)
