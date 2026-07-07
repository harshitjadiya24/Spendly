# AGENTS.md — Spendly Expense Tracker

## Dev commands

| Action | Command |
|--------|---------|
| Run dev server | `python app.py` (listens on **port 5001**, debug mode) |
| Install deps | `pip install -r requirements.txt` |
| Run tests | `pytest` (uses pytest-flask; no tests written yet) |

The virtual environment is at `venv/` (gitignored). Activate with `source venv/bin/activate` before the commands above.

## Project structure

- **`app.py`** — single Flask app instance (no factory). Contains all routes.
- **`database/db.py`** — **student stub**; must implement `get_db()`, `init_db()`, `seed_db()`. Uses raw SQLite + `sqlite3.Row`.
- **`templates/`** — Jinja2 templates extending `base.html`. App name is **Spendly**.
- **`static/css/style.css`** — single CSS file with CSS custom properties.
- **`static/js/main.js`** — single JS file (video modal only).

## Route status

Several routes return placeholder strings — they are student exercises:

| Route | Step | Status |
|-------|------|--------|
| `/logout` | Step 3 | Placeholder |
| `/profile` | Step 4 | Placeholder |
| `/expenses/add` | Step 7 | Placeholder |
| `/expenses/<id>/edit` | Step 8 | Placeholder |
| `/expenses/<id>/delete` | Step 9 | Placeholder |

## Testing

- **pytest** + **pytest-flask** are the only test deps.
- No `conftest.py` or test files exist yet.
- When writing tests, do **not** assume any test infrastructure exists; create it from scratch.

## Conventions

- No ORM — all DB access via raw SQL with `sqlite3.Row` as row factory.
- No `__init__.py` app factory — the app is the module-level `app` variable in `app.py`.
- No `.flaskenv` or `pyproject.toml` — keep setup minimal.
- No lint, format, or typecheck config — skip those unless asked.
- All monetary values use **₹** (Indian Rupee).
