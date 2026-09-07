"""Check captured webhook configuration and rejection of invalid signatures."""
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from conto_checkout import ContoCheckoutGateway, create_checkout_router


class WebhookTests(unittest.TestCase):
    def test_endpoint_accepts_only_configured_signature_after_environment_changes(self):
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "correct-secret"}, clear=True), patch(
            "conto_checkout.ContoSessionManager.from_env", return_value=object()
        ):
            gateway = ContoCheckoutGateway.from_env()
        app = FastAPI()
        app.include_router(create_checkout_router(gateway))
        payload = json.dumps({"type": "unhandled.test.event"}).encode()
        timestamp = str(int(time.time()))
        with TestClient(app) as client:
            for secret, expected in (("correct-secret", 200), ("other-endpoint-secret", 400)):
                digest = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
                result = client.post("/api/conto/stripe/webhook", content=payload,
                    headers={"Stripe-Signature": f"t={timestamp},v1={digest}"})
                self.assertEqual(result.status_code, expected)

    def test_missing_secret_fails_closed(self):
        app = FastAPI()
        app.include_router(create_checkout_router(ContoCheckoutGateway(object(), "http://localhost")))
        with TestClient(app) as client:
            response = client.post("/api/conto/stripe/webhook", content=b"{}")
        self.assertEqual(response.status_code, 400)
