import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

DATABASE = "spendly.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            type TEXT NOT NULL DEFAULT 'expense',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            month TEXT NOT NULL,
            UNIQUE(user_id, category, month),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('borrowed', 'lent')),
            name TEXT NOT NULL,
            total_amount REAL NOT NULL,
            interest_rate REAL DEFAULT 0,
            start_date TEXT NOT NULL,
            emi_amount REAL NOT NULL,
            emi_frequency TEXT DEFAULT 'monthly',
            total_emis INTEGER NOT NULL,
            paid_emis INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()

    cursor.execute("PRAGMA table_info(expenses)")
    cols = {row[1] for row in cursor.fetchall()}
    if "type" not in cols:
        cursor.execute("ALTER TABLE expenses ADD COLUMN type TEXT NOT NULL DEFAULT 'expense'")
        conn.commit()

    cursor.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in cursor.fetchall()}
    if "photo" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN photo TEXT DEFAULT NULL")
        conn.commit()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS investments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            name            TEXT NOT NULL,
            category        TEXT NOT NULL,
            investment_type TEXT NOT NULL DEFAULT 'lump_sum',
            invested_amount REAL NOT NULL,
            current_value   REAL NOT NULL,
            units           REAL DEFAULT 0,
            purchase_price  REAL DEFAULT 0,
            sip_amount      REAL DEFAULT 0,
            sip_frequency   TEXT DEFAULT 'monthly',
            sip_start_date  TEXT,
            start_date      TEXT NOT NULL,
            maturity_date   TEXT,
            interest_rate   REAL DEFAULT 0,
            status          TEXT DEFAULT 'active',
            notes           TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()


def seed_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        _seed_loans(conn)
        _seed_investments(conn)
        conn.close()
        return

    password_hash = generate_password_hash("demo123")
    cursor.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Demo User", "demo@spendly.com", password_hash),
    )
    user_id = cursor.lastrowid

    today = datetime.now()
    month = today.month
    year = today.year

    expenses = [
        (user_id, 450.00, "Food", f"{year}-{month:02d}-03", "Weekly groceries", "expense"),
        (user_id, 85.00, "Food", f"{year}-{month:02d}-10", "Lunch with team", "expense"),
        (user_id, 200.00, "Transport", f"{year}-{month:02d}-05", "Metro recharge", "expense"),
        (user_id, 1500.00, "Bills", f"{year}-{month:02d}-01", "Electricity bill", "expense"),
        (user_id, 600.00, "Health", f"{year}-{month:02d}-12", "Pharmacy", "expense"),
        (user_id, 350.00, "Entertainment", f"{year}-{month:02d}-08", "Movie tickets", "expense"),
        (user_id, 1200.00, "Shopping", f"{year}-{month:02d}-15", "New shoes", "expense"),
        (user_id, 100.00, "Other", f"{year}-{month:02d}-07", "ATM charges", "expense"),
        (user_id, 45000.00, "Salary", f"{year}-{month:02d}-01", "Monthly salary", "income"),
    ]

    cursor.executemany(
        "INSERT INTO expenses (user_id, amount, category, date, description, type) VALUES (?, ?, ?, ?, ?, ?)",
        expenses,
    )

    _seed_loans(conn, user_id)
    _seed_investments(conn, user_id)
    conn.commit()
    conn.close()


def _seed_loans(conn, user_id=None):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM loans")
    if cursor.fetchone()[0] > 0:
        return
    year = datetime.now().year
    if user_id is None:
        user = cursor.execute("SELECT id FROM users WHERE email = ?", ("demo@spendly.com",)).fetchone()
        if not user:
            return
        user_id = user["id"]
    loans = [
        (user_id, "borrowed", "Home Loan", 2500000, 8.5, f"{year}-01-15", 25000, "monthly", 120, 15, "active", "SBI home loan"),
        (user_id, "lent", "Rahul Sharma", 50000, 0, f"{year}-03-10", 5000, "monthly", 10, 4, "active", "Personal loan to friend"),
    ]
    cursor.executemany(
        "INSERT INTO loans (user_id, type, name, total_amount, interest_rate, start_date, emi_amount, emi_frequency, total_emis, paid_emis, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        loans,
    )
    conn.commit()


def _seed_investments(conn, user_id=None):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM investments")
    if cursor.fetchone()[0] > 0:
        return
    year = datetime.now().year
    if user_id is None:
        user = cursor.execute("SELECT id FROM users WHERE email = ?", ("demo@spendly.com",)).fetchone()
        if not user:
            return
        user_id = user["id"]
    investments = [
        (user_id, "Index Fund", "mutual_funds", "sip", 120000, 138000, 0, 0, 5000, "monthly", f"{year-2}-06-01", f"{year-2}-06-01", None, 0, "active", "Nifty 50 index fund"),
        (user_id, "Fixed Deposit", "fd", "lump_sum", 500000, 578000, 0, 0, 0, "monthly", None, f"{year-3}-01-01", f"{year}-01-01", 7.5, "active", "SBI 3-year FD"),
    ]
    cursor.executemany(
        "INSERT INTO investments (user_id, name, category, investment_type, invested_amount, current_value, units, purchase_price, sip_amount, sip_frequency, sip_start_date, start_date, maturity_date, interest_rate, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        investments,
    )
    conn.commit()
