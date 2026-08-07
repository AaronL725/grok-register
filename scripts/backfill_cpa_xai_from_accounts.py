#!/usr/bin/env python3
"""Batch mint CPA xai-*.json from register accounts_cli.txt.

Default: headed Chromium + turnstilePatch (headless is Cloudflare-blocked on
accounts.x.ai). Token poll is source of truth; consent Allow uses real click.

Example (from grok_reg project root):
  export DISPLAY=:0
  uv run python -u scripts/backfill_cpa_xai_from_accounts.py \\
    --limit 1 --probe
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai import existing_cpa_emails, mint_and_export, parse_accounts_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--accounts",
        default=str(_ROOT / "accounts_cli.txt"),
    )
    ap.add_argument(
        "--out-dir",
        default=str(_ROOT / "cpa_auths"),
        help="Primary output under register machine",
    )
    ap.add_argument(
        "--cpa-dir",
        default="",
        help="Optional CPA hot-load auth-dir; files are copied here after success",
    )
    ap.add_argument("--limit", type=int, default=0, help="0 = all missing")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--email", default="", help="Only this email")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", action="store_false", dest="skip_existing")
    ap.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Headless Chromium (usually blocked by Cloudflare on accounts.x.ai)",
    )
    ap.add_argument(
        "--headed",
        action="store_true",
        default=True,
        help="Show browser (default; required for stable device consent)",
    )
    ap.add_argument("--probe", action="store_true", default=True)
    ap.add_argument("--no-probe", action="store_false", dest="probe")
    ap.add_argument("--probe-chat", action="store_true", default=False)
    ap.add_argument(
        "--proxy",
        default="",
        help="Outbound proxy. Empty → read register config.json cpa_proxy/proxy, else env",
    )
    ap.add_argument(
        "--config",
        default=str(_ROOT / "config.json"),
        help="register config.json for cpa_proxy/proxy defaults",
    )
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--sleep", type=float, default=3.0, help="Sleep between accounts")
    ap.add_argument(
        "--fail-log",
        default=str(_ROOT / "cpa_auths" / "backfill_failed.jsonl"),
        help="Append failures JSONL",
    )
    ap.add_argument(
        "--force-standalone",
        action="store_true",
        default=True,
        help="Always open fresh Chromium (default)",
    )
    args = ap.parse_args()

    if args.headless:
        args.headed = False
    else:
        args.headless = False

    # Resolve proxy: CLI > config cpa_proxy/proxy > env
    if not args.proxy:
        try:
            cfg_path = Path(args.config)
            if cfg_path.is_file():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    cfg = {
                        k: v
                        for k, v in cfg.items()
                        if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))
                    }
                    args.proxy = (cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
                    if not args.cpa_dir:
                        args.cpa_dir = (cfg.get("cpa_hotload_dir") or "").strip()
        except Exception as e:  # noqa: BLE001
            print(f"warn: read config proxy failed: {e}", flush=True)
    if not args.proxy:
        args.proxy = (
            os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("http_proxy")
            or ""
        ).strip()
    print(f"proxy={args.proxy or '(none)'}", flush=True)

    accounts = parse_accounts_file(args.accounts)
    if args.email:
        accounts = [a for a in accounts if a.email.lower() == args.email.lower()]
    accounts = accounts[args.offset :]

    have = set()
    if args.skip_existing:
        have |= {e.lower() for e in existing_cpa_emails(args.out_dir)}
        if args.cpa_dir:
            have |= {e.lower() for e in existing_cpa_emails(args.cpa_dir)}

    todo = []
    for a in accounts:
        if args.skip_existing and a.email.lower() in have:
            continue
        todo.append(a)
        if args.limit and len(todo) >= args.limit:
            break

    print(f"accounts total={len(parse_accounts_file(args.accounts))} todo={len(todo)} out={args.out_dir}")
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    if args.cpa_dir:
        Path(args.cpa_dir).mkdir(parents=True, exist_ok=True)

    ok_n = fail_n = 0
    results = []
    for i, acc in enumerate(todo, 1):
        print(f"\n=== [{i}/{len(todo)}] {acc.email} ===", flush=True)

        def log(msg: str, _email=acc.email) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] [{_email}] {msg}", flush=True)

        r = mint_and_export(
            email=acc.email,
            password=acc.password,
            auth_dir=args.out_dir,
            page=None,
            proxy=args.proxy or None,
            headless=args.headless,
            probe=args.probe,
            probe_chat=args.probe_chat,
            browser_timeout_sec=args.timeout,
            force_standalone=args.force_standalone,
            sso=acc.sso or None,
            prefer_protocol=True,
            log=log,
        )
        results.append(r)
        if r.get("ok") and r.get("path"):
            ok_n += 1
            # mirror into CPA auth-dir
            if args.cpa_dir:
                src = Path(r["path"])
                dst = Path(args.cpa_dir) / src.name
                shutil.copy2(src, dst)
                os.chmod(dst, 0o600)
                print(f"copied -> {dst}", flush=True)
        else:
            fail_n += 1
            if args.fail_log:
                Path(args.fail_log).parent.mkdir(parents=True, exist_ok=True)
                with open(args.fail_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if args.sleep and i < len(todo):
            time.sleep(args.sleep)

    print(f"\n=== done ok={ok_n} fail={fail_n} ===", flush=True)
    summary = Path(args.out_dir) / f"backfill_summary_{int(time.time())}.json"
    summary.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"summary {summary}")
    return 0 if ok_n > 0 or not todo else 1


if __name__ == "__main__":
    raise SystemExit(main())
