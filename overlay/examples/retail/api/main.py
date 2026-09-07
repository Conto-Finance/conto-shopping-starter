# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Anthropic's retail demo host with a Conto-governed checkout handoff."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import demo_common.storefront as storefront_module
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from commerce_common.memory import InMemoryMemoryStore, JsonFileMemoryStore
from demo_common import (
    REPO_ROOT,
    CartAddRequest,
    MemorySeeder,
    build_storefront_host,
    load_demo_env,
)
from demo_common.sessions import UnknownSessionError
from shopping_agent import CheckoutHandoff, Order, ProductDetails
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .conto_checkout import (
    ContoCheckoutError,
    ContoCheckoutGateway,
    StagedCart,
    create_checkout_router,
)
from .conto_control_plane import ContoControlError, ControlUpdate
from .merchant import create_merchant_router
from .mock_retail import DATA_DIR, MockRetail
from .production_store import UpstashSessionCarts, UpstashSessionStore

load_demo_env(DATA_DIR.parent)
PRODUCT_IMAGES = DATA_DIR.parent / "storefront-web" / "public" / "products"
logger = logging.getLogger("anthropic_shopping.controls")
SHOPPER_SESSION_COOKIE = "anthropic_shopping_session"
SHOPPER_SESSION_MAX_AGE = 2 * 60 * 60


def memory_store_path() -> Path:
    configured = os.getenv("SHOPPING_MEMORY_STORE_PATH", "").strip()
    if configured:
        return Path(configured)
    if os.getenv("VERCEL"):
        return Path("/tmp/anthropic-shopping-memory-store.json")
    return DATA_DIR / ".memory-store.json"


def memory_seed_marker_path() -> Path:
    configured = os.getenv("SHOPPING_MEMORY_SEED_MARKER_PATH", "").strip()
    if configured:
        return Path(configured)
    if os.getenv("VERCEL"):
        return Path("/tmp/anthropic-shopping-memory-seeded.json")
    return DATA_DIR / ".memory-seeded.json"


