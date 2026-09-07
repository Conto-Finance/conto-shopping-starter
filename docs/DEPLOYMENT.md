# Deploy a shopping preview

The generated Vercel configuration contains one Next.js storefront and one FastAPI backend. `/api/*` routes to the backend and all other paths to the storefront on the same domain, following the current [Vercel Services configuration](https://vercel.com/docs/services). No Conto website proxy or multi-store routing is required.

1. Run `./dev test` and `./dev build`.
2. Run `./dev prepare-deploy`; deploy the generated directory it prints as a separate Vercel project.
3. Configure the six server values from `.env.example`, including your generated encryption key. Also configure `KV_REST_API_URL`, `KV_REST_API_TOKEN`, and `SHOPPING_AGENT_ORIGIN=https://YOUR-SHOPPING-DOMAIN`.
4. Register one Stripe test webhook at `https://YOUR-SHOPPING-DOMAIN/api/conto/stripe/webhook` for `checkout.session.completed` and `checkout.session.async_payment_succeeded`. Set its matching `STRIPE_WEBHOOK_SECRET` and redeploy.
5. Verify streamed chat, controls, blocked/reviewed/allowed purchases, Stripe return URLs, webhook retries, and persistent order/cart state using your test accounts.

Serve this starter at the root of its own domain. Leave `NEXT_PUBLIC_API_URL` unset for the hosted storefront so browser API calls use the same origin. No `NEXT_PUBLIC_DEMO_DEPLOY` or `DEMO_PUBLIC_ORIGIN` configuration is used.

Hosted state uses KV adapters; local state uses memory. Use an isolated KV store and Conto resources for the preview. Changing the encryption key while sessions are active invalidates their encrypted credentials.

The webhook signing secret is captured by the gateway at startup. A missing or mismatched secret fails signature validation. Verify actual event delivery with Stripe; a mocked test cannot confirm deployment credentials or routing. Conto authorization does not itself mark Stripe payment complete.

Preparation copies source into the ignored runtime and replaces its requirements file for deployment. The next `./dev setup` restores upstream requirements before reinstalling dependencies. This repository does not automatically publish, provision paid services, or create remote infrastructure.
