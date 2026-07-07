import re
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from database.db import get_db, init_db, seed_db

app = Flask(__name__)
app.secret_key = "spendly-dev-secret-key-change-in-production"


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


# ------------------------------------------------------------------ #
# Placeholder routes — students will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been signed out.", "success")
    return redirect(url_for("landing"))


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please sign in to view your dashboard.", "error")
        return redirect(url_for("login"))

    db = get_db()
    expenses = db.execute(
        "SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC",
        (session["user_id"],),
    ).fetchall()
    total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()[0]
    db.close()
    return render_template("dashboard.html", expenses=expenses, total=total)


@app.route("/ledger")
def ledger():
    if "user_id" not in session:
        flash("Please sign in to view your ledger.", "error")
        return redirect(url_for("login"))

    db = get_db()
    expenses = db.execute(
        "SELECT * FROM expenses WHERE user_id = ? ORDER BY date ASC, id ASC",
        (session["user_id"],),
    ).fetchall()

    total_income = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()[0]

    categories = db.execute(
        "SELECT category, SUM(amount) as total, COUNT(*) as count "
        "FROM expenses WHERE user_id = ? GROUP BY category ORDER BY total DESC",
        (session["user_id"],),
    ).fetchall()

    rows = []
    running = 0
    for e in expenses:
        running += e["amount"]
        rows.append({
            "id": e["id"],
            "date": e["date"],
            "category": e["category"],
            "description": e["description"] or "",
            "amount": e["amount"],
            "running": running,
        })

    db.close()
    return render_template(
        "ledger.html",
        rows=rows,
        categories=categories,
        total_income=total_income,
        balance=running,
    )


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        flash("Please sign in to view your profile.", "error")
        return redirect(url_for("login"))

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
                return render_template("profile.html", user=user, stats=stats, error=error)

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
                return render_template("profile.html", user=user, stats=stats, pw_error=error)

            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_pass), session["user_id"]),
            )
            db.commit()
            db.close()
            flash("Password changed successfully.", "success")
            return redirect(url_for("profile"))

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()
    stats = _get_user_stats(db, session["user_id"])
    db.close()
    return render_template("profile.html", user=user, stats=stats)


def _get_user_stats(db, user_id):
    total_expenses = db.execute(
        "SELECT COUNT(*) FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    total_amount = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    categories = db.execute(
        "SELECT COUNT(DISTINCT category) FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return {
        "total_expenses": total_expenses,
        "total_amount": total_amount,
        "categories": categories,
    }


@app.route("/expenses/add")
def add_expense():
    return "Add expense — coming in Step 7"


@app.route("/expenses/<int:id>/edit")
def edit_expense(id):
    return "Edit expense — coming in Step 8"


@app.route("/expenses/<int:id>/delete")
def delete_expense(id):
    return "Delete expense — coming in Step 9"


with app.app_context():
    init_db()
    seed_db()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
