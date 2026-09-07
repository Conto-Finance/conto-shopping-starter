# Build a shopping agent with Conto and Claude

A shopping agent can find a product, compare alternatives, and put an order together. Checkout introduces another question: is this agent allowed to spend this amount with this merchant? That decision belongs in the application backend, where the cart and spending rules can be checked before payment begins.

[Conto Shopping Starter](https://github.com/Conto-Finance/conto-shopping-starter) gives developers a working example of that boundary. It combines Anthropic's [Claude Commerce Agents](https://github.com/anthropics/commerce-agents) retail template with Conto authorization and Stripe test checkout. The repo includes a shopping storefront, a Python API, setup commands, regression tests, and a guide to connecting your own catalog. It is intended for teams that control their store's backend and payment-session creation.

The integration starts at `checkout_handoff`. The backend constructs a `StagedCart` from its product records and quantities, then passes it through the checkout gateway:

```python
state = await self.conto.stage(staged_cart)
state = await self.conto.authorize(state.checkout_id, state.csrf_token)
```

Staging binds the checkout to the cart, shopper session, and policy snapshot. Authorization checks merchant access, agent status, purchase limits, cumulative spending, and approval requirements. A blocked purchase stops there; a purchase needing review waits for a decision. An approved purchase lets the backend create a Stripe Checkout Session.

Conto supplies permission to proceed. Stripe supplies the payment result.

The current adapter calls Conto's payment-request API with `autoExecute: false`. That request uses a dedicated testnet wallet as its policy context, but the starter never executes a wallet transfer. The shopper enters payment details on Stripe's hosted page. On return, the backend verifies the Stripe session against the checkout ID, cart fingerprint, amount, currency, and paid status before recording spending and removing purchased quantities from the cart. A webhook handler supports the same completion path.

We exercised this with a $34 cart. A $20 purchase limit blocked it. Raising the purchase limit to $100 while keeping approval required above $20 produced a review request; approving it opened Stripe's sandbox checkout. After the test payment, the app recorded the order and showed $34 spent, with $466 remaining in the session's $500 daily budget. A subsequent $34 cart under a $50 approval threshold could proceed without review.

To run the starter:

```bash
git clone https://github.com/Conto-Finance/conto-shopping-starter.git
cd conto-shopping-starter
./dev setup
# Fill in the account values in .env.
./dev doctor
./dev start
```

Setup generates a private session-encryption key. Running the connected app requires Anthropic and Stripe test credentials, plus a Conto organization key, owner membership ID, and dedicated testnet wallet ID available through [Conto onboarding](https://conto.finance/contact). Developers can run the mocked tests and storefront build before configuring those accounts.

This first release is a developer preview with fictional products and test payments. Visitors can edit policies and exercise approvals to understand the flow; a production application must assign those actions to authenticated owners and approvers. The integration gates creation of a checkout session, so it does not automatically revoke a Stripe URL after a later policy change. Fulfillment, refunds, concurrent budget reservations, and operational reconciliation also need to be designed for the application.

[Start with the repository](https://github.com/Conto-Finance/conto-shopping-starter), follow the checkout, and use the included adaptation guide to connect your store.
