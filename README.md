# LTGG Trade Performance Dashboard

Interactive Streamlit dashboard. Each of the 228 trades plots as a single point: **trade weight (%) on X**, **post-trade performance (%) on Y**, coloured by transaction type. Filters for date range, transaction type, and individual companies (with search) live in the sidebar.

The app reads a single pre-processed pickle (`data.pkl`). To refresh, you re-run a Google Colab notebook against an updated copy of `LTGG_Trades_and_Performance.xlsx` and replace the pickle in this repo.

---

## Repository layout

```
.
├── app.py                       Streamlit app – reads data.pkl
├── data.pkl                     Pre-processed trades + price series (regenerate via Colab)
├── requirements.txt             Streamlit Cloud install list
├── build_data_pkl.py            Standalone script (also usable locally)
└── build_data_pkl.ipynb         Google Colab notebook (recommended)
```

The Excel workbook itself is **not** committed – only the pickle is. This keeps the repo small and makes the deployed app cold-start almost instantly.

---

## One-time setup

1. Create a new GitHub repo and commit the four code files plus a freshly-built `data.pkl` (run the Colab notebook once).
2. Go to <https://share.streamlit.io>, click **New app**, point it at this repo / branch / `app.py`, deploy.

---

## Refresh workflow (every time the trade data changes)

1. Export an up-to-date `LTGG_Trades_and_Performance.xlsx` (with the `Trades` and `Performance` sheets in the same layout).
2. Open `build_data_pkl.ipynb` in Google Colab (File → Upload notebook).
3. **Cell 1:** click *Choose Files*, select the new Excel file.
4. **Cell 2:** runs the build, prints a summary like:
   ```
   ✓ Every trade has a matching price column.
   ✓ Wrote data.pkl  (920 KB)
      Trades             :    228
      Companies          :     73
      Price-data rows    :  1,564
      Trade date range   : 2021-05-11 → 2026-04-14
      Price date range   : 2020-04-30 → 2026-04-28
   ```
5. **Cell 3:** downloads `data.pkl` to your computer.
6. Replace `data.pkl` in the repo (drag-drop on github.com works) and commit. Streamlit Cloud picks up the change within ~30 seconds and redeploys.

The footer of the dashboard shows when `data.pkl` was generated and what the latest price date is, so you can verify the refresh landed.

### Optional: push directly from Colab

`build_data_pkl.ipynb` has an optional fourth cell that PUTs the pickle straight into your GitHub repo via the API. One-time setup: create a fine-grained personal access token (Contents = Read & write, scoped to the target repo only), store it as a Colab secret called `GITHUB_TOKEN`, and edit the `REPO` / `BRANCH` constants in the cell.

---

## Run locally

```bash
pip install -r requirements.txt openpyxl
python build_data_pkl.py LTGG_Trades_and_Performance.xlsx data.pkl
streamlit run app.py
```

---

## Pickle schema

`data.pkl` is a dict with these keys:

| Key | Type | Description |
|---|---|---|
| `trades` | `pd.DataFrame` | 4 columns: `Earliest Trade Date`, `Transaction Type`, `Instrument Name`, `% Portfolio Order` |
| `prices` | `pd.DataFrame` | Daily total-return indices, `DatetimeIndex`, columns = friendly company names (incl. an `Alibaba (HK Line)` column duplicated from the ADR series) |
| `alibaba_hk_proxy` | `str` | Documents the proxy used |
| `generated_at_utc` | `datetime` | Build timestamp |
| `source_filename` | `str` | Excel file the build was run from |
| `n_trades`, `n_companies`, `n_price_dates` | `int` | Sanity-check counts |
| `trade_date_range`, `price_date_range` | `(datetime, datetime)` | Min / max dates |
| `schema_version` | `int` | Currently `1` |

---

## Notes

- Performance uses total-return indices, so dividends and corporate actions are baked in.
- "Alibaba (HK Line)" trades use the ADR series as a proxy because the HK line isn't in the price file.
- The trade date used is the **earliest** trade date when an order spans multiple days.
