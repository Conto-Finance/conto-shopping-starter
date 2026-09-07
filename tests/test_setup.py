import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from config import create_env, load_config, config_errors, REQUIRED


class SetupTests(unittest.TestCase):
    def test_setup_generates_private_secret_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.example").write_text("SHOPPING_SESSION_ENCRYPTION_KEY=\n")
            self.assertTrue(create_env(root))
            first = (root / ".env").read_text()
            self.assertGreaterEqual(len(first.split("=", 1)[1].strip()), 32)
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            self.assertFalse(create_env(root))
            self.assertEqual((root / ".env").read_text(), first)

    def test_explicit_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"CONTO_ORG_API_KEY": "from-shell"}, clear=True):
            root = Path(tmp)
            (root / ".env").write_text("CONTO_ORG_API_KEY=from-file\n")
            load_config(root)
            self.assertEqual(os.environ["CONTO_ORG_API_KEY"], "from-shell")

    def test_diagnostics_reject_live_keys_and_do_not_include_secret_values(self):
        env = {key: "configured" for key in REQUIRED}
        env["STRIPE_TEST_SECRET_KEY"] = "sk_live_not-allowed"
        env["SHOPPING_SESSION_ENCRYPTION_KEY"] = "a" * 43
        errors = " ".join(config_errors(env))
        self.assertIn("test-mode", errors)
        self.assertNotIn(env["STRIPE_TEST_SECRET_KEY"], errors)
        env["STRIPE_TEST_SECRET_KEY"] = "sk_test_example"
        self.assertEqual(config_errors(env), [])
        env["VERCEL"] = "1"
        self.assertTrue(any("SHOPPING_AGENT_ORIGIN" in e for e in config_errors(env)))