class AnthropicShoppingRetail(MockRetail):
    """The upstream retail backend with controls evaluated before payment handoff."""

    def __init__(self) -> None:
        super().__init__()
        self._locally_reconciled_checkouts: set[str] = set()
        if os.getenv("VERCEL"):
            self._carts = UpstashSessionCarts()
        self.conto = ContoCheckoutGateway.from_env()

    @staticmethod
    def _purchased_quantities(items: tuple[dict[str, Any], ...] | list[Any]) -> dict[str, int]:
        quantities: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("product_id") or "").strip()
            try:
                quantity = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if product_id and quantity > 0:
                quantities[product_id] = quantities.get(product_id, 0) + quantity
        return quantities

    async def consume_completed_cart(
        self, checkout_id: str, cart: StagedCart
    ) -> None:
        quantities = self._purchased_quantities(cart.items)
        if not quantities:
            return
        consume = getattr(self._carts, "consume_completed", None)
        if callable(consume):
            changed = bool(consume(cart.session_id, checkout_id, quantities))
        else:
            if checkout_id in self._locally_reconciled_checkouts:
                return
            for product_id, purchased in quantities.items():
                existing = self._carts.lines(cart.session_id).get(product_id)
                if existing is None:
                    continue
                remaining = existing.quantity - purchased
                if remaining > 0:
                    self._carts.set_quantity(cart.session_id, product_id, remaining)
                else:
                    self._carts.remove(cart.session_id, product_id)
            self._locally_reconciled_checkouts.add(checkout_id)
            changed = True
        if changed:
            remaining = self._carts.cart(cart.session_id)
            logger.info(
                "completed checkout cart reconciled checkout_id=%s remaining_items=%s",
                checkout_id,
                sum(item.quantity for item in remaining.items),
            )

    async def _reconcile_completed_purchases(self, session_id: str) -> None:
        try:
            purchases = await self.conto.completed_purchase_records(session_id)
        except (ContoCheckoutError, ContoControlError) as error:
            logger.warning("completed cart reconciliation deferred: %s", error)
            return
        for purchase in purchases:
            checkout_id = str(purchase.get("checkoutId") or "").strip()
            if not checkout_id:
                continue
            staged = StagedCart(
                session_id=session_id,
                amount=float(purchase.get("total") or 0),
                currency=str(purchase.get("currency") or "USD"),
                item_labels=tuple(str(label) for label in purchase.get("itemLabels", [])),
                merchant_name="ACME Retail",
                merchant_address=self.conto.sessions.merchant_address_for(session_id),
                items=tuple(
                    dict(item)
                    for item in purchase.get("items", [])
                    if isinstance(item, dict)
                ),
            )
            await self.consume_completed_cart(checkout_id, staged)

    async def get_cart(self, session):
        # A read repairs carts from a completion that arrived while the tab was away.
        await self._reconcile_completed_purchases(session.session_id)
        return await super().get_cart(session)

    async def checkout_handoff(self, session, cart):
        state = await self.conto.stage(
            StagedCart(
                session_id=session.session_id,
                amount=cart.subtotal,
                currency=cart.currency,
                item_labels=tuple(
                    f"{item.title} x {item.quantity}" for item in cart.items
                ),
                merchant_name="ACME Retail",
                merchant_address=self.conto.sessions.merchant_address_for(
                    session.session_id
                ),
                category="MERCHANDISE",
                wallet_id=os.getenv("CONTO_WALLET_ID"),
                items=tuple(
                    item.model_dump(mode="json", exclude_none=True)
                    for item in cart.items
                ),
            )
        )
        state = await self.conto.authorize(state.checkout_id, state.csrf_token)
        label = {
            "APPROVED": "Continue to secure checkout",
            "REQUIRES_APPROVAL": "Review approval",
            "PENDING": "Review approval",
            "ACTION_REQUIRED": "Review approval",
            "DENIED": "Payment blocked",
        }.get(state.status, "Review checkout")
        return [
            CheckoutHandoff(
                url=self.conto.handoff_url(state),
                label=label,
            )
        ]

    def _purchase_order(self, record: dict[str, Any]) -> Order:
        items = [
            {
                "product_id": str(item.get("product_id") or ""),
                "title": str(item.get("title") or "Purchased item"),
                "quantity": max(1, int(item.get("quantity") or 1)),
                "price": float(item.get("price") or 0),
                "option_values": dict(item.get("option_values") or {}),
                "variant_of": item.get("variant_of"),
            }
            for item in record.get("items", [])
            if isinstance(item, dict) and item.get("product_id")
        ]
        if not items:
            catalog = [*self.products.values(), *self.variants.values()]
            for index, label in enumerate(record.get("itemLabels", [])):
                title, separator, quantity_text = str(label).rpartition(" x ")
                if not separator:
                    title, separator, quantity_text = str(label).rpartition(" × ")
                title = title if separator else str(label)
                try:
                    quantity = max(1, int(quantity_text)) if separator else 1
                except ValueError:
                    quantity = 1
                product = next(
                    (candidate for candidate in catalog if candidate.title == title),
                    None,
                )
                items.append(
                    {
                        "product_id": (
                            product.product_id if product else f"purchase-{index + 1}"
                        ),
                        "title": title or "Purchased item",
                        "quantity": quantity,
                        "price": (
                            product.price
                            if product
                            else float(record.get("total") or 0) / quantity
                        ),
                        "option_values": dict(product.option_values) if product else {},
                        "variant_of": product.variant_of if product else None,
                    }
                )
        if not items:
            items = [
                {
                    "product_id": "completed-purchase",
                    "title": "Completed purchase",
                    "quantity": 1,
                    "price": float(record.get("total") or 0),
                }
            ]
        placed_at = str(record.get("placedAt") or datetime.now(UTC).isoformat())
        try:
            placed = datetime.fromisoformat(placed_at.replace("Z", "+00:00"))
        except ValueError:
            placed = datetime.now(UTC)
        return Order.model_validate(
            {
                "order_id": str(record.get("orderId") or "AS-PURCHASE"),
                "status": "processing",
                "placed_at": placed,
                "items": items,
                "total": round(float(record.get("total") or 0), 2),
                "currency": str(record.get("currency") or "USD").upper(),
                "estimated_delivery": (placed + timedelta(days=4)).date().isoformat(),
                "tracking_url": None,
            }
        )

    @staticmethod
    def _internal_tracking_order(order: Order) -> Order:
        """Keep fulfillment inside the demo instead of exposing fixture URLs."""
        return order.model_copy(update={"tracking_url": None})

    async def get_orders(self, session, limit: int = 5) -> list[Order]:
        purchases = await self.conto.completed_purchase_records(session.session_id)
        completed = [self._purchase_order(record) for record in purchases]
        fixtures = [
            self._internal_tracking_order(order)
            for order in await super().get_orders(session, limit=limit)
        ]
        seen = {order.order_id for order in completed}
        return (
            completed + [order for order in fixtures if order.order_id not in seen]
        )[:limit]

    async def get_order(self, session, order_id: str) -> Order | None:
        purchases = await self.conto.completed_purchase_records(session.session_id)
        for record in purchases:
            order = self._purchase_order(record)
            if order.order_id == order_id:
                return order
        fixture = await super().get_order(session, order_id)
        return self._internal_tracking_order(fixture) if fixture else None


# Anthropic's reference host deliberately defaults to an in-process SessionStore and
# expects a deployment to supply shared storage. Patch its constructor dependency before
# the host registers routes so session state and transcripts survive serverless routing.
if os.getenv("VERCEL"):
    storefront_module.SessionStore = UpstashSessionStore

backend = AnthropicShoppingRetail()
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
    memory_store=JsonFileMemoryStore(memory_store_path()),
)


def product_detail(product: ProductDetails) -> dict:
    return product.model_dump() | {
        "price_intelligence": backend.price_intelligence(product.product_id),
        "review_aspects": backend.review_aspects(product.product_id),
    }


