# Docker 部署

Docker 版本提供 **React WebUI + FastAPI + headless Chromium**。容器不提供 Tk GUI。

## 要求

- Docker Engine
- Docker Compose v2
- AMD64 / x86_64 主机。当前镜像只验证 AMD64，暂不承诺 ARM64 原生支持。
- 至少为 Chromium 分配 1 GB shared memory（Compose 已配置）。

## 启动

在仓库根目录执行：

```bash
docker compose up -d --build
docker compose logs -f app
```

打开：

```text
http://127.0.0.1:8092
```

首次启动时，entrypoint 会将 `config.docker.example.json` 复制到持久化配置卷。之后可以直接在 WebUI 的设置页面修改并保存配置。

## 使用已发布的 GHCR 镜像

如果不想在本机编译镜像，可以直接拉取 GitHub Container Registry 中的版本：

```bash
export GROK_REGISTER_IMAGE=ghcr.io/aaronl725/grok-register:latest
docker compose pull
docker compose up -d --no-build
```

将 `latest` 替换为指定版本（例如 `1.2.3` 或 `1.2.3-rc.1`），即可固定使用某个发布版本。发布稳定版时还会生成 `1.2.3`、`1.2`、`1` 和 `latest` 标签；预发布版本不会更新 `latest`。

创建并推送版本标签即可触发自动发布：

```bash
git tag -a v1.2.3 -m "v1.2.3"
git push origin v1.2.3
```

GitHub Actions 会先运行完整测试，再构建并发布 AMD64 镜像，最后创建带自动生成变更说明的 GitHub Release。首次发布后，请在 GitHub Packages 中确认容器包的可见性；若希望匿名拉取，需要将包设置为 Public。

## 持久化数据

Compose 创建两个命名卷：

| 卷 | 内容 |
| --- | --- |
| `grok-register-config` | `config.json` |
| `grok-register-data` | SQLite 数据库、账号 TXT/pending、邮箱凭据、Outlook 邮箱池、风控隔离文件、代理状态、Token、CPA 凭据和浏览器截图 |

容器重建或执行 `docker compose down` 不会删除数据卷。

**不要执行 `docker compose down -v`，除非确实要删除全部持久化数据。**

查看配置：

```bash
docker compose exec app cat /app/config/config.json
```

也可以将本地配置文件复制进容器：

```bash
docker compose cp ./config.json app:/app/config/config.json
docker compose exec -u root app chown grok:grok /app/config/config.json
docker compose restart app
```

备份配置与数据：

```bash
docker compose exec -T app tar -czf - -C /app/config . > grok-register-config.tar.gz
docker compose exec -T app tar -czf - -C /app/data . > grok-register-data.tar.gz
```

## 运行说明

- Chromium 在容器中以 headless 模式运行；CPA 的 `cpa_headless` 也默认为 `true`。
- 容器以非 root 用户运行。为兼容容器内 Chromium，Docker 环境会显式设置 `--no-sandbox`。
- WebUI 端口只绑定宿主机 `127.0.0.1`，不会默认暴露到局域网或公网。
- 镜像包含 Chromium 和 WebUI 所需 Python 依赖。基础镜像不包含可选的 `sing-box`；如果使用 VLESS、VMess、Trojan、Hysteria2、TUIC 或 Shadowsocks 节点，需要额外挂载 AMD64 sing-box 二进制，并在配置中设置 `proxy_singbox_path`。
- 可将代理源文件复制到持久化目录，再在 WebUI 中设置绝对路径：
  ```bash
  docker compose cp ./proxies.txt app:/app/data/proxies.txt
  ```
- 高级代理协议需要额外挂载 Linux AMD64 sing-box 二进制。先确保宿主机文件具有可执行权限，再在 Compose 中增加：
  ```yaml
  services:
    app:
      volumes:
        - ./sing-box:/usr/local/bin/sing-box:ro
  ```
  然后在 WebUI 将 `proxy_singbox_path` 设置为 `/usr/local/bin/sing-box`。
- Docker 版本面向 WebUI 使用，不提供桌面 Tk GUI。

## 常用命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f app

# 重启
docker compose restart app

# 停止容器，但保留配置和数据
docker compose down

# 重新构建
docker compose build --no-cache
docker compose up -d
```

健康检查通过容器内的 `/health` 接口完成，不会启动浏览器、注册流程或外部账号操作。
