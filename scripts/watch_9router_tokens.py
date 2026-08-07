"""Background watcher to sync 9Router rotated tokens back to cpa_auths/*.json."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from cpa_xai.schema import build_cpa_xai_auth, credential_file_name

HOME = Path.home()
DEFAULT_DB = HOME / ".9router" / "db" / "data.sqlite"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CPA_DIR = ROOT / "cpa_auths"

logger = logging.getLogger("watch_9router")


def _atomic_write_json(file_path: Path, data: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=file_path.parent, prefix="cpa_sync_")
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(temp_path, file_path)
        os.chmod(file_path, 0o600)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def refresh_cpa_token(email: str, refresh_token: str, proxy: str | None = None) -> dict | None:
    """Silently refresh access_token via OAuth refresh_token without captcha."""
    try:
        from cpa_xai.protocol_oauth import refresh_access_token
        res = refresh_access_token(refresh_token, proxy=proxy)
        logger.info(f"Refreshed token for {email}: expires_in={res.get('expires_in')}")
        return res
    except Exception as e:
        logger.warning(f"Failed to auto-refresh token for {email}: {e}")
        return None


def watch_tokens_once(db_path: Path = DEFAULT_DB, cpa_dir: Path = DEFAULT_CPA_DIR) -> int:
    if not db_path.exists():
        return 0

    cpa_dir.mkdir(parents=True, exist_ok=True)
    updated_count = 0

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT email, data FROM providerConnections WHERE provider = 'grok-cli' AND isActive = 1"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"Error querying 9Router DB: {e}")
        return 0

    for email, data_str in rows:
        if not email or not data_str:
            continue
        try:
            db_data = json.loads(data_str)
        except Exception:
            continue

        acc_token = db_data.get("accessToken")
        ref_token = db_data.get("refreshToken")
        exp_at = db_data.get("expiresAt")

        if not acc_token or not ref_token:
            continue

        json_file = cpa_dir / credential_file_name(email)
        need_update = False
        local_content = {}

        if json_file.exists():
            try:
                local_content = json.loads(json_file.read_text(encoding="utf-8"))
                if (
                    local_content.get("access_token") != acc_token
                    or local_content.get("refresh_token") != ref_token
                ):
                    need_update = True
            except Exception:
                need_update = True
        else:
            need_update = True
            local_content = build_cpa_xai_auth(
                email=email,
                access_token=acc_token,
                refresh_token=ref_token,
                sub=str(db_data.get("sub") or ""),
                id_token=str(db_data.get("idToken") or "") or None,
                expired=str(exp_at or "") or None,
                base_url=db_data.get("baseUrl", "https://cli-chat-proxy.grok.com/v1"),
            )

        if need_update:
            local_content["access_token"] = acc_token
            local_content["refresh_token"] = ref_token
            if exp_at:
                local_content["expired"] = exp_at
            _atomic_write_json(json_file, local_content)
            updated_count += 1

    return updated_count


def start_token_watcher_thread(interval_sec: float = 30.0) -> threading.Thread:
    def _loop():
        while True:
            try:
                watch_tokens_once()
            except Exception as e:
                logger.error(f"Watcher loop error: {e}")
            time.sleep(interval_sec)

    t = threading.Thread(target=_loop, name="9router-token-watcher", daemon=True)
    t.start()
    return t
