"""Shared deployment state for the serverless Anthropic Shopping demo."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Generic, Protocol, TypeVar

from demo_common.sessions import SessionConflictError, SessionStore
from demo_common.storefront_fixtures import cart_line
from pydantic import BaseModel
from shopping_agent import Cart, CartItem, ProductDetails


STORE_TTL_SECONDS = 2 * 60 * 60
StateT = TypeVar("StateT", bound=BaseModel)
logger = logging.getLogger(__name__)


class SharedStoreError(RuntimeError):
    """The production demo store could not complete a state operation."""


class JsonClient(Protocol):
    def get(self, key: str) -> str | None: ...

    def compare_and_set(
        self, key: str, expected_version: int, value: str, ttl: int
    ) -> bool: ...

    def write_messages(self, key: str, start: int, messages: str, ttl: int) -> bool: ...

    def mutate_cart(
        self, key: str, operation: str, product_id: str, value: str, ttl: int
    ) -> None: ...

    def consume_cart(
        self, cart_key: str, checkout_key: str, quantities: str, ttl: int
    ) -> bool: ...

    def delete(self, *keys: str) -> None: ...


class UpstashJsonClient:
    """Synchronous Redis operations for Anthropic's synchronous session-store contract."""

    _CAS_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
local current = 0
if raw then
  local parsed = cjson.decode(raw)
  current = tonumber(parsed.version) or 0
end
if current ~= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
""".strip()

    _MESSAGES_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
local current = {}
if raw then current = cjson.decode(raw) else raw = '[]' end
local start = tonumber(ARGV[1])
if start > 0 and #current ~= start then return 0 end
if start == 0 then raw = '[]' end
local incoming = cjson.decode(ARGV[2])
if #incoming > 0 then
  local existing_json = string.sub(raw, 2, -2)
  local incoming_json = string.sub(ARGV[2], 2, -2)
  if #existing_json > 0 then
    raw = '[' .. existing_json .. ',' .. incoming_json .. ']'
  else
    raw = '[' .. incoming_json .. ']'
  end
end
redis.call('SET', KEYS[1], raw, 'EX', ARGV[3])
return 1
""".strip()

    _CART_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
local cart = {}
if raw then cart = cjson.decode(raw) end
local operation = ARGV[1]
local product_id = ARGV[2]
if operation == 'put' then
  cart[product_id] = cjson.decode(ARGV[3])
elseif operation == 'quantity' and cart[product_id] then
  cart[product_id].quantity = tonumber(ARGV[3])
elseif operation == 'remove' then
  cart[product_id] = nil
end
if next(cart) == nil then
  redis.call('DEL', KEYS[1])
else
  redis.call('SET', KEYS[1], cjson.encode(cart), 'EX', ARGV[4])
end
return 1
""".strip()

    _CART_CONSUME_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local raw = redis.call('GET', KEYS[1])
local cart = {}
if raw then cart = cjson.decode(raw) end
local purchased = cjson.decode(ARGV[1])
for product_id, quantity in pairs(purchased) do
  local line = cart[product_id]
  if line then
    local remaining = (tonumber(line.quantity) or 0) - (tonumber(quantity) or 0)
    if remaining > 0 then
      line.quantity = remaining
    else
      cart[product_id] = nil
    end
  end
end
if next(cart) == nil then
  redis.call('DEL', KEYS[1])
else
  redis.call('SET', KEYS[1], cjson.encode(cart), 'EX', ARGV[2])
end
redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
return 1
""".strip()

    def __init__(self, url: str, token: str) -> None:
        if not url or not token:
            raise ValueError("KV_REST_API_URL and KV_REST_API_TOKEN are required")
        self._url = url.rstrip("/")
        self._token = token

    @classmethod
    def from_env(cls) -> "UpstashJsonClient":
        return cls(
            os.getenv("KV_REST_API_URL", "").strip(),
            os.getenv("KV_REST_API_TOKEN", "").strip(),
        )

    def _command(self, *parts: str) -> Any:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(parts).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            raise SharedStoreError("The shared shopping session store is unavailable") from error
        if payload.get("error"):
            raise SharedStoreError("The shared shopping session store rejected the request")
        return payload.get("result")

    def get(self, key: str) -> str | None:
        value = self._command("GET", key)
        return value if isinstance(value, str) else None

    def compare_and_set(
        self, key: str, expected_version: int, value: str, ttl: int
    ) -> bool:
        return int(
            self._command(
                "EVAL", self._CAS_SCRIPT, "1", key, str(expected_version), value, str(ttl)
            )
            or 0
        ) == 1

    def write_messages(self, key: str, start: int, messages: str, ttl: int) -> bool:
        return int(
            self._command(
                "EVAL", self._MESSAGES_SCRIPT, "1", key, str(start), messages, str(ttl)
            )
            or 0
        ) == 1

    def mutate_cart(
        self, key: str, operation: str, product_id: str, value: str, ttl: int
    ) -> None:
        self._command(
            "EVAL", self._CART_SCRIPT, "1", key, operation, product_id, value, str(ttl)
        )

    def consume_cart(
        self, cart_key: str, checkout_key: str, quantities: str, ttl: int
    ) -> bool:
        return int(
            self._command(
                "EVAL",
                self._CART_CONSUME_SCRIPT,
                "2",
                cart_key,
                checkout_key,
                quantities,
                str(ttl),
            )
            or 0
        ) == 1

    def delete(self, *keys: str) -> None:
        if keys:
            self._command("DEL", *keys)


