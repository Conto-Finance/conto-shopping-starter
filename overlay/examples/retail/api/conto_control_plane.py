"""Per-session Conto control plane for the Anthropic Shopping demo.

The public storefront never receives an organization key or an agent SDK key.  It
only carries Anthropic's unguessable shopping session id.  This module maps that id
to a short-lived, encrypted Conto workspace in a server-side store and only permits
the small set of controls exposed by the demo UI.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Protocol

from cryptography.fernet import Fernet, InvalidToken


ACME_MERCHANT_NAME = "ACME Retail"
ACME_MERCHANT_ADDRESS = "0x616e7468726f7069632d73686f7070696e672d61"
SESSION_TTL_SECONDS = 2 * 60 * 60
CHECKOUT_TTL_SECONDS = 60 * 60
PROVISIONING_WINDOW_SECONDS = 24 * 60 * 60
# A public walkthrough often involves several reloads and a small team sharing one NAT.
# Keep a finite abuse ceiling without locking legitimate demo participants out mid-session.
PROVISIONING_LIMIT = max(1, int(os.getenv("DEMO_PROVISIONING_LIMIT", "20")))
# Rotate the counter namespace after release QA so deploy-time smoke sessions do not
# consume the first public walkthroughs' allowance. Old counters expire on their own.
PROVISIONING_RATE_LIMIT_VERSION = "v4"
WALLET_LIMITS_VERSION = "v2"

DEFAULT_LIMITS = {
    "maxTransaction": 100.0,
    "approvalThreshold": 50.0,
    "dailyLimit": 500.0,
    "weeklyLimit": 2_000.0,
    "monthlyLimit": 5_000.0,
}

HARD_WALLET_LIMITS = {
    "maxTransaction": 1_000.0,
    "dailyLimit": 2_500.0,
    "weeklyLimit": 10_000.0,
    "monthlyLimit": 25_000.0,
}


def _limits_from_env(name: str, default: dict[str, float]) -> dict[str, float]:
    """Merge a JSON limit override from the environment over ``default``.

    Verticals that reuse this control plane pass their own spend defaults and wallet
    ceilings through ``DEMO_DEFAULT_LIMITS`` / ``DEMO_HARD_LIMITS`` (JSON). Missing or
    malformed values fall back to the retail defaults, so retail is unchanged.
    """
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
    if not isinstance(parsed, dict):
        return default
    merged = dict(default)
    for key in default:
        value = parsed.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[key] = float(value)
    return merged


class ContoControlError(RuntimeError):
    """A safe-to-display control-plane failure."""


class JsonTransport:
    """Small async JSON transport shared by the Conto admin and runtime clients."""

    def __init__(self, api_key: str, base_url: str = "https://conto.finance") -> None:
        if not api_key:
            raise ValueError("A Conto API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_sync, method, path, payload)

    def _request_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                message = detail.get("error") or detail.get("message")
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            raise ContoControlError(message or f"Conto returned HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ContoControlError("Conto is unavailable; the action was not authorized") from error


class KeyValueStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None: ...

    async def acquire(self, key: str, ttl: int = 90) -> str | None: ...

    async def release(self, key: str, token: str) -> None: ...

    async def increment(self, key: str, ttl: int) -> int: ...


class MemoryKeyValueStore:
    """Local-development store with the same TTL and locking contract as Redis."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, tuple[float, str]] = {}
        self._counters: dict[str, tuple[float, int]] = {}
        self._guard = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._guard:
            item = self._values.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= time.time():
                self._values.pop(key, None)
                return None
            return json.loads(json.dumps(value))

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        async with self._guard:
            self._values[key] = (time.time() + ttl, json.loads(json.dumps(value)))

    async def acquire(self, key: str, ttl: int = 90) -> str | None:
        async with self._guard:
            now = time.time()
            current = self._locks.get(key)
            if current and current[0] > now:
                return None
            token = secrets.token_urlsafe(24)
            self._locks[key] = (now + ttl, token)
            return token

    async def release(self, key: str, token: str) -> None:
        async with self._guard:
            current = self._locks.get(key)
            if current and secrets.compare_digest(current[1], token):
                self._locks.pop(key, None)

    async def increment(self, key: str, ttl: int) -> int:
        async with self._guard:
            now = time.time()
            expires_at, count = self._counters.get(key, (now + ttl, 0))
            if expires_at <= now:
                expires_at, count = now + ttl, 0
            count += 1
            self._counters[key] = (expires_at, count)
            return count


