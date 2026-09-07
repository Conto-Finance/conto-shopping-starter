# 0.1.0 validation

A fresh source extraction was installed and exercised with existing dedicated sandbox accounts on 2026-09-07. This checked the download/setup path and connected shopping flow; it was not a new-customer signup test.

- Real Claude conversation located a product and added it to the cart.
- Live Conto controls blocked a $34 cart under a $20 purchase limit.
- A $100 purchase limit and $20 approval threshold required human review.
- Approval opened Stripe sandbox checkout for the matching $34 cart.
- A standard Stripe test card completed payment; the verified return recorded one order and cleared purchased cart quantities.
- Spending showed $34 used and $466 remaining from a $500 daily budget.
- A subsequent $34 cart under a $50 approval threshold received automatic approval without another payment being submitted.

The walkthrough found and fixed a local URL-routing bug: checkout summaries must use the configured API base, not always the storefront origin. Three JavaScript regressions cover local routing, hosted routing, and untrusted handoff origins. The suite also contains 45 Python tests, upstream contract checks, and backend smoke checks.

The connected walkthrough ran with Python 3.11 and Node 25.5.0. GitHub Actions verifies a fresh install, tests, and production build on Python 3.11 and Node 22. No service credentials are needed in CI.

External webhook delivery, Link login, hosted KV persistence, public deployment, and new-customer onboarding were not exercised. Automated tests cover webhook signatures and handler wiring. No real purchase or wallet transfer occurred. See the README's developer-preview boundaries before adapting the example.
