# Connect a buyer agent

This starter is a sandbox fixture with a fictional catalog and merchant-owned Stripe test checkout. To build a supported Conto integration, put policy evaluation in the buyer's agent runtime and require authorization at its payment executor. The agent owner or budget holder sets policy; merchant catalog data, offers, and checkout details supply transaction information only. Follow the [buyer-side architecture](https://conto.finance/docs/guides/buyer-side-control) and verify the capabilities of your chosen payment path.

## Start at the checkout handoff

Read `AnthropicShoppingRetail.checkout_handoff` in `overlay/examples/retail/api/main.py`. It converts the authoritative cart into `StagedCart`, asks the gateway to stage and authorize it, then returns a `CheckoutHandoff` to the conversation. The following is the fixture handoff, inside a backend that owns `self.conto`; it is not a buyer payment executor:

```python
from shopping_agent import CheckoutHandoff
from retail.api.conto_checkout import StagedCart

async def checkout_handoff(self, session, cart):
    state = await self.conto.stage(StagedCart(
        session_id=session.session_id,
        amount=cart.subtotal,
        currency=cart.currency,
        item_labels=tuple(f"{item.title} x {item.quantity}" for item in cart.items),
        merchant_name=self.conto.sessions.merchant_name,
        merchant_address=self.conto.sessions.merchant_address_for(session.session_id),
        items=tuple(item.model_dump(mode="json", exclude_none=True) for item in cart.items),
    ))
    state = await self.conto.authorize(state.checkout_id, state.csrf_token)
    return [CheckoutHandoff(url=self.conto.handoff_url(state), label="Review purchase")]
```

`cart` must come from your backend. Do not take price, merchant identity, or permission from model-generated text or unvalidated browser inputs. The example uses cart subtotal as the charge amount; your implementation must bind the final amount including applicable shipping, tax, and fees before authorization. Reauthorize if those values change.

## Replace the catalog and cart implementation

The generated upstream runtime provides `examples/retail/api/mock_retail.py` and `examples/retail/data/`. Read those contracts, then copy the files you want to change into the matching paths under `overlay/`. Implement the catalog reads, inventory behavior, and session cart storage against your own backend while preserving the shapes expected by Anthropic's `StorefrontBackend`.

Run `./dev setup` to reapply source changes. The full upstream runtime is a dependency; `.runtime` is generated and excluded from the release. Add your own server-side tests for prices, taxes, unavailable items, quantity changes, and tampered input.

## Replace the merchant and account model

The sample uses a fictional ACME merchant and generated addresses for its testnet authorization context. Its resource provisioning, policy mutation, and visitor self-approval live in `demo/control_plane.py` and require explicit `CONTO_SANDBOX_DEMO_ENABLED=true`. Replace that entire demo account model with buyer-owned agents, authorized payment sources, and the recipient identity from the actual transaction. A merchant does not install Conto, call its policy APIs, or approve the buyer's spending.

Map the authenticated buyer to its own Conto agent and budget holder. Restrict policy changes to authorized buyer administrators and resolve review through owner-authorized approvers. Keep administration credentials separate from agent runtime credentials, and hold signing or payment credentials in a buyer executor that cannot be bypassed by the agent. A merchant request cannot select approvers, relax policy, or release funds; Conto Pay recipient profiles and request links are optional conveniences.

## Keep authorization separate from payment

Reuse the decision sequence: bind amount, currency, recipient, and material purchase details, then obtain allow, review, or deny before the buyer executor signs or uses payment credentials. Denied and unresolved requests must never execute. Changing the transaction requires a fresh authorization. An unrestricted signer can bypass a precheck; the merchant-owned Stripe adapter in this fixture does not implement this buyer boundary. After payment, verify the provider's result and match the checkout ID, fingerprint, amount, and currency before recording success. Keep the completion path idempotent for duplicate webhooks and retries.

The supplied Stripe adapter intentionally rejects live keys. Moving to real payments requires an explicit production implementation: authenticated approval roles, policy revocation behavior, concurrent budget reservations, fulfillment, refunds, and reconciliation. Do not remove the test-key guard as a shortcut to production.

## Source map

| File | Your likely change |
| --- | --- |
| `overlay/examples/retail/api/main.py` | Connect your shopping backend and stage the final cart |
| `overlay/examples/retail/api/demo/control_plane.py` | Replace demo administration with buyer identity, policy ownership, and authorized approvals |
| `overlay/examples/retail/api/conto_checkout.py` | Study the fixture lifecycle; implement and verify your buyer executor separately |
| `overlay/examples/retail/api/production_store.py` | Use persistence appropriate to your deployment |
| `overlay/examples/retail/storefront-web/` | Fit shopping and control surfaces into your app |
| `tests/` | Add your store's purchase and failure cases |

The reusable code is source included in the starter. It is not yet a published Python package with a stable public API.
