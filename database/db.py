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

    conn.close()


def seed_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
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

    conn.commit()
    conn.close()
