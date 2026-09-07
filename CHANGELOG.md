# Changelog

## 0.1.0 — 2026-09-07

Initial developer preview of Conto Shopping Starter: one shopping storefront, Conto purchase authorization, and Stripe test checkout. Includes portable setup and diagnostics, a catalog adaptation guide, checkout regression tests, and the bundled technical introduction.

Validated with an extracted source bundle, dedicated sandbox credentials, blocked and approved purchases, a human approval, and a completed $34 Stripe test payment. Local checkout-summary routing is fixed. Tests cover 45 Python cases and 3 JavaScript routing cases; the storefront production build passes.


## 0.1.0-rc.2 — sandbox walkthrough verified

- Fixed local checkout summaries polling the storefront port instead of the configured API.
- Added three URL-routing regressions covering local, hosted, and untrusted handoff origins.
- Clarified that the approval threshold must be no higher than the per-purchase limit in the blocked-purchase walkthrough.
- Verified an extracted release copy with real Anthropic and Conto credentials and a completed Stripe test payment.


## 0.1.0-rc.1 — shopping developer preview

- Focused the Conto integration on one shopping storefront and checkout workflow.
- Added `./dev` commands for setup, local diagnostics, start, tests, build, and deployment preparation.
- Added private session-key generation, configuration precedence checks, and actionable setup instructions.
- Simplified deployment to one storefront and one API at the root of a dedicated domain.
- Included checkout and catalog adaptation guides, webhook verification tests, and CI.

The app uses fictional products and Stripe test mode.
