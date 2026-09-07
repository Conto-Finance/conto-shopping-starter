# Follow one shopping checkout

Use one cart throughout this walkthrough. Ask Claude for a camping tent under $250, add the selected product, and note its actual total. The catalog is fictional; recommendation wording and the selected product can vary.

## Exercise the three outcomes

1. **Block:** In Controls, enable the agent and merchant, set cumulative budgets above the cart total, and lower the per-purchase limit below it, and set the approval threshold no higher than that purchase limit. Apply the controls and ask to check out. Expect a failed purchase-limit check and no Stripe checkout link.
2. **Review:** Raise the per-purchase limit above the cart total and lower the approval threshold below it. Apply and ask to check out again. Expect a pending approval action. Use the demo approval page to approve or reject it.
3. **Allow:** Set both the purchase limit and approval threshold above the cart total, keeping cumulative budgets sufficient. Apply and ask to check out. Expect an approved result and a route to Stripe test checkout.

Alternatively, disable the merchant or agent and verify that the corresponding check blocks checkout. Stay within the control ceilings displayed by the demo; a limit larger than the delegated wallet ceiling cannot be applied.

At Stripe Checkout, use only the test details provided by the demo. After payment, follow the return to the storefront and check the order state and recorded spending. Approval alone must never produce a paid order.

## Trace it in code

| Step | Implementation | Boundary |
| --- | --- | --- |
| Build cart | `checkout_handoff` in `overlay/examples/retail/api/main.py` | Use server catalog prices and quantities |
| Stage | `ContoCheckoutGateway.stage` | Bind checkout to cart, session, and policy snapshot |
| Authorize | `ContoCheckoutGateway.authorize` | Call Conto's `/api/sdk/payments/request` with `autoExecute: false` |
| Review | `ContoCheckoutGateway.decide` | Resolve the pending Conto approval through the demo owner action |
| Open payment | `_prepare_stripe_unlocked` | Create Stripe test checkout only after approved status |
| Confirm | `complete_stripe` and `verify_stripe_return` | Validate provider session, cart fingerprint, paid status, amount, and currency |
| Record | `record_checkout_spend` and the completion callback | Record purchase once and reconcile purchased cart quantities |

The backend may reject an obviously blocked cart during its policy checks before creating a Conto payment request. Allowed or reviewable carts reach the Conto API for a decision. Payment credentials stay with Stripe, while organization and session API keys remain on the server.

## Current enforcement boundary

The adapter gates creation of a Stripe Checkout Session. Existing approved checkout sessions can be reused, so changing policy after a checkout URL is issued does not currently invalidate that URL. A production adaptation needs a deliberate strategy for session expiration, revocation, concurrent budget reservations, and reconciliation. Do not promise that the demo kill switch cancels a checkout already open at Stripe.

The visitor's ability to approve or edit policy is for testing. Replace it with authenticated roles before accepting real purchases. Test mode does not fulfill an order or move money from the Conto wallet.
