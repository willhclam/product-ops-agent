# Product Ops Agent

A daily-automated sprint reporting dashboard for the Jeeves engineering organization. Pulls live data from **Linear** and **Google Sheets**, generates a self-contained HTML report, and publishes it to **GitHub Pages** on a daily schedule.

## Live Report

> **URL:** `https://<your-org>.github.io/<repo-name>/`
> Deployed via GitHub Actions → GitHub Pages (gh-pages branch)

---

## What It Reports

### Section 1 — Sprint Progress (Feb 11–24, 2026)
- Overall completion % by ticket count and story points
- Burndown chart (remaining points per day, per pod)
- Per-pod progress cards: Accounting · AP · Cards · Megapod · Wallet
- Tickets that are blocked or stale (In Progress >3 days without updates)

### Section 2 — Sprint Planning (Upcoming Sprint)
- Ticket count per pod (bar + line combo chart)
- Story points per engineer — stacked horizontal bar to spot workload imbalance
- Pareto chart of top 25 heaviest tickets ranked by story points
- Initiatives not yet ready for engineering, grouped by readiness stage (from Google Sheet)

---

## Project Structure

```
product-ops-agent/
├── scripts/
│   ├── fetch_linear.py      # Pull sprint data from Linear GraphQL API
│   ├── fetch_sheets.py      # Pull initiative data from Google Sheets API
│   ├── generate_report.py   # Render index.html from fetched data
│   └── run_all.py           # Master runner (calls all three in order)
├── public/
│   ├── index.html           # Generated report (committed to gh-pages)
│   └── data/
│       ├── linear_data.json   # Generated — not committed to main
│       └── sheets_data.json   # Generated — not committed to main
├── .github/
│   └── workflows/
│       └── daily-report.yml   # GitHub Actions: runs daily at 08:00 UTC
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/<your-org>/<repo-name>.git
cd product-ops-agent
pip install -r requirements.txt
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `LINEAR_API_KEY` | Linear personal API key |
| `GOOGLE_API_KEY` | Google Cloud API key (for public sheets) |
| `GOOGLE_SHEET_ID` | The spreadsheet ID from the sheet URL |
| `GOOGLE_SA_JSON` | Path to service account JSON (for private sheets) |

### 3. Grant Google Sheet access

The Google Sheet must be accessible. Choose one:

**Option A (recommended):** Share the sheet with the service account as Viewer:
```
product-ops-agent@product-ops-agent.iam.gserviceaccount.com
```

**Option B:** Set the sheet to "Anyone with the link can view" and use the API key only.

### 4. Run locally

```bash
# Set environment variables
export LINEAR_API_KEY=lin_api_...
export GOOGLE_API_KEY=AIzaSy...
export GOOGLE_SHEET_ID=1jT4_...
export GOOGLE_SA_JSON=./product-ops-agent-4240807ba556.json

python scripts/run_all.py
# Report written to public/index.html
```

Then open `public/index.html` in your browser, or serve it:
```bash
python -m http.server 8765 --directory public
# Visit http://localhost:8765
```

---

## GitHub Actions Deployment

### Configure Secrets

In your GitHub repo → **Settings → Secrets and variables → Actions**, add:

| Secret Name | Value |
|---|---|
| `LINEAR_API_KEY` | Your Linear API key |
| `GOOGLE_API_KEY` | Your Google API key |
| `GOOGLE_SHEET_ID` | The sheet ID |
| `GOOGLE_SA_JSON_B64` | Base64-encoded service account JSON (see below) |

To encode the service account JSON:
```bash
base64 -i product-ops-agent-4240807ba556.json | pbcopy   # macOS
# or
base64 product-ops-agent-4240807ba556.json               # Linux
```

### Enable GitHub Pages

1. Go to repo **Settings → Pages**
2. Set **Source** to `Deploy from a branch`
3. Set **Branch** to `gh-pages`, folder `/` (root)
4. Save — your report will be live at `https://<org>.github.io/<repo>/`

### Schedule

The workflow runs at **08:00 UTC daily** (adjust `cron` in `.github/workflows/daily-report.yml`).
You can also trigger it manually from the **Actions** tab → "Daily Product Ops Report" → "Run workflow".

---

## Pod Configuration

Pods are mapped to Linear team IDs in `scripts/fetch_linear.py`:

| Pod | Linear Team | Key |
|---|---|---|
| Megapod | EPD - Megapod | MP |
| Accounting | EPD - Accounting ERP | ACC |
| AP | Product - Payments | PAY |
| Cards | Product - Cards | CARDS |
| Wallet | Product - Wallet | WAL |

Engineers shared across pods will appear in all relevant pod charts.

---

## Updating Sprint Dates

When a new sprint starts, update `SPRINT_START` and `SPRINT_END` in `scripts/fetch_linear.py`:

```python
SPRINT_START = datetime(2026, 2, 25, tzinfo=timezone.utc)   # next sprint
SPRINT_END   = datetime(2026, 3, 10, tzinfo=timezone.utc)
```

The script auto-detects the matching Linear cycle within a ±2 day window and identifies the following cycle as the "upcoming" sprint.

---

## Tech Stack

- **Python 3.11+** — data fetching and HTML generation (zero external dependencies beyond `cryptography`)
- **Chart.js 4.4** — burndown, bar, pareto, and stacked charts (CDN, no build step)
- **GitHub Actions** — daily scheduled CI/CD
- **GitHub Pages** — static hosting (free, always-on URL)
