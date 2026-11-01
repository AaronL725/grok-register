# Docker 部署

Docker 版本提供 **React WebUI + FastAPI + headless Chromium**。容器不提供 Tk GUI。

## 要求

- Docker Engine
- Docker Compose v2
- AMD64 / x86_64 主机。当前镜像只验证 AMD64，暂不承诺 ARM64 原生支持。
- 至少为 Chromium 分配 1 GB shared memory（Compose 已配置）。

## 启动

在仓库根目录执行：

\`\`\`bash
docker compose up -d --build
docker compose logs -f app
\`\`\`

打开：

\`\`\`text
http://127.0.0.1:8092
\`\`\`

首次启动时，entrypoint 会将 \`config.docker.example.json\` 复制到持久化配置卷。之后可以直接在 WebUI 的设置页面修改并保存配置。

## 持久化数据

Compose 创建两个命名卷：

| 卷 | 内容 |
| --- | --- |
| \`grok-register-config\` | \`config.json\` |
| \`grok-register-data\` | SQLite 数据库、账号 TXT/pending、邮箱凭据、Outlook 邮箱池、风控隔离文件、代理状态、Token、CPA 凭据和浏览器截图 |

容器重建或执行 \`docker compose down\` 不会删除数据卷。

**不要执行 \`docker compose down -v\`，除非确实要删除全部持久化数据。**

查看配置：

\`\`\`bash
docker compose exec app cat /app/config/config.json
\`\`\`

也可以将本地配置文件复制进容器：

\`\`\`bash
docker compose cp ./config.json app:/app/config/config.json
docker compose restart app
\`\`\`

备份配置与数据：

\`\`\`bash
docker compose exec -T app tar -czf - -C /app/config . > grok-register-config.tar.gz
docker compose exec -T app tar -czf - -C /app/data . > grok-register-data.tar.gz
\`\`\`

## 运行说明

- Chromium 在容器中以 headless 模式运行；CPA 的 \`cpa_headless\` 也默认为 \`true\`。
- 容器以非 root 用户运行。为兼容容器内 Chromium，Docker 环境会显式设置 \`--no-sandbox\`。
- WebUI 端口只绑定宿主机 \`127.0.0.1\`，不会默认暴露到局域网或公网。
- 镜像包含 Chromium 和 WebUI 所需 Python 依赖。基础镜像不包含可选的 \`sing-box\`；如果使用 VLESS、VMess、Trojan、Hysteria2、TUIC 或 Shadowsocks 节点，需要额外挂载 AMD64 sing-box 二进制，并在配置中设置 \`proxy_singbox_path\`。
- 可将代理源文件放在持久化目录 \`/app/data\`，然后在 WebUI 中将 \`proxy_pool_file\` 设置为对应绝对路径。
- Docker 版本面向 WebUI 使用，不提供桌面 Tk GUI。

## 常用命令

\`\`\`bash
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
\`\`\`

健康检查通过容器内的 \`/health\` 接口完成，不会启动浏览器、注册流程或外部账号操作。
