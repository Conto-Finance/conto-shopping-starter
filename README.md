# Conto Shopping Starter

**Build a Claude shopping assistant with spending controls and an authorized checkout.**

A developer starter for teams that own a store's catalog and checkout backend. Claude helps a shopper find products and build a cart. Conto checks the purchase against spending limits, merchant permissions, and approval rules before the backend opens Stripe Checkout.

Start with a working shopping app, follow the checkout code, then connect your own catalog. This is an independent Conto integration built on Anthropic's [Claude Commerce Agents](https://github.com/anthropics/commerce-agents) retail template.

[Technical introduction](blog/introducing-conto-shopping-starter.md) · [Try the shopping demo](https://conto.finance/demos/claude/retail) · [Setup](docs/SETUP.md) · [Connect your store](docs/CONNECT-YOUR-STORE.md) · [Checkout walkthrough](docs/CHECKOUT.md)

## Run it

Prerequisites: Git, Python 3.11+, Node 22+, and npm. Running the connected app also requires an Anthropic API key, a Stripe **test** secret key, and three Conto account values. [Request starter access from Conto](https://conto.finance/contact) for the organization key, owner membership ID, and dedicated testnet wallet ID. Account provisioning is not automated in this preview.

Clone the repository and run:

```bash
git clone https://github.com/Conto-Finance/conto-shopping-starter.git
cd conto-shopping-starter
./dev setup
# Fill in the five account values in the generated .env.
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

Conto approves permission to spend. Stripe processes the payment, and the shopper supplies payment details on Stripe's hosted page. This starter does not execute a wallet transfer or provide autonomous purchasing on unrelated merchant websites.

## What you get

- A shopping storefront with product search, cart, conversation, purchase checks, and order display.
- Server-side adapters for Conto session agents, policy controls, purchase requests, and approval handling.
- Stripe test checkout bound to the server-issued cart, with verified returns and webhooks.
- Local setup diagnostics, checkout regressions, a storefront build, and GitHub Actions configuration.
- An adaptation guide and a single-store deployment configuration.

The main integration is `checkout_handoff` in [`overlay/examples/retail/api/main.py`](overlay/examples/retail/api/main.py). [`conto_checkout.py`](overlay/examples/retail/api/conto_checkout.py) owns the checkout lifecycle; [`conto_control_plane.py`](overlay/examples/retail/api/conto_control_plane.py) calls Conto. [Connect your store](docs/CONNECT-YOUR-STORE.md) explains what to replace and what to preserve.

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

The demo intentionally lets its visitor edit policies and exercise approvals. Production applications must restrict these actions to authenticated budget owners and approvers. Fulfillment, refunds, resource cleanup, and operational reconciliation remain application work. Authorization is checked before creating a checkout session; this preview does not claim continuous policy enforcement after a Stripe checkout URL has been issued or aggregate budget guarantees across concurrent purchases.

This starter is licensed under Apache-2.0; see [LICENSE](LICENSE) and [upstream attribution](UPSTREAM.md). The hosted Conto service is a separate dependency. Claude and Anthropic names identify upstream technology and do not imply endorsement.
