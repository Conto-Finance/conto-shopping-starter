# Conto Shopping Starter

**Explore Claude shopping, Conto policy evaluation, and Stripe test checkout in a sandbox fixture.**

A developer preview for studying a shopping conversation and purchase-policy decisions. Claude helps a shopper find fictional products and build a cart. The fixture calls Conto before creating a merchant-owned Stripe test checkout; this demonstrates policy evaluation and a test checkout handoff, not control over the buyer's payment credentials.

Conto belongs in the buyer's agent runtime, payment tools, or signing environment, where the agent owner sets policy and the buyer's executor withholds signing or credentials until authorization. Merchants supply transaction details and receive payment through supported rails. Start with this fixture, then follow the buyer integration guide. This is an independent Conto integration built on Anthropic's [Claude Commerce Agents](https://github.com/anthropics/commerce-agents) retail template.

[Original 0.1.0 article (historical fixture)](blog/introducing-conto-shopping-starter.md) · [Try the shopping demo](https://conto.finance/demos/claude/retail) · [Setup](docs/SETUP.md) · [Buyer integration guide](docs/CONNECT-YOUR-STORE.md) · [Checkout walkthrough](docs/CHECKOUT.md)

## Run it

Prerequisites: Git, Python 3.11+, Node 22+, and npm. Running the connected app also requires an Anthropic API key, a Stripe **test** secret key, and three Conto account values. [Request starter access from Conto](https://conto.finance/contact) for the organization key, owner membership ID, and dedicated testnet wallet ID. Account provisioning is not automated in this preview.

Clone the repository and run:

```bash
git clone https://github.com/Conto-Finance/conto-shopping-starter.git
cd conto-shopping-starter
./dev setup
# Fill in the five sandbox account values in the generated .env.
# Set CONTO_SANDBOX_DEMO_ENABLED=true only for your isolated sandbox.
./dev doctor
./dev start
```

Open **http://localhost:3000**. The API runs at **http://localhost:8000**. Setup generates the session encryption key and preserves an existing `.env`; environment variables take precedence over that file. Doctor checks configuration locally without sending credentials to any service.

You can inspect and test the integration before getting service credentials:

```bash
./dev setup
./dev test
./dev build
```

These checks use mocks and fake credentials. The connected shopping app needs your own accounts. Anthropic usage and hosting can incur charges; all purchases in this starter use fictional products and Stripe test mode.

## Make your first checkout decision

Ask: **“Find a camping tent under $250 and add it to my cart.”** Open **Controls** and compare the actual cart total with the rules you set. Use the [walkthrough](docs/CHECKOUT.md) to exercise a blocked purchase, a purchase needing approval, and an allowed purchase followed by test checkout.

The code enforces the handoff:

```text
Claude selects products -> backend builds authoritative cart
                                      |
                              Conto authorization
                           /          |           \
                       blocked      review       allowed
                          |           |              |
                        stop      owner approval     |
                                      +--------------+
                                                     |
                                              Stripe Checkout
                                                     |
                                          verify and record payment
```

This diagram describes the fixture only. Stripe processes a test payment using details supplied by the shopper on its hosted page. The merchant-side session gate does not establish a buyer-side execution boundary. The starter neither executes a wallet transfer nor provides autonomous purchasing on unrelated merchant websites; supported buyer paths are described in the [canonical architecture](https://conto.finance/docs/guides/buyer-side-control).

## What you get

- A shopping storefront with product search, cart, conversation, purchase checks, and order display.
- Server-side adapters for Conto session agents, policy controls, purchase requests, and approval handling.
- Stripe test checkout bound to the server-issued cart, with verified returns and webhooks.
- Local setup diagnostics, checkout regressions, a storefront build, and GitHub Actions configuration.
- An adaptation guide and a single-store deployment configuration.

The main integration is `checkout_handoff` in [`overlay/examples/retail/api/main.py`](overlay/examples/retail/api/main.py). [`conto_checkout.py`](overlay/examples/retail/api/conto_checkout.py) owns the checkout lifecycle; [`demo/control_plane.py`](overlay/examples/retail/api/demo/control_plane.py) contains opt-in demo provisioning, policy mutation, and visitor approval handling. [Buyer integration guide](docs/CONNECT-YOUR-STORE.md) explains what to replace and what to preserve.

## Developer commands

| Command | Result |
| --- | --- |
| `./dev setup` | Fetch pinned upstream, install dependencies, create a private `.env` if missing |
| `./dev doctor` | Identify missing or malformed configuration without external API calls |
| `./dev start` | Start the shopping storefront and API |
| `./dev test` | Run mocked checkout, setup, and backend checks |
| `./dev build` | Build the shopping storefront |
| `./dev prepare-deploy` | Generate the Vercel application; does not deploy it |

Make application changes in `overlay/`; `./dev setup` reapplies them to `.runtime/commerce-agents`. The generated runtime includes Anthropic's full pinned source tree as a dependency, but this starter launches, tests its host, builds, and deploys only the shopping integration. See [contributing](CONTRIBUTING.md) and [deployment](docs/DEPLOYMENT.md).

## Developer preview

The demo is disabled by default. Setting `CONTO_SANDBOX_DEMO_ENABLED=true` explicitly enables resource provisioning, visitor policy edits, and self-approval; use only an isolated sandbox organization and dedicated testnet wallet. This setting acknowledges demo behavior and does not verify the account or wallet remotely. A buyer integration must replace these fixture actions with authenticated budget-owner administration and independently authorized approvals, and gate its own executor. A merchant offer or checkout cannot change buyer policy or authorize release of buyer funds. Fulfillment, refunds, resource cleanup, and operational reconciliation remain application work. Authorization is checked before creating a checkout session; this preview does not claim continuous policy enforcement after a Stripe checkout URL has been issued or aggregate budget guarantees across concurrent purchases.

This starter is licensed under Apache-2.0; see [LICENSE](LICENSE) and [upstream attribution](UPSTREAM.md). The hosted Conto service is a separate dependency. Claude and Anthropic names identify upstream technology and do not imply endorsement.
