"""验证主模块对拆分模块公开函数和运行状态的兼容代理。"""

import unittest
from unittest.mock import patch

import app_config
import grok_register_ttk as app
import mail_service
import registration_browser
from cpa_xai import browser_confirm
from cpa_xai import browser_session


class ModuleCompatibilityTests(unittest.TestCase):
    def test_config_object_is_shared(self):
        self.assertIs(app.config, app_config.config)

    def test_original_public_functions_remain_available(self):
        names = ['_normalize_sso_token', '_pick_list_payload', 'add_token_to_grok2api_local_pool', 'add_token_to_grok2api_pools', 'add_token_to_grok2api_remote_pool', 'build_profile', 'cleanup_runtime_memory', 'click_email_signup_button', 'cloudflare_apply_auth_params', 'cloudflare_build_headers', 'cloudflare_create_account', 'cloudflare_create_temp_address', 'cloudflare_get_domains', 'cloudflare_get_message_detail', 'cloudflare_get_messages', 'cloudflare_get_oai_code', 'cloudflare_get_token', 'cloudflare_is_admin_create_path', 'cloudflare_next_default_domain', 'cloudmail_get_email_and_token', 'cloudmail_get_messages', 'cloudmail_get_oai_code', 'cloudmail_next_domain', 'create_account', 'create_browser_options', 'duckmail_get_oai_code', 'enable_nsfw_for_token', 'encode_grpc_nsfw_settings', 'extract_verification_code', 'fill_code_and_submit', 'fill_email_and_submit', 'fill_profile_and_submit', 'generate_random_birthdate', 'generate_username', 'getTurnstileToken', 'get_cloudflare_api_base', 'get_cloudflare_api_key', 'get_cloudflare_auth_mode', 'get_cloudflare_path', 'get_cloudmail_api_base', 'get_cloudmail_path', 'get_cloudmail_public_token', 'get_domains', 'get_duckmail_api_key', 'get_email_and_token', 'get_email_provider', 'get_grok2api_remote_api_bases', 'get_message_detail', 'get_messages', 'get_oai_code', 'get_token', 'get_user_agent', 'get_yyds_api_key', 'get_yyds_jwt', 'has_profile_form', 'http_get', 'http_post', 'is_cloudflare_block_response', 'open_signup_page', 'pick_domain', 'refresh_active_page', 'resolve_grok2api_local_token_file', 'response_preview', 'restart_browser', 'set_birth_date', 'set_tos_accepted', 'start_browser', 'stop_browser', 'stop_browser_proxy_bridge', 'update_nsfw_settings', 'wait_for_sso_cookie', 'yyds_create_account', 'yyds_generate_username', 'yyds_get_domains', 'yyds_get_email_and_token', 'yyds_get_message_detail', 'yyds_get_messages', 'yyds_get_oai_code', 'yyds_get_token', 'yyds_pick_domain']
        for name in names:
            self.assertTrue(callable(getattr(app, name)), name)

    def test_mail_wrapper_delegates(self):
        with patch.object(mail_service, "get_email_provider", return_value="duckmail") as mocked:
            self.assertEqual(app.get_email_provider(), "duckmail")
            mocked.assert_called_once_with()

    def test_browser_confirm_reexports_session_api(self):
        self.assertIs(browser_confirm.close_standalone, browser_session.close_standalone)
        self.assertIs(browser_confirm.normalize_cookies, browser_session.normalize_cookies)


if __name__ == "__main__":
    unittest.main()
