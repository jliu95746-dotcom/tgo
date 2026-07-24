# Windows 混合开发模式

> PostgreSQL 默认映射到宿主机 `15432`，避开 Windows/Docker 当前预留的 `5433` 端口范围；可用 `NATIVE_POSTGRES_PORT` 覆盖。

此模式用于内存较小的 Windows 开发机：

- Docker 仅运行 PostgreSQL、Redis、WuKongIM。
- `tgo-api`、`tgo-ai`、`tgo-rag`、`tgo-workflow`、`tgo-web`、`tgo-widget-js` 作为 Windows 本机进程运行。
- RAG 文档处理使用 Windows 本机 Celery `solo` worker，避免 Docker 应用容器占用额外内存。
- `tgo-api` 使用 `18000`，避免与本机 Agent Memory 的 `8000` 冲突。
- 本机进程不启用 Python 自动重载，以降低内存占用；修改后运行重启脚本。

## 首次安装

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\install.ps1
```

安装过程使用 API、AI、RAG、Workflow 各自的锁文件，并在服务目录创建 `.venv`。前端依赖安装到
`repos\tgo-web\node_modules` 和 `repos\tgo-widget-js\node_modules`。这些目录均不会提交到 Git。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\start.ps1
```

启动脚本会：

1. 停止 TGO 应用容器；
2. 启动 PostgreSQL、Redis、WuKongIM；
3. 执行 API、AI、RAG 和 Workflow 数据库迁移；
4. 启动本机 API、内部 API、AI、RAG、RAG worker、Workflow、管理端和访客端；
5. 验证健康检查、管理端 API 代理和访客端。

页面地址：

- 管理端：`http://127.0.0.1:5173/chat`
- 访客端：`http://127.0.0.1:5174`

需要测试自动意图路由时，在本机私有的 `.env.dev` 中设置
`INTENT_AUTOMATION_ENABLED=true`，并确保 `tgo-workflow` 的
`http://127.0.0.1:8004/health` 返回成功。该开关默认关闭，避免 Workflow
不可用时误启用自动路由。

## 状态、重启和停止

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\status.ps1
powershell -ExecutionPolicy Bypass -File scripts\native-dev\restart.ps1
powershell -ExecutionPolicy Bypass -File scripts\native-dev\stop.ps1
```

停止脚本默认保留基础设施。需要同时停止基础设施时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\stop.ps1 -IncludeInfrastructure
```

运行日志与 PID 状态位于 `.tmp\native-dev`。

如果只需要浏览知识库、不需要处理新上传文档，可进一步节省内存：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\restart.ps1 -SkipRagWorker
```
