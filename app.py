import re
import csv
import os
import sys
import io
import time
import math
import secrets
import logging
import statistics
from io import StringIO
from functools import wraps
from datetime import datetime, date, timedelta
from logging.handlers import TimedRotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database.db import get_db, init_db, seed_db

load_dotenv()

app = Flask(__name__)



_env = os.environ.get("FLASK_ENV", "development")
_is_prod = _env == "production"

app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    if _is_prod:
        sys.exit("FATAL: SECRET_KEY environment variable is required in production.")
    app.secret_key = "spendly-dev-fallback-key"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_is_prod,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    TESTING=_env == "test",
)

# ------------------------------------------------------------------ #
# Logging                                                              #
# ------------------------------------------------------------------ #

os.makedirs("logs", exist_ok=True)
_log_handler = TimedRotatingFileHandler("logs/app.log", when="midnight", backupCount=30)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
app.logger.addHandler(_log_handler)
app.logger.setLevel(logging.INFO)

# ------------------------------------------------------------------ #
# Rate limiter                                                        #
# ------------------------------------------------------------------ #

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=os.environ.get("REDIS_URL", "memory://"),
    default_limits=[],
)

# ------------------------------------------------------------------ #
# Security headers                                                    #
# ------------------------------------------------------------------ #

@app.after_request
def _add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'self'"
    )
    return response


# ------------------------------------------------------------------ #
# Request hooks                                                       #
# ------------------------------------------------------------------ #

@app.before_request
def _before_request():
    if request.path.startswith("/static/"):
        return
    app.logger.info("%s %s [%s]", request.method, request.path, session.get("user_id", "anon"))
    session.permanent = True
    session.modified = True
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


CATEGORIES = ["Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Salary", "Other"]

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ------------------------------------------------------------------ #
# Auth helpers                                                        #
# ------------------------------------------------------------------ #

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in first.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ #
# CSRF helpers                                                        #
# ------------------------------------------------------------------ #

@app.context_processor
def inject_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {"csrf_token": session["csrf_token"]}

def validate_csrf():
    if app.config.get("CSRF_DISABLED"):
        return True
    token = request.form.get("csrf_token")
    if not token or token != session.get("csrf_token"):
        app.logger.warning("CSRF failure route=%s user=%s ip=%s", request.path, session.get("user_id"), request.remote_addr)
        return False
    return True


# ------------------------------------------------------------------ #
# Alert helper                                                        #
# ------------------------------------------------------------------ #

def _check_alerts(user_id, db=None):
    close = db is None
    if db is None:
        db = get_db()
    now = date.today()
    month = f"{now.year}-{now.month:02d}"

    # Budget warnings (70% and 100%)
    budgets = db.execute(
        "SELECT b.*, COALESCE(SUM(e.amount),0) as spent FROM budgets b "
        "LEFT JOIN expenses e ON e.user_id=b.user_id AND e.category=b.category "
        "AND strftime('%Y-%m',e.date)=b.month AND e.type='expense' "
        "WHERE b.user_id=? AND b.month=? GROUP BY b.id",
        (user_id, month),
    ).fetchall()
    for b in budgets:
        if b["amount"] <= 0:
            continue
        pct = b["spent"] / b["amount"]
        existing = db.execute(
            "SELECT id FROM alerts WHERE user_id=? AND type IN ('budget_warning','budget_exceeded') AND data=? AND strftime('%Y-%m',created_at)=?",
            (user_id, str(b["category"]), month),
        ).fetchone()
        if pct >= 1 and not existing:
            db.execute(
                "INSERT INTO alerts (user_id,type,title,message,data) VALUES (?,?,?,?,?)",
                (user_id, "budget_exceeded", f"Budget exceeded: {b['category']}",
                 f"You've spent ₹{b['spent']:.0f} of ₹{b['amount']:.0f} budgeted for {b['category']}.", str(b["category"])),
            )
        elif pct >= 0.7 and pct < 1 and not existing:
            db.execute(
                "INSERT INTO alerts (user_id,type,title,message,data) VALUES (?,?,?,?,?)",
                (user_id, "budget_warning", f"Budget nearly used: {b['category']}",
                 f"You've used {pct*100:.0f}% of your ₹{b['amount']:.0f} budget for {b['category']}.", str(b["category"])),
            )

    # Upcoming recurring bills (within 3 days)
    recurring = db.execute(
        "SELECT * FROM recurring_patterns WHERE user_id=? AND active=1",
        (user_id,),
    ).fetchall()
    for r in recurring:
        if r["next_occurrence"]:
            next_date = datetime.strptime(r["next_occurrence"], "%Y-%m-%d").date()
            days_until = (next_date - now).days
            if 0 <= days_until <= 3:
                existing = db.execute(
                    "SELECT id FROM alerts WHERE user_id=? AND type='bill_due' AND data=?",
                    (user_id, str(r["id"])),
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO alerts (user_id,type,title,message,data) VALUES (?,?,?,?,?)",
                        (user_id, "bill_due", f"Bill due soon: {r['category']}",
                         f"₹{r['amount']:.0f} {r['category']} is due on {r['next_occurrence']}.", str(r["id"])),
                    )

    db.commit()
    if close:
        db.close()

def _get_unread_alerts(user_id):
    db = get_db()
    alerts = db.execute(
        "SELECT * FROM alerts WHERE user_id=? AND read=0 ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    db.close()
    return alerts

def _mark_alerts_read(user_id):
    db = get_db()
    db.execute("UPDATE alerts SET read=1 WHERE user_id=?", (user_id,))
    db.commit()
    db.close()

def _process_budget_rollovers(user_id, month):
    db = get_db()
    budgets = db.execute(
        "SELECT * FROM budgets WHERE user_id=? AND month=? AND rollover=1",
        (user_id, month),
    ).fetchall()
    next_year = int(month.split("-")[0])
    next_month_int = int(month.split("-")[1]) + 1
    if next_month_int > 12:
        next_month_int = 1
        next_year += 1
    next_month = f"{next_year}-{next_month_int:02d}"
    for b in budgets:
        spent = db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE user_id=? AND category=? AND strftime('%Y-%m',date)=? AND type='expense'",
            (user_id, b["category"], month),
        ).fetchone()[0]
        leftover = max(0, b["amount"] - spent)
        if leftover > 0:
            existing = db.execute(
                "SELECT id FROM budgets WHERE user_id=? AND category=? AND month=?",
                (user_id, b["category"], next_month),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE budgets SET amount = amount + ? WHERE id=?",
                    (leftover, existing["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO budgets (user_id,category,amount,month,rollover) VALUES (?,?,?,?,1)",
                    (user_id, b["category"], leftover, next_month),
                )
    db.commit()
    db.close()

# ------------------------------------------------------------------ #
# Rule matching helper                                                #
# ------------------------------------------------------------------ #

def _match_rule(description, rule):
    pattern = rule["match_pattern"]
    if rule["match_type"] == "contains":
        return pattern.lower() in description.lower()
    elif rule["match_type"] == "starts_with":
        return description.lower().startswith(pattern.lower())
    elif rule["match_type"] == "equals":
        return description.lower() == pattern.lower()
    elif rule["match_type"] == "regex":
        try:
            return re.search(pattern, description, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _apply_rules(user_id, description, category, exp_type, db=None):
    close = db is None
    if db is None:
        db = get_db()
    rules = db.execute(
        "SELECT * FROM categorization_rules WHERE user_id = ? ORDER BY priority ASC",
        (user_id,),
    ).fetchall()
    if close:
        db.close()
    for rule in rules:
        if _match_rule(description, rule):
            return rule["assign_category"], rule["assign_type"]
    return category, exp_type


# ------------------------------------------------------------------ #
# Health score helper                                                 #
# ------------------------------------------------------------------ #

def _health_score(user_id):
    db = get_db()
    now = date.today()
    month = f"{now.year}-{now.month:02d}"
    breakdown = {}
    score = 0

    # 1. Savings rate (30 pts)
    total_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'income' AND strftime('%Y-%m', date) = ?",
        (user_id, month),
    ).fetchone()[0]
    total_expense = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ?",
        (user_id, month),
    ).fetchone()[0]
    if total_income > 0:
        savings_rate = (total_income - total_expense) / total_income
        savings_pts = min(30, max(0, round(savings_rate * 30)))
    else:
        savings_pts = 0
    breakdown["savings_rate"] = {"score": savings_pts, "max": 30, "value": f"{max(0, round(savings_rate*100, 1)) if total_income > 0 else 0}%" if total_income > 0 else "N/A"}
    score += savings_pts

    # 2. Budget adherence (25 pts)
    budgets = db.execute(
        "SELECT b.amount, COALESCE(SUM(e.amount), 0) as spent FROM budgets b LEFT JOIN expenses e "
        "ON e.user_id = b.user_id AND e.category = b.category AND strftime('%Y-%m', e.date) = b.month AND e.type = 'expense' "
        "WHERE b.user_id = ? AND b.month = ? GROUP BY b.id",
        (user_id, month),
    ).fetchall()
    if budgets:
        over_count = sum(1 for b in budgets if b["spent"] > b["amount"])
        budget_pts = max(0, round((1 - over_count / len(budgets)) * 25))
    else:
        budget_pts = 12
    breakdown["budget_adherence"] = {"score": budget_pts, "max": 25, "value": f"{len(budgets) - over_count if budgets else 0}/{len(budgets) if budgets else 0} on track"}
    score += budget_pts

    # 3. Debt ratio (20 pts)
    total_emi_pay = db.execute(
        "SELECT COALESCE(SUM(emi_amount), 0) FROM loans WHERE user_id = ? AND status = 'active' AND type = 'borrowed'",
        (user_id,),
    ).fetchone()[0]
    if total_income > 0:
        debt_ratio = total_emi_pay / total_income if total_income > 0 else 0
        debt_pts = max(0, round((1 - min(debt_ratio, 1)) * 20))
    else:
        debt_pts = 10
    breakdown["debt_ratio"] = {"score": debt_pts, "max": 20, "value": f"₹{total_emi_pay:,.0f} EMI / ₹{total_income:,.0f} income"}
    score += debt_pts

    # 4. Emergency fund (15 pts)
    total_current = db.execute(
        "SELECT COALESCE(SUM(current_value), 0) FROM investments WHERE user_id = ? AND status = 'active'",
        (user_id,),
    ).fetchone()[0]
    monthly_spend = db.execute(
        "SELECT COALESCE(AVG(monthly), 0) FROM (SELECT strftime('%Y-%m', date) as m, SUM(amount) as monthly "
        "FROM expenses WHERE user_id = ? AND type = 'expense' AND date >= date('now', '-3 months') GROUP BY m)",
        (user_id,),
    ).fetchone()[0]
    if monthly_spend > 0:
        months_covered = total_current / monthly_spend
        em_pts = min(15, round(months_covered * 3))
    else:
        em_pts = 7
    breakdown["emergency_fund"] = {"score": em_pts, "max": 15, "value": f"{months_covered:.1f}mo covered" if monthly_spend > 0 else "N/A"}
    score += em_pts

    # 5. Consistency (10 pts)
    monthly_totals = db.execute(
        "SELECT strftime('%Y-%m', date) as m, SUM(amount) as monthly "
        "FROM expenses WHERE user_id = ? AND type = 'expense' AND date >= date('now', '-6 months') GROUP BY m",
        (user_id,),
    ).fetchall()
    if len(monthly_totals) >= 3:
        vals = [r["monthly"] for r in monthly_totals]
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals) if len(vals) > 1 else 0
        cv = stdev / mean if mean > 0 else 1
        consist_pts = max(0, round((1 - min(cv, 1)) * 10))
    else:
        consist_pts = 5
    breakdown["consistency"] = {"score": consist_pts, "max": 10, "value": f"{len(monthly_totals)} months tracked"}
    score += consist_pts

    db.close()
    return {"score": min(100, score), "breakdown": breakdown}


