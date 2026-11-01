# 账号重新认证与授权恢复

本模块提供基于 SQLite 的已有账号重新认证任务。只对已保存邮箱及密码的账号执行，访问官方登录界面；如果出现人工验证要求，需要用户自行完成，不会自动绕过。

## API

本地 WebUI 使用以下同源接口：

- `POST /api/reauth/start` — `{"ids":[1,2]}` 创建按顺序执行的批次。
- `GET /api/reauth/status` — 查询最新任务的逐账号状态与总数。
- `POST /api/reauth/stop` — 请求取消，当前账号在下一个取消检查点停止。
- `GET /api/reauth/history` — 查看持久化的任务历史。
- `GET /api/reauth/jobs/{job_id}` — 查询指定批次。

React WebUI 的“账号管理”页面可以选择账号启动批次、查看逐账号进度并停止任务；“任务历史”页面可以回看已完成的重新登录批次及每个账号的结果。

## 数据处理

只有成功从官方登录流程获取新的 SSO，才会替换 SQLite 中的原 SSO，更新 `authorization_status` 和 `last_authenticated_at`，并在旧标识上设置导入墓碑，防止旧 TXT 被再次导入为重复账号。

随后原子更新相关 `email----password----sso` 文件；若旧账号没有关联 TXT 文件，则写入 `accounts_reauthenticated.txt`。TXT 写入失败属于兼容导出警告，SQLite 中成功更新的授权仍然保留。

CPA 和 Grok2API 后处理复用现有代码，结果分别保存。后处理失败不抹除已成功刷新的 SSO；具体警告保存在任务详情中。

重新认证任务使用 `job_runs` 和 `job_accounts` 保存每个账号的 `pending`、`running`、`succeeded`、`failed`、`skipped` 状态。中止、缺少密码、账号删除以及登录失败均有对应状态。注册任务运行时无法并行启动重新认证。

## 测试

单元测试和 CI 完全使用 FakePage / Mock，**不进行真实账号登录、注册或联网验证**。登录页面的实时兼容性需要在有授权的环境中另外验证。
