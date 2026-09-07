"""Conto-governed Stripe checkout for Anthropic's retail agent template."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Protocol

from starlette.requests import Request

try:  # Package import in the app; direct import in the small unit-test harness.
    from .conto_control_plane import ContoControlError, ContoSessionManager
except ImportError:  # pragma: no cover - exercised by scripts/test.sh
    from conto_control_plane import ContoControlError, ContoSessionManager


logger = logging.getLogger("anthropic_shopping.checkout")


class ContoCheckoutError(RuntimeError):
    """A customer-safe failure at the governed checkout boundary."""


@dataclass(frozen=True)
class StagedCart:
    session_id: str
    amount: float
    currency: str
    item_labels: tuple[str, ...]
    merchant_name: str
    merchant_address: str
    category: str = "MERCHANDISE"
    wallet_id: str | None = None
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        payload: dict[str, Any] = {
            "sessionId": self.session_id,
            "amount": round(self.amount, 2),
            "currency": self.currency.upper(),
            "items": self.item_labels,
            "merchant": self.merchant_address.lower(),
            "category": self.category,
        }
        # Keep pre-order-history checkout fingerprints stable while binding newly
        # staged checkouts to the exact product records used to create the order.
        if self.items:
            payload["orderItems"] = self.items
        canonical = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StagedCart":
        return cls(
            session_id=str(value["session_id"]),
            amount=float(value["amount"]),
            currency=str(value["currency"]),
            item_labels=tuple(str(item) for item in value["item_labels"]),
            merchant_name=str(value["merchant_name"]),
            merchant_address=str(value["merchant_address"]),
            category=str(value.get("category") or "MERCHANDISE"),
            wallet_id=str(value["wallet_id"]) if value.get("wallet_id") else None,
            items=tuple(
                {str(key): item_value for key, item_value in item.items()}
                for item in value.get("items", [])
                if isinstance(item, dict)
            ),
        )


CheckoutCompleteHandler = Callable[[str, StagedCart], Awaitable[None]]


def _checkout_binding_fingerprint(
    cart: StagedCart, policy_checks: tuple[dict[str, str], ...]
) -> str:
    """Bind a checkout to both the exact cart and the controls evaluated for it."""

    control_snapshot = json.dumps(
        policy_checks, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(
        f"v2:{cart.fingerprint}:{control_snapshot}".encode("utf-8")
    ).hexdigest()


@dataclass
class CheckoutState:
    checkout_id: str
    csrf_token: str
    cart: StagedCart
    binding_fingerprint: str
    status: str = "READY"
    request_id: str | None = None
    approval_request_id: str | None = None
    approval_decision: str | None = None
    approval_decided_at: str | None = None
    authorization_nonce: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    policy_checks: tuple[dict[str, str], ...] = field(default_factory=tuple)
    stripe_session_id: str | None = None
    stripe_session_url: str | None = None
    completed_at: str | None = None
    receipt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkout_id": self.checkout_id,
            "csrf_token": self.csrf_token,
            "cart": asdict(self.cart),
            "binding_fingerprint": self.binding_fingerprint,
            "status": self.status,
            "request_id": self.request_id,
            "approval_request_id": self.approval_request_id,
            "approval_decision": self.approval_decision,
            "approval_decided_at": self.approval_decided_at,
            "authorization_nonce": self.authorization_nonce,
            "reasons": list(self.reasons),
            "policy_checks": list(self.policy_checks),
            "stripe_session_id": self.stripe_session_id,
            "stripe_session_url": self.stripe_session_url,
            "completed_at": self.completed_at,
            "receipt": self.receipt,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CheckoutState":
        cart = StagedCart.from_dict(dict(value["cart"]))
        return cls(
            checkout_id=str(value["checkout_id"]),
            csrf_token=str(value["csrf_token"]),
            cart=cart,
            # Checkouts created before policy snapshots used the cart fingerprint
            # directly. Keeping that fallback preserves those original bindings.
            binding_fingerprint=str(
                value.get("binding_fingerprint") or cart.fingerprint
            ),
            status=str(value.get("status") or "READY"),
            request_id=str(value["request_id"]) if value.get("request_id") else None,
            approval_request_id=(
                str(value["approval_request_id"])
                if value.get("approval_request_id")
                else None
            ),
            approval_decision=(
                str(value["approval_decision"])
                if value.get("approval_decision")
                else None
            ),
            approval_decided_at=(
                str(value["approval_decided_at"])
                if value.get("approval_decided_at")
                else None
            ),
            authorization_nonce=(
                str(value["authorization_nonce"]) if value.get("authorization_nonce") else None
            ),
            reasons=tuple(str(reason) for reason in value.get("reasons", [])),
            policy_checks=tuple(
                {
                    str(key): str(item_value)
                    for key, item_value in check.items()
                }
                for check in value.get("policy_checks", [])
                if isinstance(check, dict)
            ),
            stripe_session_id=(
                str(value["stripe_session_id"]) if value.get("stripe_session_id") else None
            ),
            stripe_session_url=(
                str(value["stripe_session_url"]) if value.get("stripe_session_url") else None
            ),
            completed_at=(
                str(value["completed_at"]) if value.get("completed_at") else None
            ),
            receipt=value.get("receipt") if isinstance(value.get("receipt"), dict) else None,
        )


class SessionManager(Protocol):
    merchant_name: str
    merchant_address: str

    async def ensure(self, session_id: str): ...

    def runtime_transport(self, session): ...

    async def checkout_precheck(
        self, session_id: str, amount: float
    ) -> tuple[bool, list[str]]: ...

    async def checkout_policy_checks(
        self, session_id: str, amount: float
    ) -> list[dict[str, str]]: ...

    async def record_checkout_spend(
        self,
        session_id: str,
        checkout_id: str,
        amount: float,
        purchase: dict[str, Any] | None = None,
    ) -> None: ...

    async def completed_checkout_ids(self, session_id: str) -> list[str]: ...

    async def completed_purchase_records(
        self, session_id: str
    ) -> list[dict[str, Any]]: ...

    async def approval_request_for_payment(
        self, session_id: str, payment_request_id: str
    ) -> str | None: ...

    async def decide_demo_approval(
        self,
        session_id: str,
        approval_request_id: str,
        payment_request_id: str,
        checkout_id: str,
        cart_fingerprint: str,
        decision: str,
    ) -> dict[str, Any]: ...

    async def get_checkout(self, checkout_id: str) -> dict[str, Any] | None: ...

    async def save_checkout(self, checkout_id: str, value: dict[str, Any]) -> None: ...

    def lock(self, namespace: str, identifier: str): ...


class StripeCheckoutClient:
    """Stripe Checkout client restricted to a sandbox key and card payments."""

    def __init__(self, secret_key: str, public_origin: str) -> None:
        if not secret_key.startswith("sk_test_"):
            raise ValueError("STRIPE_TEST_SECRET_KEY must be a Stripe test-mode secret key")
        self._secret_key = secret_key
        self._origin = public_origin.rstrip("/")

    async def create(self, state: CheckoutState) -> dict[str, Any]:
        return await asyncio.to_thread(self._create_sync, state)

    def _create_sync(self, state: CheckoutState) -> dict[str, Any]:
        amount_minor = int(
            (Decimal(str(state.cart.amount)) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        success = (
            f"{self._origin}/api/conto/checkouts/{urllib.parse.quote(state.checkout_id, safe='')}"
            "?stripe=success&session_id={CHECKOUT_SESSION_ID}"
        )
        cancel = (
            f"{self._origin}/api/conto/checkouts/{urllib.parse.quote(state.checkout_id, safe='')}"
            "?stripe=cancel"
        )
        fields = [
            ("mode", "payment"),
            # Stripe Checkout requires `card` alongside `link`; Link remains the
            # accelerated checkout option presented to returning Link customers.
            ("payment_method_types[0]", "card"),
            ("payment_method_types[1]", "link"),
            ("line_items[0][price_data][currency]", state.cart.currency.lower()),
            ("line_items[0][price_data][unit_amount]", str(amount_minor)),
            ("line_items[0][price_data][product_data][name]", "ACME shopping order"),
            (
                "line_items[0][price_data][product_data][description]",
                ", ".join(state.cart.item_labels)[:500],
            ),
            ("line_items[0][quantity]", "1"),
            ("success_url", success),
            ("cancel_url", cancel),
            ("metadata[demo]", "anthropic-shopping"),
            ("metadata[checkout_id]", state.checkout_id),
            ("metadata[cart_fingerprint]", state.binding_fingerprint),
            ("metadata[conto_request_id]", state.request_id or ""),
        ]
        return self._request("POST", "/v1/checkout/sessions", fields)

    async def retrieve(self, session_id: str) -> dict[str, Any]:
        safe_id = urllib.parse.quote(session_id, safe="")
        return await asyncio.to_thread(
            self._request, "GET", f"/v1/checkout/sessions/{safe_id}", None
        )

    def _request(
        self, method: str, path: str, fields: list[tuple[str, str]] | None
    ) -> dict[str, Any]:
        body = urllib.parse.urlencode(fields).encode("utf-8") if fields else None
        request = urllib.request.Request(
            f"https://api.stripe.com{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
                message = payload.get("error", {}).get("message")
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            raise ContoCheckoutError(message or "Stripe checkout is unavailable") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ContoCheckoutError("Stripe checkout is unavailable") from error


class ContoCheckoutGateway:
    """Persisted Stripe checkout state machine that fails closed through Conto."""

    def __init__(
        self,
        sessions: SessionManager,
        public_origin: str,
        stripe: StripeCheckoutClient | None = None,
        *,
        webhook_secret: str = "",
    ) -> None:
        self.sessions = sessions
        self._public_origin = public_origin.rstrip("/")
        self._stripe = stripe
        self.webhook_secret = webhook_secret

    @classmethod
    def from_env(cls) -> "ContoCheckoutGateway":
        origin = os.getenv("SHOPPING_AGENT_ORIGIN", "").rstrip("/")
        sessions = ContoSessionManager.from_env()
        stripe_key = os.getenv("STRIPE_TEST_SECRET_KEY", "").strip()
        stripe = StripeCheckoutClient(stripe_key, origin) if stripe_key else None
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
        return cls(sessions, origin, stripe, webhook_secret=webhook_secret)

    async def stage(self, cart: StagedCart) -> CheckoutState:
        await self.sessions.ensure(cart.session_id)
        policy_checks = tuple(
            await self.sessions.checkout_policy_checks(cart.session_id, cart.amount)
        )
        binding_fingerprint = _checkout_binding_fingerprint(cart, policy_checks)
        checkout_id = binding_fingerprint[:36]
        existing = await self.sessions.get_checkout(checkout_id)
        if existing:
            return CheckoutState.from_dict(existing)
        state = CheckoutState(
            checkout_id=checkout_id,
            csrf_token=secrets.token_urlsafe(24),
            cart=cart,
            binding_fingerprint=binding_fingerprint,
            policy_checks=policy_checks,
        )
        await self._save(state)
        return state

    async def authorize(
        self, checkout_id: str, csrf_token: str, *, refresh_checks: bool = False
    ) -> CheckoutState:
        async with self.sessions.lock("checkout", checkout_id):
            state = await self.get(checkout_id)
            self._verify_csrf(state, csrf_token)
            if state.status == "COMPLETED":
                return state
            if state.request_id and state.status in {
                "APPROVED",
                "REQUIRES_APPROVAL",
                "PENDING",
                "ACTION_REQUIRED",
            }:
                if state.status != "APPROVED":
                    state = await self._refresh_unlocked(state)
                if state.status == "APPROVED":
                    state = await self._prepare_stripe_unlocked(state)
                return state

            state.status = "AUTHORIZING"
            state.request_id = None
            state.approval_request_id = None
            state.approval_decision = None
            state.approval_decided_at = None
            state.reasons = ()
            state.stripe_session_id = None
            state.stripe_session_url = None
            state.authorization_nonce = secrets.token_urlsafe(12)
            if refresh_checks or not state.policy_checks:
                state.policy_checks = tuple(
                    await self.sessions.checkout_policy_checks(
                        state.cart.session_id, state.cart.amount
                    )
                )
            await self._save(state)

            blocked_reasons = tuple(
                check["reason"]
                for check in state.policy_checks
                if check.get("status") == "blocked" and check.get("reason")
            )
            if blocked_reasons:
                state.status = "DENIED"
                state.reasons = blocked_reasons
                await self._save(state)
                return state

            session = await self.sessions.ensure(state.cart.session_id)
            transport = self.sessions.runtime_transport(session)
            payload: dict[str, Any] = {
                "amount": round(state.cart.amount, 2),
                "recipientAddress": state.cart.merchant_address,
                "recipientName": state.cart.merchant_name,
                "purpose": f"Stripe checkout shopping cart: {', '.join(state.cart.item_labels)}",
                "category": state.cart.category,
                "idempotencyKey": (
                    f"anthropic-shopping-{state.checkout_id[:24]}-"
                    f"stripe-{state.authorization_nonce}"
                )[:128],
                "autoExecute": False,
                "context": {
                    "department": "Anthropic Shopping",
                    "project": f"stripe:{state.binding_fingerprint}",
                },
            }
            if state.cart.wallet_id or getattr(session, "wallet_id", None):
                payload["walletId"] = state.cart.wallet_id or session.wallet_id
            try:
                result = await transport.request("POST", "/api/sdk/payments/request", payload)
            except ContoControlError as error:
                raise ContoCheckoutError(str(error)) from error
            request_id = result.get("requestId")
            if not isinstance(request_id, str) or not request_id:
                raise ContoCheckoutError("Conto did not return a payment request ID")
            state.request_id = request_id
            approval_request_id = result.get("approvalRequestId")
            state.approval_request_id = (
                str(approval_request_id)
                if isinstance(approval_request_id, str) and approval_request_id
                else None
            )
            state.status = self._normalize_status(result.get("status"))
            state.reasons = tuple(str(reason) for reason in result.get("reasons", []))
            self._ensure_decision_explanation(state)
            await self._save(state)

            if state.status == "APPROVED":
                state = await self._prepare_stripe_unlocked(state)
            return state

    async def decide(
        self,
        checkout_id: str,
        csrf_token: str,
        decision: str,
    ) -> CheckoutState:
        if decision not in {"APPROVED", "REJECTED"}:
            raise ContoCheckoutError("Choose approve or deny")
        async with self.sessions.lock("checkout", checkout_id):
            state = await self.get(checkout_id)
            self._verify_csrf(state, csrf_token)
            if state.approval_decision:
                if state.approval_decision != decision:
                    raise ContoCheckoutError("This request already has a different decision")
                return state
            if state.status not in {"REQUIRES_APPROVAL", "PENDING", "ACTION_REQUIRED"}:
                raise ContoCheckoutError("This checkout is not waiting for approval")
            if not state.request_id:
                raise ContoCheckoutError("This checkout does not have an approval request")

            approval_request_id = state.approval_request_id
            if not approval_request_id:
                approval_request_id = await self.sessions.approval_request_for_payment(
                    state.cart.session_id, state.request_id
                )
            if not approval_request_id:
                raise ContoCheckoutError("Conto could not find the pending approval request")

            try:
                result = await self.sessions.decide_demo_approval(
                    state.cart.session_id,
                    approval_request_id,
                    state.request_id,
                    state.checkout_id,
                    state.binding_fingerprint,
                    decision,
                )
            except ContoControlError as error:
                raise ContoCheckoutError(str(error)) from error

            final_status = self._normalize_status(result.get("finalStatus") or decision)
            if final_status not in {"APPROVED", "DENIED"}:
                raise ContoCheckoutError("Conto did not resolve the approval request")
            state.approval_request_id = approval_request_id
            state.approval_decision = decision
            state.approval_decided_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            state.status = final_status
            if final_status == "DENIED":
                state.reasons = ("Denied by the demo owner.",)
            await self._save(state)
            if final_status == "APPROVED":
                state = await self._prepare_stripe_unlocked(state)
            return state

    async def refresh(self, checkout_id: str) -> CheckoutState:
        async with self.sessions.lock("checkout", checkout_id):
            state = await self.get(checkout_id)
            return await self._refresh_unlocked(state)

    async def _refresh_unlocked(self, state: CheckoutState) -> CheckoutState:
        if not state.request_id:
            return state
        session = await self.sessions.ensure(state.cart.session_id)
        transport = self.sessions.runtime_transport(session)
        try:
            result = await transport.request(
                "GET", f"/api/sdk/payments/{urllib.parse.quote(state.request_id, safe='')}"
            )
        except ContoControlError as error:
            raise ContoCheckoutError(str(error)) from error
        state.status = self._normalize_status(result.get("status") or state.status)
        self._ensure_decision_explanation(state)
        await self._save(state)
        if state.status == "APPROVED":
            state = await self._prepare_stripe_unlocked(state)
        return state

    async def _prepare_stripe_unlocked(self, state: CheckoutState) -> CheckoutState:
        if state.stripe_session_id and state.stripe_session_url:
            return state
        if not self._stripe:
            raise ContoCheckoutError("Stripe checkout is not configured for this deployment")
        stripe_session = await self._stripe.create(state)
        session_id = stripe_session.get("id")
        url = stripe_session.get("url")
        if not isinstance(session_id, str) or not isinstance(url, str):
            raise ContoCheckoutError("Stripe did not return a hosted checkout")
        state.stripe_session_id = session_id
        state.stripe_session_url = url
        await self._save(state)
        return state

    async def complete_stripe(
        self,
        checkout_id: str,
        stripe_session: dict[str, Any],
        *,
        on_complete: CheckoutCompleteHandler | None = None,
    ) -> CheckoutState:
        async with self.sessions.lock("checkout", checkout_id):
            state = await self.get(checkout_id)
            if state.status not in {"APPROVED", "COMPLETED"}:
                raise ContoCheckoutError("This checkout is not authorized for payment")
            if stripe_session.get("id") != state.stripe_session_id:
                raise ContoCheckoutError("Stripe checkout does not match this cart")
            metadata = stripe_session.get("metadata")
            if (
                not isinstance(metadata, dict)
                or metadata.get("cart_fingerprint") != state.binding_fingerprint
            ):
                raise ContoCheckoutError("Stripe checkout cart binding is invalid")
            expected_minor = int(
                (Decimal(str(state.cart.amount)) * Decimal("100")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            if stripe_session.get("livemode") is not False:
                raise ContoCheckoutError("Only Stripe test-mode checkout is allowed")
            if stripe_session.get("payment_status") != "paid":
                raise ContoCheckoutError("Stripe payment is not complete")
            if int(stripe_session.get("amount_total") or -1) != expected_minor:
                raise ContoCheckoutError("Stripe amount does not match this cart")
            if str(stripe_session.get("currency") or "").upper() != state.cart.currency.upper():
                raise ContoCheckoutError("Stripe currency does not match this cart")
            changed = state.status != "COMPLETED"
            if changed:
                state.status = "COMPLETED"
                state.completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                state.receipt = {
                    "processor": "stripe",
                    "stripeSessionId": state.stripe_session_id,
                    "paymentIntent": stripe_session.get("payment_intent"),
                    "contoRequestId": state.request_id,
                    "settlementMode": "STRIPE_TEST",
                    "orderId": self._order_id(state.checkout_id),
                }
            await self.sessions.record_checkout_spend(
                state.cart.session_id,
                state.checkout_id,
                state.cart.amount,
                self._purchase_record(state),
            )
            if changed:
                await self._save(state)
            if on_complete:
                # The cart store deduplicates this by checkout id. Calling it for a
                # repeated Stripe event also lets a transient cleanup failure retry.
                await on_complete(state.checkout_id, state.cart)
            if changed:
                logger.info(
                    "checkout completed order_id=%s amount=%.2f currency=%s",
                    self._order_id(state.checkout_id),
                    state.cart.amount,
                    state.cart.currency.upper(),
                )
            return state

    async def completed_purchase_records(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """Return paid checkouts and backfill records created before order history."""

        records = await self.sessions.completed_purchase_records(session_id)
        recorded = {
            str(record.get("checkoutId"))
            for record in records
            if record.get("checkoutId")
        }
        for checkout_id in reversed(await self.sessions.completed_checkout_ids(session_id)):
            if checkout_id in recorded:
                continue
            try:
                state = await self.get(checkout_id)
            except ContoCheckoutError:
                continue
            if state.status != "COMPLETED":
                continue
            if not state.completed_at:
                state.completed_at = (
                    state.approval_decided_at
                    or datetime.now(UTC).isoformat().replace("+00:00", "Z")
                )
                await self._save(state)
            record = self._purchase_record(state)
            await self.sessions.record_checkout_spend(
                session_id, checkout_id, state.cart.amount, record
            )
            records.append(record)
            recorded.add(checkout_id)
        return sorted(
            records,
            key=lambda record: str(record.get("placedAt") or ""),
            reverse=True,
        )

    async def verify_stripe_return(
        self,
        checkout_id: str,
        stripe_session_id: str,
        *,
        on_complete: CheckoutCompleteHandler | None = None,
    ) -> CheckoutState:
        if not self._stripe:
            raise ContoCheckoutError("Stripe checkout is not configured for this deployment")
        state = await self.get(checkout_id)
        if state.stripe_session_id != stripe_session_id:
            raise ContoCheckoutError("Stripe checkout does not match this cart")
        stripe_session = await self._stripe.retrieve(stripe_session_id)
        return await self.complete_stripe(
            checkout_id, stripe_session, on_complete=on_complete
        )

    async def get(self, checkout_id: str) -> CheckoutState:
        value = await self.sessions.get_checkout(checkout_id)
        if not value:
            raise ContoCheckoutError("Checkout not found or expired")
        return CheckoutState.from_dict(value)

    async def _save(self, state: CheckoutState) -> None:
        await self.sessions.save_checkout(state.checkout_id, state.to_dict())

    @staticmethod
    def _verify_csrf(state: CheckoutState, csrf_token: str) -> None:
        if not secrets.compare_digest(state.csrf_token, csrf_token):
            raise ContoCheckoutError("The checkout confirmation token is invalid")

    @staticmethod
    def _normalize_status(value: Any) -> str:
        normalized = str(value or "DENIED").upper()
        return {
            "REVIEW_REQUIRED": "REQUIRES_APPROVAL",
            "PENDING_APPROVAL": "REQUIRES_APPROVAL",
            "DECLINED": "DENIED",
            "REJECTED": "DENIED",
            "READY_TO_SEND": "APPROVED",
            "COMPLETED": "COMPLETED",
        }.get(normalized, normalized)

    def handoff_url(self, state: CheckoutState) -> str:
        return f"{self._public_origin}/api/conto/checkouts/{urllib.parse.quote(state.checkout_id, safe='')}"

    @staticmethod
    def _order_id(checkout_id: str) -> str:
        return f"AS-{checkout_id[:8].upper()}"

    def _purchase_record(self, state: CheckoutState) -> dict[str, Any]:
        return {
            "checkoutId": state.checkout_id,
            "orderId": self._order_id(state.checkout_id),
            "placedAt": state.completed_at
            or state.approval_decided_at
            or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "items": [dict(item) for item in state.cart.items],
            "itemLabels": list(state.cart.item_labels),
            "total": round(state.cart.amount, 2),
            "currency": state.cart.currency.upper(),
        }

    def decision_summary(self, state: CheckoutState) -> dict[str, Any]:
        if state.status == "APPROVED" and state.stripe_session_url:
            outcome = "approved"
            action = {
                "url": state.stripe_session_url,
                "label": "Continue to secure checkout",
            }
        elif state.status in {"REQUIRES_APPROVAL", "PENDING", "ACTION_REQUIRED"}:
            outcome = "review"
            action = {"url": self.handoff_url(state), "label": "Review approval"}
        elif state.status == "DENIED":
            outcome = "blocked"
            action = None
        elif state.status == "COMPLETED":
            outcome = "complete"
            action = None
        else:
            outcome = "checking"
            action = {"url": self.handoff_url(state), "label": "Review checkout"}
        return {
            "status": state.status,
            "outcome": outcome,
            "checks": list(state.policy_checks),
            "reasons": list(state.reasons),
            "action": action,
        }

    @staticmethod
    def _ensure_decision_explanation(state: CheckoutState) -> None:
        if state.status in {"REQUIRES_APPROVAL", "PENDING", "ACTION_REQUIRED"}:
            if not state.reasons:
                review = next(
                    (
                        check.get("detail")
                        for check in state.policy_checks
                        if check.get("status") == "review"
                    ),
                    None,
                )
                state.reasons = (
                    f"Approval required: {review}." if review else "Human approval is required.",
                )
            return
        if state.status != "DENIED":
            return
        if not state.reasons:
            state.reasons = (
                "The final authorization was denied by the active payment controls.",
            )
        if not any(check.get("status") == "blocked" for check in state.policy_checks):
            state.policy_checks = (
                *state.policy_checks,
                {
                    "key": "authorization",
                    "label": "Final authorization",
                    "status": "blocked",
                    "detail": state.reasons[0],
                    "reason": state.reasons[0],
                },
            )


def verify_stripe_signature(payload: bytes, signature: str, secret: str, tolerance: int = 300) -> None:
    if not secret:
        raise ContoCheckoutError("Stripe webhook verification is not configured")
    parts: dict[str, list[str]] = {}
    for entry in signature.split(","):
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError) as error:
        raise ContoCheckoutError("Stripe webhook signature is invalid") from error
    if abs(int(time.time()) - timestamp) > tolerance:
        raise ContoCheckoutError("Stripe webhook signature has expired")
    signed = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
        raise ContoCheckoutError("Stripe webhook signature is invalid")


def create_checkout_router(
    gateway: ContoCheckoutGateway,
    *,
    on_complete: CheckoutCompleteHandler | None = None,
):
    from fastapi import APIRouter, Form
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    router = APIRouter()

    @router.get("/api/conto/checkouts/{checkout_id}", response_class=HTMLResponse)
    async def show_checkout(
        checkout_id: str,
        refresh: bool = False,
        stripe: str | None = None,
        session_id: str | None = None,
    ) -> HTMLResponse:
        try:
            if stripe == "success" and session_id:
                state = await gateway.verify_stripe_return(
                    checkout_id, session_id, on_complete=on_complete
                )
            else:
                state = await gateway.refresh(checkout_id) if refresh else await gateway.get(checkout_id)
            return HTMLResponse(_render_checkout(state, stripe_cancelled=stripe == "cancel"))
        except ContoCheckoutError as error:
            return HTMLResponse(_render_error(str(error)), status_code=409)

    @router.get("/api/conto/checkouts/{checkout_id}/summary")
    async def checkout_summary(checkout_id: str, request: Request):
        try:
            state = await gateway.get(checkout_id)
            session_id = request.headers.get("x-session-id", "")
            if not session_id or not secrets.compare_digest(
                session_id, state.cart.session_id
            ):
                return JSONResponse({"error": "Checkout not found"}, status_code=404)
            if state.status == "COMPLETED" and on_complete:
                # Repairs checkouts completed before the shopper returned to this tab.
                await on_complete(state.checkout_id, state.cart)
            return JSONResponse(
                gateway.decision_summary(state),
                headers={"Cache-Control": "no-store"},
            )
        except ContoCheckoutError as error:
            return JSONResponse({"error": str(error)}, status_code=409)

    @router.post("/api/conto/checkouts/{checkout_id}/checkout")
    async def continue_to_checkout(
        checkout_id: str,
        csrf_token: str = Form(...),
    ):
        try:
            state = await gateway.authorize(
                checkout_id, csrf_token, refresh_checks=True
            )
            if state.status == "APPROVED" and state.stripe_session_url:
                return RedirectResponse(state.stripe_session_url, status_code=303)
            # Keep the browser on the canonical GET route while approval is pending.
            # Otherwise relative actions such as `?refresh=true` resolve against
            # this POST-only `/checkout` route and fail with HTTP 405.
            return RedirectResponse(gateway.handoff_url(state), status_code=303)
        except ContoCheckoutError as error:
            return HTMLResponse(_render_error(str(error)), status_code=409)

    @router.post("/api/conto/checkouts/{checkout_id}/decision")
    async def decide_checkout(
        checkout_id: str,
        decision: str = Form(...),
        csrf_token: str = Form(...),
    ):
        try:
            state = await gateway.decide(checkout_id, csrf_token, decision.upper())
            if state.status == "APPROVED" and state.stripe_session_url:
                return RedirectResponse(state.stripe_session_url, status_code=303)
            return RedirectResponse(gateway.handoff_url(state), status_code=303)
        except ContoCheckoutError as error:
            return HTMLResponse(_render_error(str(error)), status_code=409)

    @router.post("/api/conto/stripe/webhook")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        try:
            verify_stripe_signature(
                payload,
                request.headers.get("Stripe-Signature", ""),
                gateway.webhook_secret,
            )
            event = json.loads(payload)
            if event.get("type") in {
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            }:
                stripe_session = event.get("data", {}).get("object", {})
                metadata = stripe_session.get("metadata") if isinstance(stripe_session, dict) else {}
                checkout_id = metadata.get("checkout_id") if isinstance(metadata, dict) else None
                if isinstance(checkout_id, str) and checkout_id:
                    await gateway.complete_stripe(
                        checkout_id, stripe_session, on_complete=on_complete
                    )
            return JSONResponse({"received": True})
        except (ContoCheckoutError, json.JSONDecodeError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    return router


def _render_checkout(state: CheckoutState, *, stripe_cancelled: bool = False) -> str:
    amount = f"{state.cart.currency.upper()} {state.cart.amount:,.2f}"
    items = "".join(f"<li>{html.escape(label)}</li>" for label in state.cart.item_labels)
    reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in state.reasons)
    merchant = html.escape(state.cart.merchant_name)
    checkout_id = html.escape(state.checkout_id)
    csrf = html.escape(state.csrf_token)
    approval_heading = (
        "Approval complete"
        if state.approval_decision == "APPROVED"
        else "Payment approved"
    )
    if state.status == "READY" or (stripe_cancelled and state.status == "APPROVED"):
        notice = (
            '<p class="notice">Stripe checkout was cancelled. No test payment was recorded.</p>'
            if stripe_cancelled
            else ""
        )
        content = f"""
        <p class="eyebrow">Payment</p>
        <h1>Ready for secure checkout</h1>
        <p>{merchant} · {amount}</p><ul>{items}</ul>{notice}
        <p class="guard">Purchase controls are checked before Stripe opens.</p>
        <form method="post" action="/api/conto/checkouts/{checkout_id}/checkout" class="checkout-card">
            <input type="hidden" name="csrf_token" value="{csrf}">
            <strong>Stripe Checkout</strong><span>Test card entry with Link available for accelerated checkout.</span>
            <div class="test-details" aria-label="Stripe sandbox test details">
              <b>Test details</b>
              <p>Use fictional data only—never real payment details.</p>
              <dl>
                <dt>Email</dt><dd><code>shopper@example.com</code></dd>
                <dt>Link code</dt><dd><code>111111</code></dd>
                <dt>Card</dt><dd><code>4242 4242 4242 4242</code></dd>
                <dt>Expiry · CVC · ZIP</dt><dd><code>12/34 · 123 · 12345</code></dd>
              </dl>
            </div>
            <button type="submit">Continue to secure checkout</button>
        </form>"""
    elif state.status == "APPROVED":
        safe_url = html.escape(state.stripe_session_url or "#", quote=True)
        content = f"""<p class="eyebrow">Payment</p><h1>{approval_heading}</h1>
        <p>{merchant} · {amount}</p><a class="button" href="{safe_url}">Open secure checkout</a>"""
    elif state.status == "REQUIRES_APPROVAL":
        content = f"""<p class="eyebrow">Payment</p>
        <h1>Review required</h1><p>{merchant} · {amount}</p>
        <ul class="reasons">{reasons}</ul>
        <p class="guard">Approving continues to Stripe; denying stops the payment. No payment has opened.</p>
        <div class="activity" aria-label="Checkout activity">
          <strong>Checkout activity</strong>
          <p><span class="activity-dot done"></span>Purchase controls · Review required</p>
          <p><span class="activity-dot pending"></span>Approval · Waiting for decision</p>
          <p><span class="activity-dot"></span>Payment · Not started</p>
        </div>
        <div class="decision-actions">
          <form method="post" action="/api/conto/checkouts/{checkout_id}/decision">
            <input type="hidden" name="csrf_token" value="{csrf}">
            <input type="hidden" name="decision" value="APPROVED">
            <button type="submit">Approve {amount}</button>
          </form>
          <form method="post" action="/api/conto/checkouts/{checkout_id}/decision">
            <input type="hidden" name="csrf_token" value="{csrf}">
            <input type="hidden" name="decision" value="REJECTED">
            <button type="submit" class="secondary">Deny</button>
          </form>
        </div>
        <p class="demo-note">For this demo, you act as the approver.</p>"""
    elif state.status in {"COMPLETED", "PROCESSING", "CONFIRMING"}:
        reference = (state.receipt or {}).get("stripeSessionId")
        ref = (
            f'<p class="reference">Reference {html.escape(str(reference))}</p>'
            if reference
            else ""
        )
        content = f"""<p class="eyebrow">Payment</p><h1>Purchase submitted</h1>
        <p>{merchant} · {amount} · Stripe test mode</p>{ref}<p class="guard">The cart was authorized before payment.</p>"""
    else:
        decision_activity = (
            "<p><span class=\"activity-dot done\"></span>Approval · Denied</p>"
            if state.approval_decision == "REJECTED"
            else "<p><span class=\"activity-dot\"></span>No owner approval recorded</p>"
        )
        content = f"""<p class="eyebrow">Payment</p>
        <h1>Payment blocked</h1><p>{merchant} · {amount}</p><ul class="reasons">{reasons}</ul>
        <div class="activity" aria-label="Checkout activity">
          <strong>Checkout activity</strong>
          <p><span class="activity-dot done"></span>Purchase controls · Blocked</p>
          {decision_activity}
          <p><span class="activity-dot"></span>Payment · Not started</p>
        </div>
        <p>No funds moved. Return to the cart to start a new checkout.</p>
        <form method="post" action="/api/conto/checkouts/{checkout_id}/checkout">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <button type="submit">Re-check controls</button>
        </form>"""
    return _page(content)


def _render_error(message: str) -> str:
    return _page(
        f"<h1>Checkout unavailable</h1><p>{html.escape(message)}</p><p>No funds moved.</p>"
    )


def _page(body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width"><title>Secure checkout</title>
    <style>
    :root {{ color-scheme: light; }} * {{ box-sizing: border-box; }}
    body {{ font: 16px system-ui, sans-serif; background: #f4f2ec; color: #181713; margin: 0; padding: 48px 20px; }}
    main {{ max-width: 620px; margin: auto; background: white; border: 1px solid #ddd9cf; border-radius: 20px; padding: 32px; box-shadow: 0 12px 40px #0000000d; }}
    .eyebrow {{ font-size: 12px; text-transform: uppercase; letter-spacing: .12em; color: #6b675f; }}
    h1 {{ font-size: 32px; letter-spacing: -.04em; margin: 8px 0 12px; }} li {{ margin: 7px 0; }}
    .guard, .notice {{ border-radius: 10px; background: #f5f3ee; padding: 12px; color: #555047; font-size: 14px; }}
    .notice {{ background: #fff4d8; color: #725600; }} .reasons {{ color: #794f00; }}
    .checkout-card {{ display: flex; flex-direction: column; align-items: flex-start; gap: 8px; border: 1px solid #ddd9cf; border-radius: 14px; padding: 16px; margin-top: 18px; }}
    .checkout-card strong {{ font-size: 19px; }} .checkout-card > span {{ color: #6b675f; font-size: 13px; }}
    .test-details {{ width: 100%; border-radius: 10px; background: #f5f3ee; padding: 12px; font-size: 12px; }}
    .test-details b {{ font-size: 13px; }} .test-details p {{ color: #725600; margin: 4px 0 9px; }}
    .test-details dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 5px 10px; margin: 0; }}
    .test-details dt {{ color: #6b675f; }} .test-details dd {{ margin: 0; overflow-wrap: anywhere; }}
    code {{ font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; }}
    button, .button {{ display: inline-block; background: #181713; color: white; padding: 12px 17px; border: 0; border-radius: 9px; text-decoration: none; font-weight: 650; cursor: pointer; margin-top: 8px; }}
    button.secondary {{ background: white; color: #181713; border: 1px solid #bcb7ab; }}
    .decision-actions {{ display: flex; gap: 10px; align-items: center; margin-top: 14px; }}
    .decision-actions form {{ margin: 0; }}
    .activity {{ border: 1px solid #ddd9cf; border-radius: 12px; padding: 14px; margin: 16px 0; }}
    .activity > strong {{ display: block; margin-bottom: 9px; font-size: 13px; }}
    .activity p {{ display: flex; align-items: center; gap: 8px; margin: 7px 0; color: #6b675f; font-size: 13px; }}
    .activity-dot {{ width: 8px; height: 8px; border: 1px solid #9f9a8f; border-radius: 999px; flex: none; }}
    .activity-dot.done {{ border-color: #256b4b; background: #256b4b; }}
    .activity-dot.pending {{ border-color: #b27819; background: #f4c870; }}
    .demo-note {{ color: #6b675f; font-size: 12px; line-height: 1.5; }}
    .reference {{ overflow-wrap: anywhere; font: 12px ui-monospace, monospace; color: #6b675f; }}
    @media (max-width: 520px) {{ main {{ padding: 24px; }} }}
    </style></head><body><main>{body}</main></body></html>"""
