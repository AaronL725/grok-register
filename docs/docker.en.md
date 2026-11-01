# Docker deployment

The Docker image provides the **React WebUI, FastAPI, and headless Chromium**. It does not provide the desktop Tk GUI.

## Requirements

- Docker Engine
- Docker Compose v2
- An AMD64 / x86_64 host. The image is currently validated for AMD64 only; native ARM64 support is not claimed.
- At least 1 GB of shared memory for Chromium (already configured in Compose).

## Start

Run from the repository root:

```bash
docker compose up -d --build
docker compose logs -f app
```

Open:

```text
http://127.0.0.1:8092
```

On first start, the entrypoint copies `config.docker.example.json` into the persistent configuration volume. You can then edit and save settings from the WebUI.

## Persistent data

Compose creates two named volumes:

| Volume | Contents |
| --- | --- |
| `grok-register-config` | `config.json` |
| `grok-register-data` | SQLite database, account TXT/pending files, mailbox credentials, Outlook mailbox pool, risk quarantine, proxy state, tokens, CPA credentials, and browser screenshots |

Recreating the container or running `docker compose down` does not remove these volumes.

**Do not run `docker compose down -v` unless you intend to delete all persistent data.**

Inspect the configuration:

```bash
docker compose exec app cat /app/config/config.json
```

You can also copy a local configuration into the container:

```bash
docker compose cp ./config.json app:/app/config/config.json
docker compose exec -u root app chown grok:grok /app/config/config.json
docker compose restart app
```

Back up the configuration and data:

```bash
docker compose exec -T app tar -czf - -C /app/config . > grok-register-config.tar.gz
docker compose exec -T app tar -czf - -C /app/data . > grok-register-data.tar.gz
```

## Runtime notes

- Chromium runs headless in the container; `cpa_headless` also defaults to `true`.
- The container runs as a non-root user. Docker explicitly enables Chromium's `--no-sandbox` mode for container compatibility.
- The WebUI port is bound to host `127.0.0.1`; it is not exposed to the LAN or public internet by default.
- The image includes Chromium and the Python dependencies required by the WebUI. Optional `sing-box` is not bundled. VLESS, VMess, Trojan, Hysteria2, TUIC, and Shadowsocks nodes require an AMD64 sing-box binary to be mounted separately and selected with `proxy_singbox_path`.
- Copy proxy source files into the persistent data volume, then set the absolute path in the WebUI:
  ```bash
  docker compose cp ./proxies.txt app:/app/data/proxies.txt
  ```
- Advanced proxy protocols require a separately mounted Linux AMD64 sing-box binary. Make it executable on the host, then add this Compose override:
  ```yaml
  services:
    app:
      volumes:
        - ./sing-box:/usr/local/bin/sing-box:ro
  ```
  Set `proxy_singbox_path` to `/usr/local/bin/sing-box` in the WebUI.
- The Docker deployment is intended for WebUI use and does not provide the desktop Tk GUI.

## Common commands

```bash
# Status
docker compose ps

# Logs
docker compose logs -f app

# Restart
docker compose restart app

# Stop the container while preserving volumes
docker compose down

# Rebuild
docker compose build --no-cache
docker compose up -d
```

The container health check calls the local `/health` endpoint. It does not launch Chromium, run registration, or perform external account actions.
