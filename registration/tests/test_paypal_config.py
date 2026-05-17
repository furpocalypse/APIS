"""Tests for PayPal-related settings and SDK client initialization.

These tests exercise the module-level client construction in
``registration/paypal_payments.py`` (lines 47-64). Because that block
runs at import time, each test uses ``importlib.reload`` under
``override_settings`` to observe the effect of different settings.
"""

import contextlib
import importlib
import logging
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings, tag
from paypalserversdk.configuration import Environment
from prometheus_client import REGISTRY

from registration import paypal_payments as paypal_payments_module


def _unregister_paypal_metrics():
    for collector in list(REGISTRY._collector_to_names.keys()):
        names = REGISTRY._collector_to_names.get(collector, [])
        if any(n.startswith("paypal_requests") for n in names):
            with contextlib.suppress(KeyError):
                REGISTRY.unregister(collector)


@tag("paypal")
class PayPalConfigTests(SimpleTestCase):
    """Matrix D.1-D.4 — PayPal settings & SDK client initialization.

    The client instance is constructed at module import time at
    ``registration/paypal_payments.py:47-64``. To test setting-driven
    branches we reload the module under ``override_settings``.
    """

    def tearDown(self):
        """Restore the module to its default-settings state."""
        _unregister_paypal_metrics()
        importlib.reload(paypal_payments_module)

    # --- Environment selection (D.1) ---------------------------------

    @override_settings(PAYPAL_ENVIRONMENT="production")
    def test_environment_production_when_setting_starts_with_p(self):
        """PAYPAL_ENVIRONMENT='production' -> Environment.PRODUCTION.

        See registration/paypal_payments.py:52-56.
        """
        _unregister_paypal_metrics()
        importlib.reload(paypal_payments_module)
        self.assertEqual(
            paypal_payments_module.client.config.environment,
            Environment.PRODUCTION,
        )

    @override_settings(PAYPAL_ENVIRONMENT="sandbox")
    def test_environment_sandbox_when_setting_is_sandbox(self):
        """PAYPAL_ENVIRONMENT='sandbox' -> Environment.SANDBOX.

        See registration/paypal_payments.py:52-56.
        """
        _unregister_paypal_metrics()
        importlib.reload(paypal_payments_module)
        self.assertEqual(
            paypal_payments_module.client.config.environment,
            Environment.SANDBOX,
        )

    @override_settings(PAYPAL_ENVIRONMENT="staging")
    def test_environment_sandbox_for_unrecognized_string(self):
        """Any value not starting with 'p' -> Environment.SANDBOX.

        The production code tests ``PAYPAL_ENVIRONMENT.lower()[0] == 'p'``.
        See registration/paypal_payments.py:52-56.
        """
        _unregister_paypal_metrics()
        importlib.reload(paypal_payments_module)
        self.assertEqual(
            paypal_payments_module.client.config.environment,
            Environment.SANDBOX,
        )

    # --- Credentials (D.2) -------------------------------------------

    @override_settings(PAYPAL_CLIENT_ID="CID", PAYPAL_CLIENT_SECRET="SEC")
    def test_client_uses_configured_credentials(self):
        """Client is constructed with the configured OAuth credentials.

        See registration/paypal_payments.py:48-51.
        """
        _unregister_paypal_metrics()
        importlib.reload(paypal_payments_module)
        creds = paypal_payments_module.client.config.client_credentials_auth_credentials
        self.assertEqual(creds.o_auth_client_id, "CID")
        self.assertEqual(creds.o_auth_client_secret, "SEC")

    # --- Logging configuration (D.3 / D.4) ---------------------------

    @override_settings(DEBUG=True)
    def test_logging_config_respects_debug_true(self):
        """DEBUG=True -> log_body=True, log_headers=True.

        See registration/paypal_payments.py:57-63.
        """
        with (
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.RequestLoggingConfiguration"
            ) as mock_req,
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.ResponseLoggingConfiguration"
            ) as mock_resp,
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.LoggingConfiguration"
            ) as mock_logging,
        ):
            _unregister_paypal_metrics()
            importlib.reload(paypal_payments_module)

        mock_req.assert_called_once_with(log_body=True)
        mock_resp.assert_called_once_with(log_headers=True)
        mock_logging.assert_called_once_with(
            log_level=logging.INFO,
            request_logging_config=mock_req.return_value,
            response_logging_config=mock_resp.return_value,
        )

    @override_settings(DEBUG=False)
    def test_logging_config_respects_debug_false(self):
        """DEBUG=False -> log_body=False, log_headers=False.

        See registration/paypal_payments.py:57-63.
        """
        with (
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.RequestLoggingConfiguration"
            ) as mock_req,
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.ResponseLoggingConfiguration"
            ) as mock_resp,
            patch(
                "paypalserversdk.logging.configuration."
                "api_logging_configuration.LoggingConfiguration"
            ) as mock_logging,
        ):
            _unregister_paypal_metrics()
            importlib.reload(paypal_payments_module)

        mock_req.assert_called_once_with(log_body=False)
        mock_resp.assert_called_once_with(log_headers=False)
        mock_logging.assert_called_once_with(
            log_level=logging.INFO,
            request_logging_config=mock_req.return_value,
            response_logging_config=mock_resp.return_value,
        )