# ------------------------------------------------------------------ #
# Recurring detection helper                                          #
# ------------------------------------------------------------------ #

def _detect_recurring(user_id):
    db = get_db()
    now = date.today()

    groups = db.execute(
        "SELECT category, ROUND(amount, 0) as amt_round, description, "
        "GROUP_CONCAT(date) as dates FROM expenses "
        "WHERE user_id = ? AND type = 'expense' AND date >= date('now', '-6 months') "
        "GROUP BY category, amt_round, description HAVING COUNT(*) >= 2",
        (user_id,),
    ).fetchall()

    patterns = []
    for g in groups:
        date_strs = g["dates"].split(",")
        dates_sorted = sorted(set(date_strs))
        if len(dates_sorted) < 2:
            continue
        deltas = []
        for i in range(1, len(dates_sorted)):
            try:
                d1 = datetime.strptime(dates_sorted[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(dates_sorted[i], "%Y-%m-%d")
                deltas.append((d2 - d1).days)
            except ValueError:
                continue
        if not deltas:
            continue
        avg_gap = statistics.mean(deltas)
        if 25 <= avg_gap <= 35:
            freq = "monthly"
            next_date = now + timedelta(days=avg_gap)
        elif 6 <= avg_gap <= 10:
            freq = "weekly"
            next_date = now + timedelta(days=7)
        elif 80 <= avg_gap <= 100:
            freq = "quarterly"
            next_date = now + timedelta(days=avg_gap)
        elif 350 <= avg_gap <= 380:
            freq = "yearly"
            next_date = now + timedelta(days=avg_gap)
        else:
            continue

        patterns.append({
            "category": g["category"],
            "amount": g["amt_round"],
            "description": g["description"] or "",
            "frequency": freq,
            "next_date": next_date.strftime("%Y-%m-%d"),
        })

    db.close()
    return patterns


# ------------------------------------------------------------------ #
# Email digest helper                                                 #
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    ctx = {}
    if "user_id" in session:
        db = get_db()
        try:
            now = date.today()
            month = f"{now.year}-{now.month:02d}"
            categories = db.execute(
                "SELECT category, COALESCE(SUM(amount), 0) as total FROM expenses "
                "WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ? "
                "GROUP BY category ORDER BY total DESC",
                (session["user_id"], month),
            ).fetchall()
            total_spent = db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM expenses "
                "WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ?",
                (session["user_id"], month),
            ).fetchone()[0]
            ctx["live_categories"] = categories
            ctx["live_total"] = round(total_spent)
            ctx["live_month"] = month
        finally:
            db.close()
    return render_template("landing.html", **ctx)


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def register():
    if request.method == "POST":
        if not validate_csrf():
            return render_template("register.html", error="Session expired. Please try again.")

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None
        if not name:
            error = "Name is required."
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            error = "Invalid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif not re.search(r"[A-Z]", password):
            error = "Password must contain an uppercase letter."
        elif not re.search(r"[a-z]", password):
            error = "Password must contain a lowercase letter."
        elif not re.search(r"\d", password):
            error = "Password must contain a digit."
        elif not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            error = "Password must contain a special character."
        elif password != confirm:
            error = "Passwords do not match."

        if error is None:
            db = get_db()
            existing = db.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                error = "An account with this email already exists."
            else:
                password_hash = generate_password_hash(password)
                db.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, password_hash),
                )
                db.commit()
                db.close()
                app.logger.info("Register success email=%s ip=%s", email, request.remote_addr)
                flash("Account created successfully! Please sign in.", "success")
                return redirect(url_for("login"))
            db.close()

        return render_template("register.html", error=error)

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        if not validate_csrf():
            app.logger.warning("CSRF failure on login ip=%s", request.remote_addr)
            return render_template("login.html", error="Session expired. Please try again.")

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        error = None
        if not email or not password:
            error = "Email and password are required."
        else:
            db = get_db()
            user = db.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            db.close()

            if not user or not check_password_hash(user["password_hash"], password):
                app.logger.warning("Login failed email=%s ip=%s", email, request.remote_addr)
                error = "Invalid email or password."
            else:
                session.permanent = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["csrf_token"] = secrets.token_hex(32)

                app.logger.info("Login success user=%s ip=%s", user["id"], request.remote_addr)
                flash(f"Welcome back, {user['name']}!", "success")
                return redirect(url_for("landing"))

        return render_template("login.html", error=error)

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("landing"))
    app.logger.info("Logout user=%s ip=%s", session.get("user_id"), request.remote_addr)
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("landing"))


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ------------------------------------------------------------------ #
# Dashboard                                                           #
# ------------------------------------------------------------------ #

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    now = date.today()
    month = request.args.get("month", f"{now.year}-{now.month:02d}")

    expenses = db.execute(
        "SELECT * FROM expenses WHERE user_id = ? AND strftime('%Y-%m', date) = ? ORDER BY date DESC",
        (session["user_id"], month),
    ).fetchall()
    total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'expense' AND strftime('%Y-%m', date) = ?",
        (session["user_id"], month),
    ).fetchone()[0]
    income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'income' AND strftime('%Y-%m', date) = ?",
        (session["user_id"], month),
    ).fetchone()[0]
    avail_months = db.execute(
        "SELECT DISTINCT strftime('%Y-%m', date) as m FROM expenses WHERE user_id = ? ORDER BY m DESC",
        (session["user_id"],),
    ).fetchall()

    budgets = db.execute(
        "SELECT b.*, COALESCE(SUM(e.amount), 0) as spent "
        "FROM budgets b LEFT JOIN expenses e ON e.user_id = b.user_id "
        "AND e.category = b.category AND strftime('%Y-%m', e.date) = b.month AND e.type = 'expense' "
        "WHERE b.user_id = ? AND b.month = ? GROUP BY b.id",
        (session["user_id"], month),
    ).fetchall()

    loans_active = db.execute(
        "SELECT type, emi_amount, emi_frequency, total_emis, paid_emis FROM loans WHERE user_id = ? AND status = 'active'",
        (session["user_id"],),
    ).fetchall()
    loan_outstanding = 0
    loan_monthly_pay = 0
    loan_monthly_receive = 0
    for l in loans_active:
        remaining = l["total_emis"] - l["paid_emis"]
        loan_outstanding += remaining * l["emi_amount"]
        monthly = l["emi_amount"] * (52 / 12 if l["emi_frequency"] == "weekly" else 1)
        if l["type"] == "borrowed":
            loan_monthly_pay += monthly
        else:
            loan_monthly_receive += monthly

    inv_active = db.execute(
        "SELECT invested_amount, current_value FROM investments WHERE user_id = ? AND status = 'active'",
        (session["user_id"],),
    ).fetchall()
    inv_total_invested = sum(r["invested_amount"] for r in inv_active)
    inv_total_current = sum(r["current_value"] for r in inv_active)
    inv_pnl = inv_total_current - inv_total_invested
    inv_returns_pct = round((inv_pnl / inv_total_invested) * 100, 2) if inv_total_invested > 0 else 0

    health = _health_score(session["user_id"])
    recurring = _detect_recurring(session["user_id"])

    # Settlements summary
    settlements = db.execute(
        "SELECT sp.name, SUM(CASE WHEN sp.settled = 0 THEN sp.amount ELSE 0 END) as owing "
        "FROM splits s JOIN split_participants sp ON sp.split_id = s.id "
        "JOIN expenses e ON e.id = s.expense_id "
        "WHERE e.user_id = ? AND s.settled = 0 GROUP BY sp.name HAVING owing > 0",
        (session["user_id"],),
    ).fetchall()

    avail_months = [r["m"] for r in avail_months]
    if not avail_months:
        avail_months = [f"{date.today().year}-{date.today().month:02d}"]
    accounts = db.execute(
        "SELECT * FROM accounts WHERE user_id=? ORDER BY is_default DESC, created_at ASC",
        (session["user_id"],),
    ).fetchall()
    account_balance = sum(a["balance"] for a in accounts if a["type"] != "credit")
    account_credit = sum(a["balance"] for a in accounts if a["type"] == "credit")
    net_worth = account_balance - account_credit

    goals = db.execute(
        "SELECT * FROM savings_goals WHERE user_id=? AND status='active' ORDER BY deadline ASC",
        (session["user_id"],),
    ).fetchall()

    _check_alerts(session["user_id"], db)
    unread_alerts = db.execute(
        "SELECT * FROM alerts WHERE user_id=? AND read=0 ORDER BY created_at DESC LIMIT 5",
        (session["user_id"],),
    ).fetchall()
    unread_count = db.execute(
        "SELECT COUNT(*) FROM alerts WHERE user_id=? AND read=0",
        (session["user_id"],),
    ).fetchone()[0]

    db.close()
    return render_template("dashboard.html", expenses=expenses, total=total, income=income, month=month, avail_months=avail_months, budgets=budgets, loan_outstanding=loan_outstanding, loan_monthly_pay=round(loan_monthly_pay), loan_monthly_receive=round(loan_monthly_receive), loan_count=len(loans_active), inv_total_invested=inv_total_invested, inv_total_current=inv_total_current, inv_pnl=inv_pnl, inv_returns_pct=inv_returns_pct, inv_count=len(inv_active), health=health, recurring=recurring, settlements=settlements, accounts=accounts, account_balance=account_balance, account_credit=account_credit, net_worth=net_worth, goals=goals, unread_alerts=unread_alerts, unread_count=unread_count)


