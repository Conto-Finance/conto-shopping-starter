# Connect your store

This starter fits a store whose application controls catalog data, cart state, and payment-session creation. The backend is the place where Conto can enforce a purchase decision. Shopping across third-party sites would require a different execution integration.

## Start at the checkout handoff

Read `AnthropicShoppingRetail.checkout_handoff` in `overlay/examples/retail/api/main.py`. It converts the authoritative cart into `StagedCart`, asks the gateway to stage and authorize it, then returns a `CheckoutHandoff` to the conversation. The following is the core pattern, inside a backend that owns `self.conto`:

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

The sample uses a fictional ACME merchant and session-scoped merchant addresses for its testnet authorization context. Changing a display name is not enough to connect a real merchant. Replace that provisioning in `conto_control_plane.py` with your Conto counterparty relationship, wallet context, and business identity mapping. Also review the ACME names used in `main.py` and Stripe line-item labels in `conto_checkout.py`.

Map your authenticated shopper/session to an appropriate Conto agent and define who owns its budget. Restrict organization-level policy changes to authorized administrators. Replace the demo's owner approval action with your approval workflow. Keep privileged Conto and Stripe keys off the browser and the Claude tool interface.

## Keep authorization separate from payment

Reuse the gateway pattern: a server-bound purchase gets an allow, review, or deny decision before the application creates a payment session. After payment, verify the provider's result and match the checkout ID, fingerprint, amount, and currency before recording success. Keep the completion path idempotent for duplicate webhooks and retries.

The supplied Stripe adapter intentionally rejects live keys. Moving to real payments requires an explicit production implementation: authenticated approval roles, policy revocation behavior, concurrent budget reservations, fulfillment, refunds, and reconciliation. Do not remove the test-key guard as a shortcut to production.

## Source map

| File | Your likely change |
| --- | --- |
| `overlay/examples/retail/api/main.py` | Connect your shopping backend and stage the final cart |
| `overlay/examples/retail/api/conto_control_plane.py` | Map identity, merchant relationship, policies, and spend accounting |
| `overlay/examples/retail/api/conto_checkout.py` | Keep the decision boundary; adapt provider completion and order handling |
| `overlay/examples/retail/api/production_store.py` | Use persistence appropriate to your deployment |
| `overlay/examples/retail/storefront-web/` | Fit shopping and control surfaces into your app |
| `tests/` | Add your store's purchase and failure cases |

The reusable code is source included in the starter. It is not yet a published Python package with a stable public API.
