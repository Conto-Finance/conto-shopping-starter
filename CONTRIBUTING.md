# Working on the shopping starter

Use Python 3.11+ and Node 22+. Run `./dev setup` once. Tests and builds do not need external service credentials; connected conversations and checkout do.

Keep changes in `overlay/`, `scripts/`, or `tests/`. The pinned Anthropic source is reconstructed under `.runtime/commerce-agents`; that directory is disposable and excluded from Git and release archives. To modify an upstream file that has no overlay, copy it to the same relative path under `overlay/` first. Preserve upstream copyright headers and describe your modifications.

After application changes, run `./dev setup`, `./dev test`, and `./dev build`. Tests prepare the generated deployment files before checking backend wiring. Add behavioral tests for changes to cart binding, approval, payment completion, configuration, or persistence. Never use real provider credentials in tests.

A public bug report should describe the command, expected behavior, actual behavior, and sanitized error text. Do not include `.env`, shopper transcripts, credentials, or payment details. For account setup problems, use your Conto onboarding contact.