# ------------------------------------------------------------------ #
# Budgets                                                             #
# ------------------------------------------------------------------ #

@app.route("/budgets", methods=["GET", "POST"])
@login_required
def budgets():
    db = get_db()
    now = date.today()
    month = request.args.get("month", f"{now.year}-{now.month:02d}")

    if request.method == "POST":
        if not validate_csrf():
            flash("Session expired. Please try again.", "error")
            db.close()
            return redirect(url_for("budgets", month=month))

        category = request.form.get("category", "").strip()
        amount = request.form.get("amount", "").strip()
        rollover = 1 if request.form.get("rollover") else 0
        allowed_cats = [c for c in CATEGORIES if c != "Salary"]

        if category not in allowed_cats:
            flash("Invalid category.", "error")
        elif category and amount and re.match(r"^\d+(\.\d{1,2})?$", amount) and float(amount) > 0:
            db.execute(
                "INSERT INTO budgets (user_id, category, amount, month, rollover) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, category, month) DO UPDATE SET amount = ?, rollover = ?",
                (session["user_id"], category, float(amount), month, rollover, float(amount), rollover),
            )
            db.commit()
            flash(f"Budget set for {category}.", "success")
        else:
            flash("Invalid budget entry. Amount must be greater than 0.", "error")

        db.close()
        return redirect(url_for("budgets", month=month))

    budgets = db.execute(
        "SELECT b.*, COALESCE(SUM(e.amount), 0) as spent "
        "FROM budgets b LEFT JOIN expenses e ON e.user_id = b.user_id "
        "AND e.category = b.category AND strftime('%Y-%m', e.date) = b.month AND e.type = 'expense' "
        "WHERE b.user_id = ? AND b.month = ? GROUP BY b.id ORDER BY b.category",
        (session["user_id"], month),
    ).fetchall()

    avail_months = db.execute(
        "SELECT DISTINCT strftime('%Y-%m', date) as m FROM expenses WHERE user_id = ? ORDER BY m DESC",
        (session["user_id"],),
    ).fetchall()
    avail_months = [r["m"] for r in avail_months]
    if not avail_months:
        avail_months = [f"{date.today().year}-{date.today().month:02d}"]

    db.close()
    return render_template("budgets.html", budgets=budgets, month=month, avail_months=avail_months, categories=[c for c in CATEGORIES if c != "Salary"])


@app.route("/budgets/<int:id>/delete", methods=["POST"])
@login_required
def delete_budget(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("budgets", month=request.args.get("month", "")))
    db = get_db()
    db.execute("DELETE FROM budgets WHERE id = ? AND user_id = ?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Budget removed.", "success")
    return redirect(url_for("budgets"))


# ------------------------------------------------------------------ #
# Loans                                                                #
# ------------------------------------------------------------------ #

def _next_emi_date(start_date, paid_emis, freq="monthly"):
    try:
        parts = start_date.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return start_date
    if freq == "weekly":
        d = datetime(year, month, day) + timedelta(weeks=paid_emis)
        return d.strftime("%Y-%m-%d")
    total_months = year * 12 + month - 1 + paid_emis
    year = total_months // 12
    month = total_months % 12 + 1
    max_day = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
               31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    day = min(day, max_day)
    return f"{year:04d}-{month:02d}-{day:02d}"

def _loan_remaining(loan):
    return loan["total_emis"] - loan["paid_emis"]


@app.route("/loans")
@login_required
def loans():
    db = get_db()
    all_loans = db.execute(
        "SELECT * FROM loans WHERE user_id = ? ORDER BY created_at DESC",
        (session["user_id"],),
    ).fetchall()
    db.close()

    enriched = []
    for l in all_loans:
        remaining = _loan_remaining(l)
        next_date = _next_emi_date(l["start_date"], l["paid_emis"], l["emi_frequency"])
        enriched.append({
            "id": l["id"],
            "type": l["type"],
            "name": l["name"],
            "total_amount": l["total_amount"],
            "interest_rate": l["interest_rate"],
            "start_date": l["start_date"],
            "emi_amount": l["emi_amount"],
            "total_emis": l["total_emis"],
            "paid_emis": l["paid_emis"],
            "emi_frequency": l["emi_frequency"],
            "remaining_emis": remaining,
            "outstanding": remaining * l["emi_amount"],
            "status": l["status"],
            "notes": l["notes"],
            "next_emi_date": next_date,
            "progress": round(l["paid_emis"] / l["total_emis"] * 100) if l["total_emis"] > 0 else 0,
        })

    total_borrowed = sum(l["outstanding"] for l in enriched if l["type"] == "borrowed" and l["status"] == "active")
    total_lent = sum(l["outstanding"] for l in enriched if l["type"] == "lent" and l["status"] == "active")
    monthly_emi_pay = round(sum(
        l["emi_amount"] * (52 / 12 if l["emi_frequency"] == "weekly" else 1)
        for l in enriched if l["status"] == "active" and l["type"] == "borrowed"
    ))
    monthly_emi_receive = round(sum(
        l["emi_amount"] * (52 / 12 if l["emi_frequency"] == "weekly" else 1)
        for l in enriched if l["status"] == "active" and l["type"] == "lent"
    ))

    return render_template(
        "loans.html",
        loans=enriched,
        total_borrowed=total_borrowed,
        total_lent=total_lent,
        monthly_emi_pay=monthly_emi_pay,
        monthly_emi_receive=monthly_emi_receive,
    )


@app.route("/loans/add", methods=["GET", "POST"])
@login_required
def add_loan():
    if request.method == "POST":
        if not validate_csrf():
            return render_template("add_loan.html", error="Session expired. Please try again.")

        loan_type = request.form.get("type")
        name = request.form.get("name", "").strip()
        total_amount = request.form.get("total_amount", "").strip()
        interest_rate = request.form.get("interest_rate", "0").strip()
        start_date = request.form.get("start_date", "").strip()
        emi_amount = request.form.get("emi_amount", "").strip()
        total_emis = request.form.get("total_emis", "").strip()
        emi_frequency = request.form.get("emi_frequency", "monthly")
        notes = request.form.get("notes", "").strip()

        error = None
        if not name:
            error = "Name is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", total_amount) or float(total_amount) <= 0:
            error = "Invalid total amount."
        elif not re.match(r"^\d+(\.\d{1,2})?$", emi_amount) or float(emi_amount) <= 0:
            error = "Invalid EMI amount."
        elif not re.match(r"^\d+$", total_emis) or int(total_emis) <= 0:
            error = "Invalid number of EMIs."
        elif not start_date:
            error = "Start date is required."
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date):
            error = "Invalid start date format."
        elif interest_rate and not re.match(r"^\d+(\.\d{1,2})?$", interest_rate):
            error = "Invalid interest rate."

        if error:
            return render_template("add_loan.html", error=error)

        db = get_db()
        db.execute(
            "INSERT INTO loans (user_id, type, name, total_amount, interest_rate, start_date, emi_amount, emi_frequency, total_emis, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session["user_id"], loan_type, name, float(total_amount), float(interest_rate), start_date, float(emi_amount), emi_frequency, int(total_emis), notes),
        )
        db.commit()
        db.close()
        flash("Loan added successfully.", "success")
        return redirect(url_for("loans"))

    return render_template("add_loan.html")


