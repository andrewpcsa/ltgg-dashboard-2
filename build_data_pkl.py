"""
build_data_pkl.py
-----------------
Reads the LTGG Excel workbook (with `Trades`, `Performance`, and optionally
`Current Portfolio` sheets) and writes a single pickle file (`data.pkl`) that
the Streamlit app consumes.

Run locally or in Colab:

    python build_data_pkl.py "LTGG Trades and Performance.xlsx" data.pkl

If no input file is supplied, the script looks first for:

    LTGG Trades and Performance.xlsx

and then for the older filename:

    LTGG_Trades_and_Performance.xlsx
"""

from __future__ import annotations

import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


BEONE_CANONICAL_NAME = "BeOne Medicine"

# Display-name overrides applied consistently to trades, price columns, and the
# current-portfolio list. The refreshed workbook no longer uses Beigene, but it
# still labels the holding as "Beone Medicines HK Line". The dashboard display
# name is intentionally normalised to "BeOne Medicine".
DISPLAY_NAME_OVERRIDES = {
    "Beone Medicines HK Line": BEONE_CANONICAL_NAME,
    "BeOne Medicines HK Line": BEONE_CANONICAL_NAME,
    "BeOne Medicine HK Line": BEONE_CANONICAL_NAME,
    "Beone Medicines": BEONE_CANONICAL_NAME,
    "BeOne Medicines": BEONE_CANONICAL_NAME,
    "BeiGene Ltd": BEONE_CANONICAL_NAME,
    "Beigene Ltd": BEONE_CANONICAL_NAME,
}

DEFAULT_INPUT_CANDIDATES = (
    "LTGG Trades and Performance.xlsx",
    "LTGG_Trades_and_Performance.xlsx",
)


def _clean_name(value):
    """Normalise whitespace in security names without changing capitalisation."""
    if not isinstance(value, str):
        return value
    return " ".join(value.replace("\xa0", " ").split())


def _normalised_key(value: str) -> str:
    return _clean_name(value).casefold()


_NAME_LOOKUP = {
    _normalised_key(old): new for old, new in DISPLAY_NAME_OVERRIDES.items()
}


def canonicalise_name(value):
    """Return the dashboard display name for a workbook/security label."""
    if not isinstance(value, str):
        return value
    clean = _clean_name(value)
    return _NAME_LOOKUP.get(_normalised_key(clean), clean)


