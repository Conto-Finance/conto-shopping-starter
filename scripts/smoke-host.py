"""Verify the standalone shopping host without contacting external services."""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import time
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parents[1]
runtime = root / ".runtime" / "commerce-agents"
os.environ.update({
    "ANTHROPIC_API_KEY": "test-key", "CONTO_ORG_API_KEY": "conto_test",
    "CONTO_SHARED_WALLET_ID": "wallet_test", "CONTO_OWNER_MEMBERSHIP_ID": "owner_test",
    "SHOPPING_SESSION_ENCRYPTION_KEY": "test-only-session-secret",
    "STRIPE_TEST_SECRET_KEY": "sk_test_example", "SHOPPING_AGENT_ORIGIN": "https://shopping.example.com",
    "STRIPE_WEBHOOK_SECRET": "test-webhook-secret",
})
for key in ("VERCEL", "KV_REST_API_URL", "KV_REST_API_TOKEN"):
    os.environ.pop(key, None)
spec = importlib.util.spec_from_file_location("shopping_host", runtime / "vercel_backend.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload = json.dumps({"type": "unhandled.test.event"}).encode()
timestamp = str(int(time.time()))
signature = hmac.new(b"test-webhook-secret", timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
with TestClient(module.app, base_url="https://shopping.example.com") as client:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"Host": "unrelated.example.com"}).status_code == 400
    products = client.get("/api/products")
    assert products.status_code == 200 and products.json()
    assert client.post("/api/conto/stripe/webhook", content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}"}).status_code == 200
print("Shopping host: health, catalog, and webhook wiring OK")