@app.route("/loans/<int:id>/pay-emi", methods=["POST"])
@login_required
def pay_emi(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("loans"))
    db = get_db()
    loan = db.execute(
        "SELECT * FROM loans WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()

    if not loan:
        db.close()
        flash("Loan not found.", "error")
        return redirect(url_for("loans"))

    if loan["paid_emis"] >= loan["total_emis"]:
        db.close()
        flash("All EMIs have been paid.", "error")
        return redirect(url_for("loans"))

    today = date.today().isoformat()
    emi_num = loan["paid_emis"] + 1
    description = f"EMI #{emi_num} — {loan['name']}"

    if loan["type"] == "borrowed":
        cat = "Loan Repayment"
        exp_type = "expense"
    else:
        cat = "Loan Received"
        exp_type = "income"

    db.execute(
        "INSERT INTO expenses (user_id, amount, category, date, description, type) VALUES (?, ?, ?, ?, ?, ?)",
        (session["user_id"], loan["emi_amount"], cat, today, description, exp_type),
    )
    db.execute(
        "UPDATE loans SET paid_emis = paid_emis + 1 WHERE id = ?",
        (id,),
    )
    db.commit()
    db.close()

    flash(f"EMI #{emi_num} paid for {loan['name']}.", "success")
    return redirect(url_for("loans"))


@app.route("/loans/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_loan(id):
    db = get_db()
    loan = db.execute(
        "SELECT * FROM loans WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()

    if not loan:
        db.close()
        flash("Loan not found.", "error")
        return redirect(url_for("loans"))

    if request.method == "POST":
        if not validate_csrf():
            db.close()
            return render_template("add_loan.html", error="Session expired. Please try again.", loan=loan, edit=True)

        name = request.form.get("name", "").strip()
        total_amount = request.form.get("total_amount", "").strip()
        interest_rate = request.form.get("interest_rate", "0").strip()
        emi_amount = request.form.get("emi_amount", "").strip()
        total_emis = request.form.get("total_emis", "").strip()
        emi_frequency = request.form.get("emi_frequency", "monthly")
        paid_emis = request.form.get("paid_emis", "0").strip()
        status = request.form.get("status", "active")
        notes = request.form.get("notes", "").strip()

        error = None
        if not name:
            error = "Name is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", total_amount) or float(total_amount) <= 0:
            error = "Invalid total amount."
        elif not re.match(r"^\d+(\.\d{1,2})?$", emi_amount) or float(emi_amount) <= 0:
            error = "Invalid EMI amount."
        elif not re.match(r"^\d+$", total_emis) or int(total_emis) <= 0:
            error = "Invalid total EMIs."
        elif not re.match(r"^\d+$", paid_emis) or int(paid_emis) < 0 or int(paid_emis) > int(total_emis):
            error = "Invalid paid EMIs count."

        if error:
            db.close()
            return render_template("add_loan.html", error=error, loan=loan, edit=True)

        db.execute(
            "UPDATE loans SET name=?, total_amount=?, interest_rate=?, emi_amount=?, emi_frequency=?, total_emis=?, paid_emis=?, status=?, notes=? WHERE id=? AND user_id=?",
            (name, float(total_amount), float(interest_rate), float(emi_amount), emi_frequency, int(total_emis), int(paid_emis), status, notes, id, session["user_id"]),
        )
        db.commit()
        db.close()
        flash("Loan updated.", "success")
        return redirect(url_for("loans"))

    db.close()
    return render_template("add_loan.html", loan=loan, edit=True)


@app.route("/loans/<int:id>/delete", methods=["POST"])
@login_required
def delete_loan(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("loans"))
    db = get_db()
    db.execute("DELETE FROM loans WHERE id = ? AND user_id = ?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Loan deleted.", "success")
    return redirect(url_for("loans"))


# ------------------------------------------------------------------ #
# Investments                                                         #
# ------------------------------------------------------------------ #

INVESTMENT_CATEGORIES = ["stocks", "mutual_funds", "fd", "real_estate", "crypto", "gold", "other"]


# ------------------------------------------------------------------ #
# Analytics coming soon                                                #
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# Accounts / Wallets                                                   #
# ------------------------------------------------------------------ #

@app.route("/accounts")
@login_required
def accounts():
    db = get_db()
    accounts = db.execute(
        "SELECT * FROM accounts WHERE user_id=? ORDER BY is_default DESC, created_at ASC",
        (session["user_id"],),
    ).fetchall()
    total_balance = sum(a["balance"] for a in accounts if a["type"] != "credit")
    total_credit = sum(a["balance"] for a in accounts if a["type"] == "credit")
    net_worth = total_balance - total_credit
    db.close()
    return render_template("accounts.html", accounts=accounts, total_balance=total_balance, total_credit=total_credit, net_worth=net_worth)

@app.route("/accounts/add", methods=["POST"])
@login_required
def add_account():
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("accounts"))
    name = request.form.get("name", "").strip()
    acc_type = request.form.get("type", "checking")
    currency = request.form.get("currency", "INR").strip()
    balance = request.form.get("balance", "0").strip()
    icon = request.form.get("icon", "🏦").strip()
    if not name:
        flash("Account name is required.", "error")
        return redirect(url_for("accounts"))
    try:
        balance = float(balance)
    except ValueError:
        balance = 0
    db = get_db()
    existing_default = db.execute(
        "SELECT id FROM accounts WHERE user_id=? AND is_default=1", (session["user_id"],),
    ).fetchone()
    is_default = 1 if not existing_default else 0
    db.execute(
        "INSERT INTO accounts (user_id, name, type, currency, balance, icon, is_default) VALUES (?,?,?,?,?,?,?)",
        (session["user_id"], name, acc_type, currency, balance, icon, is_default),
    )
    db.commit()
    db.close()
    flash(f"Account '{name}' added.", "success")
    return redirect(url_for("accounts"))

@app.route("/accounts/<int:id>/edit", methods=["POST"])
@login_required
def edit_account(id):
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("accounts"))
    db = get_db()
    acc = db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?", (id, session["user_id"])).fetchone()
    if not acc:
        db.close()
        flash("Account not found.", "error")
        return redirect(url_for("accounts"))
    name = request.form.get("name", "").strip()
    balance = request.form.get("balance", "0").strip()
    icon = request.form.get("icon", "🏦").strip()
    if name:
        db.execute("UPDATE accounts SET name=?, balance=?, icon=? WHERE id=?", (name, float(balance) if balance else acc["balance"], icon, id))
        db.commit()
    db.close()
    flash("Account updated.", "success")
    return redirect(url_for("accounts"))

@app.route("/accounts/<int:id>/delete", methods=["POST"])
@login_required
def delete_account(id):
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("accounts"))
    db = get_db()
    db.execute("DELETE FROM accounts WHERE id=? AND user_id=?", (id, session["user_id"]))
    db.execute("UPDATE expenses SET account_id=NULL WHERE account_id=?", (id,))
    db.commit()
    db.close()
    flash("Account removed.", "success")
    return redirect(url_for("accounts"))


# ------------------------------------------------------------------ #
# Savings Goals                                                        #
# ------------------------------------------------------------------ #

@app.route("/goals")
@login_required
def goals():
    db = get_db()
    goals = db.execute(
        "SELECT * FROM savings_goals WHERE user_id=? ORDER BY status ASC, deadline ASC",
        (session["user_id"],),
    ).fetchall()
    enriched = []
    for g in goals:
        pct = round(g["current_amount"] / g["target_amount"] * 100, 1) if g["target_amount"] > 0 else 0
        enriched.append({**g, "progress": min(pct, 100)})
    db.close()
    return render_template("goals.html", goals=enriched)

@app.route("/goals/add", methods=["POST"])
@login_required
def add_goal():
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("goals"))
    name = request.form.get("name", "").strip()
    target = request.form.get("target_amount", "").strip()
    deadline = request.form.get("deadline", "").strip()
    icon = request.form.get("icon", "🎯").strip()
    if not name or not target:
        flash("Name and target amount are required.", "error")
        return redirect(url_for("goals"))
    try:
        target = float(target)
    except ValueError:
        flash("Invalid target amount.", "error")
        return redirect(url_for("goals"))
    db = get_db()
    db.execute(
        "INSERT INTO savings_goals (user_id, name, target_amount, deadline, icon) VALUES (?,?,?,?,?)",
        (session["user_id"], name, target, deadline or None, icon),
    )
    db.commit()
    db.close()
    flash(f"Goal '{name}' created.", "success")
    return redirect(url_for("goals"))

