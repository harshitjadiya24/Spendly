import re
import csv
import os
import time
import secrets
from io import StringIO
from functools import wraps
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database.db import get_db, init_db, seed_db

app = Flask(__name__)
app.secret_key = "spendly-dev-secret-key-change-in-production"

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
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {"csrf_token": session["csrf_token"]}

def validate_csrf():
    if app.config.get("CSRF_DISABLED"):
        return True
    token = request.form.get("csrf_token")
    if not token or token != session.get("csrf_token"):
        return False
    return True


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
                flash("Account created successfully! Please sign in.", "success")
                return redirect(url_for("login"))
            db.close()

        return render_template("register.html", error=error)

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not validate_csrf():
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
                error = "Invalid email or password."
            else:
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                flash(f"Welcome back, {user['name']}!", "success")
                return redirect(url_for("landing"))

        return render_template("login.html", error=error)

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
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
        monthly = l["emi_amount"] * (4.33 if l["emi_frequency"] == "weekly" else 1)
        if l["type"] == "borrowed":
            loan_monthly_pay += monthly
        else:
            loan_monthly_receive += monthly

    avail_months = [r["m"] for r in avail_months]
    if not avail_months:
        avail_months = [f"{date.today().year}-{date.today().month:02d}"]
    db.close()
    return render_template("dashboard.html", expenses=expenses, total=total, income=income, month=month, avail_months=avail_months, budgets=budgets, loan_outstanding=loan_outstanding, loan_monthly_pay=round(loan_monthly_pay), loan_monthly_receive=round(loan_monthly_receive), loan_count=len(loans_active))


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
        allowed_cats = [c for c in CATEGORIES if c != "Salary"]

        if category not in allowed_cats:
            flash("Invalid category.", "error")
        elif category and amount and re.match(r"^\d+(\.\d{1,2})?$", amount) and float(amount) > 0:
            db.execute(
                "INSERT INTO budgets (user_id, category, amount, month) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, category, month) DO UPDATE SET amount = ?",
                (session["user_id"], category, float(amount), month, float(amount)),
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
    return redirect(request.referrer or url_for("budgets"))


# ------------------------------------------------------------------ #
# Loans                                                                #
# ------------------------------------------------------------------ #

def _next_emi_date(start_date, paid_emis, freq="monthly"):
    parts = start_date.split("-")
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
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
        l["emi_amount"] * (4.33 if l["emi_frequency"] == "weekly" else 1)
        for l in enriched if l["status"] == "active" and l["type"] == "borrowed"
    ))
    monthly_emi_receive = round(sum(
        l["emi_amount"] * (4.33 if l["emi_frequency"] == "weekly" else 1)
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
# Trends data (JSON)                                                  #
# ------------------------------------------------------------------ #

@app.route("/api/trends")
@login_required
def api_trends():
    from flask import jsonify
    db = get_db()
    rows = db.execute(
        "SELECT strftime('%Y-%m', date) as m, "
        "COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) as spent, "
        "COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) as earned "
        "FROM expenses WHERE user_id = ? AND date >= date('now', '-11 months') "
        "GROUP BY m ORDER BY m ASC",
        (session["user_id"],),
    ).fetchall()
    db.close()
    return jsonify([{
        "month": r["m"],
        "spent": r["spent"],
        "earned": r["earned"],
    } for r in rows])


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
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    page = int(request.args.get("page", 1))
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
            # Remove all existing photos for this user (cleanup orphans)
            prefix = f"user_{session['user_id']}_"
            for fname in os.listdir(UPLOAD_FOLDER):
                if fname.startswith(prefix):
                    try:
                        os.remove(os.path.join(UPLOAD_FOLDER, fname))
                    except OSError:
                        pass
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            db.execute("UPDATE users SET photo = ? WHERE id = ?", (filename, session["user_id"]))
            db.commit()
            db.close()
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
    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        date_val = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()
        exp_type = request.form.get("type", "expense")

        error = None
        if not validate_csrf():
            return render_template("add_expense.html", error="Session expired. Please try again.", categories=CATEGORIES, today=date.today().isoformat())
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
        elif category == "Salary" and exp_type != "income":
            error = "Salary can only be set as income."
        elif category != "Salary" and exp_type == "income":
            error = f"'{category}' cannot be set as income. Use 'Salary' category for income."

        if error is None:
            db = get_db()
            db.execute(
                "INSERT INTO expenses (user_id, amount, category, date, description, type) VALUES (?, ?, ?, ?, ?, ?)",
                (session["user_id"], float(amount), category, date_val, description, exp_type),
            )
            db.commit()
            db.close()
            flash("Expense added successfully.", "success")
            return redirect(url_for("ledger"))

        return render_template("add_expense.html", error=error, categories=CATEGORIES, today=date.today().isoformat())

    return render_template("add_expense.html", categories=CATEGORIES, today=date.today().isoformat())


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

    if request.method == "POST":
        amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        date_val = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()
        exp_type = request.form.get("type", "expense")

        error = None
        if not validate_csrf():
            db.close()
            return render_template("edit_expense.html", expense=expense, error="Session expired. Please try again.", categories=CATEGORIES)
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
        elif category == "Salary" and exp_type != "income":
            error = "Salary can only be set as income."
        elif category != "Salary" and exp_type == "income":
            error = f"'{category}' cannot be set as income. Use 'Salary' category for income."

        if error is None:
            db.execute(
                "UPDATE expenses SET amount = ?, category = ?, date = ?, description = ?, type = ? WHERE id = ? AND user_id = ?",
                (float(amount), category, date_val, description, exp_type, id, session["user_id"]),
            )
            db.commit()
            db.close()
            flash("Expense updated successfully.", "success")
            return redirect(url_for("ledger"))

        db.close()
        return render_template("edit_expense.html", expense=expense, error=error, categories=CATEGORIES)

    db.close()
    return render_template("edit_expense.html", expense=expense, categories=CATEGORIES)


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
    date_from = request.args.get("from", "")
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
# Startup                                                             #
# ------------------------------------------------------------------ #

with app.app_context():
    init_db()
    seed_db()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
