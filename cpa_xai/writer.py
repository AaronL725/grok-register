"""将 CPA xAI 凭证安全写入本地 JSON 文件。"""

import json
import os
import tempfile
from pathlib import Path

from .schema import credential_file_name


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def write_cpa_xai_auth(auth_dir, payload, filename=None):
    root = Path(auth_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target_name = filename or credential_file_name(payload.get("email", ""), payload.get("sub", ""))
    target_name = Path(str(target_name)).name
    if not str(target_name).endswith(".json"):
        target_name = str(target_name) + ".json"
    destination = (root / str(target_name)).resolve()
    if not _is_relative_to(destination, root):
        raise ValueError("CPA auth filename must stay inside auth_dir")
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    file_descriptor, temp_name = tempfile.mkstemp(prefix=".xai-", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_name, 0o600)
        except Exception:
            pass
        os.replace(temp_name, str(destination))
        try:
            os.chmod(str(destination), 0o600)
        except Exception:
            pass
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return destination
