# Australia: wages vs inflation vs money supply vs house prices

An interactive chart of the Australian Wage Price Index, CPI, broad money and
house prices, all indexed to 100 at the September quarter 1997. GitHub Actions
rebuilds it every week from ABS, RBA and BIS data, and GitHub Pages hosts it.

## How it works

- `build.py` fetches the data, builds the Plotly chart and writes a single
  static page to `docs/index.html`.
- `.github/workflows/build.yml` runs `build.py` every Friday morning
  (Melbourne time) and commits the new page if anything changed.
- `data/d01hist.xlsx` is the last good copy of RBA Table D1. The RBA site
  sometimes blocks cloud servers. When that happens, the build uses this copy
  and says so in the page notes.
- If a source fails and there is no usable copy, the build fails and the
  live page is left unchanged. GitHub emails you about the failed run.

## Setup (one time)

1. Create a new **public** GitHub repository and push these files to it.
2. Go to **Settings → Pages**. Under *Build and deployment*, set Source to
   **Deploy from a branch**, Branch to **main**, and Folder to **/docs**.
3. Go to **Settings → Actions → General → Workflow permissions** and select
   **Read and write permissions**, so the workflow can commit the page.
4. On the **Actions** tab, open *Rebuild chart* and click **Run workflow** to
   test it.

The site is published at `https://<your-username>.github.io/<repo-name>/`.

## Running locally

```bash
pip install -r requirements.txt
python build.py
open docs/index.html
```

## Options

At the top of `build.py`:

- `BASE_QUARTER`: the quarter where every series equals 100 (default
  `1997Q3`, when the WPI starts).
- `MONEY_MEASURE`: `"broad"` (default) or `"M3"`.

## If the RBA download keeps failing

Download <https://www.rba.gov.au/statistics/tables/xls/d01hist.xlsx> in your
browser, replace `data/d01hist.xlsx` with it, commit, and run the workflow.

## Sources

- ABS, Wage Price Index, Australia: series A2713849C
- ABS, Consumer Price Index, Australia: series A2325846C
- RBA, Statistical Table D1: series DGFABMM (broad money) or DGFAM3M (M3)
- BIS, Residential property prices: Q.AU.N.628
