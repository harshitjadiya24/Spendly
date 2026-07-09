import re


class TestAuth:
    def test_register_page(self, client):
        r = client.get("/register")
        assert r.status_code == 200
        assert b"Create your account" in r.data

    def _register(self, client, **kw):
        data = {"name": "A", "email": "a@test.com", "password": "password123",
                "confirm_password": "password123"}
        data.update(kw)
        return client.post("/register", data=data, follow_redirects=True)

    def test_register_success(self, client):
        r = self._register(client, name="Alice", email="alice@test.com")
        assert r.status_code == 200
        assert b"Account created" in r.data

    def test_register_duplicate_email(self, client):
        self._register(client, email="dup@test.com")
        r = self._register(client, email="dup@test.com", name="Bob")
        assert b"already exists" in r.data

    def test_register_short_password(self, client):
        r = self._register(client, password="123", confirm_password="123")
        assert b"at least 8 characters" in r.data

    def test_register_invalid_email(self, client):
        r = self._register(client, email="bademail")
        assert b"Invalid email" in r.data

    def test_login_success(self, client):
        self._register(client, name="Alice", email="alice@test.com")
        r = client.post("/login", data={
            "email": "alice@test.com", "password": "password123",
        }, follow_redirects=True)
        assert b"Welcome back" in r.data

    def test_login_invalid(self, client):
        r = client.post("/login", data={
            "email": "noone@test.com", "password": "wrong",
        }, follow_redirects=True)
        assert b"Invalid email or password" in r.data

    def test_logout(self, client):
        self._register(client)
        client.post("/login", data={
            "email": "a@test.com", "password": "password123",
        })
        r = client.get("/logout", follow_redirects=True)
        assert b"signed out" in r.data


class TestAuthProtection:
    def _login(self, client):
        client.post("/register", data={
            "name": "U", "email": "u@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "u@test.com", "password": "password123",
        })

    def test_dashboard_redirects_when_logged_out(self, client):
        r = client.get("/dashboard", follow_redirects=True)
        assert b"Please sign in" in r.data

    def test_ledger_redirects_when_logged_out(self, client):
        r = client.get("/ledger", follow_redirects=True)
        assert b"Please sign in" in r.data

    def test_profile_redirects_when_logged_out(self, client):
        r = client.get("/profile", follow_redirects=True)
        assert b"Please sign in" in r.data

    def test_add_expense_redirects_when_logged_out(self, client):
        r = client.get("/expenses/add", follow_redirects=True)
        assert b"Please sign in" in r.data

    def test_export_redirects_when_logged_out(self, client):
        r = client.get("/export", follow_redirects=True)
        assert b"Please sign in" in r.data


class TestExpenseCRUD:
    def _login(self, client):
        client.post("/register", data={
            "name": "U", "email": "u@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "u@test.com", "password": "password123",
        })

    def _add(self, client, amount="100", category="Food", date="2026-07-01",
             desc="Test", typ="expense"):
        return client.post("/expenses/add", data={
            "amount": amount, "category": category, "date": date,
            "description": desc, "type": typ,
        }, follow_redirects=True)

    def _get_owned_id(self, client):
        from database.db import get_db
        with client.application.app_context():
            db = get_db()
            row = db.execute("""
                SELECT e.id FROM expenses e
                JOIN users u ON u.id = e.user_id
                WHERE u.email = 'u@test.com'
                LIMIT 1
            """).fetchone()
            db.close()
            return row["id"] if row else None

    def test_add_expense(self, client):
        self._login(client)
        r = self._add(client)
        assert b"added successfully" in r.data

    def test_add_income(self, client):
        self._login(client)
        r = self._add(client, amount="5000", category="Salary", typ="income")
        assert b"added successfully" in r.data

    def test_edit_expense(self, client):
        self._login(client)
        self._add(client)
        eid = self._get_owned_id(client)
        r = client.get(f"/expenses/{eid}/edit")
        assert r.status_code == 200
        r = client.post(f"/expenses/{eid}/edit", data={
            "amount": "300", "category": "Food", "date": "2026-07-01",
            "description": "Updated", "type": "expense",
        }, follow_redirects=True)
        assert b"updated successfully" in r.data

    def test_delete_expense(self, client):
        self._login(client)
        self._add(client)
        eid = self._get_owned_id(client)
        r = client.post(f"/expenses/{eid}/delete", follow_redirects=True)
        assert b"deleted" in r.data

    def test_cannot_edit_others_expense(self, client):
        self._login(client)
        r = client.get("/expenses/1/edit", follow_redirects=True)
        assert b"not found" in r.data or b"ledger" in r.data


