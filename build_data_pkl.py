"""
build_data_pkl.py
-----------------
Reads the LTGG Excel workbook (with `Trades` and `Performance` sheets) and
writes a single pickle file (`data.pkl`) that the Streamlit app consumes.

Run this in Google Colab (see build_data_pkl.ipynb) or locally:

    python build_data_pkl.py LTGG_Trades_and_Performance.xlsx data.pkl
"""

from __future__ import annotations

import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


# Alias map: Current-Portfolio name → name used in Trades & Performance sheets.
# Add entries here whenever a company rebrands or relists. Matching is
# case-insensitive on both sides.
NAME_ALIASES = {
    "beone medicines hk line": "Beigene Ltd",   # rebranded from BeiGene Aug-2025
}


def build_pickle(input_xlsx: str | Path, output_pkl: str | Path = "data.pkl") -> dict:
    input_xlsx = Path(input_xlsx)
    output_pkl = Path(output_pkl)

    # --- read sheets -----------------------------------------------------
    trades = pd.read_excel(input_xlsx, sheet_name="Trades")
    perf = pd.read_excel(input_xlsx, sheet_name="Performance")
    try:
        current_pf_raw = pd.read_excel(input_xlsx, sheet_name="Current Portfolio")
        current_raw = current_pf_raw["Instrument Name"].dropna().unique().tolist()
    except (ValueError, KeyError):
        current_raw = []
        print("⚠ No 'Current Portfolio' sheet found – the 'current portfolio only' "
              "toggle in the dashboard will be disabled.")

    # --- tidy trades -----------------------------------------------------
    trades["Earliest Trade Date"] = pd.to_datetime(trades["Earliest Trade Date"])
    trades = trades.dropna(subset=["Earliest Trade Date", "Instrument Name"]).reset_index(drop=True)

    # --- build friendly-name mapping (column A ↔ price columns) ---------
    friendly_names = perf["Instrument Name"].dropna().tolist()
    price_cols = perf.columns[3:].tolist()

    if len(friendly_names) != len(price_cols):
        raise ValueError(
            f"Mapping length mismatch: {len(friendly_names)} friendly names vs "
            f"{len(price_cols)} price columns. Check the workbook layout."
        )
    name_to_col = dict(zip(friendly_names, price_cols))

    # --- build prices DataFrame indexed by date, columns = friendly names
    raw = perf[["Name"] + price_cols].rename(columns={"Name": "Date"})
    raw["Date"] = pd.to_datetime(raw["Date"])
    raw = raw.dropna(subset=["Date"]).set_index("Date").sort_index()

    # Reverse mapping for column rename
    col_to_name = {v: k for k, v in name_to_col.items()}
    prices = raw.rename(columns=col_to_name)

    # Manual proxy: HK-listed Alibaba uses the ADR series
    proxy_target = "Alibaba Group Holding Sponsored ADR"
    if proxy_target in prices.columns:
        prices["Alibaba (HK Line)"] = prices[proxy_target]

    # Force float dtype so downstream arithmetic is consistent
    prices = prices.apply(pd.to_numeric, errors="coerce")

    # --- coverage check --------------------------------------------------
    trade_companies = set(trades["Instrument Name"].unique())
    missing = sorted(trade_companies - set(prices.columns))
    if missing:
        print(f"⚠ {len(missing)} trade companies have no price column: {missing}")
    else:
        print("✓ Every trade has a matching price column.")

    # --- current portfolio matching (case-insensitive + alias map) -------
    # The Current Portfolio sheet sometimes uses slightly different
    # capitalisation (e.g. "Adyen NV" vs "Adyen Nv") or a renamed company
    # (e.g. "Beone Medicines HK Line" → previously traded as "Beigene Ltd").
    # Resolve every current-portfolio name to its trade-sheet equivalent.
    trade_names_lower = {n.lower(): n for n in trade_companies}
    current_portfolio_names = set()
    unmatched_current = []
    for n in current_raw:
        key = n.lower()
        # First check the alias map for explicit name changes
        aliased = NAME_ALIASES.get(key)
        if aliased is not None:
            canonical = trade_names_lower.get(aliased.lower())
        else:
            canonical = trade_names_lower.get(key)
        if canonical is not None:
            current_portfolio_names.add(canonical)
        else:
            unmatched_current.append(n)

    if current_raw:
        print(f"✓ Current portfolio: {len(current_portfolio_names)}/{len(current_raw)} "
              f"holdings have trade history.")
        if unmatched_current:
            print(f"  ({len(unmatched_current)} held but never traded in this dataset: "
                  f"{unmatched_current})")

    # --- payload ---------------------------------------------------------
    payload = {
        "trades": trades,
        "prices": prices,
        "alibaba_hk_proxy": proxy_target,
        "current_portfolio_names": sorted(current_portfolio_names),
        "current_portfolio_raw": sorted(current_raw),
        "generated_at_utc": datetime.now(timezone.utc),
        "source_filename": input_xlsx.name,
        "n_trades": int(len(trades)),
        "n_companies": int(prices.shape[1]),
        "n_price_dates": int(len(prices)),
        "n_current_holdings": int(len(current_raw)),
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
    print(f"\n✓ Wrote {output_pkl}  ({size_kb:,.0f} KB)")
    print(f"   Trades             : {payload['n_trades']:>6,}")
    print(f"   Companies          : {payload['n_companies']:>6,}")
    print(f"   Current holdings   : {payload['n_current_holdings']:>6,}  ({len(current_portfolio_names)} matched to trades)")
    print(f"   Price-data rows    : {payload['n_price_dates']:>6,}")
    print(f"   Trade date range   : {payload['trade_date_range'][0].date()} → {payload['trade_date_range'][1].date()}")
    print(f"   Price date range   : {payload['price_date_range'][0].date()} → {payload['price_date_range'][1].date()}")
    print(f"   Generated (UTC)    : {payload['generated_at_utc'].strftime('%Y-%m-%d %H:%M:%S')}")

    return payload


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "LTGG_Trades_and_Performance.xlsx"
    dst = sys.argv[2] if len(sys.argv) > 2 else "data.pkl"
    build_pickle(src, dst)