@app.route("/goals/<int:id>/contribute", methods=["POST"])
@login_required
def contribute_goal(id):
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("goals"))
    amount = request.form.get("amount", "").strip()
    try:
        amount = float(amount)
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("goals"))
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("goals"))
    db = get_db()
    goal = db.execute("SELECT * FROM savings_goals WHERE id=? AND user_id=?", (id, session["user_id"])).fetchone()
    if not goal:
        db.close()
        flash("Goal not found.", "error")
        return redirect(url_for("goals"))
    new_current = min(goal["current_amount"] + amount, goal["target_amount"])
    db.execute("UPDATE savings_goals SET current_amount=? WHERE id=?", (new_current, id))
    if new_current >= goal["target_amount"] and goal["status"] == "active":
        db.execute("UPDATE savings_goals SET status='completed' WHERE id=?", (id,))
        db.execute(
            "INSERT INTO alerts (user_id,type,title,message,data) VALUES (?,?,?,?,?)",
            (session["user_id"], "goal_milestone", f"Goal completed: {goal['name']}",
             f"Congratulations! You've reached your target of ₹{goal['target_amount']:.0f} for {goal['name']}.", str(id)),
        )
    db.commit()
    db.close()
    flash(f"₹{amount:.0f} added to '{goal['name']}'.", "success")
    return redirect(url_for("goals"))

@app.route("/goals/<int:id>/delete", methods=["POST"])
@login_required
def delete_goal(id):
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("goals"))
    db = get_db()
    db.execute("DELETE FROM savings_goals WHERE id=? AND user_id=?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Goal removed.", "success")
    return redirect(url_for("goals"))


# ------------------------------------------------------------------ #
# Alerts                                                               #
# ------------------------------------------------------------------ #

@app.route("/alerts/read", methods=["POST"])
@login_required
def mark_alerts_read():
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("dashboard"))
    _mark_alerts_read(session["user_id"])
    return redirect(request.referrer or url_for("dashboard"))



# ------------------------------------------------------------------ #
# Investments                                                          #
# ------------------------------------------------------------------ #

@app.route("/investments")
@login_required
def investments():
    db = get_db()
    all_inv = db.execute(
        "SELECT * FROM investments WHERE user_id = ? ORDER BY created_at DESC",
        (session["user_id"],),
    ).fetchall()
    db.close()

    enriched = []
    for inv in all_inv:
        pnl = inv["current_value"] - inv["invested_amount"]
        returns_pct = round((pnl / inv["invested_amount"]) * 100, 2) if inv["invested_amount"] > 0 else 0
        enriched.append({
            "id": inv["id"],
            "name": inv["name"],
            "category": inv["category"],
            "investment_type": inv["investment_type"],
            "invested_amount": inv["invested_amount"],
            "current_value": inv["current_value"],
            "units": inv["units"],
            "purchase_price": inv["purchase_price"],
            "sip_amount": inv["sip_amount"],
            "sip_frequency": inv["sip_frequency"],
            "start_date": inv["start_date"],
            "maturity_date": inv["maturity_date"],
            "interest_rate": inv["interest_rate"],
            "status": inv["status"],
            "notes": inv["notes"],
            "pnl": round(pnl),
            "returns_pct": returns_pct,
        })

    total_invested = sum(inv["invested_amount"] for inv in enriched)
    total_current = sum(inv["current_value"] for inv in enriched)
    total_pnl = total_current - total_invested
    total_returns_pct = round((total_pnl / total_invested) * 100, 2) if total_invested > 0 else 0

    return render_template(
        "investments.html",
        investments=enriched,
        total_invested=total_invested,
        total_current=total_current,
        total_pnl=total_pnl,
        total_returns_pct=total_returns_pct,
    )


@app.route("/investments/add", methods=["GET", "POST"])
@login_required
def add_investment():
    if request.method == "POST":
        if not validate_csrf():
            return render_template("add_investment.html", error="Session expired. Please try again.")

        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        inv_type = request.form.get("investment_type", "lump_sum")
        invested = request.form.get("invested_amount", "").strip()
        current = request.form.get("current_value", "").strip()
        units = request.form.get("units", "0").strip()
        purchase_price = request.form.get("purchase_price", "0").strip()
        sip_amount = request.form.get("sip_amount", "0").strip()
        sip_frequency = request.form.get("sip_frequency", "monthly")
        sip_start_date = request.form.get("sip_start_date", "").strip()
        start_date = request.form.get("start_date", "").strip()
        maturity_date = request.form.get("maturity_date", "").strip()
        interest_rate = request.form.get("interest_rate", "0").strip()
        notes = request.form.get("notes", "").strip()

        error = None
        if not name:
            error = "Name is required."
        elif category not in INVESTMENT_CATEGORIES:
            error = "Invalid category."
        elif not re.match(r"^\d+(\.\d{1,2})?$", invested) or float(invested) <= 0:
            error = "Invalid invested amount."
        elif not re.match(r"^\d+(\.\d{1,2})?$", current) or float(current) < 0:
            error = "Invalid current value."
        elif units and not re.match(r"^\d+(\.\d{1,6})?$", units):
            error = "Invalid units."
        elif purchase_price and not re.match(r"^\d+(\.\d{1,2})?$", purchase_price):
            error = "Invalid purchase price."
        elif sip_amount and not re.match(r"^\d+(\.\d{1,2})?$", sip_amount):
            error = "Invalid SIP amount."
        elif interest_rate and not re.match(r"^\d+(\.\d{1,2})?$", interest_rate):
            error = "Invalid interest rate."
        elif not start_date:
            error = "Start date is required."

        if error:
            return render_template("add_investment.html", error=error, categories=INVESTMENT_CATEGORIES)

        db = get_db()
        db.execute(
            "INSERT INTO investments (user_id, name, category, investment_type, invested_amount, current_value, units, purchase_price, sip_amount, sip_frequency, sip_start_date, start_date, maturity_date, interest_rate, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session["user_id"], name, category, inv_type, float(invested), float(current), float(units), float(purchase_price), float(sip_amount), sip_frequency, sip_start_date or None, start_date, maturity_date or None, float(interest_rate), notes),
        )
        db.commit()
        db.close()
        flash("Investment added successfully.", "success")
        return redirect(url_for("investments"))

    return render_template("add_investment.html", categories=INVESTMENT_CATEGORIES)


