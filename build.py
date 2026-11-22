"""Build docs/index.html: Australian wages vs inflation vs money vs house prices.

Run locally with `python build.py`, or let the GitHub Actions workflow run it
on a schedule. Exits non-zero (so the page is NOT updated) if any source
cannot be fetched and no usable cached copy exists.

Data sources:
  Wages:        ABS 6345.0 Wage Price Index, total hourly rates of pay excl.
                bonuses, Australia, private and public, all industries,
                seasonally adjusted (A2713849C). Quarterly from Sep qtr 1997.
  Inflation:    ABS 6401.0 CPI, all groups, Australia, original, quarterly
                (A2325846C). Identical to RBA G1 GCPIAG.
  Money supply: RBA Table D1 monthly growth in broad money (or M3),
                seasonally adjusted and adjusted for series breaks
                (DGFABMM / DGFAM3M), chained into an index. The D3 levels
                are not break-adjusted, so growth from them is distorted.
  House prices: BIS selected residential property prices, Australia, nominal
                (Q.AU.N.628). Spliced: ABS RPPI (8 capitals, pure price) to
                Q4 2021; all dwellings, whole country from Q1 2022.
"""

from __future__ import annotations

import html
import io
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import readabs as ra
import requests

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUT = ROOT / "docs" / "index.html"

BASE_QUARTER = "1997Q3"
MONEY_MEASURE = "broad"  # "broad" or "M3"

RBA = "https://www.rba.gov.au/statistics/tables/xls/{}hist.xlsx"
MONEY_SERIES = {
    "broad": ("DGFABMM", "Broad money"),
    "M3": ("DGFAM3M", "M3"),
}
MONEY_ID, MONEY_NAME = MONEY_SERIES[MONEY_MEASURE]

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
RBA_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/octet-stream;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.rba.gov.au/statistics/tables/",
}

# Notes collected during the build and shown on the page
build_notes: list[str] = []


# --------------------------------------------------------------------- data
def fetch_wpi() -> pd.Series:
    data, _ = ra.read_abs_series(cat="6345.0", series_id="A2713849C")
    s = data["A2713849C"].dropna()
    if not isinstance(s.index, pd.PeriodIndex):
        s.index = pd.PeriodIndex(s.index, freq="Q")
    return s.rename("Wages (WPI)")


def fetch_cpi() -> pd.Series:
    data, _ = ra.read_abs_series(cat="6401.0", series_id="A2325846C")
    s = data["A2325846C"].dropna()
    if not isinstance(s.index, pd.PeriodIndex):
        s.index = pd.PeriodIndex(s.index, freq="Q")
    return s.rename("Inflation (CPI)")


def _get_rba_workbook(table: str) -> bytes:
    """Download an RBA workbook; fall back to the committed copy in data/."""
    url = RBA.format(table)
    cached = DATA_DIR / f"{table}hist.xlsx"
    session = requests.Session()
    session.headers.update(RBA_HEADERS)
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=60)
            if resp.ok and resp.content[:2] == b"PK":  # real xlsx, not a block page
                DATA_DIR.mkdir(exist_ok=True)
                cached.write_bytes(resp.content)
                return resp.content
            print(f"RBA {table}: HTTP {resp.status_code} (attempt {attempt + 1}/3)")
        except requests.RequestException as e:
            print(f"RBA {table}: {e} (attempt {attempt + 1}/3)")
        time.sleep(5 * (attempt + 1))

    if cached.exists():
        print(f"RBA {table}: download blocked, using cached {cached.name}")
        build_notes.append(
            f"RBA Table {table.upper()} could not be downloaded on this run; "
            "money supply uses the most recent copy previously saved."
        )
        return cached.read_bytes()
    raise RuntimeError(
        f"rba.gov.au blocked {url} and there is no cached copy at {cached}. "
        "Download it in a browser, save it to data/, commit, and re-run."
    )


def _read_rba(table: str, series_id: str) -> tuple[pd.Series, str]:
    raw = pd.read_excel(
        io.BytesIO(_get_rba_workbook(table)), sheet_name="Data", header=None
    )
    id_row = raw.index[raw[0] == "Series ID"][0]
    matches = raw.columns[raw.iloc[id_row] == series_id]
    if len(matches) == 0:
        raise KeyError(f"{series_id} not found in RBA table {table.upper()}")
    col = matches[0]
    pub_rows = raw.index[raw[0] == "Publication date"]
    published = ""
    if len(pub_rows):
        p = pd.to_datetime(raw.iloc[pub_rows[0], col], errors="coerce", dayfirst=True)
        published = "" if pd.isna(p) else p.strftime("%-d %b %Y")
    df = raw.iloc[id_row + 1 :, [0, col]]
    s = pd.Series(
        pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
        index=pd.to_datetime(df.iloc[:, 0], errors="coerce"),
    )
    return s[s.index.notna()].dropna(), published


