import asyncio
import hashlib
import hmac
import json
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from conto_checkout import (
    ContoCheckoutError,
    ContoCheckoutGateway,
    StagedCart,
    _render_checkout,
    create_checkout_router,
    verify_stripe_signature,
)
from conto_control_plane import (
    ContoControlError,
    ContoSession,
    ContoSessionManager,
    ControlUpdate,
    CheckoutSpendLedger,
    HARD_WALLET_LIMITS,
    MemoryKeyValueStore,
    SecretBox,
    WALLET_LIMITS_VERSION,
    public_controls,
)
from demo_common.sessions import SessionConflictError
from mock_retail import MockRetail
from production_store import UpstashJsonClient, UpstashSessionCarts, UpstashSessionStore
from shopping_agent import ShoppingSessionState


class FakeJsonClient:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def compare_and_set(self, key, expected_version, value, ttl):
        del ttl
        current = json.loads(self.values[key])["version"] if key in self.values else 0
        if current != expected_version:
            return False
        self.values[key] = value
        return True

    def write_messages(self, key, start, messages, ttl):
        del ttl
        current = json.loads(self.values.get(key, "[]"))
        if start > 0 and len(current) != start:
            return False
        if start == 0:
            current = []
        current.extend(json.loads(messages))
        self.values[key] = json.dumps(current)
        return True

    def mutate_cart(self, key, operation, product_id, value, ttl):
        del ttl
        cart = json.loads(self.values.get(key, "{}"))
        if operation == "put":
            cart[product_id] = json.loads(value)
        elif operation == "quantity" and product_id in cart:
            cart[product_id]["quantity"] = int(value)
        elif operation == "remove":
            cart.pop(product_id, None)
        self.values[key] = json.dumps(cart)

    def consume_cart(self, cart_key, checkout_key, quantities, ttl):
        del ttl
        if checkout_key in self.values:
            return False
        cart = json.loads(self.values.get(cart_key, "{}"))
        for product_id, quantity in json.loads(quantities).items():
            if product_id not in cart:
                continue
            remaining = int(cart[product_id]["quantity"]) - int(quantity)
            if remaining > 0:
                cart[product_id]["quantity"] = remaining
            else:
                cart.pop(product_id, None)
        if cart:
            self.values[cart_key] = json.dumps(cart)
        else:
            self.values.pop(cart_key, None)
        self.values[checkout_key] = "1"
        return True

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


class FakeTransport:
    def __init__(self, decision="APPROVED"):
        self.decision = decision
        self.calls = []

    async def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/api/sdk/payments/request":
            return {
                "requestId": "pay_123",
                "approvalRequestId": "approval_123"
                if self.decision == "REQUIRES_APPROVAL"
                else None,
                "status": self.decision,
                "reasons": [],
            }
        if method == "GET":
            return {"requestId": "pay_123", "status": "approved"}
        return {
            "requestId": "pay_123",
            "status": "completed",
            "receipt": {"txHash": "0xabc"},
        }


class FakeSession:
    wallet_id = "wallet-1"


class FakeSessionManager:
    merchant_name = "ACME Retail"
    merchant_address = "0x1111111111111111111111111111111111111111"

    def __init__(self, decision="APPROVED", checkout_allowed=True):
        self.transport = FakeTransport(decision)
        self.checkouts = {}
        self.checkout_allowed = checkout_allowed
        self.checkout_spend = []
        self.purchase_records = []
        self.approval_decisions = []

    async def ensure(self, session_id):
        return FakeSession()

    def runtime_transport(self, session):
        return self.transport

    async def checkout_precheck(self, session_id, amount):
        return (
            self.checkout_allowed,
            [] if self.checkout_allowed else ["Daily limit exceeded"],
        )

    async def checkout_policy_checks(self, session_id, amount):
        del session_id
        checks = [
            {"key": "agent", "label": "Agent status", "status": "passed", "detail": "Active"},
            {
                "key": "merchant",
                "label": "Merchant",
                "status": "passed",
                "detail": "ACME Retail is allowed",
            },
            {
                "key": "per_purchase",
                "label": "Per-purchase limit",
                "status": "passed",
                "detail": f"${amount:,.2f} of $100.00",
            },
            {
                "key": "daily",
                "label": "Daily limit",
                "status": "passed" if self.checkout_allowed else "blocked",
                "detail": f"${amount:,.2f} of $500.00 after purchase",
            },
            {
                "key": "weekly",
                "label": "Weekly limit",
                "status": "passed",
                "detail": f"${amount:,.2f} of $2,000.00 after purchase",
            },
            {
                "key": "monthly",
                "label": "Monthly limit",
                "status": "passed",
                "detail": f"${amount:,.2f} of $5,000.00 after purchase",
            },
            {
                "key": "approval",
                "label": "Human approval",
                "status": "review"
                if self.transport.decision == "REQUIRES_APPROVAL"
                else "passed",
                "detail": (
                    f"${amount:,.2f} is above the $50.00 threshold"
                    if self.transport.decision == "REQUIRES_APPROVAL"
                    else f"${amount:,.2f} is within the $100.00 threshold"
                ),
            },
        ]
        if not self.checkout_allowed:
            checks[3]["reason"] = "Daily limit exceeded"
        return checks

    async def record_checkout_spend(
        self, session_id, checkout_id, amount, purchase=None
    ):
        if not any(entry[1] == checkout_id for entry in self.checkout_spend):
            self.checkout_spend.append((session_id, checkout_id, amount))
        if purchase and not any(
            entry.get("checkoutId") == checkout_id for entry in self.purchase_records
        ):
            self.purchase_records.append(json.loads(json.dumps(purchase)))

    async def completed_checkout_ids(self, session_id):
        return [
            checkout_id
            for recorded_session, checkout_id, _ in self.checkout_spend
            if recorded_session == session_id
        ]

    async def completed_purchase_records(self, session_id):
        del session_id
        return list(reversed(self.purchase_records))

    async def approval_request_for_payment(self, session_id, payment_request_id):
        return "approval_123"

    async def decide_demo_approval(
        self,
        session_id,
        approval_request_id,
        payment_request_id,
        checkout_id,
        cart_fingerprint,
        decision,
    ):
        if not cart_fingerprint.startswith(checkout_id):
            raise ContoControlError("The checkout binding is invalid")
        self.approval_decisions.append(
            (approval_request_id, payment_request_id, checkout_id, decision)
        )
        self.transport.decision = decision
        return {"finalStatus": decision}

    async def get_checkout(self, checkout_id):
        return self.checkouts.get(checkout_id)

    async def save_checkout(self, checkout_id, value):
        self.checkouts[checkout_id] = json.loads(json.dumps(value))

    @asynccontextmanager
    async def lock(self, namespace, identifier):
        yield


