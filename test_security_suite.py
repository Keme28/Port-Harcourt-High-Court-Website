import os
import sys
import unittest
from fastapi.testclient import TestClient

# Ensure app package is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from app import app
import db
import auth


class SecurityAndAuthSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, follow_redirects=False)

    def test_01_public_and_health_endpoints(self):
        # /api/health should always be publicly accessible
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

        # /login should be accessible
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Sign in", resp.text)
        self.assertIn("Username or Email", resp.text)
        self.assertNotIn("Sign in with Google", resp.text)

    def test_02_unauthenticated_api_endpoints_return_401(self):
        unauth_client = TestClient(app, follow_redirects=False)
        endpoints = [
            "/api/cases",
            "/api/hearings",
            "/api/cause-list",
            "/api/counsel",
            "/api/insights",
            "/api/lookups",
        ]
        for ep in endpoints:
            resp = unauth_client.get(ep)
            self.assertEqual(resp.status_code, 401, f"Expected 401 for {ep}, got {resp.status_code}")
            self.assertFalse(resp.json().get("ok"))

    def test_03_unauthenticated_web_routes_redirect_to_login(self):
        unauth_client = TestClient(app, follow_redirects=False)
        web_routes = [
            "/",
            "/?tab=file",
            "/?tab=register",
            "/?tab=hearings",
            "/?tab=cause-list",
            "/?tab=counsel",
            "/?tab=insights",
            "/?tab=quarter",
            "/?tab=settings",
        ]
        for route in web_routes:
            resp = unauth_client.get(route)
            self.assertEqual(resp.status_code, 303, f"Expected 303 redirect for {route}")
            self.assertTrue(resp.headers.get("location", "").startswith("/login"))

    def test_04_user_creation_and_database_login(self):
        # Create users in SQLite database
        admin_user = db.get_user("admin_test")
        if not admin_user:
            admin_user = db.create_user(
                username="admin_test",
                password="password12345",
                display_name="Admin Test",
                email="admin_test@court.gov.ng",
                role="admin",
            )
        self.assertEqual(admin_user["role"], "admin")

        # Test invalid password login
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/login", data={"username": "admin_test", "password": "wrong_password"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid username/email or password", resp.text)

        # Test valid login with username
        resp = client.post("/login", data={"username": "admin_test", "password": "password12345"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/")

        # Authenticated session can access protected routes
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Admin Test", resp.text)

        # Test valid login with email
        client_email = TestClient(app, follow_redirects=False)
        resp = client_email.post("/login", data={"username": "admin_test@court.gov.ng", "password": "password12345"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/")

    def test_05_admin_creates_judge_credentials_and_resets_password(self):
        # Admin signs in
        admin_client = TestClient(app, follow_redirects=False)
        admin_client.post("/login", data={"username": "admin_test", "password": "password12345"})

        # Admin provisions new Judge credentials
        judge_username = "judge.adeyemi"
        conn = db._connect()
        conn.execute("DELETE FROM users WHERE lower(username)=?", (judge_username,))
        conn.commit()
        conn.close()

        resp = admin_client.post(
            "/settings/users/create",
            data={
                "username": judge_username,
                "display_name": "Hon. Justice S. A. Adeyemi",
                "email": "adeyemi@judiciary.gov.ng",
                "role": "readonly",
                "password": "InitialJudgePass123!",
            },
        )
        self.assertEqual(resp.status_code, 303)

        # Verify Judge account created in SQLite DB
        judge_user = db.get_user(judge_username)
        self.assertIsNotNone(judge_user)
        self.assertEqual(judge_user["role"], "readonly")
        self.assertEqual(judge_user["display_name"], "Hon. Justice S. A. Adeyemi")

        # Judge can sign in with initial password
        judge_client = TestClient(app, follow_redirects=False)
        resp = judge_client.post("/login", data={"username": judge_username, "password": "InitialJudgePass123!"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/")

        # Judge has read-only badge and access
        resp = judge_client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Hon. Justice S. A. Adeyemi", resp.text)
        self.assertIn("Judge / Viewer", resp.text)

        # Admin resets Judge's password
        new_pass = "UpdatedJudgePass456!"
        resp = admin_client.post(
            "/settings/users/password",
            data={
                "user_id": judge_user["id"],
                "new_password": new_pass,
                "confirm_password": new_pass,
            },
        )
        self.assertEqual(resp.status_code, 303)

        # Judge signs in with new password
        judge_client2 = TestClient(app, follow_redirects=False)
        resp = judge_client2.post("/login", data={"username": judge_username, "password": new_pass})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/")



    def test_06_read_only_role_enforcement(self):
        # Create or fetch a readonly user
        ro_user = db.get_user("readonly_user")
        if not ro_user:
            ro_user = db.create_user(
                username="readonly_user",
                password="password12345",
                display_name="Read-Only User",
                email="readonly@court.gov.ng",
                role="readonly",
            )
        db.update_user_role(ro_user["id"], "readonly")

        client = TestClient(app, follow_redirects=False)
        client.post("/login", data={"username": "readonly_user", "password": "password12345"})

        # Read-only user can view cases and hearings via API and Web
        resp = client.get("/api/cases")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("cases", resp.json())

        resp = client.get("/?tab=register")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("READ-ONLY MODE", resp.text)

        # Read-only user CANNOT file cases (API returns 403 Forbidden)
        resp = client.post(
            "/api/cases",
            json={
                "SUIT NUMBER": "HC/TEST/RO/2026",
                "PARTIES": "A v B",
                "CASE TYPE": "APPEAL",
            },
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Read-Only", resp.json().get("error", ""))

        # Read-only user CANNOT file cases via web form (redirects with alert)
        resp = client.post(
            "/file",
            data={
                "SUIT NUMBER": "HC/TEST/RO/2026",
                "PARTIES": "A v B",
                "CASE TYPE": "APPEAL",
            },
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("alert", resp.cookies.get("hct6_flash_kind", ""))

    def test_07_global_read_only_mode_toggle(self):
        # Create a registrar user
        reg_user = db.get_user("registrar_test")
        if not reg_user:
            reg_user = db.create_user(
                username="registrar_test",
                password="password12345",
                display_name="Registrar Test",
                email="registrar_test@court.gov.ng",
                role="registrar",
            )
        db.update_user_role(reg_user["id"], "registrar")

        client = TestClient(app, follow_redirects=False)
        client.post("/login", data={"username": "registrar_test", "password": "password12345"})

        # Turn ON global read-only mode
        db.set_read_only_mode(True)
        self.assertTrue(db.is_read_only_mode())

        # Registrar write attempt is blocked when global read-only mode is active
        resp = client.post(
            "/api/cases",
            json={
                "SUIT NUMBER": "HC/TEST/GLOBAL_RO/2026",
                "PARTIES": "A v B",
            },
        )
        self.assertEqual(resp.status_code, 403)

        # Restore read-only mode to False
        db.set_read_only_mode(False)
        self.assertFalse(db.is_read_only_mode())

    def test_08_security_headers(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")

    def test_09_judgment_entered_reflected_on_insights(self):
        # Admin signs in
        admin_client = TestClient(app, follow_redirects=False)
        admin_client.post("/login", data={"username": "admin_test", "password": "password12345"})

        suit = "HC/TEST/JUDGMENT/2026"
        from datetime import date
        today_iso = date.today().isoformat()
        current_year = date.today().year
        current_quarter = f"Q{(date.today().month - 1) // 3 + 1}"

        # 1. File test matter
        admin_client.post(
            "/api/cases",
            json={
                "SUIT NUMBER": suit,
                "PARTIES": "Plaintiff Corp v Defendant Ltd",
                "CASE TYPE": "WRIT",
                "NATURE OF CLAIM": "CONTRACT",
                "STATUS": "ACTIVE",
                "DATE FILED": today_iso,
            },
        )

        # 2. Record Judgment Hearing
        resp = admin_client.post(
            "/api/hearings",
            json={
                "SUIT NUMBER": suit,
                "HEARING DATE": today_iso,
                "PURPOSE": "FOR JUDGMENT",
                "OUTCOME": "JUDGMENT DELIVERED",
                "DATE CONCLUDED": today_iso,
            },
        )
        self.assertEqual(resp.status_code, 200)

        # 3. Verify Insights Page displays the judgment
        resp = admin_client.get(f"/?tab=insights&year={current_year}&quarter={current_quarter}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Judgments Delivered", resp.text)
        self.assertIn(suit, resp.text)
        self.assertIn("Plaintiff Corp v Defendant Ltd", resp.text)
        self.assertIn("JUDGMENT DELIVERED", resp.text)

        # 4. Verify API Insights endpoint includes judgment in list and counts
        api_resp = admin_client.get(f"/api/insights?year={current_year}&quarter={current_quarter}")
        self.assertEqual(api_resp.status_code, 200)
        data = api_resp.json()
        self.assertGreaterEqual(data["judgments_this_quarter"], 1)
        found = any(j["suit"] == suit for j in data.get("judgments", []))
        self.assertTrue(found, f"Expected {suit} in judgments list, got {data.get('judgments')}")


if __name__ == "__main__":
    unittest.main()

