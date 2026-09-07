# Upstream provenance

- Repository: https://github.com/anthropics/commerce-agents
- Revision: `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf`
- License: Apache-2.0; original notices are retained on modified upstream files.

Bootstrap fetches this fixed revision and applies `overlay/` at matching relative paths. The full upstream source is a generated dependency. Only the retail shopping integration is launched, built, and exposed by this starter's deployment.

The Conto integration originated in commit `a9be2e499ea124acb65a358a0a5bb98d2dd654dc`, path `examples/anthropic-shopping`. This shopping-focused starter was prepared on 2026-09-07. It contains shopping and shared storefront overlays, checkout and control-plane adapters, persistence support, and regression tests.

Conto modifications add purchase controls, checkout authorization, Stripe test checkout, completion handling, and shopping UI changes. Starter packaging adds local setup commands, configuration diagnostics, documentation, tests, and root-domain deployment configuration. The Conto platform source and credentials are not included.

Conto-authored starter additions are licensed under Apache-2.0. Anthropic and Claude names identify upstream technology, not an endorsement.