def fetch_money() -> pd.Series:
    """Chain break-adjusted monthly growth into an index; quarterly average."""
    growth, published = _read_rba("d01", MONEY_ID)
    if published:
        build_notes.append(f"RBA Table D1 publication date: {published}.")
    index = (1 + growth / 100).cumprod()
    q = index.groupby(index.index.to_period("Q"))
    return q.mean()[q.count() == 3].rename(MONEY_NAME)  # complete quarters only


def fetch_house_prices() -> pd.Series:
    url = (
        "https://stats.bis.org/api/v2/data/dataflow/"
        "BIS/WS_SPP/1.0/Q.AU.N.628?format=csv"
    )
    resp = requests.get(url, timeout=60, headers={"User-Agent": BROWSER_UA})
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    s = pd.Series(
        pd.to_numeric(df["OBS_VALUE"], errors="coerce").values,
        index=pd.PeriodIndex(df["TIME_PERIOD"].str.replace("-", ""), freq="Q"),
        name="House prices",
    ).dropna()
    return s.sort_index()


def build_dataframe() -> pd.DataFrame:
    series = [fetch_wpi(), fetch_cpi(), fetch_money(), fetch_house_prices()]
    df = pd.concat(series, axis=1).sort_index().loc[BASE_QUARTER:]
    missing = df.columns[df.loc[BASE_QUARTER].isna()]
    if len(missing):
        raise ValueError(f"No {BASE_QUARTER} value for: {list(missing)}")
    indexed = df.div(df.loc[BASE_QUARTER]).mul(100)
    indexed["Real wages (WPI / CPI)"] = (
        indexed["Wages (WPI)"] / indexed["Inflation (CPI)"] * 100
    )
    return indexed.dropna(how="all")


def latest(df: pd.DataFrame, col: str):
    s = df[col].dropna()
    return s.index[-1], s.iloc[-1]


# -------------------------------------------------------------------- chart
STYLES = {
    "Wages (WPI)": dict(color="#1f77b4", width=2.5),
    "Inflation (CPI)": dict(color="#d62728", width=2.5),
    MONEY_NAME: dict(color="#2ca02c", width=2.5),
    "House prices": dict(color="#9467bd", width=2.5),
    "Real wages (WPI / CPI)": dict(color="#ff7f0e", width=2, dash="dash"),
}


def make_chart(df: pd.DataFrame) -> go.Figure:
    x = df.index.to_timestamp()
    fig = go.Figure()
    for col, style in STYLES.items():
        fig.add_trace(
            go.Scatter(
                x=x,
                y=df[col],
                name=col,
                line=style,
                hovertemplate="%{y:.1f}<extra>" + col + "</extra>",
            )
        )
    fig.add_hline(y=100, line_dash="dot", line_color="grey", opacity=0.6)
    brk = pd.Period("2022Q1").to_timestamp()
    fig.add_vline(x=brk, line_dash="dot", line_color="#9467bd", opacity=0.4)
    fig.add_annotation(
        x=brk, y=0.32, yref="paper", xanchor="right", yanchor="middle",
        text="House price<br>series break", showarrow=False, align="right",
        font=dict(size=10, color="#9467bd"),
    )
    fig.update_layout(
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        yaxis_title=f"Index ({BASE_QUARTER} = 100)",
        height=620,
        margin=dict(l=60, r=20, t=60, b=40),
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"),
        updatemenus=[
            dict(
                type="buttons", direction="left",
                x=0.0, xanchor="left", y=1.0, yanchor="top",
                pad=dict(l=5, t=5),
                buttons=[
                    dict(label="Linear", method="relayout",
                         args=[{"yaxis.type": "linear"}]),
                    dict(label="Log", method="relayout",
                         args=[{"yaxis.type": "log"}]),
                ],
            )
        ],
        xaxis=dict(rangeslider=dict(visible=True)),
    )
    return fig


# --------------------------------------------------------------------- page
def qtr_label(p: pd.Period) -> str:
    return f"{p.end_time.strftime('%b')} qtr {p.year}"


