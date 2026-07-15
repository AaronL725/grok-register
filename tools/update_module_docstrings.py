#!/usr/bin/env python3
"""Temporarily update and verify module docstrings for every project Python file."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

DESCRIPTIONS = {
    "grok_register_ttk.py": "GUI 与 CLI 主入口，并为拆分后的注册模块保留兼容适配。",
    "app_config.py": "负责应用配置的默认值、加载保存、规范化和运行前校验。",
    "account_outputs.py": "负责账号结果、pending 恢复以及 grok2api token 池的安全持久化。",
    "browser_runtime.py": "提供共享的 HTTP 请求、代理处理和 Chromium 启动参数。",
    "registration_browser.py": "管理主注册浏览器生命周期并实现注册页面自动化操作。",
    "registration_flow.py": "编排 GUI 与 CLI 共用的单账号注册和批量执行流程。",
    "mail_service.py": "接入临时邮箱服务并负责邮箱创建、邮件轮询和验证码提取。",
    "cpa_export.py": "在注册成功后可选生成 CPA xAI OIDC 凭证并复制到热加载目录。",
    "cf_mail_debug.py": "提供 Cloudflare 临时邮箱接口的命令行诊断工具。",
    "cpa_xai/__init__.py": "导出 CPA xAI OIDC 凭证生成流程的公共接口。",
    "cpa_xai/browser_session.py": "管理 CPA 授权浏览器会话、代理、Cookie 注入和资源清理。",
    "cpa_xai/browser_confirm.py": "自动完成 xAI 登录、设备授权确认和相关页面交互。",
    "cpa_xai/oauth_device.py": "实现 OAuth Device Authorization 的发现、启动和 token 轮询。",
    "cpa_xai/proxyutil.py": "解析认证代理并为 CPA 浏览器提供本地代理桥。",
    "cpa_xai/mint.py": "协调浏览器授权、OAuth 轮询和 CPA 凭证导出流程。",
    "cpa_xai/schema.py": "定义并规范化 CPA xAI 凭证文件的数据结构。",
    "cpa_xai/writer.py": "将 CPA xAI 凭证安全写入本地 JSON 文件。",
    "tests/test_cloudflare_admin_api.py": "验证 Cloudflare 临时邮箱 admin 创建和鉴权接口行为。",
    "tests/test_browser_session.py": "验证 CPA 浏览器会话的复用、取消、代理和清理行为。",
    "tests/test_pending_recovery.py": "验证 pending 账号恢复的去重、锁、原子更新和异常处理。",
    "tests/test_cpa_core.py": "验证 CPA 凭证结构、写入和核心 mint 流程。",
    "tests/test_grok2api_remote_pool.py": "验证 grok2api 远端 token 入池及并发安全回退逻辑。",
    "tests/test_registration_flow.py": "验证共享注册流程的重试、统计、取消、清理和后处理边界。",
    "tests/test_oauth_device.py": "验证 OAuth Device Authorization 的发现、重试、轮询和错误处理。",
    "tests/test_post_modularization_regressions.py": "验证模块化改造后的配置、浏览器、邮箱和兼容性回归。",
    "tests/test_minimal_boundary_regressions.py": "验证后处理警告、邮件重试、目标锁和常量兼容边界。",
    "tests/test_module_compatibility.py": "验证主模块对拆分模块公开函数和运行状态的兼容代理。",
}


def project_python_files():
    files = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == SELF:
            continue
        relative = path.relative_to(ROOT)
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return sorted(files)


def replace_docstring(path, description):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines(keepends=True)
    replacement = f'"""{description}"""\n'

    first = tree.body[0] if tree.body else None
    has_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    if has_docstring:
        start = first.lineno - 1
        end = first.end_lineno
        lines[start:end] = [replacement]
    else:
        insert_at = 0
        if lines and lines[0].startswith("#!"):
            insert_at = 1
        if insert_at < len(lines) and "coding" in lines[insert_at][:80]:
            insert_at += 1
        lines[insert_at:insert_at] = [replacement, "\n"]

    updated = "".join(lines)
    parsed = ast.parse(updated, filename=str(path))
    if ast.get_docstring(parsed, clean=False) != description:
        raise RuntimeError(f"module docstring verification failed: {path}")
    path.write_text(updated, encoding="utf-8")


def main():
    actual = project_python_files()
    expected = sorted(DESCRIPTIONS)
    if actual != expected:
        missing = sorted(set(actual) - set(expected))
        stale = sorted(set(expected) - set(actual))
        raise RuntimeError(f"Python file inventory mismatch; unmapped={missing}, missing={stale}")

    for relative, description in DESCRIPTIONS.items():
        replace_docstring(ROOT / relative, description)

    main_source = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    legacy = "整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py"
    if legacy in main_source:
        raise RuntimeError("legacy grok_register_ttk header text remains")

    print(f"updated module docstrings for {len(DESCRIPTIONS)} Python files")


if __name__ == "__main__":
    main()