host = build_storefront_host(
    title="Anthropic Shopping with Conto",
    example_root=DATA_DIR.parent,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(
        DATA_DIR / "memory-seed.json", marker=memory_seed_marker_path()
    ),
    product_detail=product_detail,
)
app = host.app
app.include_router(
    create_checkout_router(backend.conto, on_complete=backend.consume_completed_cart)
)
app.include_router(create_merchant_router(backend, InMemoryMemoryStore()), prefix="/api/merchant")
if PRODUCT_IMAGES.is_dir():
    app.mount("/products", StaticFiles(directory=PRODUCT_IMAGES), name="products")


class ShopperSessionRequest(BaseModel):
    user_id: str = Field(default="demo-user", min_length=1, max_length=64)


class CartItemRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)


class CartQuantityRequest(CartItemRequest):
    quantity: int = Field(ge=1, le=24)


# Replace the reference template's always-new session route with a validated,
# short-lived cookie resume. The opaque token never becomes script-readable.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/api/session"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
]


@app.post("/api/session")
async def start_or_resume_session(
    request: Request,
    body: ShopperSessionRequest | None = None,
) -> JSONResponse:
    requested = body or ShopperSessionRequest()
    record = None
    persisted = request.cookies.get(SHOPPER_SESSION_COOKIE)
    if persisted:
        try:
            candidate = host.sessions.require(persisted)
            if candidate.user_id == requested.user_id:
                record = candidate
        except UnknownSessionError:
            pass
    if record is None:
        record = host.sessions.start(requested.user_id)
    profile = await backend.get_preferences(host.context(record))
    response = JSONResponse(
        {
            "session_id": record.session_id,
            "user_id": record.user_id,
            "name": profile.display_name,
            "tier": profile.loyalty_tier,
        }
    )
    forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        SHOPPER_SESSION_COOKIE,
        record.session_id,
        max_age=SHOPPER_SESSION_MAX_AGE,
        path="/",
        secure=forwarded_scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


class ContoControlUpdateRequest(BaseModel):
    maxTransaction: float = Field(gt=0, le=1_000_000)
    approvalThreshold: float = Field(gt=0, le=1_000_000)
    dailyLimit: float = Field(gt=0, le=1_000_000)
    weeklyLimit: float = Field(gt=0, le=1_000_000)
    monthlyLimit: float = Field(gt=0, le=1_000_000)
    merchantAllowed: bool
    agentActive: bool


def provisioning_client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",", 1)[0].strip()
    return address or (request.client.host if request.client else "unknown")


@app.get("/api/conto/controls")
async def conto_controls(request: Request, record: host.CurrentSession) -> dict:
    try:
        return await backend.conto.sessions.controls(
            record.session_id, provisioning_client(request)
        )
    except ContoControlError as error:
        logger.warning("Conto controls unavailable: %s", error)
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.patch("/api/conto/controls")
async def update_conto_controls(
    body: ContoControlUpdateRequest,
    request: Request,
    record: host.CurrentSession,
) -> dict:
    try:
        return await backend.conto.sessions.update_controls(
            record.session_id,
            ControlUpdate(
                max_transaction=body.maxTransaction,
                approval_threshold=body.approvalThreshold,
                daily_limit=body.dailyLimit,
                weekly_limit=body.weeklyLimit,
                monthly_limit=body.monthlyLimit,
                merchant_allowed=body.merchantAllowed,
                agent_active=body.agentActive,
            ),
            provisioning_client(request),
        )
    except ContoControlError as error:
        logger.warning("Conto control update rejected: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/cart/add")
async def cart_add(request: CartAddRequest, record: host.CurrentSession) -> dict:
    return await cart_write(
        record,
        "add_to_cart",
        {"product_id": request.product_id, "quantity": request.quantity},
    )


async def cart_write(
    record: host.CurrentSession,
    tool: str,
    tool_input: dict[str, Any],
) -> dict:
    """Apply a cart control through the agent's gates without starting a chat turn."""
    executor = host.agent.executor_class(
        backend=host.backend,
        config=host.agent.config,
        skills=host.agent.skills,
        session=host.context(record),
        state=record.state,
        memory=host.agent.memory,
    )
    execution = await executor.execute(tool, tool_input)
    if execution.refused:
        detail = execution.result_text.split(". ", 1)[0].strip()
        raise HTTPException(status_code=400, detail=detail or "The cart could not be updated")
    cart = await host.cart_payload(record)
    logger.info(
        "cart mutation completed tool=%s item_count=%s",
        tool,
        cart.get("item_count", 0),
    )
    return {"ok": True, "cart": cart}


@app.post("/api/cart/quantity")
async def cart_quantity(request: CartQuantityRequest, record: host.CurrentSession) -> dict:
    return await cart_write(
        record,
        "update_cart_item",
        {"product_id": request.product_id, "quantity": request.quantity},
    )


@app.post("/api/cart/remove")
async def cart_remove(request: CartItemRequest, record: host.CurrentSession) -> dict:
    return await cart_write(
        record,
        "remove_from_cart",
        {"product_id": request.product_id},
    )