class FakeStripe:
    def __init__(self):
        self.created = []

    async def create(self, state):
        self.created.append(state.checkout_id)
        return {"id": "cs_test_123", "url": "https://checkout.stripe.com/test"}

    async def retrieve(self, session_id):
        return {}


def cart():
    return StagedCart(
        session_id="session-1",
        amount=54.97,
        currency="USD",
        item_labels=("Paper towels × 1", "Dish soap × 1"),
        merchant_name="ACME Retail",
        merchant_address="0x1111111111111111111111111111111111111111",
    )


class ProductionStoreTests(unittest.TestCase):
    def test_message_append_preserves_nested_empty_json_objects(self):
        self.assertNotIn("cjson.encode(current)", UpstashJsonClient._MESSAGES_SCRIPT)
        self.assertIn("string.sub(ARGV[2], 2, -2)", UpstashJsonClient._MESSAGES_SCRIPT)

    def test_session_and_transcript_survive_a_new_serverless_instance(self):
        client = FakeJsonClient()
        first = UpstashSessionStore(ShoppingSessionState, client)
        record = first.start("demo-user")
        record.pending_app_events.append("Cart updated")
        record.messages.append({"role": "user", "content": "Find a dog bed"})
        first.save(record)

        second = UpstashSessionStore(ShoppingSessionState, client)
        restored = second.require(record.session_id)

        self.assertEqual(restored.user_id, "demo-user")
        self.assertEqual(restored.pending_app_events, ["Cart updated"])
        self.assertEqual(restored.messages[0]["content"], "Find a dog bed")

    def test_session_compare_and_set_rejects_a_stale_write(self):
        client = FakeJsonClient()
        store = UpstashSessionStore(ShoppingSessionState, client)
        created = store.start("demo-user")

        first = store.require(created.session_id)
        stale = store.require(created.session_id)
        first.pending_app_events.append("First write")
        store.save(first)
        stale.pending_app_events.append("Stale write")

        with self.assertRaisesRegex(SessionConflictError, created.session_id):
            store.save(stale)

    def test_transcript_repairs_tool_input_flattened_by_lua_cjson(self):
        client = FakeJsonClient()
        store = UpstashSessionStore(ShoppingSessionState, client)
        session_id = "session-with-empty-tool-input"
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        transcript_key = f"anthropic-shopping:storefront-messages:{digest}"
        client.values[transcript_key] = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "get_cart",
                            "input": [],
                        },
                        {
                            "type": "tool_use",
                            "id": "tool-2",
                            "name": "search_products",
                            "input": {"query": "dog bed"},
                        },
                    ],
                }
            ]
        )

        messages = store.read_messages(session_id)

        self.assertEqual(messages[0]["content"][0]["input"], {})
        self.assertEqual(messages[0]["content"][1]["input"], {"query": "dog bed"})

    def test_cart_survives_a_new_serverless_instance(self):
        client = FakeJsonClient()
        product = MockRetail().product("AR-1501")
        self.assertIsNotNone(product)

        first = UpstashSessionCarts(client)
        first.put("session-1", product, 1)
        second = UpstashSessionCarts(client)

        self.assertEqual(second.cart("session-1").items[0].product_id, "AR-1501")
        self.assertEqual(second.set_quantity("session-1", "AR-1501", 2).items[0].quantity, 2)
        self.assertEqual(second.remove("session-1", "AR-1501").items, [])

    def test_cart_accepts_lua_cjson_empty_option_values_from_an_existing_record(self):
        client = FakeJsonClient()
        product = MockRetail().product("AR-1501")
        self.assertIsNotNone(product)

        carts = UpstashSessionCarts(client)
        carts.put("session-1", product, 1)
        cart_key = next(iter(client.values))
        encoded = json.loads(client.values[cart_key])
        encoded["AR-1501"]["option_values"] = []
        client.values[cart_key] = json.dumps(encoded)

        self.assertEqual(carts.cart("session-1").items[0].option_values, {})

    def test_cart_accepts_lua_cjson_empty_cart_from_an_existing_record(self):
        client = FakeJsonClient()
        session_id = "session-with-empty-cart"
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        cart_key = f"anthropic-shopping:storefront-cart:{digest}"
        client.values[cart_key] = "[]"

        self.assertEqual(UpstashSessionCarts(client).cart(session_id).items, [])

    def test_removing_the_final_cart_line_deletes_the_redis_key(self):
        script = UpstashJsonClient._CART_SCRIPT

        self.assertIn("if next(cart) == nil then", script)
        self.assertIn("redis.call('DEL', KEYS[1])", script)

    def test_completed_checkout_consumes_purchased_quantity_once(self):
        client = FakeJsonClient()
        product = MockRetail().product("AR-1501")
        self.assertIsNotNone(product)
        carts = UpstashSessionCarts(client)
        carts.put("session-1", product, 3)

        self.assertTrue(
            carts.consume_completed("session-1", "checkout-1", {"AR-1501": 2})
        )
        self.assertEqual(carts.cart("session-1").items[0].quantity, 1)
        self.assertFalse(
            carts.consume_completed("session-1", "checkout-1", {"AR-1501": 2})
        )
        self.assertEqual(carts.cart("session-1").items[0].quantity, 1)
        self.assertTrue(
            carts.consume_completed("session-1", "checkout-2", {"AR-1501": 1})
        )
        self.assertEqual(carts.cart("session-1").items, [])

        script = UpstashJsonClient._CART_CONSUME_SCRIPT
        self.assertIn("EXISTS', KEYS[2]", script)
        self.assertIn("remaining > 0", script)


