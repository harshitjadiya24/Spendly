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
            created_at TEXT DEFAULT (datetime('now')),
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
        (user_id, 450.00, "Food", f"{year}-{month:02d}-03", "Weekly groceries"),
        (user_id, 85.00, "Food", f"{year}-{month:02d}-10", "Lunch with team"),
        (user_id, 200.00, "Transport", f"{year}-{month:02d}-05", "Metro recharge"),
        (user_id, 1500.00, "Bills", f"{year}-{month:02d}-01", "Electricity bill"),
        (user_id, 600.00, "Health", f"{year}-{month:02d}-12", "Pharmacy"),
        (user_id, 350.00, "Entertainment", f"{year}-{month:02d}-08", "Movie tickets"),
        (user_id, 1200.00, "Shopping", f"{year}-{month:02d}-15", "New shoes"),
        (user_id, 100.00, "Other", f"{year}-{month:02d}-07", "ATM charges"),
    ]

    cursor.executemany(
        "INSERT INTO expenses (user_id, amount, category, date, description) VALUES (?, ?, ?, ?, ?)",
        expenses,
    )

    conn.commit()
    conn.close()
