#!/usr/bin/env python3
"""Convert ~/.grok/auth.json (Grok Build CLI) → CPA xai-<email>.json.

Usage (from grok_reg project root):
  uv run python scripts/export_cpa_xai_from_grok_auth.py \\
    --auth-json ~/.grok/auth.json \\
    --out-dir ./cpa_auths

Then any agent can use CPA:
  POST http://127.0.0.1:8317/v1/chat/completions  model=grok-4.5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai import (  # noqa: E402
    DEFAULT_BASE_URL,
    build_cpa_xai_auth,
    write_cpa_xai_auth,
)


def load_grok_auth(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise SystemExit(f"empty or invalid auth.json: {path}")
    # grok stores one top-level key: "https://auth.x.ai::<client_id>"
    entry = next(iter(raw.values()))
    if not isinstance(entry, dict):
        raise SystemExit("auth.json entry is not an object")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--auth-json",
        default=str(Path.home() / ".grok" / "auth.json"),
        help="Grok Build CLI auth.json path",
    )
    ap.add_argument(
        "--out-dir",
        default=str(_ROOT / "cpa_auths"),
        help="Output dir for xai-*.json (register cpa_auths or CPA auth-dir)",
    )
    ap.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Must be cli-chat-proxy for free Grok 4.5 promo",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print JSON only, do not write")
    args = ap.parse_args()

    entry = load_grok_auth(Path(args.auth_json).expanduser())
    access = entry.get("key") or entry.get("access_token") or ""
    refresh = entry.get("refresh_token") or ""
    email = entry.get("email") or ""
    sub = entry.get("user_id") or entry.get("principal_id") or entry.get("sub") or ""

    payload = build_cpa_xai_auth(
        email=email,
        access_token=access,
        refresh_token=refresh,
        sub=sub,
        base_url=args.base_url,
    )

    if args.dry_run:
        redacted = dict(payload)
        for k in ("access_token", "refresh_token", "id_token"):
            if k in redacted and isinstance(redacted[k], str) and redacted[k]:
                v = redacted[k]
                redacted[k] = v[:16] + f"...(len={len(v)})"
        print(json.dumps(redacted, indent=2, ensure_ascii=False))
        return 0

    path = write_cpa_xai_auth(args.out_dir, payload)
    print(f"wrote {path}")
    print(f"email={payload.get('email')} base_url={payload.get('base_url')}")
    print("CPA will hot-reload; test:")
    print('  curl -sS http://127.0.0.1:8317/v1/models -H "Authorization: Bearer <CPA_KEY>" | grep grok-4.5')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
