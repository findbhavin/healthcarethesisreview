# Payment Gateway — Razorpay Integration

## Overview

The Healthcare Thesis Review platform uses **Razorpay** as its payment gateway. Payment is **entirely optional** — a user can always download the PDF report for free. If the gateway is not configured (environment variables absent), the payment modal is skipped automatically and the download proceeds immediately.

### Why Razorpay?

| Criterion | Detail |
|-----------|--------|
| Open-source SDKs | Apache 2.0 licensed Python client (`razorpay==1.4.2`) |
| INR support | Native INR, ideal for Indian academic publishers |
| International | Accepts credit/debit cards (Visa, Mastercard, Amex), UPI, wallets, net banking across 100+ currencies |
| Sandbox | Full test mode with no real money movement |
| Security | HMAC-SHA256 payment signature verification; PCI-DSS compliant |

---

## Environment Variables

Set these before starting the application:

```bash
# Required for payment to be enabled
export RAZORPAY_KEY_ID="rzp_test_xxxxxxxxxxxx"      # from Razorpay Dashboard → API Keys
export RAZORPAY_KEY_SECRET="your_secret_key_here"   # keep this secret — never expose in JS

# Required for AI reviews (unrelated to payment)
export ANTHROPIC_API_KEY="sk-ant-..."
```

If either `RAZORPAY_KEY_ID` or `RAZORPAY_KEY_SECRET` is missing or empty, `PAYMENT_ENABLED` is set to `False` and the platform silently falls back to free downloads.

### Current Payment Amount

```python
PAYMENT_AMOUNT_PAISE = 10_000  # ₹100 per document (100 INR = 10,000 paise)
PAYMENT_CURRENCY     = "INR"
```

To change the price, edit these constants in `app.py`.

---

## Payment Flow

```
Browser                          Flask Server                    Razorpay API
  │                                   │                               │
  │─── GET /payment/config ──────────►│                               │
  │◄── {enabled, key_id, amount} ─────│                               │
  │                                   │                               │
  │  [User clicks Download PDF]       │                               │
  │  [Payment modal shown if enabled] │                               │
  │                                   │                               │
  │─── POST /payment/create-order ───►│                               │
  │    {review_id}                    │──── order.create({}) ────────►│
  │                                   │◄─── {id, amount, currency} ───│
  │◄── {order_id, key_id, amount} ────│                               │
  │                                   │                               │
  │  [Razorpay JS Checkout popup]     │                               │
  │  [User enters card/UPI details]   │                               │
  │──────────────────────────────────────── payment authorized ──────►│
  │◄── {payment_id, signature} ───────────────────────────────────────│
  │                                   │                               │
  │─── POST /payment/verify ─────────►│                               │
  │    {order_id, payment_id,         │  HMAC-SHA256 verify           │
  │     signature, review_id}         │  (no network call needed)     │
  │◄── {verified: true} ──────────────│                               │
  │                                   │                               │
  │─── GET /download/<review_id> ────►│                               │
  │◄── PDF file ──────────────────────│                               │
```

### Step-by-step Explanation

1. **Config fetch** — On page load the browser calls `GET /payment/config`. If `enabled` is `false`, every download bypasses the payment modal entirely.

2. **Create order** — When the user clicks the download button and payment is enabled, the browser posts `{review_id}` to `POST /payment/create-order`. The server calls `razorpay.Client.order.create()` on Razorpay's servers, which returns a unique `order_id`. This order is locked to the exact amount (₹100) and can only be paid once.

3. **Razorpay Checkout popup** — The browser loads Razorpay's official JS (`https://checkout.razorpay.com/v1/checkout.js`) and opens the hosted payment form. The user enters their payment details. Razorpay handles all PCI-sensitive data — the application never sees raw card numbers.

4. **Payment captured** — Razorpay processes the payment and returns three fields to the browser's `handler` callback:
   - `razorpay_order_id` — the same order ID from step 2
   - `razorpay_payment_id` — a unique payment ID for this transaction
   - `razorpay_signature` — HMAC-SHA256 signature for verification

5. **Signature verification** — The browser posts all three fields plus `review_id` to `POST /payment/verify`. The server re-computes the expected HMAC:

   ```python
   expected = hmac.new(
       RAZORPAY_KEY_SECRET.encode(),
       f"{order_id}|{payment_id}".encode(),
       hashlib.sha256,
   ).hexdigest()
   ```

   If `hmac.compare_digest(expected, signature)` passes, the review is marked `payment_verified = True` in the in-memory store. **No Razorpay API call is made in this step** — verification is purely local using the shared secret.

