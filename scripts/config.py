"""Local setup validation. Never prints credential values or calls external services."""
from pathlib import Path
import os
import secrets
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("ANTHROPIC_API_KEY", "CONTO_ORG_API_KEY", "CONTO_OWNER_MEMBERSHIP_ID",
            "CONTO_SHARED_WALLET_ID", "SHOPPING_SESSION_ENCRYPTION_KEY", "STRIPE_TEST_SECRET_KEY")


def load_config(root=ROOT):
    load_dotenv(root / ".env", override=False)


def config_errors(env):
    errors = [f"{name}: missing" for name in REQUIRED if not env.get(name, "").strip()]
    stripe = env.get("STRIPE_TEST_SECRET_KEY", "").strip()
    if stripe and not stripe.startswith("sk_test_"):
        errors.append("STRIPE_TEST_SECRET_KEY: use a Stripe test-mode secret key")
    secret = env.get("SHOPPING_SESSION_ENCRYPTION_KEY", "")
    if secret and len(secret) < 32:
        errors.append("SHOPPING_SESSION_ENCRYPTION_KEY: use at least 32 random characters")
    for name in ("CONTO_API_URL", "SHOPPING_AGENT_ORIGIN"):
        if env.get(name):
            url = urlparse(env[name])
            if (url.scheme not in ("https", "http") or not url.netloc
                    or url.username or url.password or url.query or url.fragment
                    or url.path not in ("", "/")):
                errors.append(f"{name}: use an http(s) origin without credentials, path, or query")
    if bool(env.get("KV_REST_API_URL")) != bool(env.get("KV_REST_API_TOKEN")):
        errors.append("KV_REST_API_URL and KV_REST_API_TOKEN: configure both or neither")
    if env.get("VERCEL"):
        for name in ("SHOPPING_AGENT_ORIGIN", "KV_REST_API_URL", "KV_REST_API_TOKEN", "STRIPE_WEBHOOK_SECRET"):
            if not env.get(name, "").strip():
                errors.append(f"{name}: required for hosted checkout")
    return errors


def create_env(root=ROOT):
    path = root / ".env"
    content = (root / ".env.example").read_text().replace(
        "SHOPPING_SESSION_ENCRYPTION_KEY=", "SHOPPING_SESSION_ENCRYPTION_KEY=" + secrets.token_urlsafe(32), 1
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    return True


if __name__ == "__main__":
    if sys.argv[1:] == ["setup"]:
        created = create_env()
        print("Created a private .env with a generated encryption key." if created else "Existing .env preserved.")
        print("Fill in the five account credentials in .env, then run ./dev doctor.")
    else:
        load_config()
        errors = config_errors(os.environ)
        for error in errors:
            print(error)
        if errors:
            print("See docs/SETUP.md for where each value comes from.")
            raise SystemExit(1)
        print("Local configuration is complete. Credential validity has not been checked with external services.")
        print("Run ./dev start to open the shopping app.")
