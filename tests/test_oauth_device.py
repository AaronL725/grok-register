import unittest
from unittest.mock import patch

from cpa_xai import oauth_device


class OAuthDeviceTests(unittest.TestCase):
    def test_post_form_checks_cancel_before_network_request(self):
        with patch.object(oauth_device, "_build_opener") as opener:
            with self.assertRaises(oauth_device.OAuthDeviceError):
                oauth_device._post_form(
                    "https://auth.x.ai/token",
                    {"a": "b"},
                    cancel=lambda: True,
                )
        opener.assert_not_called()

    def test_request_device_code_uses_one_explicit_retry_policy(self):
        discovery = {
            "device_authorization_endpoint": "https://auth.x.ai/device",
            "token_endpoint": "https://auth.x.ai/token",
        }
        payload = {
            "device_code": "device",
            "user_code": "user",
            "verification_uri": "https://accounts.x.ai/oauth2/device",
            "verification_uri_complete": "https://accounts.x.ai/oauth2/device?user_code=user",
            "expires_in": 1800,
            "interval": 5,
        }
        with patch.object(oauth_device, "discover", return_value=discovery) as discover, \
                patch.object(oauth_device, "_post_form", return_value=(200, payload)) as post:
            session = oauth_device.request_device_code(
                timeout=14,
                cancel=lambda: False,
                retries=2,
            )

        self.assertEqual(session.device_code, "device")
        discover.assert_called_once()
        self.assertEqual(discover.call_args.kwargs["timeout"], 14)
        self.assertEqual(discover.call_args.kwargs["retries"], 2)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["timeout"], 14)
        self.assertEqual(post.call_args.kwargs["retries"], 2)
        self.assertIn("cancel", post.call_args.kwargs)

    def test_poll_uses_configurable_timeout_without_forcing_five_seconds(self):
        payload = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }
        with patch.object(
            oauth_device,
            "_post_form",
            return_value=(200, payload),
        ) as post:
            result = oauth_device.poll_device_token(
                "device",
                "https://auth.x.ai/token",
                timeout=12,
                cancel=lambda: False,
            )

        self.assertEqual(result.access_token, "access")
        self.assertEqual(post.call_args.kwargs["timeout"], 12.0)
        self.assertEqual(post.call_args.kwargs["retries"], 0)
        self.assertIn("cancel", post.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
