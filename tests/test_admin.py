"""
tests/test_admin.py
Integration tests for the admin authentication and guidelines management routes.

Tests cover:
  - GET  /admin                         (HTML page served)
  - GET  /admin/check-auth              (unauthenticated → 401)
  - POST /admin/login                   (bad creds → 401, good creds → 200)
  - POST /admin/logout                  (clears session)
  - GET  /admin/guidelines/raw          (requires auth)
  - POST /admin/guidelines/save         (requires auth; validate → save flow)
  - POST /admin/credentials             (requires auth; validation rules)
  - POST /admin/reload-guidelines       (public endpoint, no auth required)

Admin credentials from admin_config.json defaults: admin / prakash
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "prakash"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client):
    """Log in with default admin credentials. Returns the response."""
    return client.post(
        "/admin/login",
        data=json.dumps({"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------

class TestAdminPage(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_admin_page_returns_200(self):
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)

    def test_admin_page_is_html(self):
        resp = self.client.get("/admin")
        self.assertIn(b"<!DOCTYPE html>", resp.data)

    def test_admin_page_contains_login_form(self):
        """The page should include the login screen (shown before auth)."""
        resp = self.client.get("/admin")
        self.assertIn(b"login", resp.data.lower())


# ---------------------------------------------------------------------------
# Authentication: check-auth, login, logout
# ---------------------------------------------------------------------------

class TestAdminAuthentication(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        self.client = app.test_client()

    def test_check_auth_unauthenticated_returns_401(self):
        resp = self.client.get("/admin/check-auth")
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.data)
        self.assertFalse(data["authenticated"])

    def test_login_with_wrong_password_returns_401(self):
        resp = self.client.post(
            "/admin/login",
            data=json.dumps({"username": ADMIN_USERNAME, "password": "wrongpassword"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_login_with_wrong_username_returns_401(self):
        resp = self.client.post(
            "/admin/login",
            data=json.dumps({"username": "notadmin", "password": ADMIN_PASSWORD}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_with_correct_credentials_returns_200(self):
        resp = _login(self.client)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["username"], ADMIN_USERNAME)

    def test_check_auth_after_login_returns_200(self):
        _login(self.client)
        resp = self.client.get("/admin/check-auth")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["authenticated"])

    def test_logout_clears_session(self):
        _login(self.client)
        resp = self.client.post("/admin/logout")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["logged_out"])
        # Should now be unauthenticated
        auth_resp = self.client.get("/admin/check-auth")
        self.assertEqual(auth_resp.status_code, 401)

    def test_login_with_empty_body_returns_401(self):
        resp = self.client.post(
            "/admin/login",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# Admin guidelines: raw, save
# ---------------------------------------------------------------------------

class TestAdminGuidelinesRaw(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        self.client = app.test_client()

    def test_raw_without_auth_returns_401(self):
        resp = self.client.get("/admin/guidelines/raw")
        self.assertEqual(resp.status_code, 401)

    def test_raw_with_auth_returns_200(self):
        _login(self.client)
        resp = self.client.get("/admin/guidelines/raw")
        self.assertEqual(resp.status_code, 200)

    def test_raw_contains_yaml_content(self):
        _login(self.client)
        resp = self.client.get("/admin/guidelines/raw")
        data = json.loads(resp.data)
        self.assertIn("yaml", data)
        self.assertIn("metadata:", data["yaml"])
        self.assertIn("stages:", data["yaml"])


class TestAdminGuidelinesSave(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        self.client = app.test_client()

    def test_save_without_auth_returns_401(self):
        resp = self.client.post(
            "/admin/guidelines/save",
            data=json.dumps({"yaml": "invalid: yaml: content"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_save_with_invalid_yaml_returns_error(self):
        _login(self.client)
        bad_yaml = "this: is: not: valid: yaml: ::::"
        resp = self.client.post(
            "/admin/guidelines/save",
            data=json.dumps({"yaml": bad_yaml}),
            content_type="application/json",
        )
        # Should not be 200 — either 400 or 422
        self.assertNotEqual(resp.status_code, 200)

    def test_save_with_missing_required_sections_returns_error(self):
        """YAML without stages should fail validation."""
        _login(self.client)
        incomplete_yaml = "metadata:\n  version: '9.9'\n  last_updated: '2099-01-01'\n"
        resp = self.client.post(
            "/admin/guidelines/save",
            data=json.dumps({"yaml": incomplete_yaml}),
            content_type="application/json",
        )
        self.assertNotEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Admin credentials change
# ---------------------------------------------------------------------------

class TestAdminCredentials(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        self.client = app.test_client()

    def test_credentials_without_auth_returns_401(self):
        resp = self.client.post(
            "/admin/credentials",
            data=json.dumps({"username": "x", "password": "y", "confirm_password": "y"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_credentials_mismatched_passwords_returns_400(self):
        _login(self.client)
        resp = self.client.post(
            "/admin/credentials",
            data=json.dumps({
                "username": "admin",
                "password": "newpass123",
                "confirm_password": "differentpass",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_credentials_password_too_short_returns_400(self):
        _login(self.client)
        resp = self.client.post(
            "/admin/credentials",
            data=json.dumps({
                "username": "admin",
                "password": "abc",
                "confirm_password": "abc",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_credentials_empty_username_returns_400(self):
        _login(self.client)
        resp = self.client.post(
            "/admin/credentials",
            data=json.dumps({
                "username": "",
                "password": "validpass",
                "confirm_password": "validpass",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Public endpoint: reload-guidelines (no auth required)
# ---------------------------------------------------------------------------

class TestAdminReloadGuidelines(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_reload_without_auth_returns_200(self):
        """This endpoint is intentionally public for hot-reload support."""
        resp = self.client.post("/admin/reload-guidelines")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["reloaded"])

    def test_reload_returns_version(self):
        resp = self.client.post("/admin/reload-guidelines")
        data = json.loads(resp.data)
        self.assertIn("version", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