def _unique_clean_strings(values: Iterable) -> list[str]:
    """Clean values, drop nulls, and preserve first-seen order."""
    seen = set()
    output = []
    for value in values:
        if pd.isna(value):
            continue
        clean = _clean_name(str(value))
        if not clean:
            continue
        key = clean.casefold()
        if key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _dedupe_price_columns(prices: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate price columns created by display-name overrides."""
    if not prices.columns.has_duplicates:
        return prices
    return prices.T.groupby(level=0, sort=False).first().T


def _resolve_default_input() -> Path:
    for filename in DEFAULT_INPUT_CANDIDATES:
        candidate = Path(filename)
        if candidate.exists():
            return candidate
        candidate = Path.cwd() / filename
        if candidate.exists():
            return candidate
        candidate = Path("/mnt/data") / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No input workbook supplied and none of the default filenames were found: "
        + ", ".join(DEFAULT_INPUT_CANDIDATES)
    )


def build_pickle(input_xlsx: str | Path, output_pkl: str | Path = "data.pkl") -> dict:
    input_xlsx = Path(input_xlsx)
    output_pkl = Path(output_pkl)

    trades = pd.read_excel(input_xlsx, sheet_name="Trades")
    perf = pd.read_excel(input_xlsx, sheet_name="Performance")

    try:
        current_pf_raw = pd.read_excel(input_xlsx, sheet_name="Current Portfolio")
        current_raw = _unique_clean_strings(current_pf_raw["Instrument Name"].tolist())
    except (ValueError, KeyError):
        current_raw = []
        print("No 'Current Portfolio' sheet found. Current-portfolio filtering will be disabled.")

    # Tidy trades.
    trades["Earliest Trade Date"] = pd.to_datetime(trades["Earliest Trade Date"])
    trades = trades.dropna(subset=["Earliest Trade Date", "Instrument Name"]).reset_index(drop=True)
    trades["Instrument Name"] = trades["Instrument Name"].map(canonicalise_name)

    # Build friendly-name mapping from Performance column A to price columns.
    friendly_names = [canonicalise_name(n) for n in _unique_clean_strings(perf["Instrument Name"].tolist())]
    price_cols = list(perf.columns[3:])

    if len(friendly_names) != len(price_cols):
        raise ValueError(
            f"Mapping length mismatch: {len(friendly_names)} friendly names vs "
            f"{len(price_cols)} price columns. Check the workbook layout."
        )

    raw_prices = perf[["Name"] + price_cols].rename(columns={"Name": "Date"})
    raw_prices["Date"] = pd.to_datetime(raw_prices["Date"])
    raw_prices = raw_prices.dropna(subset=["Date"]).set_index("Date").sort_index()

    # Rename price columns using the friendly-name row in the same order.
    col_to_name = {col: name for name, col in zip(friendly_names, price_cols)}
    prices = raw_prices.rename(columns=col_to_name)
    prices = prices.rename(columns={col: canonicalise_name(col) for col in prices.columns})
    prices = _dedupe_price_columns(prices)

    # Manual proxy: HK-listed Alibaba uses the ADR series.
    proxy_target = "Alibaba Group Holding Sponsored ADR"
    if proxy_target in prices.columns:
        prices["Alibaba (HK Line)"] = prices[proxy_target]

    prices = prices.apply(pd.to_numeric, errors="coerce")
    prices.index = pd.DatetimeIndex(prices.index.values.astype("datetime64[ns]"), name=prices.index.name)

    trade_companies = set(trades["Instrument Name"].dropna().unique())
    price_companies = set(prices.columns)

    missing_prices = sorted(trade_companies - price_companies)
    if missing_prices:
        print(f"Warning: {len(missing_prices)} trade companies have no price column: {missing_prices}")
    else:
        print("Every trade has a matching price column.")

    # Current portfolio: canonicalise the raw workbook labels first, then match
    # case-insensitively against the dashboard universe. This is the key step
    # that makes "BeOne Medicine" appear when the app is filtered to current
    # holdings, and also resolves case-only workbook differences such as
    # "Adyen NV" vs "Adyen Nv".
    current_display = [canonicalise_name(name) for name in current_raw]
    dashboard_universe = trade_companies & price_companies
    universe_by_key = {_normalised_key(name): name for name in dashboard_universe}

    matched_current = set()
    current_display_for_app = []
    unmatched_current = []

    for name in current_display:
        matched_name = universe_by_key.get(_normalised_key(name)) if isinstance(name, str) else None
        if matched_name is not None:
            matched_current.add(matched_name)
            current_display_for_app.append(matched_name)
        else:
            current_display_for_app.append(name)
            unmatched_current.append(name)

    current_portfolio_names = sorted(matched_current)
    current_portfolio_raw = sorted(set(current_display_for_app))
    unmatched_current = sorted(set(unmatched_current))

    if current_raw:
        print(
            f"Current portfolio: {len(current_portfolio_names)}/{len(current_portfolio_raw)} "
            "holdings have both trade history and price data."
        )
        if unmatched_current:
            print(f"Held but not in the trade/price universe: {unmatched_current}")

    # Hard validation for the rebrand/current-holding issue.
    if BEONE_CANONICAL_NAME not in trade_companies:
        raise ValueError(f"{BEONE_CANONICAL_NAME} is missing from trades after normalisation.")
    if BEONE_CANONICAL_NAME not in price_companies:
        raise ValueError(f"{BEONE_CANONICAL_NAME} is missing from price columns after normalisation.")
    if BEONE_CANONICAL_NAME not in current_portfolio_names:
        raise ValueError(f"{BEONE_CANONICAL_NAME} is missing from current_portfolio_names.")
    if BEONE_CANONICAL_NAME not in current_portfolio_raw:
        raise ValueError(f"{BEONE_CANONICAL_NAME} is missing from current_portfolio_raw.")

    payload = {
        "trades": trades,
        "prices": prices,
        "alibaba_hk_proxy": proxy_target,
        "current_portfolio_names": current_portfolio_names,
        "current_portfolio_raw": current_portfolio_raw,
        "current_portfolio_display_names": current_portfolio_raw,
        "current_portfolio_companies": current_portfolio_names,
        "current_portfolio_workbook_raw": sorted(current_raw),
        "display_name_overrides": DISPLAY_NAME_OVERRIDES,
        "generated_at_utc": datetime.now(timezone.utc),
        "source_filename": input_xlsx.name,
        "n_trades": int(len(trades)),
        "n_companies": int(prices.shape[1]),
        "n_price_dates": int(len(prices)),
        "n_current_holdings": int(len(current_portfolio_raw)),
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
    print(f"\nWrote {output_pkl} ({size_kb:,.0f} KB)")
    print(f"   Source workbook    : {payload['source_filename']}")
    print(f"   Trades             : {payload['n_trades']:>6,}")
    print(f"   Companies          : {payload['n_companies']:>6,}")
    print(
        f"   Current holdings   : {payload['n_current_holdings']:>6,} "
        f"({len(current_portfolio_names)} matched to trades/prices)"
    )
    print(f"   Price-data rows    : {payload['n_price_dates']:>6,}")
    print(f"   Trade date range   : {payload['trade_date_range'][0].date()} to {payload['trade_date_range'][1].date()}")
    print(f"   Price date range   : {payload['price_date_range'][0].date()} to {payload['price_date_range'][1].date()}")
    print(f"   Generated UTC      : {payload['generated_at_utc'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   BeOne check        : {BEONE_CANONICAL_NAME} is in trades, prices, and current portfolio")

    return payload


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else _resolve_default_input()
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data.pkl")
    build_pickle(src, dst)