@app.route("/investments/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_investment(id):
    db = get_db()
    inv = db.execute(
        "SELECT * FROM investments WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()

    if not inv:
        db.close()
        flash("Investment not found.", "error")
        return redirect(url_for("investments"))

    if request.method == "POST":
        if not validate_csrf():
            db.close()
            return render_template("add_investment.html", error="Session expired. Please try again.", investment=inv, edit=True, categories=INVESTMENT_CATEGORIES)

        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        inv_type = request.form.get("investment_type", "lump_sum")
        invested = request.form.get("invested_amount", "").strip()
        current = request.form.get("current_value", "").strip()
        units = request.form.get("units", "0").strip()
        purchase_price = request.form.get("purchase_price", "0").strip()
        sip_amount = request.form.get("sip_amount", "0").strip()
        sip_frequency = request.form.get("sip_frequency", "monthly")
        sip_start_date = request.form.get("sip_start_date", "").strip()
        start_date = request.form.get("start_date", "").strip()
        maturity_date = request.form.get("maturity_date", "").strip()
        interest_rate = request.form.get("interest_rate", "0").strip()
        status = request.form.get("status", "active")
        notes = request.form.get("notes", "").strip()

        error = None
        if not name:
            error = "Name is required."
        elif category not in INVESTMENT_CATEGORIES:
            error = "Invalid category."
        elif not re.match(r"^\d+(\.\d{1,2})?$", invested) or float(invested) <= 0:
            error = "Invalid invested amount."
        elif not re.match(r"^\d+(\.\d{1,2})?$", current) or float(current) < 0:
            error = "Invalid current value."
        elif units and not re.match(r"^\d+(\.\d{1,6})?$", units):
            error = "Invalid units."
        elif purchase_price and not re.match(r"^\d+(\.\d{1,2})?$", purchase_price):
            error = "Invalid purchase price."
        elif sip_amount and not re.match(r"^\d+(\.\d{1,2})?$", sip_amount):
            error = "Invalid SIP amount."
        elif interest_rate and not re.match(r"^\d+(\.\d{1,2})?$", interest_rate):
            error = "Invalid interest rate."
        elif not start_date:
            error = "Start date is required."

        if error:
            db.close()
            return render_template("add_investment.html", error=error, investment=inv, edit=True, categories=INVESTMENT_CATEGORIES)

        db.execute(
            "UPDATE investments SET name=?, category=?, investment_type=?, invested_amount=?, current_value=?, units=?, purchase_price=?, sip_amount=?, sip_frequency=?, sip_start_date=?, start_date=?, maturity_date=?, interest_rate=?, status=?, notes=? WHERE id=? AND user_id=?",
            (name, category, inv_type, float(invested), float(current), float(units), float(purchase_price), float(sip_amount), sip_frequency, sip_start_date or None, start_date, maturity_date or None, float(interest_rate), status, notes, id, session["user_id"]),
        )
        db.commit()
        db.close()
        flash("Investment updated.", "success")
        return redirect(url_for("investments"))

    db.close()
    return render_template("add_investment.html", investment=inv, edit=True, categories=INVESTMENT_CATEGORIES)


@app.route("/investments/<int:id>/delete", methods=["POST"])
@login_required
def delete_investment(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("investments"))
    db = get_db()
    db.execute("DELETE FROM investments WHERE id = ? AND user_id = ?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Investment deleted.", "success")
    return redirect(url_for("investments"))


@app.route("/investments/<int:id>/update-value", methods=["POST"])
@login_required
def update_investment_value(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("investments"))
    new_value = request.form.get("current_value", "").strip()
    if not re.match(r"^\d+(\.\d{1,2})?$", new_value) or float(new_value) < 0:
        flash("Invalid value.", "error")
        return redirect(url_for("investments"))
    db = get_db()
    db.execute(
        "UPDATE investments SET current_value = ? WHERE id = ? AND user_id = ?",
        (float(new_value), id, session["user_id"]),
    )
    db.commit()
    db.close()
    flash("Value updated.", "success")
    return redirect(url_for("investments"))


# ------------------------------------------------------------------ #
# Trends data (JSON)                                                  #
# ------------------------------------------------------------------ #

@app.route("/api/trends")
@login_required
def api_trends():
    period = request.args.get("period", "year")
    period_config = {
        "day":  {"fmt": "%Y-%m-%d %H:00", "range": "0 days",  "label": "%H:00"},
        "week": {"fmt": "%Y-%m-%d",       "range": "-6 days",  "label": "%b %d"},
        "month":{"fmt": "%Y-%m-%d",       "range": "-29 days", "label": "%b %d"},
        "year": {"fmt": "%Y-%m",          "range": "-11 months", "label": "%b %Y"},
    }
    cfg = period_config.get(period, period_config["year"])
    db = get_db()
    rows = db.execute(
        "SELECT strftime(?, date) as m, "
        "COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) as spent, "
        "COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) as earned "
        "FROM expenses WHERE user_id = ? AND date >= date('now', ?) "
        "GROUP BY m ORDER BY m ASC",
        (cfg["fmt"], session["user_id"], cfg["range"]),
    ).fetchall()
    db.close()
    data = []
    cum_spent = 0
    spent_values = []
    for i, r in enumerate(rows):
        cum_spent += r["spent"]
        spent_values.append(r["spent"])
        window = spent_values[max(0, i - 2):i + 1]
        ma = sum(window) / len(window)
        data.append({
            "month": r["m"],
            "spent": r["spent"],
            "earned": r["earned"],
            "net": r["earned"] - r["spent"],
            "cumulative_spent": cum_spent,
            "moving_avg": round(ma, 2),
        })
    return jsonify(data)


@app.route("/api/category-trends")
@login_required
def api_category_trends():
    period = request.args.get("period", "year")
    period_config = {
        "day":  {"range": "0 days"},
        "week": {"range": "-6 days"},
        "month":{"range": "-29 days"},
        "year": {"range": "-11 months"},
    }
    cfg = period_config.get(period, period_config["year"])
    db = get_db()
    rows = db.execute(
        "SELECT category, COALESCE(SUM(amount), 0) as total "
        "FROM expenses WHERE user_id = ? AND type = 'expense' "
        "AND date >= date('now', ?) "
        "GROUP BY category ORDER BY total DESC",
        (session["user_id"], cfg["range"]),
    ).fetchall()
    db.close()
    return jsonify([{"category": r["category"], "total": r["total"]} for r in rows])


@app.route("/api/monthly-category-trends")
@login_required
def api_monthly_category_trends():
    period = request.args.get("period", "year")
    period_config = {
        "day":  {"fmt": "%Y-%m-%d %H:00", "range": "0 days"},
        "week": {"fmt": "%Y-%m-%d",       "range": "-6 days"},
        "month":{"fmt": "%Y-%m-%d",       "range": "-29 days"},
        "year": {"fmt": "%Y-%m",          "range": "-11 months"},
    }
    cfg = period_config.get(period, period_config["year"])
    db = get_db()
    rows = db.execute(
        "SELECT strftime(?, date) as m, category, "
        "COALESCE(SUM(amount), 0) as total "
        "FROM expenses WHERE user_id = ? AND type = 'expense' "
        "AND date >= date('now', ?) "
        "GROUP BY m, category ORDER BY m ASC, total DESC",
        (cfg["fmt"], session["user_id"], cfg["range"]),
    ).fetchall()
    db.close()
    return jsonify([{"month": r["m"], "category": r["category"], "total": r["total"]} for r in rows])


# ------------------------------------------------------------------ #
# Ledger                                                              #
# ------------------------------------------------------------------ #

@app.route("/ledger")
@login_required
def ledger():
    db = get_db()

    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "date")
    order = request.args.get("order", "asc")
    date_from = request.args.get("date_from", "") or request.args.get("from", "")
    date_to = request.args.get("to", "")
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    per_page = 25

    conditions = ["user_id = ?"]
    params = [session["user_id"]]

    if q:
        conditions.append("description LIKE ?")
        params.append(f"%{q}%")
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)

    where = " AND ".join(conditions)

    sort_col = "date"
    if sort == "amount":
        sort_col = "amount"
    elif sort == "category":
        sort_col = "category"
    dir = "ASC" if order == "asc" else "DESC"

    count = db.execute(f"SELECT COUNT(*) FROM expenses WHERE {where}", params).fetchone()[0]
    total_pages = max(1, (count + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    expenses = db.execute(
        f"SELECT * FROM expenses WHERE {where} ORDER BY {sort_col} {dir}, id ASC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    total_income = db.execute(
        f"SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE type = 'income' AND {where}",
        params,
    ).fetchone()[0]

    categories = db.execute(
        f"SELECT category, SUM(amount) as total, COUNT(*) as count "
        f"FROM expenses WHERE type = 'expense' AND {where} GROUP BY category ORDER BY total DESC",
        params,
    ).fetchall()

    balance = db.execute(
        f"SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) FROM expenses WHERE {where}",
        params,
    ).fetchone()[0]

    # Compute running balance across ALL matching records (not just current page)
    all_expenses = db.execute(
        f"SELECT * FROM expenses WHERE {where} ORDER BY date ASC, id ASC",
        params,
    ).fetchall()
    all_rows = []
    running = 0
    for e in all_expenses:
        amt = -e["amount"] if e["type"] == "expense" else e["amount"]
        running += amt
        all_rows.append({
            "id": e["id"],
            "date": e["date"],
            "category": e["category"],
            "description": e["description"] or "",
            "amount": e["amount"],
            "type": e["type"],
            "running": running,
        })

    # Slice to current page
    rows = all_rows[offset:offset + per_page]

    # True overall balance (unfiltered, unpaginated)
    true_balance = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) FROM expenses WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()[0]

    db.close()
    return render_template(
        "ledger.html",
        rows=rows,
        categories=categories,
        total_income=total_income,
        income=total_income,
        balance=balance,
        true_balance=true_balance,
        q=q,
        sort=sort,
        order=order,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        show_running=sort == "date",
    )


# ------------------------------------------------------------------ #
# Profile                                                             #
# ------------------------------------------------------------------ #

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()

    if request.method == "POST":
        if not validate_csrf():
            db.close()
            flash("Session expired. Please try again.", "error")
            return redirect(url_for("profile"))

        action = request.form.get("action")

        if action == "update_profile":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            error = None

            if not name:
                error = "Name is required."
            elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                error = "Invalid email address."
            else:
                existing = db.execute(
                    "SELECT id FROM users WHERE email = ? AND id != ?",
                    (email, session["user_id"]),
                ).fetchone()
                if existing:
                    error = "This email is already taken."

            if error:
                user = db.execute(
                    "SELECT * FROM users WHERE id = ?", (session["user_id"],)
                ).fetchone()
                stats = _get_user_stats(db, session["user_id"])
                db.close()
                return render_template("profile.html", user=user, stats=stats, error=error, cache_buster=int(time.time()))

            db.execute(
                "UPDATE users SET name = ?, email = ? WHERE id = ?",
                (name, email, session["user_id"]),
            )
            db.commit()
            session["user_name"] = name
            db.close()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("profile"))

        elif action == "change_password":
            current = request.form.get("current_password", "")
            new_pass = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            error = None

            user = db.execute(
                "SELECT * FROM users WHERE id = ?", (session["user_id"],)
            ).fetchone()

            if not current or not new_pass or not confirm:
                error = "All password fields are required."
            elif not check_password_hash(user["password_hash"], current):
                error = "Current password is incorrect."
            elif len(new_pass) < 8:
                error = "New password must be at least 8 characters."
            elif not re.search(r"[A-Z]", new_pass):
                error = "New password must contain an uppercase letter."
            elif not re.search(r"[a-z]", new_pass):
                error = "New password must contain a lowercase letter."
            elif not re.search(r"\d", new_pass):
                error = "New password must contain a digit."
            elif not re.search(r"[!@#$%^&*(),.?\":{}|<>]", new_pass):
                error = "New password must contain a special character."
            elif new_pass != confirm:
                error = "New passwords do not match."

            if error:
                stats = _get_user_stats(db, session["user_id"])
                db.close()
                return render_template("profile.html", user=user, stats=stats, pw_error=error, cache_buster=int(time.time()))

            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_pass), session["user_id"]),
            )
            db.commit()
            db.close()
            flash("Password changed successfully.", "success")
            return redirect(url_for("profile"))

        elif action == "upload_photo":
            file = request.files.get("photo")
            error = None

            if not file or not file.filename:
                error = "No file selected."
            elif not allowed_file(file.filename):
                error = "Allowed formats: PNG, JPG, JPEG, GIF, WebP."

            if error:
                db.close()
                flash(error, "error")
                return redirect(url_for("profile"))

            filename = secure_filename(f"user_{session['user_id']}_{file.filename}")
            db.execute("UPDATE users SET photo = ? WHERE id = ?", (filename, session["user_id"]))
            db.commit()
            db.close()
            # Remove all existing photos after DB is settled
            prefix = f"user_{session['user_id']}_"
            for fname in os.listdir(UPLOAD_FOLDER):
                if fname.startswith(prefix) and fname != filename:
                    try:
                        os.remove(os.path.join(UPLOAD_FOLDER, fname))
                    except OSError:
                        pass
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            flash("Profile photo updated.", "success")
            return redirect(url_for("profile"))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()
    stats = _get_user_stats(db, session["user_id"])
    db.close()
    return render_template("profile.html", user=user, stats=stats, cache_buster=int(time.time()))