class GatewayTests(unittest.TestCase):
    def test_ready_checkout_includes_one_safe_stripe_handoff(self):
        gateway = ContoCheckoutGateway(FakeSessionManager(), "https://shop.example")
        staged = asyncio.run(gateway.stage(cart()))

        page = _render_checkout(staged)

        self.assertIn("shopper@example.com", page)
        self.assertIn("111111", page)
        self.assertIn("4242 4242 4242 4242", page)
        self.assertIn("never real payment details", page)
        self.assertIn("Continue to secure checkout", page)
        self.assertNotIn("Stablecoin", page)

    def test_webhook_request_is_bound_as_the_http_request(self):
        router = create_checkout_router(
            ContoCheckoutGateway(FakeSessionManager(), "https://shop.example", FakeStripe())
        )
        route = next(
            item
            for item in router.routes
            if getattr(item, "path", "") == "/api/conto/stripe/webhook"
        )

        self.assertEqual(route.dependant.request_param_name, "request")
        self.assertEqual(route.dependant.query_params, [])

    def test_pending_approval_redirects_to_canonical_checkout_route(self):
        gateway = ContoCheckoutGateway(
            FakeSessionManager("REQUIRES_APPROVAL"),
            "https://shop.example",
            FakeStripe(),
        )
        staged = asyncio.run(gateway.stage(cart()))
        router = create_checkout_router(gateway)
        route = next(
            item
            for item in router.routes
            if getattr(item, "path", "") == "/api/conto/checkouts/{checkout_id}/checkout"
        )

        response = asyncio.run(
            route.endpoint(
                staged.checkout_id,
                csrf_token=staged.csrf_token,
            )
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], gateway.handoff_url(staged))

        pending = asyncio.run(gateway.get(staged.checkout_id))
        page = _render_checkout(pending)
        self.assertIn("Review required", page)
        self.assertIn("Approve USD 54.97", page)
        self.assertIn('name="decision" value="REJECTED"', page)
        self.assertIn("For this demo, you act as the approver", page)
        self.assertIn("No payment has opened", page)
        self.assertIn("Approval required:", page)
        self.assertNotIn("keeps the policy decision", page)
        self.assertNotIn("Open agent", page)

    def test_demo_owner_can_approve_pending_checkout_without_executing_payment(self):
        sessions = FakeSessionManager("REQUIRES_APPROVAL")
        gateway = ContoCheckoutGateway(sessions, "https://shop.example", FakeStripe())
        staged = asyncio.run(gateway.stage(cart()))
        pending = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        approved = asyncio.run(
            gateway.decide(pending.checkout_id, pending.csrf_token, "APPROVED")
        )

        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.approval_decision, "APPROVED")
        self.assertEqual(len(sessions.approval_decisions), 1)
        self.assertEqual(approved.checkout_id, approved.binding_fingerprint[:36])
        self.assertFalse(any(path.endswith("/execute") for _, path, _ in sessions.transport.calls))
        self.assertEqual(approved.stripe_session_url, "https://checkout.stripe.com/test")

    def test_demo_owner_can_deny_pending_checkout_without_opening_stripe(self):
        sessions = FakeSessionManager("REQUIRES_APPROVAL")
        stripe = FakeStripe()
        gateway = ContoCheckoutGateway(sessions, "https://shop.example", stripe)
        staged = asyncio.run(gateway.stage(cart()))
        pending = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        denied = asyncio.run(
            gateway.decide(pending.checkout_id, pending.csrf_token, "REJECTED")
        )

        self.assertEqual(denied.status, "DENIED")
        self.assertEqual(denied.approval_decision, "REJECTED")
        self.assertEqual(stripe.created, [])
        page = _render_checkout(denied)
        self.assertIn("Approval · Denied", page)
        self.assertIn("Payment · Not started", page)

    def test_stages_exact_cart_idempotently_without_requesting_payment(self):
        sessions = FakeSessionManager()
        gateway = ContoCheckoutGateway(sessions, "http://localhost:8000")
        first = asyncio.run(gateway.stage(cart()))
        second = asyncio.run(gateway.stage(cart()))

        self.assertEqual(first.checkout_id, second.checkout_id)
        self.assertEqual(first.status, "READY")
        self.assertEqual(sessions.transport.calls, [])
        self.assertTrue(gateway.handoff_url(first).startswith("http://localhost:8000/api/conto/"))

    def test_control_change_creates_a_fresh_decision_for_the_same_cart(self):
        sessions = FakeSessionManager()
        gateway = ContoCheckoutGateway(sessions, "https://shop.example")
        first = asyncio.run(gateway.stage(cart()))

        sessions.checkout_allowed = False
        second = asyncio.run(gateway.stage(cart()))

        self.assertNotEqual(first.checkout_id, second.checkout_id)
        self.assertEqual(
            next(check for check in second.policy_checks if check["key"] == "daily")["status"],
            "blocked",
        )

    def test_stripe_opens_only_after_conto_and_records_verified_test_payment(self):
        sessions = FakeSessionManager()
        stripe = FakeStripe()
        gateway = ContoCheckoutGateway(sessions, "https://shop.example", stripe)
        staged = asyncio.run(gateway.stage(cart()))
        approved = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(approved.stripe_session_id, "cs_test_123")
        self.assertEqual(stripe.created, [staged.checkout_id])

        reconciled = []

        async def reconcile(checkout_id, completed_cart):
            reconciled.append((checkout_id, completed_cart.session_id))

        completed = asyncio.run(gateway.complete_stripe(staged.checkout_id, {
            "id": "cs_test_123",
            "livemode": False,
            "payment_status": "paid",
            "amount_total": 5497,
            "currency": "usd",
            "payment_intent": "pi_test_123",
            "metadata": {"cart_fingerprint": staged.binding_fingerprint},
        }, on_complete=reconcile))
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.receipt["contoRequestId"], "pay_123")
        self.assertEqual(
            completed.receipt["orderId"], f"AS-{staged.checkout_id[:8].upper()}"
        )
        self.assertEqual(len(sessions.checkout_spend), 1)
        # A checkout completed before order-history support is reconstructed from
        # the existing Conto spend index, then persisted for subsequent reads.
        sessions.purchase_records.clear()
        purchases = asyncio.run(gateway.completed_purchase_records("session-1"))
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["orderId"], completed.receipt["orderId"])
        self.assertEqual(purchases[0]["total"], 54.97)
        # Stripe retries are idempotent for both spend and order history.
        asyncio.run(
            gateway.complete_stripe(
                staged.checkout_id,
                {
                    "id": "cs_test_123",
                    "livemode": False,
                    "payment_status": "paid",
                    "amount_total": 5497,
                    "currency": "usd",
                    "payment_intent": "pi_test_123",
                    "metadata": {"cart_fingerprint": staged.binding_fingerprint},
                },
                on_complete=reconcile,
            )
        )
        self.assertEqual(len(sessions.checkout_spend), 1)
        self.assertEqual(len(sessions.purchase_records), 1)
        self.assertEqual(
            reconciled,
            [
                (staged.checkout_id, "session-1"),
                (staged.checkout_id, "session-1"),
            ],
        )
        summary = gateway.decision_summary(completed)
        self.assertEqual(summary["outcome"], "complete")
        self.assertIsNone(summary["action"])
        payload = sessions.transport.calls[0][2]
        self.assertEqual(payload["amount"], 54.97)
        self.assertEqual(
            payload["context"]["project"], f"stripe:{staged.binding_fingerprint}"
        )
        self.assertEqual(payload["autoExecute"], False)
        self.assertFalse(any(path.endswith("/execute") for _, path, _ in sessions.transport.calls))

    def test_checkout_cumulative_precheck_fails_closed_before_conto_or_stripe(self):
        sessions = FakeSessionManager(checkout_allowed=False)
        stripe = FakeStripe()
        gateway = ContoCheckoutGateway(sessions, "https://shop.example", stripe)
        staged = asyncio.run(gateway.stage(cart()))
        denied = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        self.assertEqual(denied.status, "DENIED")
        self.assertEqual(denied.reasons, ("Daily limit exceeded",))
        self.assertEqual(
            next(check for check in denied.policy_checks if check["key"] == "daily")["status"],
            "blocked",
        )
        self.assertEqual(stripe.created, [])
        self.assertEqual(sessions.transport.calls, [])

    def test_decision_summary_exposes_all_checks_before_checkout(self):
        sessions = FakeSessionManager("REQUIRES_APPROVAL")
        gateway = ContoCheckoutGateway(sessions, "https://shop.example", FakeStripe())
        staged = asyncio.run(gateway.stage(cart()))
        pending = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        summary = gateway.decision_summary(pending)

        self.assertEqual(summary["outcome"], "review")
        self.assertEqual(
            [check["key"] for check in summary["checks"]],
            ["agent", "merchant", "per_purchase", "daily", "weekly", "monthly", "approval"],
        )
        self.assertEqual(summary["action"]["label"], "Review approval")
        self.assertIn("$54.97 is above the $50.00 threshold", summary["reasons"][0])

    def test_denied_payment_always_has_a_visible_reason(self):
        gateway = ContoCheckoutGateway(
            FakeSessionManager("DENIED"), "https://shop.example", FakeStripe()
        )
        staged = asyncio.run(gateway.stage(cart()))
        denied = asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))

        self.assertEqual(denied.status, "DENIED")
        self.assertEqual(
            denied.reasons,
            ("The final authorization was denied by the active payment controls.",),
        )
        summary = gateway.decision_summary(denied)
        self.assertEqual(summary["outcome"], "blocked")
        self.assertIsNone(summary["action"])
        self.assertEqual(summary["checks"][-1]["status"], "blocked")

    def test_rejects_invalid_confirmation_token(self):
        gateway = ContoCheckoutGateway(FakeSessionManager(), "http://localhost:8000")
        staged = asyncio.run(gateway.stage(cart()))
        with self.assertRaises(ContoCheckoutError):
            asyncio.run(gateway.authorize(staged.checkout_id, "wrong-token"))

    def test_rejects_mismatched_stripe_cart(self):
        gateway = ContoCheckoutGateway(FakeSessionManager(), "https://shop.example", FakeStripe())
        staged = asyncio.run(gateway.stage(cart()))
        asyncio.run(gateway.authorize(staged.checkout_id, staged.csrf_token))
        with self.assertRaises(ContoCheckoutError):
            asyncio.run(gateway.complete_stripe(staged.checkout_id, {
                "id": "cs_test_123", "livemode": False, "payment_status": "paid",
                "amount_total": 5497, "currency": "usd",
                "metadata": {"cart_fingerprint": "wrong"},
            }))


