# Equity Research

An open-source toolkit for equity research — transparent, reproducible, and
built so every number traces back to its source.

This repo is a home for small, focused research tools. The first one:

### 📄 [Tearsheet](tearsheet/) — sector valuation dashboards

A deterministic pipeline that pulls financials from **SEC EDGAR**, market data
from **Yahoo Finance**, computes a full set of valuation multiples, and renders
polished, self-contained HTML dashboards — with per-metric sourcing, formulas,
and an explicit reason behind every `N/A`.

🔗 **[Live demo — multi-sector dashboard](https://adamstaley123.github.io/equity-research/)**
&nbsp;·&nbsp; browse all three sectors with a **sector dropdown** and a
**market-cap filter** (every number still links to its filing).

```bash
cd tearsheet
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export EDGAR_USER_AGENT="YourName your@email.com"   # courtesy to SEC; optional

# A built-in sector (payments | semiconductors | consumer_staples):
.venv/bin/python pipeline/run.py --sector payments
# → open output/Payments/payments_valuation.html

# …or any tickers you like, no config file (metadata auto-filled from Yahoo):
.venv/bin/python pipeline/run.py --tickers NEE,DUK,SO --name "Utilities"

# Combine the curated sectors into one browsable dashboard (sector + cap filters):
.venv/bin/python pipeline/build_combined.py        # → docs/index.html
```

Keyless by default (SEC EDGAR + Yahoo Finance); every number links to its source.
The combined view is a **deterministic render over verified sectors** — no live
screening, no model in the data path.

See [`tearsheet/README.md`](tearsheet/README.md) for the full guide, including
how to add your own sector in a single YAML file.

---

More tools will live here over time (screeners, deep-dive models, data
adapters). Contributions and ideas welcome — see each tool's README.

**Not investment advice.** For research and educational use only.
Licensed under [MIT](LICENSE).