def _get_user_stats(db, user_id):
    total_expenses = db.execute(
        "SELECT COUNT(*) FROM expenses WHERE user_id = ? AND type = 'expense'", (user_id,)
    ).fetchone()[0]
    total_amount = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'expense'", (user_id,)
    ).fetchone()[0]
    total_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type = 'income'", (user_id,)
    ).fetchone()[0]
    categories = db.execute(
        "SELECT COUNT(DISTINCT category) FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return {
        "total_expenses": total_expenses,
        "total_amount": total_amount,
        "total_income": total_income,
        "categories": categories,
    }


# ------------------------------------------------------------------ #
# Expense CRUD                                                        #
# ------------------------------------------------------------------ #

@app.route("/expenses/add", methods=["GET", "POST"])
@login_required
def add_expense():
    db = get_db()
    user_accounts = db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY is_default DESC", (session["user_id"],)).fetchall()
    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        date_val = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()
        exp_type = request.form.get("type", "expense")
        account_id = request.form.get("account_id", "").strip()
        currency = request.form.get("currency", "INR").strip()
        exchange_rate = request.form.get("exchange_rate", "1").strip()

        error = None
        if not validate_csrf():
            db.close()
            return render_template("add_expense.html", error="Session expired.", categories=CATEGORIES, today=date.today().isoformat(), accounts=user_accounts)
        if not amount:
            error = "Amount is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", amount):
            error = "Invalid amount."
        elif float(amount) <= 0:
            error = "Amount must be greater than 0."
        elif not category:
            error = "Category is required."
        elif not date_val:
            error = "Date is required."
        elif category not in CATEGORIES:
            error = "Invalid category."
        elif category == "Salary" and exp_type != "income":
            error = "Salary can only be set as income."
        elif category != "Salary" and exp_type == "income":
            error = f"'{category}' cannot be set as income. Use 'Salary' category for income."

        if error is None:
            category, exp_type = _apply_rules(session["user_id"], description, category, exp_type, db=db)
            try:
                exchange_rate = float(exchange_rate)
            except ValueError:
                exchange_rate = 1
            account_id = int(account_id) if account_id and account_id.isdigit() else None
            cursor = db.execute(
                "INSERT INTO expenses (user_id, amount, category, date, description, type, account_id, currency, exchange_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session["user_id"], float(amount), category, date_val, description, exp_type, account_id, currency, exchange_rate),
            )
            expense_id = cursor.lastrowid

            split_names = request.form.get("split_names", "").strip()
            if split_names:
                names = [n.strip() for n in split_names.split(",") if n.strip()]
                total_amt = float(amount)
                share = round(total_amt / len(names), 2)
                cursor2 = db.execute(
                    "INSERT INTO splits (expense_id, created_by, total_amount) VALUES (?, ?, ?)",
                    (expense_id, session["user_id"], total_amt),
                )
                split_id = cursor2.lastrowid
                for name in names:
                    db.execute(
                        "INSERT INTO split_participants (split_id, name, amount) VALUES (?, ?, ?)",
                        (split_id, name, share),
                    )

            db.commit()
            db.close()
            flash("Expense added successfully.", "success")
            return redirect(url_for("ledger"))

        db.close()
        return render_template("add_expense.html", error=error, categories=CATEGORIES, today=date.today().isoformat(), accounts=user_accounts)

    db.close()
    return render_template("add_expense.html", categories=CATEGORIES, today=date.today().isoformat(), accounts=user_accounts)


@app.route("/expenses/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_expense(id):
    db = get_db()
    expense = db.execute(
        "SELECT * FROM expenses WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()

    if not expense:
        db.close()
        flash("Expense not found.", "error")
        return redirect(url_for("ledger"))

    user_accounts = db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY is_default DESC", (session["user_id"],)).fetchall()

    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        date_val = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()
        exp_type = request.form.get("type", "expense")
        account_id = request.form.get("account_id", "").strip()
        currency = request.form.get("currency", "INR").strip()

        error = None
        if not validate_csrf():
            db.close()
            return render_template("edit_expense.html", expense=expense, error="Session expired.", categories=CATEGORIES, accounts=user_accounts)
        if not amount:
            error = "Amount is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", amount):
            error = "Invalid amount."
        elif float(amount) <= 0:
            error = "Amount must be greater than 0."
        elif not category:
            error = "Category is required."
        elif not date_val:
            error = "Date is required."
        elif category not in CATEGORIES:
            error = "Invalid category."
        elif category == "Salary" and exp_type != "income":
            error = "Salary can only be set as income."
        elif category != "Salary" and exp_type == "income":
            error = f"'{category}' cannot be set as income. Use 'Salary' category for income."

        if error is None:
            account_id = int(account_id) if account_id and account_id.isdigit() else None
            db.execute(
                "UPDATE expenses SET amount=?, category=?, date=?, description=?, type=?, account_id=?, currency=? WHERE id=? AND user_id=?",
                (float(amount), category, date_val, description, exp_type, account_id, currency, id, session["user_id"]),
            )
            db.commit()
            db.close()
            flash("Expense updated successfully.", "success")
            return redirect(url_for("ledger"))

        db.close()
        return render_template("edit_expense.html", expense=expense, error=error, categories=CATEGORIES, accounts=user_accounts)

    db.close()
    return render_template("edit_expense.html", expense=expense, categories=CATEGORIES, accounts=user_accounts)


@app.route("/expenses/<int:id>/delete", methods=["POST"])
@login_required
def delete_expense(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("ledger"))
    db = get_db()
    expense = db.execute(
        "SELECT id FROM expenses WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()

    if not expense:
        db.close()
        flash("Expense not found.", "error")
    else:
        db.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (id, session["user_id"]))
        db.commit()
        db.close()
        flash("Expense deleted.", "success")

    return redirect(url_for("ledger"))


# ------------------------------------------------------------------ #
# CSV Export                                                          #
# ------------------------------------------------------------------ #

@app.route("/export")
@login_required
def export_csv():
    q = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "") or request.args.get("from", "")
    date_to = request.args.get("to", "")

    conditions = ["user_id = ?"]
    params = [session["user_id"]]

    if q:
        conditions.append("description LIKE ?")
        params.append(f"%{q}%")
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)

    where = " AND ".join(conditions)

    db = get_db()
    expenses = db.execute(
        f"SELECT date, type, category, amount, description FROM expenses WHERE {where} ORDER BY date ASC",
        params,
    ).fetchall()
    db.close()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Type", "Category", "Amount", "Description"])
    for e in expenses:
        writer.writerow([e["date"], e["type"], e["category"], e["amount"], e["description"]])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=spendly_export.csv"},
    )


# ------------------------------------------------------------------ #
# Cash flow forecast API                                               #
# ------------------------------------------------------------------ #

@app.route("/api/forecast")
@login_required
def api_forecast():
    db = get_db()
    now = date.today()

    daily_avg = db.execute(
        "SELECT COALESCE(AVG(daily), 0) FROM (SELECT date, SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) as daily "
        "FROM expenses WHERE user_id = ? AND type='expense' AND date >= date('now', '-90 days') GROUP BY date)",
        (session["user_id"],),
    ).fetchone()[0]

    month_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ? AND type='income' AND strftime('%Y-%m', date) = ?",
        (session["user_id"], f"{now.year}-{now.month:02d}"),
    ).fetchone()[0]

    recurring = _detect_recurring(session["user_id"])

    current_balance = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE -amount END), 0) FROM expenses WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()[0]

    projection = []
    balance = current_balance
    for day_offset in range(30):
        d = now + timedelta(days=day_offset)
        if day_offset > 0:
            balance -= daily_avg
        for rec in recurring:
            if rec["next_date"] == d.isoformat():
                balance -= rec["amount"]
        if d.day <= 5 and month_income > 0:
            pass
        projection.append({"date": d.isoformat(), "balance": round(balance, 2)})

    db.close()
    return jsonify(projection)


# ------------------------------------------------------------------ #
# Categorization rules                                                #
# ------------------------------------------------------------------ #

