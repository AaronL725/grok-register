from pathlib import Path


MAIL_PATH = Path("mail_service.py")
TEST_PATH = Path("tests/test_cloudflare_admin_api.py")
WORKFLOW_PATH = Path(".github/workflows/fix-cloudflare-fallback-error.yml")
SELF_PATH = Path(__file__)


def patch_mail_service():
    source = MAIL_PATH.read_text(encoding="utf-8")
    function_start = source.index("def get_email_and_token(api_key=None):")
    block_start = source.index('    if provider == "cloudflare":', function_start)
    block_end = source.index(
        "    key = api_key or get_duckmail_api_key()\n"
        "    domain = pick_domain(api_key=key)",
        block_start,
    )
    current = source[block_start:block_end]
    if "fallback_stage" in current:
        return
    replacement = '''    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        create_path = get_cloudflare_path(
            "cloudflare_path_accounts", "/api/new_address"
        )
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 保留现有 Mail.tm 风格回退；仅在回退也失败时补充两次异常。
            fallback_stage = "获取域名列表"
            try:
                key = api_key or get_cloudflare_api_key()
                domains = cloudflare_get_domains(api_base, api_key=key)
                if not domains:
                    raise Exception("兼容接口未返回可用域名")
                fallback_stage = "选择可用域名"
                verified = [d for d in domains if d.get("isVerified")]
                target = verified[0] if verified else domains[0]
                domain = target.get("domain")
                if not domain:
                    raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
                username = generate_username(10)
                address = f"{username}@{domain}"
                password = secrets.token_urlsafe(12)
                fallback_stage = "创建兼容邮箱账户"
                cloudflare_create_account(
                    api_base, address, password, api_key=key, expires_in=0
                )
                fallback_stage = "获取兼容邮箱 token"
                token = cloudflare_get_token(
                    api_base, address, password, api_key=key
                )
                if not token:
                    raise Exception("获取 Cloudflare 邮箱 token 失败")
                return address, token
            except Exception as fallback_exc:
                raise RuntimeError(
                    "Cloudflare 创建邮箱失败；"
                    f"主接口 {create_path}: "
                    f"{primary_exc.__class__.__name__}: {primary_exc}；"
                    f"兼容回退（{fallback_stage}）: "
                    f"{fallback_exc.__class__.__name__}: {fallback_exc}"
                ) from fallback_exc
'''
    MAIL_PATH.write_text(
        source[:block_start] + replacement + source[block_end:],
        encoding="utf-8",
    )


def patch_tests():
    source = TEST_PATH.read_text(encoding="utf-8")
    if "import mail_service\n" not in source:
        source = source.replace(
            "import grok_register_ttk as app\n",
            "import grok_register_ttk as app\nimport mail_service\n",
            1,
        )
    if "test_cloudflare_fallback_reports_both_errors" in source:
        TEST_PATH.write_text(source, encoding="utf-8")
        return
    marker = '\n\nif __name__ == "__main__":\n'
    insert_at = source.index(marker)
    additions = '''
    def test_cloudflare_fallback_still_succeeds(self):
        app.config.update({
            "email_provider": "cloudflare",
            "cloudflare_api_base": "https://temp-mail.example.com",
            "cloudflare_api_key": "",
            "cloudflare_auth_mode": "none",
            "cloudflare_path_accounts": "/api/new_address",
        })
        with patch.object(mail_service, "config", app.config), patch.object(
            mail_service, "cloudflare_create_temp_address",
            side_effect=RuntimeError("primary failed"),
        ), patch.object(
            mail_service, "cloudflare_get_domains",
            return_value=[{"domain": "example.com", "isVerified": True}],
        ), patch.object(
            mail_service, "generate_username", return_value="testuser",
        ), patch.object(
            mail_service, "cloudflare_create_account", return_value={},
        ), patch.object(
            mail_service, "cloudflare_get_token", return_value="fallback-token",
        ):
            address, token = mail_service.get_email_and_token()

        self.assertEqual(address, "testuser@example.com")
        self.assertEqual(token, "fallback-token")

    def test_cloudflare_fallback_reports_both_errors(self):
        app.config.update({
            "email_provider": "cloudflare",
            "cloudflare_api_base": "https://temp-mail.example.com",
            "cloudflare_api_key": "admin-secret",
            "cloudflare_auth_mode": "x-admin-auth",
            "cloudflare_path_accounts": "/admin/new_address",
        })
        with patch.object(mail_service, "config", app.config), patch.object(
            mail_service, "cloudflare_create_temp_address",
            side_effect=RuntimeError("primary 401"),
        ), patch.object(
            mail_service, "cloudflare_get_domains",
            side_effect=RuntimeError("fallback 403"),
        ):
            with self.assertRaises(RuntimeError) as caught:
                mail_service.get_email_and_token()

        message = str(caught.exception)
        self.assertIn("/admin/new_address", message)
        self.assertIn("primary 401", message)
        self.assertIn("获取域名列表", message)
        self.assertIn("fallback 403", message)
'''
    TEST_PATH.write_text(
        source[:insert_at] + "\n" + additions + source[insert_at:],
        encoding="utf-8",
    )


def main():
    patch_mail_service()
    patch_tests()
    if WORKFLOW_PATH.exists():
        WORKFLOW_PATH.unlink()
    if SELF_PATH.exists():
        SELF_PATH.unlink()


if __name__ == "__main__":
    main()
