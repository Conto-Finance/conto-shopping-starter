#!/usr/bin/env bash
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$DEMO_ROOT/.runtime/commerce-agents"

if [[ ! -x "$RUNTIME_ROOT/.venv/bin/python" ]]; then
  echo "Run ./scripts/bootstrap.sh first" >&2
  exit 1
fi

if [[ -x "$RUNTIME_ROOT/.venv/bin/python" ]]; then
  PYTHON="$RUNTIME_ROOT/.venv/bin/python"
elif [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3)"
fi

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
PYTHONPATH="$DEMO_ROOT/overlay/examples/retail/api:$RUNTIME_ROOT/examples/retail/api:$RUNTIME_ROOT/examples:$RUNTIME_ROOT/shopping-agent/core:$RUNTIME_ROOT/commerce-common" \
  "$PYTHON" -m unittest discover -s "$DEMO_ROOT/tests" -p 'test_*.py' -v

if [[ -d "$RUNTIME_ROOT/.venv" ]]; then
  PYTHONPATH="$DEMO_ROOT/overlay/examples/retail/api:$RUNTIME_ROOT/shopping-agent/core:$RUNTIME_ROOT/commerce-common" \
    "$RUNTIME_ROOT/.venv/bin/python" - <<'PY'
from shopping_agent import Cart, CartItem, CheckoutHandoff, ShoppingSessionContext
from conto_checkout import StagedCart, create_checkout_router

