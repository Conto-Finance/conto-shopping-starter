"""Sandbox opt-in must fail before any credentialed client or store is created."""
import os
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unittest.mock import patch, Mock

from demo.control_plane import ContoControlError, ContoSessionManager
from conto_checkout import ContoCheckoutGateway
from config import config_errors, REQUIRED


class SandboxBoundaryTests(unittest.TestCase):
    def test_default_constructor_rejects_implicit_or_truthy_opt_in(self):
        for value in (False, None, "true", 1):
            with self.subTest(value=value):
                admin, store, box = Mock(), Mock(), Mock()
                with self.assertRaisesRegex(ContoControlError, "disabled"):
                    ContoSessionManager(admin, store, box, wallet_id="test-wallet",
                                        sandbox_demo_enabled=value)
                self.assertEqual(admin.mock_calls, [])
                self.assertEqual(store.mock_calls, [])
                self.assertEqual(box.mock_calls, [])

    def test_runtime_and_host_factory_fail_before_accessing_credentials(self):
        for value in (None, "", "false", "TRUE", "1", " true "):
            env = {} if value is None else {"CONTO_SANDBOX_DEMO_ENABLED": value}
            with self.subTest(value=value), patch.dict(os.environ, env, clear=True):
                with patch("demo.control_plane.JsonTransport") as transport, patch("demo.control_plane.store_from_env") as store:
                    for factory in (ContoSessionManager.from_env, ContoCheckoutGateway.from_env):
                        with self.assertRaisesRegex(ContoControlError, "CONTO_SANDBOX_DEMO_ENABLED=true"):
                            factory()
                    transport.assert_not_called()
                    store.assert_not_called()

    def test_explicit_sandbox_opt_in_constructs_without_network_requests(self):
        env = {
            "CONTO_SANDBOX_DEMO_ENABLED": "true",
            "CONTO_ORG_API_KEY": "test-only-key",
            "CONTO_SHARED_WALLET_ID": "test-wallet",
            "SHOPPING_SESSION_ENCRYPTION_KEY": "test-only-session-secret",
        }
        with patch.dict(os.environ, env, clear=True), patch("demo.control_plane.JsonTransport") as transport:
            manager = ContoSessionManager.from_env()
            self.assertIsInstance(manager, ContoSessionManager)
            transport.return_value.request.assert_not_called()

    def test_doctor_requires_explicit_opt_in_even_with_valid_credential_shapes(self):
        env = {key: "configured" for key in REQUIRED}
        env.update(STRIPE_TEST_SECRET_KEY="sk_test_example", SHOPPING_SESSION_ENCRYPTION_KEY="x" * 43)
        self.assertTrue(any("CONTO_SANDBOX_DEMO_ENABLED" in error for error in config_errors(env)))
        env["CONTO_SANDBOX_DEMO_ENABLED"] = "true"
        self.assertEqual(config_errors(env), [])