6. **Download** — Once verified (or if the user chooses "Skip & download free"), the browser fetches `GET /download/<review_id>` which streams the PDF.

---

## API Endpoints

### `GET /payment/config`

Returns public payment configuration. Called once on page load.

**Response**
```json
{
  "enabled": true,
  "key_id": "rzp_test_xxxxxxxxxxxx",
  "amount": 10000,
  "currency": "INR",
  "description": "Peer Review Report Download — ₹100 per document",
  "amount_display": "₹100"
}
```

When `enabled` is `false`, `key_id` is an empty string.

---

### `POST /payment/create-order`

Creates a Razorpay payment order. Requires a valid `review_id` that exists in the server's review store.

**Request body**
```json
{ "review_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Success response (200)**
```json
{
  "order_id": "order_XXXXXXXXXXXXXXXXXX",
  "amount": 10000,
  "currency": "INR",
  "key_id": "rzp_test_xxxxxxxxxxxx",
  "review_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Error responses**
| Status | Reason |
|--------|--------|
| 400 | Invalid or expired `review_id` |
| 503 | Payment gateway not configured |
| 500 | Razorpay API call failed |

---

### `POST /payment/verify`

Verifies the HMAC-SHA256 signature returned by Razorpay after a successful payment. Marks the review as paid.

**Request body**
```json
{
  "razorpay_order_id":  "order_XXXXXXXXXXXXXXXXXX",
  "razorpay_payment_id": "pay_XXXXXXXXXXXXXXXXXX",
  "razorpay_signature":  "abc123...",
  "review_id":           "550e8400-e29b-41d4-a716-446655440000"
}
```

**Success response (200)**
```json
{ "verified": true, "review_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Error responses**
| Status | Reason |
|--------|--------|
| 400 | Missing fields |
| 400 | Signature mismatch — tampered or replayed request |
| 503 | Payment gateway not configured |

---

## Optional Payment — User Choice

The payment is entirely at the user's discretion. The UI presents two options in the payment modal:

- **Pay ₹100 and Download** — Initiates the Razorpay checkout flow.
- **Skip & Download Free** — Calls `/download/<review_id>` directly, bypassing payment entirely.

No feature is gated behind payment; the same full PDF is delivered in both paths. The payment option exists to support the platform financially but is never forced.

---

## Security Notes

- `RAZORPAY_KEY_SECRET` is **only used server-side**. It is never sent to the browser.
- `RAZORPAY_KEY_ID` is public-safe (it appears in the browser for the Checkout popup).
- Signature verification uses `hmac.compare_digest` (constant-time comparison) to prevent timing attacks.
- Razorpay orders are single-use — an `order_id` cannot be charged twice.
- The server validates that the `review_id` exists before creating an order, preventing spurious charges.

---

## Testing in Sandbox Mode

1. Log into [https://dashboard.razorpay.com](https://dashboard.razorpay.com) and switch to **Test Mode**.
2. Go to **Settings → API Keys → Generate Test Key**.
3. Set the test keys as environment variables (`rzp_test_...` prefix).
4. Use Razorpay's [test card numbers](https://razorpay.com/docs/payments/payments/test-payment/) — no real money is moved.

```bash
# Test card (always succeeds)
Card Number : 4111 1111 1111 1111
Expiry      : any future date
CVV         : any 3 digits
OTP         : 1234
```

---

## Going Live Checklist

- [ ] Switch to live Razorpay keys (`rzp_live_...`) from the Dashboard.
- [ ] Set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` in your production environment (Cloud Run secrets / `.env`).
- [ ] Enable **Webhook** in Razorpay Dashboard → Webhooks → `payment.captured` event pointing to your server, for reliable payment confirmation in multi-instance deployments.
- [ ] Replace the in-memory `_review_store` in `app.py` with **Cloud Firestore** or **Cloud Storage** for stateful payment tracking across Cloud Run instances.
- [ ] Review Razorpay's [KYC requirements](https://razorpay.com/docs/onboarding/) for live payouts.
- [ ] Confirm GST applicability if required by your jurisdiction.