cart = Cart(items=[CartItem(product_id="sku-1", title="Daypack", price=54.97, quantity=1)])
session = ShoppingSessionContext(session_id="s-1", user_id="u-1")
staged = StagedCart(
    session_id=session.session_id,
    amount=cart.subtotal,
    currency=cart.currency,
    item_labels=tuple(f"{item.title} x {item.quantity}" for item in cart.items),
    merchant_name="ACME Retail",
    merchant_address="0x1111111111111111111111111111111111111111",
)
assert staged.amount == 54.97
assert CheckoutHandoff(url="http://localhost:8000/checkout").url.endswith("/checkout")
print("Pinned upstream checkout contracts: OK")
PY

  (
    cd "$RUNTIME_ROOT"
    ANTHROPIC_API_KEY=test-key \
    CONTO_ORG_API_KEY=conto_test \
    CONTO_SHARED_WALLET_ID=wallet_test \
    SHOPPING_SESSION_ENCRYPTION_KEY=test-only-session-secret \
    STRIPE_TEST_SECRET_KEY=sk_test_example \
    CONTO_MERCHANT_ADDRESS=0x1111111111111111111111111111111111111111 \
    PYTHONPATH='examples:shopping-agent/core:shopping-agent/runtime-messages-api:commerce-common:merchant-agent/core:merchant-agent/runtime-messages-api' \
      .venv/bin/python - <<'PY'
import os
import asyncio
from unittest.mock import patch

from fastapi.testclient import TestClient
from retail.api.main import app, backend, memory_seed_marker_path, memory_store_path
from shopping_agent import ShoppingSessionContext

paths = set()
for route in app.routes:
    path = getattr(route, "path", None)
    if path:
        paths.add(path)
    original_router = getattr(route, "original_router", None)
    if original_router:
        prefix = getattr(route.include_context, "prefix", "")
        paths.update(prefix + child.path for child in original_router.routes)

assert backend.__class__.__name__ == "AnthropicShoppingRetail"
assert "/api/conto/checkouts/{checkout_id}" in paths
assert "/api/conto/checkouts/{checkout_id}/checkout" in paths
assert "/api/conto/checkouts/{checkout_id}/execute" not in paths
assert "/api/conto/checkouts/{checkout_id}/rail" not in paths
assert "/api/conto/stripe/webhook" in paths
assert "/api/conto/controls" in paths
assert "/api/cart/quantity" in paths
assert "/api/cart/remove" in paths
assert sum(
    getattr(route, "path", None) == "/api/session"
    and "POST" in (getattr(route, "methods", set()) or set())
    for route in app.routes
) == 1

with TestClient(app, base_url="http://localhost") as client:
    first_session = client.post("/api/session").json()["session_id"]
    second_session = client.post("/api/session").json()["session_id"]
    assert first_session == second_session
    cookie = client.cookies.get("anthropic_shopping_session")
    assert cookie == first_session

    headers = {"X-Session-Id": first_session}
    context = ShoppingSessionContext(session_id=first_session, user_id="demo-user")
    asyncio.run(backend.add_to_cart(context, "AR-1501", 1))
    quantity = client.post(
        "/api/cart/quantity",
        json={"product_id": "AR-1501", "quantity": 2},
        headers=headers,
    )
    assert quantity.status_code == 200
    assert quantity.json()["cart"]["items"][0]["quantity"] == 2
    removed = client.post(
        "/api/cart/remove",
        json={"product_id": "AR-1501"},
        headers=headers,
    )
    assert removed.status_code == 200
    assert removed.json()["cart"]["items"] == []
    assert client.get("/api/cart", headers=headers).json()["items"] == []

async def completed_purchase_records(_session_id):
    return [{
        "checkoutId": "12345678abcdef",
        "orderId": "AS-12345678",
        "placedAt": "2026-09-03T12:00:00Z",
        "items": [{
            "product_id": "demo-sku",
            "title": "Demo item",
            "quantity": 2,
            "price": 17.0,
        }],
        "itemLabels": ["Demo item x 2"],
        "total": 34.0,
        "currency": "USD",
    }]

backend.conto.completed_purchase_records = completed_purchase_records
orders = asyncio.run(backend.get_orders(
    ShoppingSessionContext(session_id="order-history-test", user_id="unknown-user"),
    limit=5,
))
assert orders[0].order_id == "AS-12345678"
assert orders[0].items[0].quantity == 2
assert orders[0].status == "processing"

async def no_completed_purchase_records(_session_id):
    return []

backend.conto.completed_purchase_records = no_completed_purchase_records
fixture_context = ShoppingSessionContext(
    session_id="tracking-fixture-test", user_id="demo-user"
)
fixture_orders = asyncio.run(backend.get_orders(fixture_context, limit=10))
assert any(order.order_id == "AR-78214" for order in fixture_orders)
assert all(order.tracking_url is None for order in fixture_orders)
fixture_order = asyncio.run(backend.get_order(fixture_context, "AR-78214"))
assert fixture_order is not None
assert fixture_order.tracking_url is None

async def completed_cart_records(session_id):
    if session_id != "completed-cart-test":
        return await completed_purchase_records(session_id)
    return [{
        "checkoutId": "completed-cart-checkout",
        "orderId": "AS-COMPLETED",
        "placedAt": "2026-09-03T12:00:00Z",
        "items": [{
            "product_id": "AR-1501",
            "title": "ACME Playroom Stacking Wooden Block Set (54 pc)",
            "quantity": 1,
            "price": 34.0,
        }],
        "itemLabels": ["ACME Playroom Stacking Wooden Block Set (54 pc) x 1"],
        "total": 34.0,
        "currency": "USD",
    }]

backend.conto.completed_purchase_records = completed_cart_records
completed_context = ShoppingSessionContext(
    session_id="completed-cart-test", user_id="demo-user"
)
asyncio.run(backend.add_to_cart(completed_context, "AR-1501", 1))
assert asyncio.run(backend.get_cart(completed_context)).items == []
# Re-adding the same SKU after purchase must survive later cart reads.
asyncio.run(backend.add_to_cart(completed_context, "AR-1501", 1))
assert asyncio.run(backend.get_cart(completed_context)).items[0].quantity == 1
with patch.dict(os.environ, {"VERCEL": "1"}, clear=False):
    assert str(memory_store_path()) == "/tmp/anthropic-shopping-memory-store.json"
    assert str(memory_seed_marker_path()) == "/tmp/anthropic-shopping-memory-seeded.json"
print("Patched Anthropic retail API and Conto routes: OK")
PY
  )
fi

# Verify the standalone shopping host and its webhook configuration.
"$DEMO_ROOT/scripts/prepare-deploy.sh" >/dev/null
"$RUNTIME_ROOT/.venv/bin/python" "$DEMO_ROOT/scripts/smoke-host.py"

node --test "$DEMO_ROOT/tests/test_checkout_url.mjs"