def _key(namespace: str, session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"anthropic-shopping:{namespace}:{digest}"


def _repair_tool_use_inputs(messages: list[dict[str, Any]]) -> int:
    """Restore empty tool input objects flattened by Redis Lua cjson.

    Lua cjson represents both an empty JSON object and an empty JSON array as the
    same empty Lua table, then serializes that table as ``[]``. Anthropic requires
    every ``tool_use.input`` value to be an object, including calls with no inputs.
    """
    repaired = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if isinstance(block.get("input"), dict):
                continue
            block["input"] = {}
            repaired += 1
    return repaired


class UpstashSessionStore(SessionStore[StateT], Generic[StateT]):
    """Anthropic session records and transcripts shared by every serverless instance."""

    def __init__(self, state_type: type[StateT], client: JsonClient | None = None) -> None:
        super().__init__(state_type)
        self._client = client or UpstashJsonClient.from_env()

    def read_state(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        raw = self._client.get(_key("storefront-state", session_id))
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return int(value["version"]), dict(value["document"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise SharedStoreError("The shared shopping session is invalid") from error

    def write_state(self, session_id: str, document: dict[str, Any], version: int) -> None:
        encoded = json.dumps(
            {"version": version + 1, "document": document},
            separators=(",", ":"),
        )
        if not self._client.compare_and_set(
            _key("storefront-state", session_id), version, encoded, STORE_TTL_SECONDS
        ):
            raise SessionConflictError(session_id)

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        raw = self._client.get(_key("storefront-messages", session_id))
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SharedStoreError("The shared shopping transcript is invalid") from error
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise SharedStoreError("The shared shopping transcript is invalid")
        repaired = _repair_tool_use_inputs(value)
        if repaired:
            logger.warning("repaired invalid stored tool inputs count=%d", repaired)
        return value

    def write_messages(
        self, session_id: str, messages: list[dict[str, Any]], start: int
    ) -> None:
        encoded = json.dumps(messages, separators=(",", ":"))
        if not self._client.write_messages(
            _key("storefront-messages", session_id), start, encoded, STORE_TTL_SECONDS
        ):
            raise SessionConflictError(session_id)

    def delete(self, session_id: str) -> None:
        self._client.delete(
            _key("storefront-state", session_id),
            _key("storefront-messages", session_id),
        )

    def session_ids_for_user(self, user_id: str) -> list[str]:
        # The public storefront never enumerates a user's sessions.
        del user_id
        return []


class UpstashSessionCarts:
    """Retail cart lines shared by every serverless instance."""

    def __init__(self, client: JsonClient | None = None) -> None:
        self._client = client or UpstashJsonClient.from_env()

    def _cart_key(self, session_id: str) -> str:
        return _key("storefront-cart", session_id)

    def lines(self, session_id: str) -> dict[str, CartItem]:
        raw = self._client.get(self._cart_key(session_id))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            # Lua cjson writes an empty table as `[]`. Empty carts created by
            # older deployments are safe to treat as the absence of cart lines.
            if value == []:
                return {}
            if not isinstance(value, dict):
                raise TypeError
            normalized: dict[str, CartItem] = {}
            for key, item in value.items():
                if not isinstance(item, dict):
                    raise TypeError
                # Lua cjson cannot distinguish an empty object from an empty array.
                # Accept carts written before empty defaults were omitted below.
                if item.get("option_values") == []:
                    item = {**item, "option_values": {}}
                normalized[str(key)] = CartItem.model_validate(item)
            return normalized
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise SharedStoreError("The shared shopping cart is invalid") from error

    def cart(self, session_id: str) -> Cart:
        return Cart(items=list(self.lines(session_id).values()))

    def put(self, session_id: str, product: ProductDetails, quantity: int) -> Cart:
        line = cart_line(product, quantity)
        self._client.mutate_cart(
            self._cart_key(session_id),
            "put",
            product.product_id,
            # Omitting empty defaults keeps Lua cjson from round-tripping `{}` as `[]`.
            line.model_dump_json(exclude_defaults=True),
            STORE_TTL_SECONDS,
        )
        return self.cart(session_id)

    def set_quantity(self, session_id: str, product_id: str, quantity: int) -> Cart:
        self._client.mutate_cart(
            self._cart_key(session_id),
            "quantity",
            product_id,
            str(quantity),
            STORE_TTL_SECONDS,
        )
        return self.cart(session_id)

    def remove(self, session_id: str, product_id: str) -> Cart:
        self._client.mutate_cart(
            self._cart_key(session_id),
            "remove",
            product_id,
            "",
            STORE_TTL_SECONDS,
        )
        return self.cart(session_id)

    def consume_completed(
        self,
        session_id: str,
        checkout_id: str,
        quantities: dict[str, int],
    ) -> bool:
        """Remove purchased quantities once, preserving later additions."""
        normalized = {
            str(product_id): max(1, int(quantity))
            for product_id, quantity in quantities.items()
            if product_id and int(quantity) > 0
        }
        if not normalized:
            return False
        checkout_key = _key(
            "storefront-cart-checkout", f"{session_id}:{checkout_id}"
        )
        return self._client.consume_cart(
            self._cart_key(session_id),
            checkout_key,
            json.dumps(normalized, separators=(",", ":"), sort_keys=True),
            STORE_TTL_SECONDS,
        )

    def reset(self, session_id: str) -> None:
        self._client.delete(self._cart_key(session_id))