class ControlTests(unittest.TestCase):
    def test_existing_link_spend_migrates_to_checkout_spend(self):
        session = ContoSession.from_dict(
            {
                "session_id": "s",
                "agent_id": "a",
                "agent_name": "Anthropic Shopping",
                "wallet_id": "w",
                "encrypted_sdk_key": "secret",
                "policy_ids": {},
                "created_at": "now",
                "expires_at": "later",
                "link_spend": {"today": 20, "this_week": 30, "this_month": 40},
            }
        )

        self.assertEqual(session.checkout_spend.today, 20)
        self.assertEqual(session.checkout_spend.this_week, 30)
        self.assertEqual(session.checkout_spend.this_month, 40)

    def test_memory_store_provisioning_counter_has_a_ttl_window(self):
        store = MemoryKeyValueStore()
        self.assertEqual(asyncio.run(store.increment("client", 60)), 1)
        self.assertEqual(asyncio.run(store.increment("client", 60)), 2)

    def test_control_update_enforces_order_and_hard_ceiling(self):
        valid = ControlUpdate(1000, 50, 2500, 10000, 25000, True, True)
        valid.validate()
        with self.assertRaisesRegex(
            ContoControlError,
            "Approval threshold cannot exceed the per-purchase limit",
        ):
            ControlUpdate(100, 110, 500, 2000, 5000, True, True).validate()
        with self.assertRaises(ContoControlError):
            ControlUpdate(1001, 50, 2500, 10000, 25000, True, True).validate()
        with self.assertRaisesRegex(
            ContoControlError,
            r"Approval threshold cannot exceed the effective Conto ceiling of \$50.00",
        ):
            ControlUpdate(100, 75, 500, 2000, 5000, True, True).validate(
                {
                    "maxTransaction": 500,
                    "approvalThreshold": 50,
                    "dailyLimit": 1000,
                    "weeklyLimit": 5000,
                    "monthlyLimit": 20000,
                }
            )

    def test_existing_session_wallet_ceiling_is_upgraded_once(self):
        store = MemoryKeyValueStore()
        admin = FakeTransport()
        manager = ContoSessionManager(
            admin, store, SecretBox("test-secret"), wallet_id="wallet"
        )
        session = ContoSession(
            session_id="s",
            agent_id="agent",
            agent_name="Anthropic Shopping",
            wallet_id="wallet",
            encrypted_sdk_key="key",
            policy_ids={},
            created_at="now",
            expires_at="later",
        )
        asyncio.run(
            store.set(manager._session_key("s"), session.to_dict(), 60)
        )

        migrated = asyncio.run(manager.ensure("s"))
        again = asyncio.run(manager.ensure("s"))

        self.assertEqual(migrated.wallet_limits_version, WALLET_LIMITS_VERSION)
        self.assertEqual(again.wallet_limits_version, WALLET_LIMITS_VERSION)
        wallet_updates = [
            call for call in admin.calls
            if call[0] == "PATCH" and call[1] == "/api/agents/agent/wallets/wallet"
        ]
        self.assertEqual(len(wallet_updates), 1)
        self.assertEqual(
            wallet_updates[0][2],
            {
                "spendLimitPerTx": HARD_WALLET_LIMITS["maxTransaction"],
                "spendLimitDaily": HARD_WALLET_LIMITS["dailyLimit"],
                "spendLimitWeekly": HARD_WALLET_LIMITS["weeklyLimit"],
                "spendLimitMonthly": HARD_WALLET_LIMITS["monthlyLimit"],
            },
        )

    def test_legacy_wallet_upgrade_failure_keeps_stricter_session_available(self):
        class RejectingWalletTransport:
            def __init__(self):
                self.calls = []

            async def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                raise ContoControlError("Legacy wallet link cannot be changed")

        store = MemoryKeyValueStore()
        admin = RejectingWalletTransport()
        manager = ContoSessionManager(
            admin, store, SecretBox("test-secret"), wallet_id="wallet"
        )
        session = ContoSession(
            session_id="s",
            agent_id="agent",
            agent_name="Anthropic Shopping",
            wallet_id="wallet",
            encrypted_sdk_key="key",
            policy_ids={},
            created_at="now",
            expires_at="later",
        )
        asyncio.run(store.set(manager._session_key("s"), session.to_dict(), 60))

        migrated = asyncio.run(manager.ensure("s"))
        again = asyncio.run(manager.ensure("s"))

        self.assertEqual(migrated.wallet_limits_version, WALLET_LIMITS_VERSION)
        self.assertEqual(again.wallet_limits_version, WALLET_LIMITS_VERSION)
        self.assertEqual(len(admin.calls), 1)

    def test_public_demo_allows_a_small_shared_network_walkthrough(self):
        class ProvisioningSessionManager(ContoSessionManager):
            async def _provision(self, session_id):
                return ContoSession(
                    session_id=session_id,
                    agent_id="agent",
                    agent_name="Anthropic Shopping",
                    wallet_id="wallet",
                    encrypted_sdk_key="key",
                    policy_ids={},
                    created_at="now",
                    expires_at="later",
                )

        store = MemoryKeyValueStore()
        manager = ProvisioningSessionManager(
            FakeTransport(), store, SecretBox("test-secret"), wallet_id="wallet"
        )

        for index in range(20):
            asyncio.run(manager.ensure(f"session-{index}", "shared-ip"))
        with self.assertRaises(ContoControlError):
            asyncio.run(manager.ensure("session-over-limit", "shared-ip"))

        self.assertTrue(
            any(key.startswith("anthropic-shopping:provisioning:v4:") for key in store._counters)
        )

    def test_public_controls_reports_checkout_spend_without_wallet_transactions(self):
        session = ContoSession(
            session_id="s", agent_id="a", agent_name="Anthropic Shopping", wallet_id="w",
            encrypted_sdk_key="secret", policy_ids={}, created_at="now", expires_at="later",
            checkout_spend=CheckoutSpendLedger(today=20, this_week=30, this_month=40),
        )
        controls = public_controls(
            {"name": "Agent", "status": "ACTIVE"},
            {"policies": [
                {"name": "Spend", "rules": [
                    {"type": "MAX_AMOUNT", "value": 100},
                    {"type": "DAILY_LIMIT", "value": 500},
                    {"type": "WEEKLY_LIMIT", "value": 2000},
                    {"type": "MONTHLY_LIMIT", "value": 5000},
                ]},
                {"name": "Approval", "rules": [{"type": "REQUIRE_APPROVAL_ABOVE", "value": 50}]},
                {"name": "Merchants", "rules": [{"type": "ALLOWED_COUNTERPARTIES", "value": json.dumps(["0xmerchant"])}]},
            ]},
            {"wallets": [{"limits": {"perTransaction": 500, "daily": 1000, "weekly": 5000, "monthly": 20000}, "spent": {"today": 10, "thisWeek": 15, "thisMonth": 25}}]},
            {"counterparties": [{"name": "ACME Retail", "address": "0xmerchant", "approvalStatus": "APPROVED"}]},
            session=session, merchant_name="ACME Retail", merchant_address="0xmerchant",
        )
        self.assertEqual(controls["spend"]["spent"]["today"], 20)
        self.assertEqual(controls["spend"]["remaining"]["today"], 480)
        self.assertEqual(controls["checkout"]["label"], "Stripe Checkout")
        self.assertTrue(controls["merchants"]["current"]["allowed"])
        self.assertNotIn("address", controls["merchants"]["current"])

    def test_public_controls_reports_effective_parent_policy_ceiling(self):
        session = ContoSession(
            session_id="s",
            agent_id="a",
            agent_name="Anthropic Shopping",
            wallet_id="w",
            encrypted_sdk_key="secret",
            policy_ids={
                "spend": "session-spend",
                "approval": "session-approval",
                "merchant": "session-merchant",
            },
            created_at="now",
            expires_at="later",
        )
        policies = [
            {
                "id": "session-spend",
                "name": "Session spend",
                "rules": [
                    {"ruleType": "MAX_AMOUNT", "value": "100"},
                    {"ruleType": "DAILY_LIMIT", "value": "500"},
                    {"ruleType": "WEEKLY_LIMIT", "value": "2000"},
                    {"ruleType": "MONTHLY_LIMIT", "value": "5000"},
                ],
            },
            {
                "id": "session-approval",
                "name": "Session approval",
                "rules": [{"ruleType": "REQUIRE_APPROVAL_ABOVE", "value": "100"}],
            },
            {
                "id": "session-merchant",
                "name": "Session merchant",
                "rules": [
                    {
                        "ruleType": "ALLOWED_COUNTERPARTIES",
                        "value": json.dumps(["0xmerchant"]),
                    }
                ],
            },
            {
                "id": "organization-approval",
                "name": "Organization approval",
                "scope": "organization",
                "rules": [{"ruleType": "REQUIRE_APPROVAL_ABOVE", "value": "50"}],
            },
        ]

        controls = public_controls(
            {"name": "Agent", "status": "ACTIVE"},
            {"policies": policies},
            {
                "wallets": [
                    {
                        "limits": {
                            "perTransaction": 500,
                            "daily": 1000,
                            "weekly": 5000,
                            "monthly": 20000,
                        },
                        "spent": {},
                    }
                ]
            },
            {
                "counterparties": [
                    {
                        "name": "ACME Retail",
                        "address": "0xmerchant",
                        "approvalStatus": "APPROVED",
                    }
                ]
            },
            session=session,
            merchant_name="ACME Retail",
            merchant_address="0xmerchant",
        )

        self.assertEqual(controls["spend"]["approvalThreshold"], 50)
        self.assertEqual(controls["spend"]["controlCeilings"]["approvalThreshold"], 50)
        self.assertEqual(controls["spend"]["controlCeilings"]["maxTransaction"], 500)
        self.assertTrue(controls["merchants"]["current"]["allowed"])

    def test_stripe_signature_is_timestamped_and_verified(self):
        payload = b'{"type":"checkout.session.completed"}'
        timestamp = int(time.time())
        digest = hmac.new(b"whsec_test", str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
        verify_stripe_signature(payload, f"t={timestamp},v1={digest}", "whsec_test")
        with self.assertRaises(ContoCheckoutError):
            verify_stripe_signature(payload, f"t={timestamp},v1=bad", "whsec_test")


class PresentationTests(unittest.TestCase):
    def test_product_images_use_catalog_urls_and_fall_back_cleanly(self):
        product_tile = (
            Path(__file__).resolve().parents[1]
            / "overlay/examples/retail/storefront-web/components/ProductTile.tsx"
        ).read_text()

        self.assertNotIn("/demos/claude/", product_tile)
        self.assertIn('src={imageUrl}', product_tile)
        self.assertIn('onError={() => setFailedImageUrl(imageUrl)}', product_tile)

    def test_deployment_exposes_shopping_at_root_and_api_without_demo_prefix(self):
        demo = Path(__file__).resolve().parents[1]
        deployment = json.loads((demo / "deploy/vercel.json").read_text())
        import re
        def service_for(path):
            for rule in deployment["rewrites"]:
                if re.fullmatch(rule["source"], path):
                    return rule["destination"]["service"]
        self.assertFalse(deployment.get("redirects"))
        for path in ("/", "/products/tent.png", "/_next/static/app.js"):
            self.assertEqual(service_for(path), "storefront")
        for path in ("/api/health", "/api/conto/stripe/webhook", "/api/chat"):
            self.assertEqual(service_for(path), "backend")
        self.assertEqual(set(deployment["services"]), {"storefront", "backend"})

    def test_control_errors_are_specific_and_visible(self):
        overlay = Path(__file__).resolve().parents[1] / "overlay/examples/retail/storefront-web"
        controls = (overlay / "components/views/ControlsView.tsx").read_text()
        api = (overlay / "lib/api.ts").read_text()

        self.assertIn("Approval threshold must be at or below", controls)
        self.assertIn("Raise Per purchase first", controls)
        self.assertIn("Adjustable up to", controls)
        self.assertIn("candidate.detail", api)
        self.assertNotIn("Check the limit order and try again", controls)
        self.assertNotIn("Open agent", controls)

    def test_storefront_uses_conto_brand_system_without_decorative_gradients(self):
        overlay = Path(__file__).resolve().parents[1] / "overlay/examples/retail/storefront-web"
        styles = (overlay / "app/globals.css").read_text()
        layout = (overlay / "app/layout.tsx").read_text()
        page = (overlay / "app/page.tsx").read_text()
        home = (overlay / "components/views/HomeView.tsx").read_text()
        cart = (overlay / "components/CartPanel.tsx").read_text()
        mark = (overlay / "components/ContoMark.tsx").read_text()

        for token in ("#faf7f0", "#f5f1e8", "#0a0a0a", "#9cb8a8", "--radius: 4px"):
            self.assertIn(token, styles)
        self.assertNotIn("gradient", styles)
        self.assertNotIn("agent-orbit", home)
        self.assertIn('viewBox="-100 -100 200 200"', mark)
        self.assertIn("Conto · Anthropic Shopping", layout)
        self.assertIn(">Conto</span>", page)
        self.assertIn("Conto controls active", page)
        self.assertIn("Conto control layer", home)
        self.assertIn("spend within bounds", home)
        self.assertIn("Checked by Conto", cart)
        self.assertNotIn("Shopping agent ready", home)

    def test_shop_navigation_and_wordmark_start_a_fresh_chat(self):
        demo = Path(__file__).resolve().parents[1]
        page = (
            demo / "overlay/examples/retail/storefront-web/app/page.tsx"
        ).read_text()
        shell = (
            demo / "overlay/examples/web-shared/storefront/Shell.tsx"
        ).read_text()

        self.assertIn("window.location.reload()", page)
        self.assertIn('aria-label="Refresh shopping chat"', page)
        self.assertIn("onHomeClick={refreshChat}", page)
        self.assertIn("item.id === home && onHomeClick", shell)

    def test_demo_uses_neutral_shopper_identities(self):
        users_path = (
            Path(__file__).resolve().parents[1]
            / "overlay/examples/retail/data/users.json"
        )
        users = json.loads(users_path.read_text())["users"]

        self.assertEqual(users[0]["display_name"], "Demo shopper")
        self.assertEqual(users[1]["display_name"], "Guest shopper")
        self.assertNotIn("Priya", users_path.read_text())
        self.assertNotIn("Sam", users_path.read_text())

    def test_checkout_handoff_uses_shopper_facing_copy(self):
        main = (
            Path(__file__).resolve().parents[1]
            / "overlay/examples/retail/api/main.py"
        ).read_text()

        self.assertIn("state = await self.conto.authorize", main)
        self.assertIn('"DENIED": "Payment blocked"', main)
        self.assertNotIn("Choose Conto-governed checkout", main)

    def test_checkout_feed_is_policy_first_and_hides_developer_metrics(self):
        demo = Path(__file__).resolve().parents[1]
        checkout = (
            demo
            / "overlay/examples/retail/storefront-web/components/generative/CheckoutSummary.tsx"
        ).read_text()
        inspector = (
            demo / "overlay/examples/web-shared/Inspector.tsx"
        ).read_text()
        chat = (
            demo / "overlay/examples/retail/storefront-web/components/Chat.tsx"
        ).read_text()
        page = (
            demo / "overlay/examples/retail/storefront-web/app/page.tsx"
        ).read_text()
        cart_panel = (
            demo / "overlay/examples/retail/storefront-web/components/CartPanel.tsx"
        ).read_text()
        api = (
            demo / "overlay/examples/retail/storefront-web/lib/api.ts"
        ).read_text()
        main = (demo / "overlay/examples/retail/api/main.py").read_text()

        self.assertIn("Purchase checks", checkout)
        self.assertIn('"Reason" : "Reasons"', checkout)
        self.assertIn("Payment happens only after the checks above pass", checkout)
        self.assertIn("CHECKOUT_POLL_MS", checkout)
        self.assertIn("Purchase logged", checkout)
        self.assertIn("This card updates automatically", checkout)
        self.assertIn('cache: "no-store"', (
            demo / "overlay/examples/retail/storefront-web/lib/api.ts"
        ).read_text())
        self.assertIn('event.target.value', (
            demo
            / "overlay/examples/retail/storefront-web/components/views/ControlsView.tsx"
        ).read_text())
        self.assertNotIn('Number(event.target.value)', (
            demo
            / "overlay/examples/retail/storefront-web/components/views/ControlsView.tsx"
        ).read_text())
        self.assertNotIn("tokens", inspector)
        self.assertNotIn("Reply {turn}", inspector)
        self.assertNotIn("durationMs", inspector)
        self.assertIn('segment.block.component === "checkout"', chat)
        self.assertIn("segments.slice(0, checkoutIndex + 1)", chat)
        self.assertIn("suggestions: []", chat)
        self.assertIn("Order recorded", chat)
        self.assertIn("View in Orders", chat)
        self.assertIn("setCheckoutStaged(false)", page)
        self.assertIn('outcome === "blocked" || outcome === "complete"', page)
        self.assertIn("setOrdersRevision", page)
        self.assertIn("if (next) handleCartUpdate(next)", page)
        self.assertIn("onCartUpdate={handleCartUpdate}", page)
        self.assertIn("removeCartItem(item.product_id)", cart_panel)
        self.assertIn("updateCartItem(item.product_id, quantity)", cart_panel)
        self.assertIn("api.fetchCart<CartPayload>()", cart_panel)
        self.assertIn("do not reuse any previous checkout summary", cart_panel)
        self.assertIn('"/cart/quantity"', api)
        self.assertIn('"/cart/remove"', api)
        self.assertIn('@app.post("/api/cart/quantity")', main)
        self.assertIn('@app.post("/api/cart/remove")', main)
        self.assertNotIn("host.direct_add", main)
        self.assertIn("on_complete=backend.consume_completed_cart", main)
        self.assertIn("SHOPPER_SESSION_COOKIE", main)
        self.assertIn("httponly=True", main)
        self.assertIn('samesite="lax"', main)
        self.assertNotIn("Conto reference demo", page)
        self.assertNotIn("Conto policy", page)

    def test_order_tracking_stays_inside_the_demo(self):
        demo = Path(__file__).resolve().parents[1]
        tracking_panel = (
            demo
            / "overlay/examples/retail/storefront-web/components/OrderTrackingPanel.tsx"
        ).read_text()
        status_card = (
            demo
            / "overlay/examples/retail/storefront-web/components/generative/OrderStatusCard.tsx"
        ).read_text()
        page = (
            demo / "overlay/examples/retail/storefront-web/app/page.tsx"
        ).read_text()
        shared_orders = (
            demo / "overlay/examples/web-shared/storefront/orders.tsx"
        ).read_text()
        backend = (demo / "overlay/examples/retail/api/main.py").read_text()

        self.assertIn('role="dialog"', tracking_panel)
        self.assertIn("Delivery details", tracking_panel)
        self.assertIn("Available after shipment", tracking_panel)
        self.assertIn("Ask the agent about this delivery", tracking_panel)
        self.assertIn("openOrderTracking(order)", status_card)
        self.assertIn("View delivery details", status_card)
        self.assertNotIn('target="_blank"', status_card)
        self.assertNotIn("Track package", status_card)
        self.assertIn("ORDER_TRACKING_EVENT", page)
        self.assertIn("onOpenOrder={setTrackingOrder}", page)
        self.assertIn("onOpenOrder?: (order: Order) => void", shared_orders)
        self.assertIn("self._internal_tracking_order(order)", backend)
        self.assertIn('update={"tracking_url": None}', backend)


if __name__ == "__main__":
    unittest.main()
