# AGENTS.md — Spendly Expense Tracker

## Dev commands

| Action | Command |
|--------|---------|
| Run dev server | `python app.py` (listens on **port 5001**, debug mode) |
| Install deps | `pip install -r requirements.txt` |
| Run tests | `pytest` |

The virtual environment is at `venv/` (gitignored). Activate with `source venv/bin/activate` before the commands above.

## Project structure

- **`app.py`** — single Flask app instance (no factory). Contains all routes and helper functions.
- **`database/db.py`** — SQLite database init, migrations, seeding. Uses raw SQLite + `sqlite3.Row`.
- **`templates/`** — Jinja2 templates extending `base.html`. App name is **Spendly**.
- **`static/css/style.css`** — single CSS file with CSS custom properties.
- **`static/js/main.js`** — single JS file (video modal only).

## Features implemented

- **Categorization rules engine** (`/rules`): auto-categorize transactions based on description patterns (contains, starts_with, equals, regex). Rules have priority ordering with move up/down.
- **Financial health score** (dashboard): 0–100 score based on savings rate, budget adherence, debt ratio, emergency fund, and spending consistency. SVG gauge visualization.
- **Recurring transaction detection** (dashboard): auto-detects weekly/monthly/quarterly/yearly patterns from past 6 months. Shows upcoming bills.
- **Cash flow forecast** (dashboard): 30-day balance projection using daily spending average + recurring expenses. Sparkline chart.
- **UPI/Bank CSV import** (`/import`): auto-detects column mapping from GPay, PhonePe, bank statements. Smart category assignment by description keywords.
- **Weekly email digest**: auto-sent on login if 7+ days since last digest. Contains week's spend/earn summary, top categories, budget alerts, upcoming bills. Configurable in profile.
- **Receipt OCR** (`/expenses/add`): upload receipt image → OCR via pytesseract → pre-fill amount, date, merchant. Falls back gracefully if pytesseract not installed.
- **Bill splitting** (`/expenses/add`): split an expense equally with people (comma-separated). View and settle outstanding splits on dashboard.

## Route inventory

| Route | Methods | Description |
|-------|---------|-------------|
| `/` | GET | Landing page |
| `/register` | GET, POST | User registration |
| `/login` | GET, POST | Login with digest trigger |
| `/logout` | POST | Logout |
| `/dashboard` | GET | Dashboard with health score, forecast, charts, budgets, loans, investments, recurring bills, settlements |
| `/ledger` | GET | Filterable/paginated transaction table |
| `/budgets` | GET, POST | Budget CRUD |
| `/budgets/<id>/delete` | POST | Delete budget |
| `/loans` | GET | Loan list with progress |
| `/loans/add` | GET, POST | Add loan |
| `/loans/<id>/pay-emi` | POST | Pay EMI |
| `/loans/<id>/edit` | GET, POST | Edit loan |
| `/loans/<id>/delete` | POST | Delete loan |
| `/investments` | GET | Investment dashboard |
| `/investments/add` | GET, POST | Add investment |
| `/investments/<id>/edit` | GET, POST | Edit investment |
| `/investments/<id>/delete` | POST | Delete investment |
| `/investments/<id>/update-value` | POST | Update current value |
| `/expenses/add` | GET, POST | Add expense with rules + split |
| `/expenses/<id>/edit` | GET, POST | Edit expense |
| `/expenses/<id>/delete` | POST | Delete expense |
| `/expenses/add/scan` | POST | Receipt OCR scan |
| `/profile` | GET, POST | Profile edit, password change, photo upload, digest prefs |
| `/export` | GET | CSV export |
| `/rules` | GET, POST | Categorization rules CRUD |
| `/rules/<id>/delete` | POST | Delete rule |
| `/rules/<id>/move` | POST | Reorder rule priority |
| `/import` | GET, POST | CSV import |
| `/splits/settle` | POST | Settle a split |
| `/api/trends` | GET | Spending trend data (JSON) |
| `/api/category-trends` | GET | Category breakdown (JSON) |
| `/api/monthly-category-trends` | GET | Category trends by month (JSON) |
| `/api/forecast` | GET | 30-day cash flow projection (JSON) |

## Testing

- `pytest` + `pytest-flask`
- `conftest.py` disables CSRF + rate limiter in tests
- Tests cover auth, protection, expense CRUD, ledger, profile, export, investments

## Conventions

- No ORM — all DB access via raw SQL with `sqlite3.Row` as row factory.
- No `__init__.py` app factory — the app is the module-level `app` variable in `app.py`.
- No `.flaskenv` or `pyproject.toml` — keep setup minimal.
- No lint, format, or typecheck config — skip those unless asked.
- All monetary values use **₹** (Indian Rupee).