class UpstashKeyValueStore:
    """Minimal Upstash REST client; values are JSON documents with explicit TTLs."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token

    async def _command(self, *parts: str) -> Any:
        return await asyncio.to_thread(self._command_sync, list(parts))

    def _command_sync(self, parts: list[str]) -> Any:
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
            raise ContoControlError("The secure demo session store is unavailable") from error
        if payload.get("error"):
            raise ContoControlError("The secure demo session store rejected the request")
        return payload.get("result")

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._command("GET", key)
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ContoControlError("The secure demo session is invalid") from error
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        await self._command("SET", key, json.dumps(value, separators=(",", ":")), "EX", str(ttl))

    async def acquire(self, key: str, ttl: int = 90) -> str | None:
        token = secrets.token_urlsafe(24)
        result = await self._command("SET", key, token, "NX", "EX", str(ttl))
        return token if result == "OK" else None

    async def release(self, key: str, token: str) -> None:
        current = await self._command("GET", key)
        if isinstance(current, str) and secrets.compare_digest(current, token):
            await self._command("DEL", key)

    async def increment(self, key: str, ttl: int) -> int:
        result = await self._command("INCR", key)
        try:
            count = int(result)
        except (TypeError, ValueError) as error:
            raise ContoControlError("The secure demo rate limit is unavailable") from error
        if count == 1:
            await self._command("EXPIRE", key, str(ttl))
        return count


def store_from_env() -> KeyValueStore:
    url = os.getenv("KV_REST_API_URL", "").strip()
    token = os.getenv("KV_REST_API_TOKEN", "").strip()
    if url and token:
        return UpstashKeyValueStore(url, token)
    if os.getenv("VERCEL"):
        raise ValueError("KV_REST_API_URL and KV_REST_API_TOKEN are required on Vercel")
    return MemoryKeyValueStore()


class SecretBox:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("SHOPPING_SESSION_ENCRYPTION_KEY is required")
        material = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(material)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise ContoControlError("The Conto demo session credential is invalid") from error


@dataclass
class CheckoutSpendLedger:
    day: str = ""
    week: str = ""
    month: str = ""
    today: float = 0.0
    this_week: float = 0.0
    this_month: float = 0.0
    completed_checkout_ids: list[str] = field(default_factory=list)
    purchase_records: list[dict[str, Any]] = field(default_factory=list)

    def roll(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        day = current.strftime("%Y-%m-%d")
        week = current.strftime("%G-W%V")
        month = current.strftime("%Y-%m")
        if self.day != day:
            self.day, self.today = day, 0.0
        if self.week != week:
            self.week, self.this_week = week, 0.0
        if self.month != month:
            self.month, self.this_month = month, 0.0


@dataclass
class ContoSession:
    session_id: str
    agent_id: str
    agent_name: str
    wallet_id: str
    encrypted_sdk_key: str
    policy_ids: dict[str, str]
    created_at: str
    expires_at: str
    merchant_address: str = ACME_MERCHANT_ADDRESS
    wallet_limits_version: str = ""
    checkout_spend: CheckoutSpendLedger = field(default_factory=CheckoutSpendLedger)
    effective_policies: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContoSession":
        raw_ledger = value.get("checkout_spend", value.get("link_spend"))
        ledger_value = raw_ledger if isinstance(raw_ledger, dict) else {}
        checkout_spend = CheckoutSpendLedger(
            day=str(ledger_value.get("day") or ""),
            week=str(ledger_value.get("week") or ""),
            month=str(ledger_value.get("month") or ""),
            today=float(ledger_value.get("today") or 0),
            this_week=float(ledger_value.get("this_week") or 0),
            this_month=float(ledger_value.get("this_month") or 0),
            completed_checkout_ids=[
                str(checkout_id)
                for checkout_id in (ledger_value.get("completed_checkout_ids") or [])
            ],
            purchase_records=[
                dict(record)
                for record in (ledger_value.get("purchase_records") or [])
                if isinstance(record, dict)
            ],
        )
        return cls(
            session_id=str(value["session_id"]),
            agent_id=str(value["agent_id"]),
            agent_name=str(value["agent_name"]),
            wallet_id=str(value["wallet_id"]),
            encrypted_sdk_key=str(value["encrypted_sdk_key"]),
            policy_ids={str(k): str(v) for k, v in dict(value["policy_ids"]).items()},
            created_at=str(value["created_at"]),
            expires_at=str(value["expires_at"]),
            merchant_address=str(value.get("merchant_address") or ACME_MERCHANT_ADDRESS),
            wallet_limits_version=str(value.get("wallet_limits_version") or ""),
            checkout_spend=checkout_spend,
            effective_policies=[
                item
                for item in value.get("effective_policies", [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ControlUpdate:
    max_transaction: float
    approval_threshold: float
    daily_limit: float
    weekly_limit: float
    monthly_limit: float
    merchant_allowed: bool
    agent_active: bool

    def validate(
        self,
        ceilings: dict[str, float | None] | None = None,
        hard_limits: dict[str, float] | None = None,
    ) -> None:
        hard = hard_limits or HARD_WALLET_LIMITS
        values = (
            self.max_transaction,
            self.approval_threshold,
            self.daily_limit,
            self.weekly_limit,
            self.monthly_limit,
        )
        if any(not 0 < value <= 1_000_000 for value in values):
            raise ContoControlError("Spend controls must be positive numbers")
        if self.approval_threshold > self.max_transaction:
            raise ContoControlError("Approval threshold cannot exceed the per-purchase limit")
        if not self.max_transaction <= self.daily_limit <= self.weekly_limit <= self.monthly_limit:
            raise ContoControlError("Limits must increase from purchase to day, week, and month")
        if self.max_transaction > hard["maxTransaction"]:
            raise ContoControlError("Per-purchase limit exceeds the demo wallet ceiling")
        if self.daily_limit > hard["dailyLimit"]:
            raise ContoControlError("Daily limit exceeds the demo wallet ceiling")
        if self.weekly_limit > hard["weeklyLimit"]:
            raise ContoControlError("Weekly limit exceeds the demo wallet ceiling")
        if self.monthly_limit > hard["monthlyLimit"]:
            raise ContoControlError("Monthly limit exceeds the demo wallet ceiling")

        if ceilings:
            checks = (
                ("Per-purchase", self.max_transaction, ceilings.get("maxTransaction")),
                ("Approval threshold", self.approval_threshold, ceilings.get("approvalThreshold")),
                ("Daily", self.daily_limit, ceilings.get("dailyLimit")),
                ("Weekly", self.weekly_limit, ceilings.get("weeklyLimit")),
                ("Monthly", self.monthly_limit, ceilings.get("monthlyLimit")),
            )
            exceeded = next(
                (
                    (label, ceiling)
                    for label, value, ceiling in checks
                    if ceiling is not None and value > ceiling
                ),
                None,
            )
            if exceeded:
                label, ceiling = exceeded
                raise ContoControlError(
                    f"{label} cannot exceed the effective Conto ceiling of ${ceiling:,.2f}"
                )


class ContoSessionManager:
    """Provision, read, and narrowly mutate a shopper's Conto policy workspace."""

    def __init__(
        self,
        admin: JsonTransport,
        store: KeyValueStore,
        secrets_box: SecretBox,
        *,
        wallet_id: str,
        merchant_name: str = ACME_MERCHANT_NAME,
        merchant_address: str = ACME_MERCHANT_ADDRESS,
        owner_membership_id: str | None = None,
        base_url: str = "https://conto.finance",
        default_limits: dict[str, float] | None = None,
        hard_limits: dict[str, float] | None = None,
        provisioning_namespace: str = "",
    ) -> None:
        self._admin = admin
        self._store = store
        self._box = secrets_box
        self._wallet_id = wallet_id
        self.merchant_name = merchant_name
        self.merchant_address = merchant_address.lower()
        self._owner_membership_id = owner_membership_id
        self._base_url = base_url.rstrip("/")
        self._default_limits = default_limits or DEFAULT_LIMITS
        self._hard_limits = hard_limits or HARD_WALLET_LIMITS
        self._provisioning_namespace = provisioning_namespace.strip()

    @classmethod
    def from_env(cls) -> "ContoSessionManager":
        base_url = os.getenv("CONTO_API_URL", "https://conto.finance")
        return cls(
            JsonTransport(os.environ["CONTO_ORG_API_KEY"], base_url),
            store_from_env(),
            SecretBox(os.environ["SHOPPING_SESSION_ENCRYPTION_KEY"]),
            wallet_id=os.environ["CONTO_SHARED_WALLET_ID"],
            merchant_name=os.getenv("CONTO_MERCHANT_NAME", ACME_MERCHANT_NAME),
            merchant_address=os.getenv("CONTO_MERCHANT_ADDRESS", ACME_MERCHANT_ADDRESS),
            owner_membership_id=os.getenv("CONTO_OWNER_MEMBERSHIP_ID"),
            base_url=base_url,
            default_limits=_limits_from_env("DEMO_DEFAULT_LIMITS", DEFAULT_LIMITS),
            hard_limits=_limits_from_env("DEMO_HARD_LIMITS", HARD_WALLET_LIMITS),
            provisioning_namespace=os.getenv("DEMO_VERTICAL_NAMESPACE", ""),
        )

    def _session_key(self, session_id: str) -> str:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return f"anthropic-shopping:session:{digest}"

    def _checkout_key(self, checkout_id: str) -> str:
        return f"anthropic-shopping:checkout:{checkout_id}"

    def merchant_address_for(self, session_id: str) -> str:
        seed = f"{self.merchant_address}:{session_id}".encode("utf-8")
        return "0x" + hashlib.sha256(seed).hexdigest()[:40]

    async def load(self, session_id: str) -> ContoSession | None:
        value = await self._store.get(self._session_key(session_id))
        if not value:
            return None
        session = ContoSession.from_dict(value)
        if not secrets.compare_digest(session.session_id, session_id):
            raise ContoControlError("The Conto demo session does not match this shopper")
        session.checkout_spend.roll()
        return session

    async def save(self, session: ContoSession) -> None:
        session.checkout_spend.roll()
        await self._store.set(self._session_key(session.session_id), session.to_dict(), SESSION_TTL_SECONDS)

    @asynccontextmanager
    async def lock(self, namespace: str, identifier: str) -> AsyncIterator[None]:
        key = f"anthropic-shopping:lock:{namespace}:{hashlib.sha256(identifier.encode()).hexdigest()}"
        token: str | None = None
        for _ in range(40):
            token = await self._store.acquire(key, 90)
            if token:
                break
            await asyncio.sleep(0.25)
        if not token:
            raise ContoControlError("This demo session is busy; try again in a moment")
        try:
            yield
        finally:
            await self._store.release(key, token)

    async def ensure(
        self,
        session_id: str,
        provisioner: str | None = None,
    ) -> ContoSession:
        existing = await self.load(session_id)
        if existing:
            return await self._ensure_current_wallet_limits(existing)
        async with self.lock("provision", session_id):
            existing = await self.load(session_id)
            if existing:
                return await self._ensure_current_wallet_limits(existing)
            if provisioner:
                digest = hashlib.sha256(provisioner.encode("utf-8")).hexdigest()
                scope = f"{self._provisioning_namespace}:" if self._provisioning_namespace else ""
                count = await self._store.increment(
                    f"anthropic-shopping:provisioning:{PROVISIONING_RATE_LIMIT_VERSION}:{scope}{digest}",
                    PROVISIONING_WINDOW_SECONDS,
                )
                if count > PROVISIONING_LIMIT:
                    raise ContoControlError(
                        "This browser has reached the public demo session limit; try again tomorrow"
                    )
            return await self._provision(session_id)

    async def _ensure_current_wallet_limits(self, session: ContoSession) -> ContoSession:
        """Upgrade short-lived demo wallet links when the adjustable range changes."""

        if session.wallet_limits_version == WALLET_LIMITS_VERSION:
            return session
        async with self.lock("wallet-limits", session.session_id):
            current = await self.load(session.session_id)
            if not current:
                raise ContoControlError("The Conto demo session expired; refresh and try again")
            if current.wallet_limits_version == WALLET_LIMITS_VERSION:
                return current
            try:
                await self._admin.request(
                    "PATCH",
                    "/api/agents/"
                    + urllib.parse.quote(current.agent_id, safe="")
                    + "/wallets/"
                    + urllib.parse.quote(current.wallet_id, safe=""),
                    {
                        "spendLimitPerTx": HARD_WALLET_LIMITS["maxTransaction"],
                        "spendLimitDaily": HARD_WALLET_LIMITS["dailyLimit"],
                        "spendLimitWeekly": HARD_WALLET_LIMITS["weeklyLimit"],
                        "spendLimitMonthly": HARD_WALLET_LIMITS["monthlyLimit"],
                    },
                )
            except ContoControlError:
                # Widening a legacy session's wallet ceiling is optional. Keep the
                # existing, stricter delegation usable and let the session expire
                # naturally instead of taking its controls and checkout offline.
                current.wallet_limits_version = WALLET_LIMITS_VERSION
                await self.save(current)
                return current
            current.wallet_limits_version = WALLET_LIMITS_VERSION
            await self.save(current)
            return current

    async def _resolve_owner(self) -> str:
        if self._owner_membership_id:
            return self._owner_membership_id
        result = await self._admin.request("GET", "/api/organizations/me/members")
        members = result.get("members") if isinstance(result.get("members"), list) else []
        owners = [member for member in members if str(member.get("role", "")).upper() == "OWNER"]
        candidates = owners or members
        if len(candidates) != 1 or not candidates[0].get("id"):
            raise ContoControlError("A single Conto owner could not be resolved for the demo")
        self._owner_membership_id = str(candidates[0]["id"])
        return self._owner_membership_id

    async def _provision(self, session_id: str) -> ContoSession:
        owner_id = await self._resolve_owner()
        suffix = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
        merchant_address = self.merchant_address_for(session_id)
        created_policy_ids: list[str] = []
        agent_id: str | None = None
        counterparty_id: str | None = None
        try:
            agent = await self._admin.request(
                "POST",
                "/api/agents",
                {
                    "name": f"Anthropic Shopping · {suffix}",
                    "description": "Short-lived Claude commerce-agents shopping demo session.",
                    "agentType": "ANTHROPIC_CLAUDE",
                    "purpose": "Shop ACME while Conto enforces shopper-configured controls",
                    "externalId": f"anthropic-shopping-{suffix}",
                    "environment": "DEV",
                    "riskTier": "MEDIUM",
                    "ownerMembershipId": owner_id,
                    "allowedContexts": ["shopping.checkout", "stripe.checkout"],
                    "identityTags": ["anthropic-shopping", "public-demo", "short-lived"],
                },
            )
            agent_id = str(agent["id"])
            agent_name = str(agent.get("name") or f"Anthropic Shopping · {suffix}")

            counterparty = await self._admin.request(
                "POST",
                "/api/counterparties",
                {
                    "name": f"{self.merchant_name} · {suffix}",
                    "type": "VENDOR",
                    "address": merchant_address,
                    "category": "MERCHANDISE",
                    "description": "Session-scoped fictional merchant for Anthropic Shopping.",
                    "approvalStatus": "APPROVED",
                },
            )
            counterparty_id = str(counterparty["id"])

            await self._admin.request(
                "POST",
                f"/api/agents/{urllib.parse.quote(agent_id, safe='')}/wallets",
                {
                    "walletId": self._wallet_id,
                    "delegationType": "LIMITED",
                    "spendLimitPerTx": self._hard_limits["maxTransaction"],
                    "spendLimitDaily": self._hard_limits["dailyLimit"],
                    "spendLimitWeekly": self._hard_limits["weeklyLimit"],
                    "spendLimitMonthly": self._hard_limits["monthlyLimit"],
                    "allowedHoursStart": 0,
                    "allowedHoursEnd": 24,
                    "allowedDays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                },
            )

            policies: dict[str, str] = {}
            for key, payload in self._policy_payloads(
                agent_id, suffix, merchant_address
            ).items():
                policy = await self._admin.request("POST", "/api/policies", payload)
                policy_id = str(policy["id"])
                policies[key] = policy_id
                created_policy_ids.append(policy_id)

            key_result = await self._admin.request(
                "POST",
                f"/api/agents/{urllib.parse.quote(agent_id, safe='')}/sdk-keys",
                {"name": "Anthropic Shopping runtime", "keyType": "standard", "expiresInDays": 1},
            )
            runtime_key = str(key_result.get("key") or "")
            if not runtime_key.startswith("conto_agent_"):
                raise ContoControlError("Conto did not return a standard agent key")

            now = datetime.now(UTC)
            session = ContoSession(
                session_id=session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                wallet_id=self._wallet_id,
                encrypted_sdk_key=self._box.encrypt(runtime_key),
                policy_ids=policies,
                created_at=now.isoformat(),
                expires_at=datetime.fromtimestamp(now.timestamp() + SESSION_TTL_SECONDS, UTC).isoformat(),
                merchant_address=merchant_address,
                wallet_limits_version=WALLET_LIMITS_VERSION,
            )
            await self.save(session)
            return session
        except Exception:
            for policy_id in reversed(created_policy_ids):
                try:
                    await self._admin.request("DELETE", f"/api/policies/{urllib.parse.quote(policy_id, safe='')}")
                except Exception:
                    pass
            if agent_id:
                try:
                    await self._admin.request("DELETE", f"/api/agents/{urllib.parse.quote(agent_id, safe='')}")
                except Exception:
                    pass
            if counterparty_id:
                try:
                    await self._admin.request(
                        "DELETE",
                        f"/api/counterparties/{urllib.parse.quote(counterparty_id, safe='')}",
                    )
                except Exception:
                    pass
            raise

    def _policy_payloads(
        self,
        agent_id: str,
        suffix: str,
        merchant_address: str,
    ) -> dict[str, dict[str, Any]]:
        return {
            "spend": {
                "name": f"Anthropic Shopping spend · {suffix}",
                "description": "Shopper-controlled purchase, daily, weekly, and monthly limits.",
                "policyType": "SPEND_LIMIT",
                "priority": 60,
                "scope": "assigned",
                "agentIds": [agent_id],
                "rules": self._spend_rules(self._default_limits),
            },
            "approval": {
                "name": f"Anthropic Shopping approval · {suffix}",
                "description": "Requires human review above the shopper-selected threshold.",
                "policyType": "APPROVAL_THRESHOLD",
                "priority": 70,
                "scope": "assigned",
                "agentIds": [agent_id],
                "rules": self._approval_rules(self._default_limits["approvalThreshold"]),
            },
            "merchant": {
                "name": f"Anthropic Shopping merchants · {suffix}",
                "description": "Restricts checkout to the shopper-approved merchant allowlist.",
                "policyType": "COUNTERPARTY",
                "priority": 80,
                "scope": "assigned",
                "agentIds": [agent_id],
                "rules": self._merchant_rules(True, merchant_address),
            },
        }

    @staticmethod
    def _spend_rules(limits: dict[str, float]) -> list[dict[str, str]]:
        return [
            {"ruleType": "MAX_AMOUNT", "operator": "LTE", "value": str(limits["maxTransaction"]), "action": "ALLOW"},
            {"ruleType": "DAILY_LIMIT", "operator": "LTE", "value": str(limits["dailyLimit"]), "action": "ALLOW"},
            {"ruleType": "WEEKLY_LIMIT", "operator": "LTE", "value": str(limits["weeklyLimit"]), "action": "ALLOW"},
            {"ruleType": "MONTHLY_LIMIT", "operator": "LTE", "value": str(limits["monthlyLimit"]), "action": "ALLOW"},
        ]

    @staticmethod
    def _approval_rules(threshold: float) -> list[dict[str, str]]:
        return [{
            "ruleType": "REQUIRE_APPROVAL_ABOVE",
            "operator": "GREATER_THAN",
            "value": str(threshold),
            "action": "REQUIRE_APPROVAL",
        }]

    def _merchant_rules(
        self,
        allowed: bool,
        merchant_address: str | None = None,
    ) -> list[dict[str, str]]:
        return [{
            "ruleType": "ALLOWED_COUNTERPARTIES",
            "operator": "IN_LIST",
            "value": json.dumps([merchant_address or self.merchant_address] if allowed else []),
            "action": "ALLOW",
        }]

    def runtime_transport(self, session: ContoSession) -> JsonTransport:
        return JsonTransport(self._box.decrypt(session.encrypted_sdk_key), self._base_url)

    async def _effective_policies(self, session: ContoSession) -> list[dict[str, Any]]:
        result = await self.runtime_transport(session).request(
            "GET",
            "/api/sdk/policies?walletId="
            + urllib.parse.quote(session.wallet_id, safe=""),
        )
        policies = _list_payload(result, "policies")
        if not policies:
            raise ContoControlError("Conto did not return the agent's effective policies")
        return policies

    async def controls(
        self,
        session_id: str,
        provisioner: str | None = None,
    ) -> dict[str, Any]:
        session = await self.ensure(session_id, provisioner)
        # Read through the server-only admin surface so a paused agent can still
        # display its controls and be resumed from the demo UI.
        agent_result, assignments_result, wallets_result, counterparties_result = await asyncio.gather(
            self._admin.request(
                "GET", f"/api/agents/{urllib.parse.quote(session.agent_id, safe='')}"
            ),
            self._admin.request(
                "GET", f"/api/agents/{urllib.parse.quote(session.agent_id, safe='')}/policies"
            ),
            self._admin.request(
                "GET", f"/api/agents/{urllib.parse.quote(session.agent_id, safe='')}/wallets"
            ),
            self._admin.request(
                "GET",
                "/api/counterparties?search="
                + urllib.parse.quote(session.merchant_address)
                + "&limit=20",
            ),
        )
        assignments = _list_payload(assignments_result, "policies")
        policies = [
            item.get("policy") if isinstance(item.get("policy"), dict) else item
            for item in assignments
        ]
        agent = _mapping_payload(agent_result, "agent")
        if str(agent.get("status") or "").upper() == "ACTIVE":
            policies = await self._effective_policies(session)
            session.effective_policies = policies
        elif session.effective_policies:
            session_policy_ids = set(session.policy_ids.values())
            policies = [
                policy
                for policy in session.effective_policies
                if str(policy.get("id") or policy.get("policyId") or "")
                not in session_policy_ids
            ] + policies
        admin_wallets = _list_payload(wallets_result, "wallets")
        spending_result = {
            "wallets": [
                {
                    "limits": {
                        "perTransaction": wallet.get("spendLimitPerTx"),
                        "daily": wallet.get("spendLimitDaily"),
                        "weekly": wallet.get("spendLimitWeekly"),
                        "monthly": wallet.get("spendLimitMonthly"),
                    },
                    "spent": {
                        "today": wallet.get("spentToday"),
                        "thisWeek": wallet.get("spentThisWeek"),
                        "thisMonth": wallet.get("spentThisMonth"),
                    },
                }
                for wallet in admin_wallets
            ]
        }
        session.checkout_spend.roll()
        await self.save(session)
        return public_controls(
            agent_result,
            {"policies": policies},
            spending_result,
            counterparties_result,
            session=session,
            merchant_name=self.merchant_name,
            merchant_address=session.merchant_address,
        )

    async def update_controls(
        self,
        session_id: str,
        update: ControlUpdate,
        provisioner: str | None = None,
    ) -> dict[str, Any]:
        update.validate(hard_limits=self._hard_limits)
        current = await self.controls(session_id, provisioner)
        update.validate(current["spend"]["controlCeilings"], hard_limits=self._hard_limits)
        async with self.lock("controls", session_id):
            session = await self.ensure(session_id, provisioner)
            spend_id = urllib.parse.quote(session.policy_ids["spend"], safe="")
            approval_id = urllib.parse.quote(session.policy_ids["approval"], safe="")
            merchant_id = urllib.parse.quote(session.policy_ids["merchant"], safe="")
            desired_status = "ACTIVE" if update.agent_active else "PAUSED"

            await self._admin.request(
                "PUT",
                f"/api/policies/{spend_id}/rules",
                {"rules": self._spend_rules({
                    "maxTransaction": update.max_transaction,
                    "dailyLimit": update.daily_limit,
                    "weeklyLimit": update.weekly_limit,
                    "monthlyLimit": update.monthly_limit,
                })},
            )
            await self._admin.request(
                "PUT",
                f"/api/policies/{approval_id}/rules",
                {"rules": self._approval_rules(update.approval_threshold)},
            )
            await self._admin.request(
                "PUT",
                f"/api/policies/{merchant_id}/rules",
                {
                    "rules": self._merchant_rules(
                        update.merchant_allowed, session.merchant_address
                    )
                },
            )
            await self._admin.request(
                "PATCH",
                f"/api/agents/{urllib.parse.quote(session.agent_id, safe='')}",
                {"status": desired_status},
            )
        return await self.controls(session_id)

    async def checkout_policy_checks(
        self, session_id: str, amount: float
    ) -> list[dict[str, str]]:
        """Return the shopper-readable result of every active checkout control."""

        controls = await self.controls(session_id)
        spend = controls["spend"]
        merchant = controls["merchants"]["current"]
        ledger = controls["checkout"]["spent"]
        checks: list[dict[str, str]] = []

        def add(key: str, label: str, status: str, detail: str, reason: str = "") -> None:
            check = {"key": key, "label": label, "status": status, "detail": detail}
            if reason:
                check["reason"] = reason
            checks.append(check)

        active = controls["agent"]["status"] == "ACTIVE"
        add(
            "agent",
            "Agent status",
            "passed" if active else "blocked",
            "Active" if active else "Paused",
            "The shopping agent is paused." if not active else "",
        )

        merchant_allowed = bool(merchant.get("allowed")) and bool(merchant.get("registered"))
        merchant_name = str(merchant.get("name") or self.merchant_name)
        add(
            "merchant",
            "Merchant",
            "passed" if merchant_allowed else "blocked",
            f"{merchant_name} is allowed"
            if merchant_allowed
            else f"{merchant_name} is not allowed",
            f"{merchant_name} is not in the allowed merchant list."
            if not merchant_allowed
            else "",
        )

        max_transaction = spend.get("maxTransaction")
        max_ok = not isinstance(max_transaction, (int, float)) or amount <= float(max_transaction)
        max_detail = (
            f"${amount:,.2f} of ${float(max_transaction):,.2f}"
            if isinstance(max_transaction, (int, float))
            else f"${amount:,.2f} · no limit configured"
        )
        add(
            "per_purchase",
            "Per-purchase limit",
            "passed" if max_ok else "blocked",
            max_detail,
            (
                f"This ${amount:,.2f} purchase exceeds the "
                f"${float(max_transaction):,.2f} per-purchase limit."
            )
            if not max_ok
            else "",
        )

        period_checks = (
            ("daily", "Daily limit", spend.get("dailyLimit"), ledger.get("today")),
            ("weekly", "Weekly limit", spend.get("weeklyLimit"), ledger.get("thisWeek")),
            ("monthly", "Monthly limit", spend.get("monthlyLimit"), ledger.get("thisMonth")),
        )
        for key, label, limit, used_value in period_checks:
            used = float(used_value or 0)
            projected = used + amount
            allowed = not isinstance(limit, (int, float)) or projected <= float(limit)
            detail = (
                f"${projected:,.2f} of ${float(limit):,.2f} after purchase"
                if isinstance(limit, (int, float))
                else f"${projected:,.2f} after purchase · no limit configured"
            )
            add(
                key,
                label,
                "passed" if allowed else "blocked",
                detail,
                (
                    f"This purchase would bring {key} spend to ${projected:,.2f}, "
                    f"above the ${float(limit):,.2f} limit."
                )
                if not allowed
                else "",
            )

        threshold = spend.get("approvalThreshold")
        review = isinstance(threshold, (int, float)) and amount > float(threshold)
        approval_detail = (
            f"${amount:,.2f} is above the ${float(threshold):,.2f} threshold"
            if review
            else (
                f"${amount:,.2f} is within the ${float(threshold):,.2f} threshold"
                if isinstance(threshold, (int, float))
                else "No approval threshold configured"
            )
        )
        add(
            "approval",
            "Human approval",
            "review" if review else "passed",
            approval_detail,
        )
        return checks

    async def checkout_precheck(self, session_id: str, amount: float) -> tuple[bool, list[str]]:
        """Fail closed on any blocked live control before requesting payment."""

        checks = await self.checkout_policy_checks(session_id, amount)
        reasons = [
            str(check["reason"])
            for check in checks
            if check["status"] == "blocked" and check.get("reason")
        ]
        return not reasons, reasons

    async def record_checkout_spend(
        self,
        session_id: str,
        checkout_id: str,
        amount: float,
        purchase: dict[str, Any] | None = None,
    ) -> None:
        async with self.lock("checkout-spend", session_id):
            session = await self.ensure(session_id)
            ledger = session.checkout_spend
            ledger.roll()
            changed = False
            if checkout_id not in ledger.completed_checkout_ids:
                ledger.today = round(ledger.today + amount, 2)
                ledger.this_week = round(ledger.this_week + amount, 2)
                ledger.this_month = round(ledger.this_month + amount, 2)
                ledger.completed_checkout_ids = (
                    ledger.completed_checkout_ids + [checkout_id]
                )[-100:]
                changed = True
            if purchase and not any(
                record.get("checkoutId") == checkout_id
                for record in ledger.purchase_records
            ):
                ledger.purchase_records = (ledger.purchase_records + [purchase])[-100:]
                changed = True
            if changed:
                await self.save(session)

    async def completed_checkout_ids(self, session_id: str) -> list[str]:
        session = await self.load(session_id)
        if not session:
            return []
        return list(session.checkout_spend.completed_checkout_ids)

    async def completed_purchase_records(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        session = await self.load(session_id)
        if not session:
            return []
        records = [dict(record) for record in session.checkout_spend.purchase_records]
        return sorted(
            records,
            key=lambda record: str(record.get("placedAt") or ""),
            reverse=True,
        )

    async def approval_request_for_payment(
        self, session_id: str, payment_request_id: str
    ) -> str | None:
        """Recover the workflow ID for checkouts created before it was persisted locally."""

        session = await self.ensure(session_id)
        result = await self.runtime_transport(session).request(
            "GET", "/api/sdk/approval-requests?type=payment&limit=100"
        )
        for request in _list_payload(result, "requests"):
            if str(request.get("paymentRequestId") or "") != payment_request_id:
                continue
            approval_request_id = request.get("approvalRequestId")
            if isinstance(approval_request_id, str) and approval_request_id:
                return approval_request_id
        return None

    async def decide_demo_approval(
        self,
        session_id: str,
        approval_request_id: str,
        payment_request_id: str,
        checkout_id: str,
        cart_fingerprint: str,
        decision: str,
    ) -> dict[str, Any]:
        """Record the visitor's sandbox-only owner decision in Conto."""

        session = await self.ensure(session_id)
        if decision not in {"APPROVED", "REJECTED"}:
            raise ContoControlError("Choose approve or deny")
        if not secrets.compare_digest(checkout_id, cart_fingerprint[:36]):
            raise ContoControlError("The checkout binding is invalid")
        return await self._admin.request(
            "POST",
            "/api/demos/anthropic-shopping/approval-requests/"
            + urllib.parse.quote(approval_request_id, safe="")
            + "/decision",
            {
                "decision": decision,
                "checkoutId": checkout_id,
                "cartFingerprint": cart_fingerprint,
                "paymentRequestId": payment_request_id,
            },
        )

    async def get_checkout(self, checkout_id: str) -> dict[str, Any] | None:
        return await self._store.get(self._checkout_key(checkout_id))

    async def save_checkout(self, checkout_id: str, value: dict[str, Any]) -> None:
        await self._store.set(self._checkout_key(checkout_id), value, CHECKOUT_TTL_SECONDS)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_payload(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value: Any = result
    if isinstance(value.get("data"), dict):
        value = value["data"]
    if isinstance(value, dict):
        value = value.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapping_payload(result: dict[str, Any], key: str) -> dict[str, Any]:
    value: Any = result
    if isinstance(value.get("data"), dict):
        value = value["data"]
    if isinstance(value, dict) and isinstance(value.get(key), dict):
        value = value[key]
    return value if isinstance(value, dict) else {}


def _parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).lower() for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.lower()]
        if isinstance(parsed, list):
            return [str(item).lower() for item in parsed]
    return []


def _lower_ceiling(current: float | None, candidate: Any) -> float | None:
    number = _number(candidate)
    if number is None:
        return current
    return number if current is None else min(current, number)


def public_controls(
    agent_result: dict[str, Any],
    policy_result: dict[str, Any],
    spending_result: dict[str, Any],
    counterparties_result: dict[str, Any],
    *,
    session: ContoSession,
    merchant_name: str,
    merchant_address: str,
) -> dict[str, Any]:
    """Build the browser-safe, live Conto control snapshot."""

    agent = _mapping_payload(agent_result, "agent")
    policies = _list_payload(policy_result, "policies")
    wallets = _list_payload(spending_result, "wallets")
    counterparties = _list_payload(counterparties_result, "counterparties")
    wallet = wallets[0] if wallets else {}
    wallet_limits = wallet.get("limits") if isinstance(wallet.get("limits"), dict) else {}
    session_policy_ids = set(session.policy_ids.values())

    values: dict[str, float | None] = {
        "maxTransaction": None,
        "approvalThreshold": None,
        "dailyLimit": None,
        "weeklyLimit": None,
        "monthlyLimit": None,
    }
    control_ceilings: dict[str, float | None] = {
        "maxTransaction": _number(wallet_limits.get("perTransaction")),
        "approvalThreshold": None,
        "dailyLimit": _number(wallet_limits.get("daily")),
        "weeklyLimit": _number(wallet_limits.get("weekly")),
        "monthlyLimit": _number(wallet_limits.get("monthly")),
    }
    allowlist_results: list[bool] = []
    public_policies: list[dict[str, Any]] = []
    rule_map = {
        "MAX_AMOUNT": "maxTransaction",
        "REQUIRE_APPROVAL_ABOVE": "approvalThreshold",
        "DAILY_LIMIT": "dailyLimit",
        "WEEKLY_LIMIT": "weeklyLimit",
        "MONTHLY_LIMIT": "monthlyLimit",
    }

    for policy in policies:
        if policy.get("isActive") is False:
            continue
        policy_id = str(policy.get("id") or policy.get("policyId") or "")
        is_session_policy = policy_id in session_policy_ids
        rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
        public_rules: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_type = str(rule.get("type") or rule.get("ruleType") or "").upper()
            value = rule.get("value")
            public_rules.append({"type": rule_type, "value": value})
            if rule_type in rule_map:
                number = _number(value)
                key = rule_map[rule_type]
                if number is not None and (values[key] is None or number < float(values[key])):
                    values[key] = number
                if not is_session_policy:
                    control_ceilings[key] = _lower_ceiling(control_ceilings[key], number)
            elif rule_type == "ALLOWED_COUNTERPARTIES":
                addresses = _parse_list(value)
                allowlist_results.append(merchant_address.lower() in set(addresses))
        public_policies.append({
            "name": str(policy.get("name") or "Conto policy"),
            "description": str(policy.get("description") or ""),
            "status": "ACTIVE" if policy.get("isActive", True) else "PAUSED",
            "rules": public_rules,
        })

    wallet_rule_map = {
        "maxTransaction": "perTransaction",
        "dailyLimit": "daily",
        "weeklyLimit": "weekly",
        "monthlyLimit": "monthly",
    }
    for key, wallet_key in wallet_rule_map.items():
        values[key] = _lower_ceiling(values[key], wallet_limits.get(wallet_key))

    checkout_spent = {
        "today": session.checkout_spend.today,
        "thisWeek": session.checkout_spend.this_week,
        "thisMonth": session.checkout_spend.this_month,
    }
    combined = checkout_spent
    remaining = {
        "today": max(float(values["dailyLimit"]) - combined["today"], 0)
        if values["dailyLimit"] is not None else None,
        "thisWeek": max(float(values["weeklyLimit"]) - combined["thisWeek"], 0)
        if values["weeklyLimit"] is not None else None,
        "thisMonth": max(float(values["monthlyLimit"]) - combined["thisMonth"], 0)
        if values["monthlyLimit"] is not None else None,
    }

    registered = []
    for item in counterparties:
        address = str(item.get("address") or "").lower()
        if address == merchant_address.lower() or str(item.get("name") or "") == merchant_name:
            registered.append({
                "name": str(item.get("name") or merchant_name),
                "status": str(item.get("approvalStatus") or "PENDING").upper(),
            })

    return {
        "agent": {
            "name": str(agent.get("name") or session.agent_name),
            "status": str(agent.get("status") or "UNKNOWN").upper(),
            "sessionScoped": True,
            "expiresAt": session.expires_at,
        },
        "spend": {
            "currency": "USD",
            **values,
            "spent": combined,
            "remaining": remaining,
            "walletCeilings": {
                "maxTransaction": _number(wallet_limits.get("perTransaction")),
                "dailyLimit": _number(wallet_limits.get("daily")),
                "weeklyLimit": _number(wallet_limits.get("weekly")),
                "monthlyLimit": _number(wallet_limits.get("monthly")),
            },
            "controlCeilings": control_ceilings,
        },
        "merchants": {
            "mode": "RESTRICTED",
            "current": {
                "name": merchant_name,
                "allowed": bool(allowlist_results) and all(allowlist_results),
                "registered": bool(registered),
            },
            "registered": registered,
        },
        "checkout": {
            "label": "Stripe Checkout",
            "settlement": "Stripe test mode",
            "spent": checkout_spent,
        },
        "policies": public_policies,
        "manage": {
            "agent": f"https://conto.finance/agents/{session.agent_id}",
            "policies": "https://conto.finance/policies",
            "merchants": "https://conto.finance/counterparties",
        },
    }
