import re
import csv
import os
import time
from io import StringIO
from datetime import datetime, date
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
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in first.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        error = None
        if not name:
            error = "Name is required."
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            error = "Invalid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."

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
                return redirect(url_for("profile"))

        return render_template("login.html", error=error)

    return render_template("login.html")


@app.route("/logout")
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

    db.close()
    return render_template("dashboard.html", expenses=expenses, total=total, income=income, month=month, avail_months=[r["m"] for r in avail_months], budgets=budgets)


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
        category = request.form.get("category", "").strip()
        amount = request.form.get("amount", "").strip()

        if category and amount and re.match(r"^\d+(\.\d{1,2})?$", amount):
            db.execute(
                "INSERT INTO budgets (user_id, category, amount, month) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, category, month) DO UPDATE SET amount = ?",
                (session["user_id"], category, float(amount), month, float(amount)),
            )
            db.commit()
            flash(f"Budget set for {category}.", "success")
        else:
            flash("Invalid budget entry.", "error")

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

    db.close()
    return render_template("budgets.html", budgets=budgets, month=month, avail_months=[r["m"] for r in avail_months], categories=[c for c in CATEGORIES if c != "Salary"])


@app.route("/budgets/<int:id>/delete", methods=["POST"])
@login_required
def delete_budget(id):
    db = get_db()
    db.execute("DELETE FROM budgets WHERE id = ? AND user_id = ?", (id, session["user_id"]))
    db.commit()
    db.close()
    flash("Budget removed.", "success")
    return redirect(request.referrer or url_for("budgets"))


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

    rows = []
    running = 0
    for e in expenses:
        amount = -e["amount"] if e["type"] == "expense" else e["amount"]
        running += amount
        rows.append({
            "id": e["id"],
            "date": e["date"],
            "category": e["category"],
            "description": e["description"] or "",
            "amount": e["amount"],
            "type": e["type"],
            "running": running,
        })

    db.close()
    return render_template(
        "ledger.html",
        rows=rows,
        categories=categories,
        total_income=total_income,
        income=total_income,
        balance=running,
        q=q,
        sort=sort,
        order=order,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
    )


# ------------------------------------------------------------------ #
# Profile                                                             #
# ------------------------------------------------------------------ #

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()

    if request.method == "POST":
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
        if not amount:
            error = "Amount is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", amount):
            error = "Invalid amount."
        elif not category:
            error = "Category is required."
        elif not date_val:
            error = "Date is required."

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
        if not amount:
            error = "Amount is required."
        elif not re.match(r"^\d+(\.\d{1,2})?$", amount):
            error = "Invalid amount."
        elif not category:
            error = "Category is required."
        elif not date_val:
            error = "Date is required."

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
    db = get_db()
    expenses = db.execute(
        "SELECT date, type, category, amount, description FROM expenses WHERE user_id = ? ORDER BY date ASC",
        (session["user_id"],),
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