class TestLedger:
    def _setup(self, client):
        client.post("/register", data={
            "name": "U", "email": "u@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "u@test.com", "password": "password123",
        })
        client.post("/expenses/add", data={
            "amount": "300", "category": "Food", "date": "2026-07-01",
            "description": "Groceries", "type": "expense",
        }, follow_redirects=True)
        client.post("/expenses/add", data={
            "amount": "5000", "category": "Salary", "date": "2026-07-01",
            "description": "Monthly pay", "type": "income",
        }, follow_redirects=True)

    def test_ledger_page_loads(self, client):
        self._setup(client)
        r = client.get("/ledger")
        assert r.status_code == 200
        assert b"Filtered balance" in r.data
        assert b"Transaction ledger" in r.data

    def test_ledger_search(self, client):
        self._setup(client)
        r = client.get("/ledger?q=Groceries")
        assert b"Groceries" in r.data

    def test_ledger_sort_amount(self, client):
        self._setup(client)
        r = client.get("/ledger?sort=amount&order=desc")
        assert r.status_code == 200

    def test_ledger_date_filter(self, client):
        self._setup(client)
        r = client.get("/ledger?from=2026-07-01&to=2026-07-31")
        assert r.status_code == 200


class TestProfile:
    def _login(self, client):
        client.post("/register", data={
            "name": "Alice", "email": "alice@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "alice@test.com", "password": "password123",
        })

    def test_profile_page(self, client):
        self._login(client)
        r = client.get("/profile")
        assert r.status_code == 200
        assert b"Alice" in r.data
        assert b"Account stats" in r.data

    def test_update_profile(self, client):
        self._login(client)
        r = client.post("/profile", data={
            "action": "update_profile", "name": "Alice B", "email": "alice@test.com",
        }, follow_redirects=True)
        assert b"Profile updated" in r.data

    def test_change_password(self, client):
        self._login(client)
        r = client.post("/profile", data={
            "action": "change_password", "current_password": "password123",
            "new_password": "newpass456", "confirm_password": "newpass456",
        }, follow_redirects=True)
        assert b"Password changed" in r.data


class TestExport:
    def test_csv_export(self, client):
        client.post("/register", data={
            "name": "U", "email": "u@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "u@test.com", "password": "password123",
        })
        client.post("/expenses/add", data={
            "amount": "100", "category": "Food", "date": "2026-07-01",
            "description": "Test", "type": "expense",
        }, follow_redirects=True)
        r = client.get("/export")
        assert r.status_code == 200
        assert r.mimetype == "text/csv"
        assert b"Amount" in r.data


class TestInvestments:
    def _login(self, client):
        client.post("/register", data={
            "name": "U", "email": "u@test.com", "password": "password123",
            "confirm_password": "password123",
        })
        client.post("/login", data={
            "email": "u@test.com", "password": "password123",
        })

    def _add(self, client, name="Test Fund", category="mutual_funds", invested="50000",
             current="55000", start_date="2026-01-01", inv_type="lump_sum"):
        return client.post("/investments/add", data={
            "name": name, "category": category, "invested_amount": invested,
            "current_value": current, "start_date": start_date, "investment_type": inv_type,
            "units": "0", "purchase_price": "0", "sip_amount": "0",
            "sip_frequency": "monthly", "interest_rate": "0",
        }, follow_redirects=True)

    def test_investments_page_redirects_when_logged_out(self, client):
        r = client.get("/investments", follow_redirects=True)
        assert b"Please sign in" in r.data

    def test_add_investment_page_loads(self, client):
        self._login(client)
        r = client.get("/investments/add")
        assert r.status_code == 200
        assert b"Add an investment" in r.data

    def test_add_investment_success(self, client):
        self._login(client)
        r = self._add(client)
        assert b"added successfully" in r.data

    def test_add_investment_sip(self, client):
        self._login(client)
        r = self._add(client, name="SIP Fund", inv_type="sip")
        assert b"added successfully" in r.data

    def test_investments_page_shows_data(self, client):
        self._login(client)
        self._add(client)
        r = client.get("/investments")
        assert b"Test Fund" in r.data
        assert b"Total invested" in r.data

    def test_delete_investment(self, client):
        self._login(client)
        self._add(client)
        r = client.post("/investments/1/delete", follow_redirects=True)
        assert b"deleted" in r.data
