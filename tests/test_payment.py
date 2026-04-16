"""
tests/test_payment.py
Unit and integration tests for the Razorpay payment routes.

Tests cover:
  - GET  /payment/config       (enabled + disabled states)
  - POST /payment/create-order (disabled → 503, bad review_id → 400, valid → 200)
  - POST /payment/verify       (disabled → 503, missing fields → 400,
                                bad signature → 400, valid HMAC → 200)
  - GET  /payment/test         (sandbox page renders correctly)

No real Razorpay API calls are made — app._create_razorpay_order is mocked.
"""

import hashlib
import hmac as hmac_lib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app, _review_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DUMMY_REVIEW_ID   = "test-payment-review-001"
DUMMY_ORDER_ID    = "order_TestABC123"
DUMMY_PAYMENT_ID  = "pay_TestXYZ456"
TEST_KEY_ID       = "rzp_test_dummykeyid"
TEST_KEY_SECRET   = "dummysecret1234567890"

DUMMY_REVIEW = {
    "status": "done",
    "manuscript_title": "Payment Test Manuscript",
    "decision": "Major revision",
    "word_count": 1500,
    "review_text": "Test review text.",
    "filename": "test.docx",
}


def _valid_signature(order_id: str, payment_id: str, secret: str) -> str:
    """Compute the HMAC-SHA256 signature the same way the server does."""
    return hmac_lib.new(
        secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _env_with_payment(**kwargs):
    """Return an os.environ patch dict with payment keys set."""
    base = {k: v for k, v in os.environ.items()}
    base["RAZORPAY_KEY_ID"]     = kwargs.get("key_id",     TEST_KEY_ID)
    base["RAZORPAY_KEY_SECRET"] = kwargs.get("key_secret", TEST_KEY_SECRET)
    return base


def _env_without_payment():
    """Return an os.environ patch dict with payment keys removed."""
    return {k: v for k, v in os.environ.items()
            if k not in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")}


# ---------------------------------------------------------------------------
# /payment/config
# ---------------------------------------------------------------------------

class TestPaymentConfig(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_config_disabled_when_no_keys(self):
        with patch.dict(os.environ, _env_without_payment(), clear=True):
            import importlib
            import app as app_module
            # Module-level constants are set at import; patch them directly
            with patch.object(app_module, "PAYMENT_ENABLED", False), \
                 patch.object(app_module, "RAZORPAY_KEY_ID", ""):
                resp = self.client.get("/payment/config")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data["enabled"])
        self.assertEqual(data["key_id"], "")

    def test_config_enabled_when_keys_present(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID):
            resp = self.client.get("/payment/config")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["key_id"], TEST_KEY_ID)

    def test_config_has_all_required_fields(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID):
            resp = self.client.get("/payment/config")
        data = json.loads(resp.data)
        for field in ("enabled", "key_id", "amount", "currency", "description", "amount_display"):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_config_amount_is_5000_paise(self):
        """₹50 = 5000 paise."""
        resp = self.client.get("/payment/config")
        data = json.loads(resp.data)
        self.assertEqual(data["amount"], 5_000)

    def test_config_currency_is_inr(self):
        resp = self.client.get("/payment/config")
        data = json.loads(resp.data)
        self.assertEqual(data["currency"], "INR")


# ---------------------------------------------------------------------------
# /payment/create-order
# ---------------------------------------------------------------------------

class TestPaymentCreateOrder(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _review_store[DUMMY_REVIEW_ID] = DUMMY_REVIEW

    def tearDown(self):
        _review_store.pop(DUMMY_REVIEW_ID, None)

    def test_create_order_disabled_returns_503(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", False):
            resp = self.client.post(
                "/payment/create-order",
                data=json.dumps({"review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_create_order_missing_review_id_returns_400(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True):
            resp = self.client.post(
                "/payment/create-order",
                data=json.dumps({}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_create_order_unknown_review_id_returns_400(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True):
            resp = self.client.post(
                "/payment/create-order",
                data=json.dumps({"review_id": "nonexistent-review-id"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_create_order_valid_review_id_returns_order(self):
        """Mock _create_razorpay_order to return a fake order."""
        mock_order = {
            "id": DUMMY_ORDER_ID,
            "amount": 5_000,
            "currency": "INR",
        }

        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", TEST_KEY_SECRET), \
             patch.object(app_module, "_create_razorpay_order", return_value=mock_order):
            resp = self.client.post(
                "/payment/create-order",
                data=json.dumps({"review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["order_id"], DUMMY_ORDER_ID)
        self.assertEqual(data["amount"], 5_000)
        self.assertEqual(data["currency"], "INR")
        self.assertEqual(data["review_id"], DUMMY_REVIEW_ID)
        self.assertIn("key_id", data)

    def test_create_order_razorpay_failure_returns_500(self):
        """If the Razorpay REST call throws, the endpoint should return 500."""
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", TEST_KEY_SECRET), \
             patch.object(
                 app_module,
                 "_create_razorpay_order",
                 side_effect=RuntimeError("Razorpay API unavailable"),
             ):
            resp = self.client.post(
                "/payment/create-order",
                data=json.dumps({"review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.data)
        self.assertIn("error", data)


# ---------------------------------------------------------------------------
# _create_razorpay_order (direct HTTP helper)
# ---------------------------------------------------------------------------

class TestCreateRazorpayOrderHelper(unittest.TestCase):
    """
    Unit-test the direct-HTTP helper to confirm the REST call is shaped
    correctly — Basic auth header, JSON body, POST method — before hitting
    Razorpay's live API.
    """

    def test_helper_posts_correct_request_and_parses_response(self):
        import base64 as _b64
        from unittest.mock import MagicMock
        import app as app_module

        fake_response_body = json.dumps({
            "id": "order_HELPERTEST",
            "amount": 5_000,
            "currency": "INR",
            "receipt": "review_abc",
        }).encode()

        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_response_body
        fake_resp.__enter__ = lambda self_: self_
        fake_resp.__exit__ = lambda *_args: False

        captured = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            captured["timeout"] = timeout
            return fake_resp

        with patch.object(app_module, "RAZORPAY_KEY_ID", "rzp_test_helperkey"), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", "helpersecret"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = app_module._create_razorpay_order(
                amount_paise=5_000,
                currency="INR",
                receipt="review_abc",
                notes={"review_id": "abc"},
            )

        self.assertEqual(result["id"], "order_HELPERTEST")
        self.assertEqual(captured["url"], "https://api.razorpay.com/v1/orders")
        self.assertEqual(captured["method"], "POST")

        # Headers come back title-cased from header_items()
        lc_headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(lc_headers["content-type"], "application/json")
        expected_auth = "Basic " + _b64.b64encode(
            b"rzp_test_helperkey:helpersecret"
        ).decode()
        self.assertEqual(lc_headers["authorization"], expected_auth)

        body = json.loads(captured["body"].decode())
        self.assertEqual(body["amount"], 5_000)
        self.assertEqual(body["currency"], "INR")
        self.assertEqual(body["receipt"], "review_abc")
        self.assertEqual(body["notes"], {"review_id": "abc"})

    def test_helper_raises_runtime_error_on_http_error(self):
        import urllib.error
        import app as app_module

        http_err = urllib.error.HTTPError(
            url="https://api.razorpay.com/v1/orders",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"description":"bad amount"}}'),
        )

        with patch.object(app_module, "RAZORPAY_KEY_ID", "rzp_test_x"), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", "s"), \
             patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(RuntimeError) as cm:
                app_module._create_razorpay_order(
                    amount_paise=5_000,
                    currency="INR",
                    receipt="r",
                    notes={},
                )

        self.assertIn("400", str(cm.exception))
        self.assertIn("bad amount", str(cm.exception))


# ---------------------------------------------------------------------------
# /payment/verify
# ---------------------------------------------------------------------------

class TestPaymentVerify(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _review_store[DUMMY_REVIEW_ID] = dict(DUMMY_REVIEW)

    def tearDown(self):
        _review_store.pop(DUMMY_REVIEW_ID, None)

    def _post_verify(self, payload, *, payment_enabled=True,
                     key_secret=TEST_KEY_SECRET):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", payment_enabled), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", key_secret):
            return self.client.post(
                "/payment/verify",
                data=json.dumps(payload),
                content_type="application/json",
            )

    def test_verify_disabled_returns_503(self):
        resp = self._post_verify({}, payment_enabled=False)
        self.assertEqual(resp.status_code, 503)

    def test_verify_missing_fields_returns_400(self):
        """Partial payload should be rejected."""
        resp = self._post_verify({
            "razorpay_order_id": DUMMY_ORDER_ID,
            # payment_id and signature missing
            "review_id": DUMMY_REVIEW_ID,
        })
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_verify_empty_body_returns_400(self):
        resp = self._post_verify({})
        self.assertEqual(resp.status_code, 400)

    def test_verify_bad_signature_returns_400(self):
        """A tampered or wrong signature should be rejected."""
        resp = self._post_verify({
            "razorpay_order_id":  DUMMY_ORDER_ID,
            "razorpay_payment_id": DUMMY_PAYMENT_ID,
            "razorpay_signature":  "deadbeef" * 8,   # invalid hex
            "review_id":           DUMMY_REVIEW_ID,
        })
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)
        self.assertIn("signature", data["error"].lower())

    def test_verify_valid_hmac_returns_verified_true(self):
        """Correct HMAC-SHA256 signature should succeed."""
        sig = _valid_signature(DUMMY_ORDER_ID, DUMMY_PAYMENT_ID, TEST_KEY_SECRET)
        resp = self._post_verify({
            "razorpay_order_id":  DUMMY_ORDER_ID,
            "razorpay_payment_id": DUMMY_PAYMENT_ID,
            "razorpay_signature":  sig,
            "review_id":           DUMMY_REVIEW_ID,
        })
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["verified"])
        self.assertEqual(data["review_id"], DUMMY_REVIEW_ID)

    def test_verify_marks_review_as_paid(self):
        """Successful verification should set payment_verified=True in review store."""
        sig = _valid_signature(DUMMY_ORDER_ID, DUMMY_PAYMENT_ID, TEST_KEY_SECRET)
        self._post_verify({
            "razorpay_order_id":  DUMMY_ORDER_ID,
            "razorpay_payment_id": DUMMY_PAYMENT_ID,
            "razorpay_signature":  sig,
            "review_id":           DUMMY_REVIEW_ID,
        })
        entry = _review_store.get(DUMMY_REVIEW_ID, {})
        self.assertTrue(entry.get("payment_verified"),
                        "review store not updated after successful verify")
        self.assertEqual(entry.get("payment_id"), DUMMY_PAYMENT_ID)

    def test_verify_wrong_secret_returns_400(self):
        """Signature computed with a different secret must be rejected."""
        sig = _valid_signature(DUMMY_ORDER_ID, DUMMY_PAYMENT_ID, "wrong_secret")
        resp = self._post_verify({
            "razorpay_order_id":  DUMMY_ORDER_ID,
            "razorpay_payment_id": DUMMY_PAYMENT_ID,
            "razorpay_signature":  sig,
            "review_id":           DUMMY_REVIEW_ID,
        }, key_secret=TEST_KEY_SECRET)  # server uses different secret
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# /payment/check-order (mobile UPI/GPay fallback)
# ---------------------------------------------------------------------------

class TestPaymentCheckOrder(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _review_store[DUMMY_REVIEW_ID] = dict(DUMMY_REVIEW)

    def tearDown(self):
        _review_store.pop(DUMMY_REVIEW_ID, None)

    def test_check_order_disabled_returns_503(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", False):
            resp = self.client.post(
                "/payment/check-order",
                data=json.dumps({"order_id": "order_abc", "review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)

    def test_check_order_missing_fields_returns_400(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True):
            resp = self.client.post(
                "/payment/check-order",
                data=json.dumps({}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_check_order_captured_payment_marks_review_paid(self):
        """When Razorpay returns a captured payment, mark review as paid."""
        from unittest.mock import MagicMock
        fake_body = json.dumps({
            "items": [{"id": "pay_TestCapture", "status": "captured"}],
            "count": 1,
        }).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: False

        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", TEST_KEY_SECRET), \
             patch("urllib.request.urlopen", return_value=fake_resp):
            resp = self.client.post(
                "/payment/check-order",
                data=json.dumps({"order_id": "order_abc", "review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["paid"])
        self.assertTrue(_review_store[DUMMY_REVIEW_ID].get("payment_verified"))

    def test_check_order_no_captured_payment_returns_not_paid(self):
        """When Razorpay returns no captured payments, paid should be false."""
        from unittest.mock import MagicMock
        fake_body = json.dumps({"items": [], "count": 0}).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = fake_body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: False

        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID), \
             patch.object(app_module, "RAZORPAY_KEY_SECRET", TEST_KEY_SECRET), \
             patch("urllib.request.urlopen", return_value=fake_resp):
            resp = self.client.post(
                "/payment/check-order",
                data=json.dumps({"order_id": "order_abc", "review_id": DUMMY_REVIEW_ID}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data["paid"])


# ---------------------------------------------------------------------------
# /payment/test
# ---------------------------------------------------------------------------

class TestPaymentTestPage(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_payment_test_page_returns_200(self):
        resp = self.client.get("/payment/test")
        self.assertEqual(resp.status_code, 200)

    def test_payment_test_page_is_html(self):
        resp = self.client.get("/payment/test")
        self.assertIn(b"<!DOCTYPE html>", resp.data)

    def test_payment_test_page_shows_not_configured_when_disabled(self):
        """When keys are absent the page should warn the user."""
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", False), \
             patch.object(app_module, "RAZORPAY_KEY_ID", ""):
            resp = self.client.get("/payment/test")
        self.assertIn(b"NOT configured", resp.data)

    def test_payment_test_page_shows_active_when_enabled(self):
        import app as app_module
        with patch.object(app_module, "PAYMENT_ENABLED", True), \
             patch.object(app_module, "RAZORPAY_KEY_ID", TEST_KEY_ID):
            resp = self.client.get("/payment/test")
        self.assertIn(b"active", resp.data)

    def test_payment_test_page_seeds_review_store(self):
        """The test page should add a dummy entry to _review_store."""
        before = set(_review_store.keys())
        self.client.get("/payment/test")
        after = set(_review_store.keys())
        new_entries = after - before
        self.assertEqual(len(new_entries), 1,
                         "Expected exactly one new review store entry from /payment/test")
        rid = next(iter(new_entries))
        self.assertTrue(rid.startswith("test-"))
        # Cleanup
        _review_store.pop(rid, None)

    def test_payment_test_page_shows_test_card_details(self):
        """Sandbox page should display Razorpay test card numbers."""
        resp = self.client.get("/payment/test")
        self.assertIn(b"4111", resp.data)             # Visa test card
        self.assertIn(b"success@razorpay", resp.data)  # UPI success

    def test_payment_test_page_shows_razorpay_flow_steps(self):
        """Page should explain the 4-step payment verification flow."""
        resp = self.client.get("/payment/test")
        self.assertIn(b"/payment/create-order", resp.data)
        self.assertIn(b"/payment/verify", resp.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