@app.route("/rules", methods=["GET", "POST"])
@login_required
def rules():
    db = get_db()
    if request.method == "POST":
        if not validate_csrf():
            flash("Session expired. Please try again.", "error")
            db.close()
            return redirect(url_for("rules"))

        match_type = request.form.get("match_type", "").strip()
        match_pattern = request.form.get("match_pattern", "").strip()
        assign_category = request.form.get("assign_category", "").strip()
        assign_type = request.form.get("assign_type", "expense")

        error = None
        if match_type not in ("contains", "starts_with", "equals", "regex"):
            error = "Invalid match type."
        elif not match_pattern:
            error = "Match pattern is required."
        elif assign_category not in CATEGORIES:
            error = "Invalid category."

        if error:
            rules = db.execute(
                "SELECT * FROM categorization_rules WHERE user_id = ? ORDER BY priority ASC",
                (session["user_id"],),
            ).fetchall()
            db.close()
            return render_template("rules.html", rules=rules, error=error, categories=CATEGORIES)

        max_priority = db.execute(
            "SELECT COALESCE(MAX(priority), -1) + 1 FROM categorization_rules WHERE user_id = ?",
            (session["user_id"],),
        ).fetchone()[0]

        db.execute(
            "INSERT INTO categorization_rules (user_id, match_type, match_pattern, assign_category, assign_type, priority) VALUES (?, ?, ?, ?, ?, ?)",
            (session["user_id"], match_type, match_pattern, assign_category, assign_type, max_priority),
        )
        db.commit()
        db.close()
        flash("Rule added.", "success")
        return redirect(url_for("rules"))

    rules = db.execute(
        "SELECT * FROM categorization_rules WHERE user_id = ? ORDER BY priority ASC",
        (session["user_id"],),
    ).fetchall()
    db.close()
    return render_template("rules.html", rules=rules, categories=[c for c in CATEGORIES if c != "Salary"])


@app.route("/rules/<int:id>/delete", methods=["POST"])
@login_required
def delete_rule(id):
    if not validate_csrf():
        flash("Session expired. Please try again.", "error")
        return redirect(url_for("rules"))
    db = get_db()
    db.execute("DELETE FROM categorization_rules WHERE id = ? AND user_id = ?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Rule deleted.", "success")
    return redirect(url_for("rules"))


@app.route("/rules/<int:id>/move", methods=["POST"])
@login_required
def move_rule(id):
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("rules"))
    direction = request.form.get("direction", "up")
    db = get_db()
    rule = db.execute(
        "SELECT * FROM categorization_rules WHERE id = ? AND user_id = ?", (id, session["user_id"])
    ).fetchone()
    if not rule:
        db.close()
        flash("Rule not found.", "error")
        return redirect(url_for("rules"))

    swap_rule = None
    if direction == "up":
        swap_rule = db.execute(
            "SELECT * FROM categorization_rules WHERE user_id = ? AND priority < ? ORDER BY priority DESC LIMIT 1",
            (session["user_id"], rule["priority"]),
        ).fetchone()
    else:
        swap_rule = db.execute(
            "SELECT * FROM categorization_rules WHERE user_id = ? AND priority > ? ORDER BY priority ASC LIMIT 1",
            (session["user_id"], rule["priority"]),
        ).fetchone()

    if swap_rule:
        db.execute("UPDATE categorization_rules SET priority = ? WHERE id = ?", (swap_rule["priority"], rule["id"]))
        db.execute("UPDATE categorization_rules SET priority = ? WHERE id = ?", (rule["priority"], swap_rule["id"]))
        db.commit()
    db.close()
    return redirect(url_for("rules"))


# ------------------------------------------------------------------ #
# CSV Import                                                          #
# ------------------------------------------------------------------ #

@app.route("/import", methods=["GET", "POST"])
@login_required
def import_csv():
    if request.method == "POST":
        if not validate_csrf():
            flash("Session expired.", "error")
            return redirect(url_for("import_csv"))

        file = request.files.get("file")
        if not file or not file.filename:
            flash("No file selected.", "error")
            return redirect(url_for("import_csv"))

        if not file.filename.lower().endswith(".csv"):
            flash("Please upload a CSV file.", "error")
            return redirect(url_for("import_csv"))

        try:
            content = file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                content = file.read().decode("latin-1")
            except Exception:
                flash("Could not read file encoding.", "error")
                return redirect(url_for("import_csv"))

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            flash("CSV file is empty.", "error")
            return redirect(url_for("import_csv"))

        headers = reader.fieldnames or []
        col_map = _detect_csv_columns(headers)
        db = get_db()
        count = 0
        errors = 0
        for row in rows:
            try:
                date_val = row.get(col_map.get("date", ""), "").strip()
                amt_str = row.get(col_map.get("amount", ""), "").strip()
                desc = row.get(col_map.get("description", ""), "").strip()
                type_str = row.get(col_map.get("type", ""), "").strip()

                if not date_val or not amt_str:
                    errors += 1
                    continue

                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
                    try:
                        parsed = datetime.strptime(date_val, fmt)
                        date_val = parsed.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
                    errors += 1
                    continue

                amt = float(amt_str.replace(",", "").replace("₹", "").strip())
                if amt <= 0:
                    errors += 1
                    continue

                if type_str.lower() in ("debit", "expense", "withdrawal", "payment", "sent"):
                    exp_type = "expense"
                    amt = abs(amt)
                elif type_str.lower() in ("credit", "income", "deposit", "received", "refund"):
                    exp_type = "income"
                else:
                    exp_type = "expense"

                category = "Other"
                cat_lower = desc.lower()
                if any(w in cat_lower for w in ("swiggy", "zomato", "food", "grocer", "restaurant", "dining", "cafe")):
                    category = "Food"
                elif any(w in cat_lower for w in ("uber", "ola", "metro", "petrol", "fuel", "cab", "auto", "bus", "train", "transport")):
                    category = "Transport"
                elif any(w in cat_lower for w in ("electricity", "bill", "phone", "internet", "wifi", "water", "rent", "broadband")):
                    category = "Bills"
                elif any(w in cat_lower for w in ("hospital", "pharmacy", "doctor", "medicine", "health", "clinic", "medical")):
                    category = "Health"
                elif any(w in cat_lower for w in ("movie", "netflix", "prime", "spotify", "game", "entertainment", "ticket")):
                    category = "Entertainment"
                elif any(w in cat_lower for w in ("mall", "amazon", "flipkart", "myntra", "shoe", "cloth", "shopping", "store")):
                    category = "Shopping"
                elif any(w in cat_lower for w in ("salary", "income", "credit salary")):
                    category = "Salary"

                db.execute(
                    "INSERT INTO expenses (user_id, amount, category, date, description, type) VALUES (?, ?, ?, ?, ?, ?)",
                    (session["user_id"], amt, category, date_val, desc, exp_type),
                )
                count += 1
            except (ValueError, KeyError):
                errors += 1

        db.commit()
        db.close()
        flash(f"Imported {count} transactions ({errors} skipped).", "success")
        return redirect(url_for("ledger"))

    return render_template("import.html")


def _detect_csv_columns(headers):
    lc = {h.lower().strip(): h for h in headers}
    mapping = {}
    date_keys = ["date", "transaction date", "txn date", "value dat", "posting date", "transaction_date", "txn_date"]
    amt_keys = ["amount", "value", "withdrawal", "deposit", "debit", "credit", "transaction amount", "txn_amount", "sum"]
    desc_keys = ["description", "narration", "particulars", "memo", "details", "transaction details", "remarks", "note"]
    type_keys = ["type", "transaction type", "txn type", "dr/cr", "mode"]

    for k in date_keys:
        if k in lc:
            mapping["date"] = lc[k]
            break
    if "date" not in mapping and headers:
        mapping["date"] = headers[0]

    for k in amt_keys:
        if k in lc:
            mapping["amount"] = lc[k]
            break
    if "amount" not in mapping and len(headers) > 1:
        mapping["amount"] = headers[1]

    for k in desc_keys:
        if k in lc:
            mapping["description"] = lc[k]
            break

    for k in type_keys:
        if k in lc:
            mapping["type"] = lc[k]
            break

    return mapping


# ------------------------------------------------------------------ #
# Receipt scan                                                        #
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# Settle split                                                        #
# ------------------------------------------------------------------ #

@app.route("/splits/settle", methods=["POST"])
@login_required
def settle_split():
    if not validate_csrf():
        flash("Session expired.", "error")
        return redirect(url_for("dashboard"))
    name = request.form.get("name", "").strip()
    if not name:
        flash("Name required.", "error")
        return redirect(url_for("dashboard"))
    db = get_db()
    db.execute(
        "UPDATE split_participants SET settled = 1 WHERE split_id IN "
        "(SELECT s.id FROM splits s JOIN expenses e ON e.id = s.expense_id "
        "WHERE e.user_id = ?) AND name = ?",
        (session["user_id"], name),
    )
    underpaid = db.execute(
        "SELECT s.id FROM splits s JOIN split_participants sp ON sp.split_id = s.id "
        "JOIN expenses e ON e.id = s.expense_id "
        "WHERE e.user_id = ? AND sp.settled = 0 AND s.settled = 0 GROUP BY s.id HAVING COUNT(*) = 0",
        (session["user_id"],),
    ).fetchall()
    for s in underpaid:
        db.execute("UPDATE splits SET settled = 1 WHERE id = ?", (s["id"],))
    db.commit()
    db.close()
    flash(f"Settled up with {name}.", "success")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------ #
# Error handlers                                                       #
# ------------------------------------------------------------------ #

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found"), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="Access denied"), 403

@app.errorhandler(500)
def server_error(e):
    app.logger.exception("Internal server error")
    return render_template("error.html", code=500, message="Something went wrong"), 500

@app.errorhandler(413)
def payload_too_large(e):
    return render_template("error.html", code=413, message="Upload is too large (max 5 MB)"), 413


# ------------------------------------------------------------------ #
# Startup                                                             #
# ------------------------------------------------------------------ #

with app.app_context():
    init_db()
    seed_db()
    try:
        os.chmod("spendly.db", 0o600)
    except OSError:
        pass

if __name__ == "__main__":
    app.run(debug=not _is_prod, port=5001)