def build_page(df: pd.DataFrame, fig: go.Figure) -> str:
    chart = fig.to_html(
        full_html=False, include_plotlyjs="cdn",
        config={"responsive": True, "displaylogo": False},
    )
    built = datetime.now(ZoneInfo("Australia/Melbourne")).strftime(
        "%-d %B %Y, %-I:%M %p %Z"
    )
    base = pd.Period(BASE_QUARTER, freq="Q")
    rows = []
    for col in df.columns:
        q, v = latest(df, col)
        years = (q - base).n / 4
        cagr = ((v / 100) ** (1 / years) - 1) * 100
        rows.append(
            f'<tr><td><span class="swatch" style="background:{STYLES[col]["color"]}"></span>'
            f"{html.escape(col)}</td><td>{qtr_label(q)}</td><td>{v:.1f}</td>"
            f"<td>{v - 100:+.1f}%</td><td>{cagr:+.2f}%</td></tr>"
        )
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in build_notes)
    money_desc = (
        "broad money (M3 plus other borrowings from the private sector by "
        "non-ADI financial institutions)"
        if MONEY_MEASURE == "broad"
        else "M3 (currency plus all deposits with ADIs)"
    )
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Australia: wages vs inflation vs money supply vs house prices</title>
<meta name="description" content="Australian wages, CPI, {MONEY_NAME.lower()} and house prices indexed to {qtr_label(base)}, updated automatically from ABS, RBA and BIS data.">
<style>
  :root {{ --ink:#1d2330; --muted:#5b6475; --line:#e3e6ec; --bg:#fafbfc; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
         color:var(--ink); background:var(--bg); }}
  main {{ max-width:1100px; margin:0 auto; padding:32px 20px 56px; }}
  h1 {{ font-size:1.6rem; line-height:1.25; margin:0 0 6px; }}
  h2 {{ font-size:1.15rem; margin:36px 0 10px; }}
  .sub {{ color:var(--muted); margin:0 0 20px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff;
           border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:.95rem; }}
  th, td {{ padding:9px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ background:#f3f5f8; font-weight:600; }}
  tr:last-child td {{ border-bottom:none; }}
  .swatch {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:8px; }}
  .table-wrap {{ overflow-x:auto; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; }}
  a {{ color:#1f5fbf; }}
  @media (max-width: 600px) {{
    main {{ padding:20px 12px 40px; }}
    h1 {{ font-size:1.3rem; }}
    table {{ font-size:.82rem; }}
    th, td {{ padding:7px 6px; }}
    td:first-child {{ white-space:normal; }}
    .swatch {{ margin-right:5px; }}
    .card {{ padding:4px; }}
  }}
  footer {{ margin-top:36px; color:var(--muted); font-size:.85rem; }}
</style>
</head>
<body>
<main>
  <h1>Australia: wages vs inflation vs money supply vs house prices</h1>
  <p class="sub">All series indexed to 100 at the {qtr_label(base)} (the start of the
  Wage Price Index). Each line runs to its latest published quarter.
  Page last rebuilt {built}.</p>

  <div class="card">{chart}</div>

  <h2>Growth since the {qtr_label(base)}</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Series</th><th>Latest</th><th>Index</th><th>Cumulative</th><th>Per year</th></tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
  </div>

  <h2>Notes on the data</h2>
  <ul>
    <li><strong>Wages</strong> use the ABS Wage Price Index (total hourly rates of pay
    excluding bonuses, all sectors, seasonally adjusted). It holds the mix of jobs
    constant, so it measures pay rates rather than changes in who is employed.</li>
    <li><strong>Inflation</strong> is the ABS all groups CPI for Australia. The ABS
    re-referenced it to September 2025 = 100 when the complete monthly CPI was
    introduced; this does not affect growth rates.</li>
    <li><strong>Money supply</strong> is {money_desc}. It is built from the RBA's
    monthly growth rates, which are seasonally adjusted and adjusted for breaks in
    the series. The RBA's published dollar levels are not break-adjusted (for
    example, the July 2019 change in data collection), so growth calculated from
    them understates the true increase.</li>
    <li><strong>House prices</strong> are the BIS residential property price index
    for Australia. It is a spliced series: to the December quarter 2021 it is the
    ABS index for all dwellings in the eight capital cities; from the March quarter
    2022, after the ABS discontinued that index, it covers all dwellings across the
    whole country. Compare movements either side of 2022 with care. It is usually
    published a quarter or two behind the other series.</li>
    <li><strong>Real wages</strong> are wages divided by CPI: above 100 means pay
    rates have grown faster than consumer prices since 1997.</li>
    {notes}
  </ul>

  <h2>Sources</h2>
  <ul>
    <li>Australian Bureau of Statistics, <a href="https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/wage-price-index-australia/latest-release">Wage Price Index, Australia</a> (series A2713849C)</li>
    <li>Australian Bureau of Statistics, <a href="https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release">Consumer Price Index, Australia</a> (series A2325846C)</li>
    <li>Reserve Bank of Australia, <a href="https://www.rba.gov.au/statistics/tables/">Statistical Table D1, Growth in Selected Financial Aggregates</a> (series {MONEY_ID})</li>
    <li>Bank for International Settlements, <a href="https://data.bis.org/topics/RPP">Residential property prices</a> (Q.AU.N.628)</li>
  </ul>

  <footer>Source data: ABS, RBA and BIS; calculations and indexing are our own and
  are not endorsed by those organisations. Rebuilt automatically each week.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    df = build_dataframe()
    fig = make_chart(df)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build_page(df, fig), encoding="utf-8")
    for col in df.columns:
        q, v = latest(df, col)
        print(f"  {col:<26} {q}  {v - 100:+7.1f}%")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # fail the workflow so a broken page is never published
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        sys.exit(1)
