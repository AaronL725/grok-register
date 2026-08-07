#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync grok-regkit endpoints & credentials directly to 9router SQLite database."""

from __future__ import annotations

import datetime
import glob
import json
import os
import sqlite3
import uuid
from pathlib import Path

HOME = Path.home()
N9ROUTER_DB = HOME / ".9router" / "db" / "data.sqlite"
ROOT = Path(__file__).resolve().parent.parent
CPA_AUTHS_DIR = ROOT / "cpa_auths"


def _connection_data(cursor: sqlite3.Cursor, connection_id: str, provider: str) -> dict:
    cursor.execute(
        "SELECT data FROM providerConnections WHERE id = ? AND provider = ?",
        (connection_id, provider),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _merged_data(existing: dict, updates: dict) -> str:
    merged = dict(existing)
    for key, value in updates.items():
        if (
            key == "providerSpecificData"
            and isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return json.dumps(merged)


def sync_to_9router(
    cpa_url: str = "http://127.0.0.1:8317/v1",
    cpa_key: str = "cpa_local_key",
    g2a_url: str = "http://127.0.0.1:8010/v1",
    g2a_key: str = "g2a_local_key",
) -> dict:
    if not N9ROUTER_DB.exists():
        return {
            "ok": False,
            "detail": f"Database 9router tidak ditemukan di {N9ROUTER_DB}. Pastikan 9router sudah terinstall.",
        }

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn = sqlite3.connect(N9ROUTER_DB, timeout=10.0)
    cursor = conn.cursor()

    synced = []

    cpa_models = [
        "grok-4.5",
        "grok-4.5-high",
        "grok-4.5-medium",
        "grok-4.5-low",
        "grok-build",
    ]

    # 1. Sync CPA / Grok 4.5 Connection (Provider: openai-compatible-grok-cpa)
    cpa_id = "grok-regkit-cpa-4-5"
    cpa_name = "Grok 4.5 & Build (grok-regkit CPA)"
    cpa_data = _merged_data(
        _connection_data(cursor, cpa_id, "openai-compatible-grok-cpa"),
        {
            "apiKey": cpa_key,
            "baseUrl": cpa_url.rstrip("/"),
            "customModels": cpa_models,
            "models": cpa_models,
            "testStatus": "active",
            "providerSpecificData": {
                "baseUrl": cpa_url.rstrip("/")
            }
        },
    )

    cursor.execute(
        """
        INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            provider=excluded.provider,
            name=excluded.name,
            data=excluded.data,
            isActive=1,
            updatedAt=excluded.updatedAt
    """,
        (
            cpa_id,
            "openai-compatible-grok-cpa",
            "api-key",
            cpa_name,
            "cpa@grok-regkit.local",
            1,
            1,
            cpa_data,
            now_iso,
            now_iso,
        ),
    )
    synced.append(cpa_name)

    # 2. Sync SSO Pool / grok2api Connection (Provider: openai-compatible-grok-web)
    g2a_models = [
        "grok-2",
        "grok-3",
        "grok-vision-beta",
    ]
    g2a_id = "grok-regkit-g2a-pool"
    g2a_name = "Grok Web Pool (grok2api)"
    g2a_data = _merged_data(
        _connection_data(cursor, g2a_id, "openai-compatible-grok-web"),
        {
            "apiKey": g2a_key,
            "baseUrl": g2a_url.rstrip("/"),
            "customModels": g2a_models,
            "models": g2a_models,
            "testStatus": "active",
            "providerSpecificData": {
                "baseUrl": g2a_url.rstrip("/")
            }
        },
    )

    cursor.execute(
        """
        INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            provider=excluded.provider,
            name=excluded.name,
            data=excluded.data,
            isActive=1,
            updatedAt=excluded.updatedAt
    """,
        (
            g2a_id,
            "openai-compatible-grok-web",
            "api-key",
            g2a_name,
            "pool@grok-regkit.local",
            2,
            1,
            g2a_data,
            now_iso,
            now_iso,
        ),
    )
    synced.append(g2a_name)

    # 3. Sync individual xai auth JSON files from cpa_auths directory into 9router
    account_files = list(CPA_AUTHS_DIR.glob("xai-*.json"))
    for file_path in account_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                auth_data = json.load(f)
            email = auth_data.get("email")
            if not email:
                continue
            acc_id = f"cpa-acc-{email}"
            acc_name = f"xAI ({email})"
            
            ex_data = _connection_data(cursor, acc_id, "grok-cli")
            # Preserve rotated tokens if 9router already updated them.
            if ex_data.get("accessToken"):
                auth_data["access_token"] = ex_data["accessToken"]
            if ex_data.get("refreshToken"):
                auth_data["refresh_token"] = ex_data["refreshToken"]
            if ex_data.get("expiresAt"):
                auth_data["expired"] = ex_data["expiresAt"]

            acc_updates = {
                "accessToken": auth_data.get("access_token", ""),
                "refreshToken": auth_data.get("refresh_token", ""),
                "expiresAt": auth_data.get("expired", now_iso),
            }
            if not ex_data:
                acc_updates.update({
                    "email": email,
                    "sub": auth_data.get("sub", ""),
                    "baseUrl": auth_data.get("base_url", "https://cli-chat-proxy.grok.com/v1"),
                    "testStatus": "active",
                    "lastError": None
                })
            if auth_data.get("id_token"):
                acc_updates["idToken"] = auth_data["id_token"]
            acc_data_str = _merged_data(ex_data, acc_updates)

            cursor.execute(
                """
                INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider=excluded.provider,
                    name=excluded.name,
                    email=excluded.email,
                    data=excluded.data,
                    isActive=1,
                    updatedAt=excluded.updatedAt
            """,
                (
                    acc_id,
                    "grok-cli",
                    "oauth",
                    acc_name,
                    email,
                    3,
                    1,
                    acc_data_str,
                    now_iso,
                    now_iso,
                ),
            )
            synced.append(acc_name)
        except Exception:
            pass

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "detail": f"Berhasil sinkronisasi {len(synced)} koneksi ke 9router ({N9ROUTER_DB})",
        "synced_count": len(synced),
        "synced": synced,
        "models": cpa_models + g2a_models,
    }


if __name__ == "__main__":
    res = sync_to_9router()
    print(json.dumps(res, indent=2, ensure_ascii=False))
