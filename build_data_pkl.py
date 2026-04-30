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


def build_pickle(input_xlsx: str | Path, output_pkl: str | Path = "data.pkl") -> dict:
    input_xlsx = Path(input_xlsx)
    output_pkl = Path(output_pkl)

    # --- read both sheets ------------------------------------------------
    trades = pd.read_excel(input_xlsx, sheet_name="Trades")
    perf = pd.read_excel(input_xlsx, sheet_name="Performance")

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

    # --- payload ---------------------------------------------------------
    payload = {
        "trades": trades,
        "prices": prices,
        "alibaba_hk_proxy": proxy_target,
        "generated_at_utc": datetime.now(timezone.utc),
        "source_filename": input_xlsx.name,
        "n_trades": int(len(trades)),
        "n_companies": int(prices.shape[1]),
        "n_price_dates": int(len(prices)),
        "trade_date_range": (
            trades["Earliest Trade Date"].min().to_pydatetime(),
            trades["Earliest Trade Date"].max().to_pydatetime(),
        ),
        "price_date_range": (
            prices.index.min().to_pydatetime(),
            prices.index.max().to_pydatetime(),
        ),
        "schema_version": 1,
    }

    with open(output_pkl, "wb") as f:
        pickle.dump(payload, f, protocol=4)

    size_kb = output_pkl.stat().st_size / 1024
    print(f"\n✓ Wrote {output_pkl}  ({size_kb:,.0f} KB)")
    print(f"   Trades             : {payload['n_trades']:>6,}")
    print(f"   Companies          : {payload['n_companies']:>6,}")
    print(f"   Price-data rows    : {payload['n_price_dates']:>6,}")
    print(f"   Trade date range   : {payload['trade_date_range'][0].date()} → {payload['trade_date_range'][1].date()}")
    print(f"   Price date range   : {payload['price_date_range'][0].date()} → {payload['price_date_range'][1].date()}")
    print(f"   Generated (UTC)    : {payload['generated_at_utc'].strftime('%Y-%m-%d %H:%M:%S')}")

    return payload


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "LTGG_Trades_and_Performance.xlsx"
    dst = sys.argv[2] if len(sys.argv) > 2 else "data.pkl"
    build_pickle(src, dst)
