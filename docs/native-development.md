# Windows 混合开发模式

> PostgreSQL 默认映射到宿主机 `15432`，避开 Windows/Docker 当前预留的 `5433` 端口范围；可用 `NATIVE_POSTGRES_PORT` 覆盖。

> Redis 的 Windows 本机端口使用 `.env.dev` 中的 `NATIVE_REDIS_PORT`，开发示例配置为 `16379`，只绑定 `127.0.0.1`。容器内部仍使用 `REDIS_PORT=6379`；未设置或留空 `NATIVE_REDIS_PORT` 时兼容原有 `REDIS_PORT`。若 Docker 报 `ports are not available`，先通过 `netsh interface ipv4 show excludedportrange protocol=tcp` 核对系统保留段，再选择可用的本机端口。本机启动器与各服务、Celery 共用同一端口解析，非法端口会在启动前拒绝。

此模式用于内存较小的 Windows 开发机：

- Docker 仅运行 PostgreSQL、Redis、WuKongIM。
- `tgo-api`、`tgo-ai`、`tgo-rag`、`tgo-platform`、`tgo-workflow`、`tgo-device-control`、
  `tgo-web`、`tgo-widget-js` 作为 Windows 本机进程运行。
- RAG 文档处理使用 Windows 本机 Celery `solo` worker，避免 Docker 应用容器占用额外内存。
- `tgo-api` 使用 `18000`，避免与本机 Agent Memory 的 `8000` 冲突。
- 管理端固定使用项目专属入口端口 `5173`，由 `.env.dev` 中的
  `TGO_WEB_PORT` 配置。
- 混合启动会移除 Docker 版 `tgo-web` 和 `tgo-widget-js` 容器，避免与
  Windows 本机前端重复运行或打开到错误页面。
- 本机进程不启用 Python 自动重载，以降低内存占用；修改后运行重启脚本。
- Workflow API 与 Worker 的 `API_INTERNAL_SERVICE_URL` 使用本机内部监听端口 `18001`，用于核验业务事件来源等内部授权。

## 设备授权管理的隔离验收（2026-10-26～27）

本机设备卡片新增“管理授权范围”，页面入口 `http://127.0.0.1:5173/ai/device-control`。本阶段新增文件策略撤销迁移 `0012_device_read_revocation`、管理端代理及范围表单。常驻业务数据库没有执行这次迁移；验收入口仅对其随机自有数据库执行完整 Alembic 升级。普通生产/开发库升级仍需单独备份、校验版本及目标发布验收。

`scripts/native-dev/tests/device-policy-browser-runtime.py` 是显式人工验收助手，不是常驻设备模拟器。需要先运行 `$local-services` 状态检查，确认本机 PostgreSQL/Redis、登录态 Web 和 API 就绪，HTTP `8085` 与 TCP `9876` 均未被其他服务占用，再在本项目配置的 Device Control 环境中运行，传入当前测试管理员所属的项目 UUID。可复用 `scripts/native-dev/common.ps1` 的 `Set-NativeEnvironment -Service device`，不得打印环境或密钥。入口不读取浏览器凭证。

脚本创建严格命名的随机数据库、合成设备和 TCP 客户端，HTTP 使用真实控制服务。只在该次目录 `command.json` 明确写入 `authorize_screen: true` 且文件已撤销/旧连接结束后，生成第二次合成本机授权；结束写入 `stop: true`。过程不执行真实文件/屏幕操作，不修改原企业员工、客户、订单或知识库。整个助手有 20 分钟期限；应持续观察同一运行进程，不因一次观察超时重启实例。

退出必须检查本次代码/报告时间、文件与屏幕各自撤销版本、TCP 确认、运行退出码和清理结果；`ready: true` 是历史状态，不能代替活进程。已修正 Windows `TIME_WAIT` 被 `bind()` 误判为活动监听的问题：使用有界连接拒绝检查，仅 `ECONNREFUSED/10061` 证明监听已停止，超时和其他错误不当作成功。数据库清理先于监听复核，避免检查失败跳过数据库删除。三项专项测试覆盖这些条件，原失败入口的退出 1 保留，不修改成成功。

本批已在 Ego 登录态页面完成文件撤销、新本机授权、缩小范围发布/确认及屏幕撤销，并查看桌面/390 CSS px 界面；TCP 对端为合成客户端。真实 Windows 客户端业务验证、完整 MCP 链路、其他软件和深色视觉检查仍待实施。隔离数据库恢复审计于 2026-10-27 15:12:49Z 完成：重新读库确认文件/屏幕均撤销至版本 2、原 TCP 确认与 Ego 记录，在零活动会话下删除自有库并复查；两个监听已停止，测试令牌未出现在日志中。报告 `.tmp/device-policy-ui-recovery-final.json` 独立于原失败入口，不把原退出 1 修改为通过。全量 Python 静态检查尚未通过，不能据界面成功宣称全工程可发布。

本轮曾发现原八个本机服务和 Docker 引擎均已停止；按最小集恢复基础设施、API、内部 API、AI 与 Web，未执行常驻迁移，未重启其他项目。10 月 27 日收尾时这些常驻进程再次不可达，仅恢复已有的 PostgreSQL/Redis/WuKongIM 用于本次数据库恢复及清理，没有重新启动开发全栈。进程/PID 和健康状态需每次现场检查，不能沿用旧记录。用户要求本阶段后暂停，故不自动启动下一阶段或发布云端。

## 首次安装

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\install.ps1
```

安装过程使用 API、AI、RAG、Platform、Workflow、Device Control 各自的锁文件，并在服务目录创建
`.venv`。前端依赖安装到 `repos\tgo-web\node_modules` 和
`repos\tgo-widget-js\node_modules`。这些目录均不会提交到 Git。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File scripts\native-dev\start.ps1
```

启动脚本会：

1. 停止 TGO 应用容器；
2. 启动 PostgreSQL、Redis、WuKongIM；
3. 执行 API、AI、RAG、Platform 和 Workflow 数据库迁移；
4. 启动本机 API、内部 API、AI、RAG、RAG worker、Platform、Workflow、管理端和访客端；
5. 验证健康检查、管理端 API 代理和访客端。

页面地址：

- 管理端：`http://127.0.0.1:5173/chat`
- 访客端：`http://127.0.0.1:5174`
- 消息平台服务：`http://127.0.0.1:8003`
- 设备控制 HTTP：`http://127.0.0.1:8085`
- 设备接入 TCP：`本机局域网 IP:9876`

## 桌面一键启动

运行 `scripts\native-dev\create-shortcut.ps1` 会在当前用户桌面创建
`TGO 客服系统.lnk`。双击后会自动：

1. 检查并启动 Docker Desktop；
2. 防止重复启动第二套 TGO；
3. 启动混合开发栈并等待全部健康检查通过；
4. 打开 `http://127.0.0.1:5173/chat`；
5. 启动失败时显示中文错误，并将详细日志写入 `.tmp\native-dev`。

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

## 定时触发的隔离运行验证

先确认本机 PostgreSQL、Redis 可用，Workflow 和 API 的 `.venv` 已安装依赖，`.env.dev` 指向本机基础设施。执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-timers.ps1
```

脚本拒绝远程基础设施，要求显式运行及开启 Python 断言。它新建随机 `tgo_scenarios_test_timer_...` 测试库，执行 Workflow 迁移，并在该库准备合成管理员；使用临时 HTTP 端口和随机专用队列，创建自己的 API 私有监听、Workflow、Worker 与 Beat，不要求重启常驻服务。数据库账号须有创建和删除本次测试库的权限。首项等待真正的未来到期分钟；后续仅调整合成数据的等待时间，不修改系统时钟。

验证覆盖实际私有 HTTP 授权→Redis→Worker→运行记录、实际投递后注入回执丢失并恢复、重复消息只执行一次、排队后撤权以及排队后 HTTP 停用。只使用定时与模板返回节点，不调用模型、真实设备、客户会话或业务写接口。它直接访问临时 Workflow 服务，不代替员工网关、登录态页面或生产验收。

报告为 `.tmp/native-dev/workflow-timers-report.json`，应同时核对本次退出码、开始/核对时间、四项 `checks`、`verification_completed` 和各清理标记，不能复用旧成功报告。过程日志在该报告随机队列名称对应的目录中，连接凭证和测试密钥只经进程环境传入。结束时只停止所创建的进程、删除新建测试库、专用队列/绑定及已登记运行结果，不清空 Redis 或修改常驻数据库；其他周期结果/共享控制元数据按原生命周期处理。清理或日志密钥检查失败时整体失败，保留日志供定位。

Workflow 支持以下服务配置；本机 API、Worker、Beat 应保持一致：

- `WORKFLOW_QUEUE_NAME`：默认 `workflow`。本机和 Docker 的 Worker 使用 Celery 应用的默认队列，周期任务及执行任务跟随同一配置。
- `TIMER_EXECUTION_ENABLED`：默认 `false`。本回归只在临时子进程中设为 `true`，不写入私有环境文件。正式启用前仍需迁移、私有授权、Worker/Beat 健康、页面配置、停用/恢复及目标发布验收；孤立地打开开关不证明这条路径已就绪。

上述隔离脚本本身不升级常驻库。早期执行时常驻库尚未应用 `0018/0019`；随后备份/恢复/迁移批次升至 `0021_webhook_deliveries`，见开发计划 4.2.222～228；最新常驻版本为下节审批执行时钟批次的 `0022_execution_segments`，见 4.2.232。查看入口为 `http://127.0.0.1:5173/ai/workflows`。

**当前常驻定时状态（2026-10-25）：**六项隔离运行检查于 17:00:13Z 全部通过并清理，之后在零启用来源和零在途/队列任务的只读核对下，仅开启本项目 `.env.dev` 的 `TIMER_EXECUTION_ENABLED=true`，Webhook 原开关保持开启。私有环境备份在 `.tmp/private-config/`，仅重载本项目 Workflow API、Worker、Beat，其余五个原进程保留；没有迁移或重启基础设施。真实 Beat→Redis→Worker→数据库探针、八个进程身份及七个 HTTP 入口正常，证据 `.tmp/native-timer-runtime-ready.json`、`.tmp/native-timer-activation-phase2.json`。

Ego 正式页面已完成合成流程预览五次、保存发布、停用登记后启用、17:07/17:08 UTC 自然到期及实际输出查看。17:08:31Z 停用，刷新并跨过下一到期分钟后没有新记录，测试流程清理且原有五个流程保留；证据 `.tmp/workflow-timer-ego-evidence.json`。最终无启用计划、在途或排队测试任务，配置除本次 timer 行外一致，审计 `.tmp/native-timer-activation-final-audit.json` 通过。该结果是本机合成场景，指针遮挡、截图及其他正式 UI 条件仍保留，未发布云端。回退前先核对在途并停用来源，再恢复本批备份的开关、有界重载三个 Workflow 进程；保留日志和审计数据。完整范围见计划 4.2.228。

## Webhook 外部通知的隔离回归

确认本机 PostgreSQL、Redis 已就绪，API 与 Workflow 的 `.venv` 已安装依赖后执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-webhooks.ps1
```

入口从本项目私有环境配置解析本机基础设施，拒绝远程数据库、远程 Redis 及关闭 Python 断言的运行方式。脚本创建随机 `tgo_scenarios_test_webhook_...` 测试库；各用例另建独立 schema、随机队列与 Redis 前缀、临时 HTTP 监听和子进程，不升级常驻数据库、不修改私有配置、不轮换常驻密钥，也不启动模型、真实业务连接或正式渠道任务。

用例包含登记与密钥轮换竞争、同命令回执恢复、迁移保护、签名通知去重、真实私有授权 HTTP 和员工 JWT 管理路由。员工令牌通过正式 `/v1/staff/login` 和随机测试密码获得，旧测试令牌接口必须为 `404`；另通过正式流程路由创建、发布、绑定停用来源、启用、签名投递、查看完成记录、轮换及停用。公开通知用例故意让即时入队确认失败，随后由真正的 Celery Beat 按生产配置的 30 秒周期恢复，不能靠脚本手动投递恢复任务代替；另验证 Worker 重启后的恢复、匿名拒绝、未知流程拒绝及员工撤权后旧 JWT 拒绝。API 侧使用临时合成员工库和正式路由/鉴权依赖，未启动主应用生命周期，不等同于常驻完整应用或登录态页面验收。

最新报告为 `.tmp/native-dev/workflow-webhooks-report.json`，原始过程与 JUnit 结果在报告的随机 `run_directory` 下。必须同时核对本次退出码、时间、`verification_completed`、`required_runtime_cases_passed`、`skipped=0`、`owned_database_removed` 和日志凭证检查，不能只看通过数量或旧报告。必需的三个实际登录/HTTP/调度用例没有全部通过时，即使其他测试达到至少 15 项，整体仍失败。

用例结束时停止本次子进程、清理自己的 Redis 前缀与随机来源限流键；入口最后仅删除严格命名且由自己创建的测试库。数据库清理或凭证日志检查失败时整体失败，日志中检测到的连接密码会脱敏。临时调度状态、合成 SQLite 身份库和测试输出留在忽略的本地测试目录中，不提交仓库。运行中断或主机重启时，不应假设清理已完成，应依据本次报告和实际进程、数据库、队列归属核查。

本入口的八项保护测试通过，覆盖远程目标拒绝以及日志密码检测；后者对短密码按 URL 用户信息和明确密码字段识别，支持编码值，避免把普通项目路径误判为密码，较长原文密码仍会脱敏。首次运行出现的路径误判保留为失败报告。扩展后的完整组合于 **2026-10-25T14:39:36Z** 完成 **15 passed / 0 skipped**，三个必需运行用例、临时库清理和日志检查均通过，原始报告在 `.tmp/native-dev/webhook-live-b306235633404c33889ba984b656695d/`。不能以此日期的成功替代下一次执行结果。8 GB 本机与常驻后端并发启动多个集成子进程时曾出现监听超时，失败报告保留；重跑只暂停并核验本批拥有的后台进程、串行运行，结束后恢复，禁止为测试停止其他项目服务。

**当前本机状态（2026-10-25T14:50:10Z）：**常驻开发库为 `0021_webhook_deliveries`，独立密钥保留在忽略的私有环境文件中。零在途/待审批/启用来源/定时任务及空队列预检后，仅在本项目 `.env.dev` 开启 `WEBHOOK_EXECUTION_ENABLED=true`，原文件备份在 `.tmp/private-config/`，不提交配置或备份。Web、两个 API、AI、Workflow、Worker 与 Beat 七个进程身份核验，六个 HTTP 入口为 200，能力返回 Webhook 可执行，并实际观察 Beat 恢复任务成功；证据 `.tmp/webhook-native-active-health.json`。观察健康超时后应先检查同一进程是否稍后就绪，不因此重复启动。

用户在 Ego 登录后，已实际完成新测试流程的拖拽、连线、保存、发布 v1、创建停用配置、启用、凭证轮换自动停用与刷新状态；证据 `.tmp/webhook-ego-ui-evidence.json`。随后另建仅有 Webhook 和文本回复的流程，使用本次临时来源凭证在内存计算签名，向常驻公开接收地址发送相同合成通知两次，均返回 `202`、去重编号相同；页面实际打开关联运行并核对“执行成功”及模板输出，展开记录为“执行完成 / 共 1 条”。完成与清理证据 `.tmp/webhook-ego-delivery-evidence.json`，详细范围见开发计划 4.2.223。两个测试来源均停用，测试流程由页面清理，原有五个流程保留；未读取用户登录凭据或调用真实模型/业务系统。

管理端查看入口仍为 `http://127.0.0.1:5173/ai/workflows`。原生客户端、真实业务订单、全部正式 UI 条件和生产验收需分别完成。回退先核查启用来源及在途任务，再停用来源、恢复开关并重载本机进程；保留密钥和审计表，勿自动删表降级。

**AI 迁移补记（2026-10-25T15:41:05Z）：**整库备份验证后，在实际恢复的独立数据库中演练 AI 三段迁移，再仅暂停本项目 AI 并升级本机至 `f4a5b6c7d8e9`。25 张原有 AI 表数据摘要保持一致，新增字段为空，API/Workflow 版本不变；其余六个常驻进程保留。恢复 AI 健康为 200，Ego 中员工与工具列表实际可读，`bound_device_policy_digest` 缺失错误关闭。报告 `.tmp/ai-upgrade-rehearsal-report.json`、`.tmp/ai-upgrade-native-report.json`、`.tmp/ai-upgrade-maintenance-report.json`、`.tmp/ai-upgrade-ego-lists.json`，详细失败历史、备份与边界见计划 4.2.224。RAG 尚未启动，其资源读取 `502` 仍待处理；列表和健康正常不代替知识检索或真实业务验收。

**RAG 恢复补记（2026-10-25T16:10:28Z）：**只读检查确认本机与源码均为 `72097dc45151` 后，仅增加 RAG API，保留原有七个进程，不启动 RAG Worker/Beat、迁移或重新处理资料。健康中的数据库、Redis、向量检查通过；Ego 知识库列表及原文档详情实际可读，显示已处理和 7 个知识片段，资源读取的 `502` 关闭。本次启动配置 URL 与凭据完整隐藏，4 组测试通过；已有历史日志不自动清除。当前八个登记进程身份和七个 HTTP 入口核验正常，证据 `.tmp/native-resource-health.json`、`.tmp/rag-native-list-service-report.json`、`.tmp/rag-ego-resource-evidence.json`。详情及全量检查失败记录见计划 4.2.225；统一检查器的 `app` 路径不适用于 RAG，实际源码目录是 `src`，不可据 `Can't list 'app'` 认定编译通过。

**关联运行查看补记（2026-10-25）：**Webhook/定时记录的关联运行现使用独立历史模式，只显示保存的步骤与结果，不再提供测试输入或执行/取消操作。27 项相关回归和 Web 类型检查、全量 Lint、正式构建通过；Ego 实际签名合成通知后核对完成记录、快捷键及关闭后返回调试，并停用/清理本次流程、保留原有五个流程。证据 `.tmp/workflow-history-ego-evidence.json`；截图受窗口最小化限制未取得，其他正式 UI 条件仍需验收，详细范围见计划 4.2.226。查看入口为 `http://127.0.0.1:5173/ai/workflows`，从通知/定时记录打开关联运行；未发布云端。

**编辑加载补记（2026-10-25）：**新建跳转和读取期间暂不开放编辑，迟到读取不覆盖新流程或当前草稿，读取失败保留错误和重试。55 项相关回归通过，Ego 中名称保存后刷新保持，测试草稿清理且原有五个流程保留；实际 1440/1024/390 CSS px 的浅色 DOM 宽度无整页横向溢出。当前 Web 类型检查、全量 Lint 和完整 `npm.cmd run build` 已通过；最终报告 `.tmp/workflow-editor-loading-web-bounded-build.json` 于 2026-10-25T16:53:27Z 完成，真实构建退出 0、新产物及本批源码摘要核对通过。前面宿主 `Stack overflow`、esbuild `errno=1455` 及 PowerShell 将打包警告当异常的失败记录保留，不覆盖为成功；最终仅为子进程限制 Node/Go 内存和并发，并保留全部检查与构建入口。详见计划 4.2.227；截图/深色等正式 UI 条件仍待验收，本机定时开关仍关闭，云端未发布。

## 会话结束事件的本机回归

先启动本机 API、内部 API、Workflow、Workflow Worker，以及 PostgreSQL、Redis、WuKongIM。数据库须已按当前源码升级。执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-session-events.ps1
```

入口先检查服务和 Worker，然后创建随机测试企业、管理员、网页渠道及访客。脚本使用正式登录、工作流发布、订阅和关闭会话接口，发送本机 IM 模拟消息，等待后台自动投递和真实 Worker 执行，核对片段脱敏、输入清理及单一来源记录。最后停用订阅，删除本次工作流与测试企业，确认工作流和运行记录返回 `404`，清理本次 IM 频道。测试只使用事件和返回结果节点，访客设为人工模式，渠道 AI 关闭；不会准备或调用真实客户、外部业务连接及大模型任务。

报告位于 `.tmp/native-dev/workflow-session-events-report.json`，包含时间、随机测试编号、逐项检查、`verification_completed` 与清理结果；凭证只在进程内使用，不写入员工 CLI 的原配置。执行失败时不能把旧报告或部分检查当作通过；应结合本次退出码、报告时间、完成标记和 `cleanup_errors` 核对。脚本拒绝远程 HTTP/数据库目标、未明确运行及关闭 Python 断言的模式。数据库清理前会核对随机企业的名称和密钥归属，仅删除该企业范围的数据。

IM 频道删除不证明底层消息文件被物理擦除，模拟消息可能仍在本机 IM 存储中。该脚本用于本机合成数据的跨服务回归，不替代正式登录态页面、真实客户消息、设备、模型或生产验收。

## 事件回执密钥轮换的本机回归

确保 PostgreSQL、Redis 与本机 Workflow Worker 可用，再执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-event-key-rotation.ps1
```

脚本用空闲本机端口创建临时 Workflow 实例，跨进程切换随机测试密钥；创建、执行并清理独立工作流，验证旧格式回执导入、活动密钥切换、旧密钥缺失时 `503`、恢复后沿用原回执，以及不同正文冲突。可用 `-Port` 指定临时端口；已被占用的端口会拒绝使用，不会停止其拥有者。它不写入私有环境文件或轮换实际服务的共享密钥；临时 HTTP 密钥在测试各阶段保持相同。本项覆盖回执核验；待投递输入的加密密钥轮换使用下节独立脚本验证，两者均不能替代生产轮换验收。

报告位于 `.tmp/native-dev/workflow-event-key-rotation-report.json`。应核对 `verification_completed`、清理状态和日志密钥检测结果；过程日志位于同目录下以随机测试编号命名的子目录。结束时只停止本程序创建的进程，清理其工作流、发布版本和运行记录；Redis 中合成任务结果仍按原有期限过期。本机常驻服务继续运行。

Workflow 默认 `DATABASE_ECHO=false`。确需查看 SQL 语句时可在私有环境中显式开启；连接工厂始终设置 `hide_parameters=True`，隐藏 SQLAlchemy 的绑定参数。已有历史日志没有随本次配置修复自动清理，仍需按留存策略管理。

## 冻结投递密钥轮换与回执丢失的本机回归

先确认本机 API、内部 API、Workflow、Workflow Worker、PostgreSQL、Redis 和 WuKongIM 正常，数据库已升级，再执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-event-delivery-rotation.ps1
```

脚本创建随机测试企业、人工模式访客与仅包含事件/返回结果节点的工作流，向本机 IM 写入合成片段，直接准备已关闭会话事实及其待投递记录。只对该夹具设置租约和重试时间，防止常驻扫描器抢占。临时本机转发器在 Workflow 第一次真实接受事件后丢弃成功回执；三个独立发送进程依次载入旧密钥、仅新密钥、旧新密钥并保留新项活动。程序验证缺少旧项时密文不丢失且不投递，恢复后发送同一正文/事件编号，接收端沿用原运行，最终只存在一条执行记录，接受后清除冻结密文。各进程还单独检查新字符串加密使用活动编号。

报告位于 `.tmp/native-dev/workflow-event-delivery-rotation-report.json`。结合本次退出码、开始/核对时间、9 项检查、`verification_completed`、临时转发器及测试数据移除标记、`cleanup_errors` 判断结果；失败时不得沿用旧报告。本机常驻服务及私有密钥不变，测试密钥只通过进程环境与内存使用。入口拒绝远程 HTTP/数据库、缺少明确运行开关和关闭 Python 断言的模式。清理前核对企业归属，仅移除本次工作流、运行、API 记录和 IM 频道/群组；删除 IM 频道不证明消息存储物理擦除，队列结果按原有期限过期。

本脚本覆盖冻结投递恢复，不替代正式关闭会话与自动收集链路，后者用 `verify-workflow-session-events.ps1` 单独验证。它也不证明生产、多实例、共享 HTTP/JWT 轮换、真实客户/设备/模型或正式页面可用。

## 定时流程的人工确认与来源撤销

定时来源可连接“人工确认”并在确认后继续只读流程。创建时选择当前企业的员工，确认后动作保持“仅继续流程”，再保存、发布和登记定时计划。该来源没有客户会话上下文，不能选择创建客户售后核对待办；`agent`、`tool`、`api`、`business_query` 等未开放组合仍由后端拒绝。事件/Webhook 的人工确认尚未开放。

计划停用或修订会取消尚未完成的审批/恢复运行并清理恢复检查点；Worker 在确认后执行前仍核对来源、版本和授权。拒绝、过期或撤权等终态也清理相应输入，已完成历史保留。前端现按触发来源限制动作：定时任务只配置“仅继续流程”；事件/Webhook 仍提示不支持人工确认。旧草稿含不适用动作时保留原选择并标记不可用，按提示主动改为“仅继续流程”；切回客户输入前原类别不会自动清除。详情及 51 项回归、Web 完整构建与实际页面验收见开发计划 4.2.230。

2026-10-25 本批 108 项相关回归通过，其中包括新增来源生命周期 15 项和定时确认 7 项。Ego 正式页面在自然 17:55 UTC 到期后取得真实等待审批，核对预览并由当前账号确认，Worker 恢复完成，三个节点各执行一次；关联的只读记录展示预期输出。停用后删除该临时流程，原有五个流程保留。审批页读取到“等待恢复执行”时先刷新并核对关联运行，不因该文案重复批准或派发。详情见开发计划 4.2.229；全服务质量检查仍受缺失检查器和已有超长行问题限制。

清理报告 `.tmp/timer-approval-final-quiescence.json` 为 2026-10-25T17:59:51Z，零启用来源、在途、待审批、未完投递及队列任务；`.tmp/timer-approval-db-deleted.json` 仅核对本次临时工作流及关联记录均为空。后台常驻服务继续运行，本批不调用真实模型、客户业务系统或线上环境。

本机有界 Worker 启动探针包含 Python/Celery 导入时间，冷启动可能超过其 8 秒期限。超时后应先核对进程身份、同组 API/Worker 日志和实际探针回执，保留初次失败报告；不要直接替换仍存活的实例。定时确认批次 API/Worker 为 23516/23720，加密批次为 15380/5580，执行时钟批次为 16240/27604；最新事件正文批次为下节的 12664/6972，另六个登记进程保持不变。

## 审批检查点加密与保管密钥

新的审批恢复上下文保存为密文封套；读取时复核审批员工、说明、截止时间、企业、工作流、运行、来源和固定发布版本，完成、拒绝、过期或取消后保留清理回执。旧普通/定时来源的明文检查点保留兼容读取，事件/Webhook 不接受明文降级，其人工确认组合仍未开放。

可选环境项是 `WORKFLOW_APPROVAL_KEYS` 与 `WORKFLOW_APPROVAL_ACTIVE_KEY_ID`。空配置使用至少 32 UTF-8 字节的 `SECRET_KEY` 加密，封套编号为 `legacy`；版本配置是编号到随机密钥的 JSON 映射，最多八项，活动编号用于新写。API 与 Worker 必须使用同一配置；切换前备份私有环境，保留未结束运行需要的旧项。将原 `SECRET_KEY` 导入 `legacy` 项后可读取旧模式密文，配置版本键时不能把 `legacy` 设为活动编号。缺少历史键会拒绝恢复，不把其他用途的键或活动键当作替代。示例文件只保留空项，真实材料留在私有环境中。

2026-10-25 本批 218 项相关回归通过。零任务预检后仅重载 Workflow API/Worker 为 15380/5580，未修改实际保管密钥、环境开关或数据库结构。Ego 正式页面验证合成任务的加密等待、本人预览确认、真实 Worker 恢复原输入、三节点各一次和终态清理；普通运行完成后输入的 JSON `null` 应与 SQL `NULL` 一并按已清空处理。临时流程已删除，原有五个流程保留，最终只读清理报告 `.tmp/approval-checkpoint-final-quiescence.json` 为 18:55:24Z。

详细实现、失败记录、工程检查限制和剩余来源生命周期开发见开发计划 4.2.231～232。实际服务当前使用 `SECRET_KEY` 模式；单元密钥轮换不等同于生产或多进程轮换。等待审批后的执行时钟已由下节实现；事件输入生命周期和独立进程轮换仍需完善，完成对应验证后才开放事件/Webhook 普通确认。

## 审批恢复的执行时钟与隔离回归

首次领取和每次成功恢复审批时，同步写入私有列 `active_segment_started_at`。恢复保留原 `started_at`，重复领取不刷新时限；来源超时扫描与原子终止都按本段时间判断，旧列为空时兼容原开始时间。等待审批和等待恢复不算正在执行。`0022_execution_segments` 是新增可空列的迁移，不回填旧时间；存在新时间历史时降级拒绝丢列。升级须先控制来源与在途任务，再统一升级 Workflow API/Worker；回退保留列和检查点，不通过清空字段绕过保护。

需要验证长等待、两次确认、实际消息重投和超时清理时执行：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-approval-segments.ps1
```

该入口解析本项目本机基础设施，创建随机独立数据库、专用队列、临时 HTTP 和 Worker。它经真实内部员工核验、审批 HTTP、Redis 派发及 Worker 重启完成两次恢复，验证原批次变量、重复消息无重放、原起点保留及检查点清理；另在实际 PostgreSQL 扫描三类来源的新段和真正超时段。仅调整自有合成记录的时间，不改系统时钟。计划为测试夹具，不是自然 Beat 或正式页面验收，也不调用模型、客户和真实业务系统。

核对本次退出码和 `.tmp/native-dev/workflow-approval-segments-report.json` 的四项 `checks`、开始/结束时间、`verification_completed`、自有进程/数据库/队列清理与日志密钥检查。最新四项结果为 2026-10-25T19:27:44Z，通过且清理完成；下一次执行必须核对新结果。观察超时不证明进程死亡，应核对同一对象，不自动起替代进程或停其他项目服务。

本机已在实际备份恢复副本演练升级、回退和历史保护，12 张 Workflow 表原记录摘要一致。19:29:28Z 常驻库升至 `0022_execution_segments`，新列为空，API 迁移版本不变；只重载 Workflow API/Worker 为 **16240/27604**，另六个进程与私有环境不变。19:31:26Z 最终核对为零启用来源、在途、待审批与队列任务，原五个流程保留，新列类型通过；报告 `.tmp/execution-segment-migration-rehearsal.json`、`-migration-apply.json`、`-native-upgrade.json`、`-final-quiescence.json`。Ego 恢复到同项目空间 5 后，既有列表和两条完成历史可读；没有新增常驻审批任务，不代替完整 UI 验收。238 项相关回归通过，但全服务检查器缺失、其他服务工程质量、来源输入生命周期与审批密钥独立进程轮换仍保留待办。详细失败记录和范围见计划 4.2.232；未发布云端。

## 事件正文保管与进程间密钥恢复

新事件的调用正文保存在 `_event_payload` 密文封套，独立来源坐标用于权限核验。可选配置 `WORKFLOW_EVENT_INPUT_KEYS` / `WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID` 与审批、回执键分开；空配置使用至少 32 UTF-8 字节的 `SECRET_KEY`，API 与 Worker 保持一致。轮换正文键不要求更换 HTTP/JWT 共享键；保留所有未结束输入需要的旧项，旧模式可将原共享键导入 `legacy` 项，但不能以 `legacy` 作为活动版本编号。只读准备入口会发现不可用的保管配置。

领取前先检查来源待处理保留上限和当前授权，再核验正文键；缺历史键时保留原运行及密文，显示待恢复原因，恢复后使用原派发恢复机制。来源已失效时可在不解密正文或检查点的情况下终止对应状态并清理；持续巡检等待状态和取消审批状态同步仍待补齐，Event/Webhook 普通确认暂不开放。旧明文事件需通过原始 HMAC 及固定事件身份核验，不自动批量改写历史数据。

独立验证入口：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-event-input.ps1
```

它只使用本机基础设施，在自有随机数据库、队列、临时 API/Worker 中完成旧键写入、缺键保留、原回执去重、恢复后真实扫描派发、原正文精确返回和新活动键使用。新写入先在 Worker 停止时核对密文编号，再交给仅持有新键的 Worker 执行；只凭任务完成不能证明已使用新键。报告为 `.tmp/native-dev/workflow-event-input-report.json`，核对本次退出码、四项检查、时间、完成及进程/库/队列清理标记和日志键检查。最新完整结果为 2026-10-25T20:15:43Z，通过且清理完成；下一次必须查看新结果。它不切换常驻实际键，不调用模型或客户，不等同于审批检查点键或生产轮换。

2026-10-25T20:05:09Z，仅重载本机 Workflow API/Worker 为 **12664/6972**，其余六个进程与私有配置不变；常驻版本仍为 `0022_execution_segments`，本批没有迁移。实际正文保管使用原 `SECRET_KEY` 兼容模式。原关闭会话事件入口随后完成九项常驻验证，临时员工 CLI、真实 IM 脱敏片段和 Worker 链路通过；自己的企业/流程/运行及 IM 频道清理，IM 底层物理擦除不在此证明范围。299 项相关回归通过，完整服务质量检查仍受工具缺失限制。

最终独立审计 `.tmp/event-input-final-audit.json` 于 **20:17:38Z** 核对零启用来源/在途/待审批/队列、原五个流程保留、本批三次独立库及常驻自有来源记录均移除。原通用只读核验报告可能在后续预检被更新，应按各批独立输出和本次时间读取。Ego 原空间失效后恢复为同项目空间 6，既有列表正常；没有把列表读取当成新来源审批或全量视觉验收。查看入口 `http://127.0.0.1:5173/ai/workflows`，详细实现、失败记录和剩余工作见开发计划 4.2.233；云端未发布。


## 等待事件来源巡检与审批同步

常驻 Workflow Beat 每 60 秒派发 `audit_waiting_event_sources`，只扫描具有来源会话的正式事件等待审批/等待恢复记录。每批 100 条、45 秒预算，单条 10 秒，90 秒 Redis 租约和 7 天轮询游标；数据库、队列、内部接口与共享键改变会切换进度范围。旧租约不能写新游标。每次任务关闭自身 HTTP 连接池，下一任务不复用旧循环。授权不明时保留等待，明确失效时终止对应状态并取消未决定审批；已决定历史保留。巡检不需要正文或检查点历史保管键，不代表这些键可用于恢复。

独立验收入口：

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-event-source-audit.ps1
```

报告 `.tmp/native-dev/workflow-event-source-audit-report.json` 最新于 2026-10-25T21:02:39.758560+00:00 七项通过，包含自然 Beat、实际 API 权限、Redis 租约、同 Worker 重复检查、服务中断及四种撤销、待审批同步和零节点执行。仅等待检查点为合成设置，Event/Webhook 普通确认仍关闭。退出先停 Beat、等自有排队/预取消息空闲，再停 Worker；不要删除共享 `unacked` 或其他队列。最新临时库/进程/队列及自有未确认消息清理通过，后续运行必须核对新报告、退出码与时间。

本机 2026-10-25T21:03:13.9176921Z 重载后 API/Worker/Beat PID 为 23580/17632/4672；其他五进程和私有环境不变，无迁移，版本 0022。新 Beat/Worker 已自然执行一次空巡检，最终 2026-10-25T21:04:50.774630+00:00 只读核对原五流程身份与定义摘要不变、零在途/队列。314 项相关回归通过；完整检查工具缺失，CLI 常用登录未配置，不能视为全工程通过。Ego 使用空间 7，工作流/我的审批可读，未把列表读取算作新组合验收。查看 http://127.0.0.1:5173/ai/workflows；剩余审批键跨进程恢复、组合发布和真实页面见计划 4.2.234，未发布云端。

## 审批保管键缺失与真实恢复

批准恢复缺少历史保管键或键配置不可用时，保留同一 `pending_resume`、审批决定及密文，运行详情显示安全的等待原因；不会领取执行段或重放节点。恢复原键后，原恢复扫描可再次派发并单次领取。并发来源终止优先，终态原因不会被缺键提示覆盖；损坏密文与缺键分开处理。API 与 Worker 的 `WORKFLOW_APPROVAL_KEYS` / `WORKFLOW_APPROVAL_ACTIVE_KEY_ID` 必须按保留中的检查点协调配置，旧记录需要的键不能提前删除。实际键不在日志/报告中输出。

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-approval-keys.ps1
```

该入口在自有随机库、队列、内部 API、Workflow API 与多个 Worker 中，真实执行输入/两次确认/返回结果；旧键写入、缺键保留、真实 HTTP 提示、旧键恢复、新键接续、重复消息及实际员工撤权均覆盖。API 模型仅建 Project/Staff，Workflow 使用真实迁移；不代替完整 API 迁移或 Event/Webhook 审批组合。最新 `.tmp/native-dev/workflow-approval-keys-report.json` 于 **2026-10-25T21:16:31Z** 六项通过，生产摘要不变，自有库/进程/队列/未确认消息已清理，日志未检出测试键。仅测试数据库的派发时间调整为可恢复，不修改机器时钟或常驻实际密钥。

本机仅在 **21:17:19Z** 重载 API/Worker 为 **2888/18080**，Beat **4672** 和其他五进程保持；私有配置与 `0022_execution_segments` 不变，实际审批键仍为原共享键兼容模式。只读 `.tmp/approval-key-native-quiescence.json` 于 **21:17:45Z** 核对原五流程身份/定义摘要未变、零在途/待审批/队列。318 项相关回归与目标 Mypy/Flake8 通过，完整工程工具缺失及常用 CLI 未配置分别保留。Ego 空间 7 的既有审批页可刷新；这不代替新组合实际业务页面验收。后续 Event/Webhook 普通确认仍需能力同步、真实输入/来源生命周期与页面验证，详见开发计划 4.2.235。查看 http://127.0.0.1:5173/ai/workflows，未发布云端。

## Event/Webhook 普通确认与来源撤销

截至 **2026-10-25T22:06:42Z**，已开放并在本机加载“事件/外部通知 → 员工确认 → 返回结果”，更新前文“组合仍关闭”的历史状态。审批说明支持受限标量路径，如 `source.data.summary`、`source.body.question`，不接受完整对象或缺失引用。这两类来源仍不提供客户售后动作、员工、业务工具、API 或受控业务查询节点；页面配置仅显示“仅继续流程”。

自然 Beat 原每 60 秒巡检现在同时核验正式事件与 Webhook 的 `waiting_approval`、`pending_resume`。权限接口暂时不可用时保留等待；明确停用、改绑、清除或撤权时按预期状态终止，清理输入和检查点、取消未决定审批，保留已批准历史。后台巡检与领取前来源核验不依赖正文或检查点旧键，不能据此推断缺键也可以继续执行。批量、超时、租约、游标和实际运行失败关闭边界保持原设置。

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-source-approvals.ps1
```

该入口在自有随机数据库、队列、内部 API、Workflow API、Redis、Beat 与多个 Worker 中发布六个事件和三个 Webhook 流程，实际运行到审批，不直接制造等待状态。关闭会话/订阅/投递事实为合成夹具，当前来源权限走实际 API；不能代替真实客服关闭会话的业务验收。包括两类确认恢复/重复去重、自然 Beat 在缺两类旧键时保留七条等待、旧键恢复、实际停用/改绑/清除/撤权、已批准历史保留和正文清理。最新 `.tmp/native-dev/workflow-source-approvals-report.json` 于 **21:50:23Z** 九项通过、三条完成六条取消、21 条唯一节点回执；自有进程/库/队列/未确认消息均清理，日志无测试键，生产摘要未变。复验须重新核对本次退出码、时间和所有清理标记；测试使用的 API 选定模型不等同完整 API 迁移。

常驻 Ego 空间 **7** 已分别完成页面发布、人工预览/二次确认、运行记录刷新/展开及原文结果核对。手动事件由测试面板提交；Webhook 在页面代码内用自有一次性凭证签名，经本机公开入口进入真实 Worker，没有输出或保存凭证。草稿由既有前端服务创建，不把这一步冒充完整画布手工创建。证据 `.tmp/source-approval-ego-event-result.json/png`、`-webhook-result.json/png`；两条临时流程、运行、审批、节点回执、发布、Webhook 与命令记录已移除。仅使用合成内容，无模型/真实客户/渠道或业务系统调用。

374 项后端相关回归、42 项前端专项、目标 Mypy/Flake8、Web 全量类型和 lint 通过。两次 Web 构建因本机内存不足失败后，仅暂停本项目 Web/Workflow API/Worker 串行执行，**22:03:42Z** 完整构建退出 0、新产物与源码摘要通过；保留现有分包大小警告。**22:04:07Z** 自动恢复后当前登记 PID：Web **26084**、Workflow API **8704**、Worker **3796**；Beat **4672** 及其他四个登记进程保持原 PID，见 `.tmp/source-approval-serial-build.json`。没有迁移，仍为 `0022_execution_segments`；实际保管键仍为原共享键兼容模式，没有切换常驻实际键。**22:05:44Z** Ego 重新加载、登录态与审批页正常。

最终只读 `.tmp/source-approval-native-quiescence.json` 于 **22:06:42Z** 核对原五流程身份/定义摘要一致、七张表的自有页面验收记录均为零、自有随机库已移除，零启用来源、在途、待审批、队列与未确认消息。完整 Python 工程检查器缺失及常用 CLI 未配置仍单列，不把专项结果算作全工程通过；云端未发布。查看 http://127.0.0.1:5173/ai/workflows，后续完整范围和交接见开发计划 **4.2.236**。

## 并行组运行中取消与持久时间证据

已有并行执行现在在等待分支时定期检查原运行/来源/员工授权，间隔 250 毫秒、核验本身耗时另计；Worker、同步和 SSE 入口均接入。停止监控与节点回调串行访问同一数据库会话，组结束后协作退出，避免取消到一半的数据库事务影响清理。终态已取消时可按原企业、运行及并行分支保存脱敏的取消时间/耗时，不恢复输入或输出、不覆盖已存在回执。仅全部成功后才汇合；分支失败、超时或取消停止其他本地等待。不能据此承诺撤销第三方已接收的请求或开放写入。

```powershell
pwsh -NoProfile -File scripts/native-dev/verify-workflow-parallel-control.ps1
```

该入口创建随机自有数据库和队列，启动真实 Workflow API/Worker、PostgreSQL/Redis 与独立合成 AI HTTP 服务，不修改常驻 `AI_SERVICE_URL` 或调用外部提供商。通过实际发布的七条流程验证多步分支重叠、五分支重叠与确定结果、HTTP 503、组超时、Worker/同步/SSE 运行中取消、跨企业取消拒绝、重复消息不重放。监控使用实际数据库状态，分支走真实 HTTP，不替换节点函数；合成服务不代表真实 Provider 验收。

最新 `.tmp/native-dev/workflow-parallel-control-report.json` 于 **2026-10-25T22:26:16Z** 十项通过，`run_evidence` 留存实际节点时间/状态/耗时，`synthetic_http_trace` 留存合成请求到达/返回/断开时间。自有进程/库/队列/未确认消息清理通过，日志无测试键，生产摘要未变；下一次需核对新退出码、时间和清理标记。443 项相关回归通过，四个目标模块 Mypy 及新增文件完整 Flake8 通过；完整工程检查器缺失、API 两处既有类型错误、默认 CLI 未配置仍保留，不能把专项结果算为全工程验收。

**22:27:23Z** 只重载本机 Workflow API/Worker，当前登记 PID 为 **1404/12200**；其他六进程、私有环境、`0022_execution_segments` 不变，Web **26084**、Beat **4672** 保持。本批未更换实际密钥、修改前端源码或重跑前端构建。Ego 空间 **7** 核对临时画布的 2～5 分支、1～300 秒限时及必须全部完成，草稿已删除；没有运行模型，不能把该页面核对算作完整并行结果页面验收。

最终只读 `.tmp/parallel-control-native-quiescence.json` 于 **22:29:12Z** 核对原五流程身份/定义摘要未变、自有运行库已移除、零启用来源/在途/待审批/队列/未确认消息。查看 http://127.0.0.1:5173/ai/workflows，详细实现、失败记录和剩余范围见开发计划 **4.2.237**。云端未发布，完整目标继续。

## Windows 单窗口客户端基础

2026-10-25 本批在 `repos/tgo-device-agent` 新增默认关闭的单窗口截图、区域点击及原生普通文本输入。需要设备使用者在本机核对窗口、程序和点击范围；远端不能更换窗口，截图凭据一次使用，内容/焦点/身份变化与重连使旧授权失效。已有只读文件策略仍仅允许 `fs_read`。操作不代表业务办理完成，不能用来提前开放业务屏幕模板。配置/停止/限制和重现命令见该服务 README。

普通 `go test -count=1 ./...` 本次为 51 项通过、3 项条件跳过；显式 `TGO_SCREEN_NATIVE_ACCEPTANCE=owned-fixture-only` 的 `TestNativeOwnedWindow` 另外在当前进程自有窗口完成真实截图、区域点击、中文输入、图像变化、密码控件拒绝和关闭检查。最新 `.tmp/native-dev/windows-screen-owned-acceptance-current/report.json` 于 **2026-10-25T23:06:00Z** 八项通过，退出码 0，包含 `owned_fixture_closed`；下次必须核对本次时间/退出码/报告。首次清理失败的早期报告无效，已改为先确认窗口与线程关闭再报告成功。没有操作已有客户软件或浏览器。

系统 Go 不在 PATH，官方 Go 1.27.3 工具链在本项目 `.tmp/toolchains/` 验证大小和 SHA-256 后使用，缓存/模块查询/进程内 PATH 仅限测试子进程；没有全局安装。统一 Agent 静态技能 `go vet ./...`、Windows 二进制构建/版本输出、Linux amd64 屏幕包交叉编译通过；不是 Linux 运行验收，也未另证 Go 1.22 编译。图片/工具接口继续使用现有协议。

本机 `$local-services` 核查八登记进程及六个 HTTP 入口正常，Workflow API/Worker **1404/12200**、Web **26084**、Beat **4672** 保持；没有重启、迁移、修改私有环境或新增设备绑定。Device Control 等原可选服务仍未启动，Agent 仅本地构建未安装/连接实际服务。CLI 默认 8000 的预检不能代表本机 18000 健康状态；常用 CLI 未配置与客户端独立检查分别记录。

下一步按开发计划 **4.2.238** 补窗口选择、屏幕策略发布/回执、工具能力名及完整控制服务链路，再做录制回放。查看 http://127.0.0.1:5173/ai/device-control；页面不会自动出现在线屏幕设备。未发布云端，D1～D7 及原微信客服/G0～G5 完整目标继续。

## 本机窗口选择与可见停止入口

`tgo-device-agent --screen-select` 已支持中文原生选窗、逐项输入许可、有限点击区域和操作上限；需要原设备绑定凭据。默认没有目标/输入权限，换窗/刷新清空许可，取消不连接服务。确认后正常激活所选窗口并等待前台事实，绑定时再次核对同一进程/窗口/布局；无强制激活或后台操作。独立本机状态窗显示目标并提供停止，关闭也停止，显示时不抢目标焦点。详情/标准 Go 运行命令见服务 README。

原截图子控件有标题栏偏移，已改为只在内存绘制绑定窗口，再裁切客户区作为 PNG；父/子像素标记同时验证，坐标与点击一致，返回中不包含标题栏。实际 UI 截图已核对按钮完整。首次组合因夹具重复退出消息而超时，无效；已补自有线程/窗口清理，不能把旧组合报告当本次结果。

最终普通客户端回归 **60 passed / 5 skipped / 0 failed**；额外三组原生窗口测试在同一进程顺序通过。最新 `.tmp/windows-screen-picker-dpi-accepted.jsonl` 和 `.tmp/native-dev/windows-screen-picker-dpi-accepted/` 于 **2026-10-25T23:36:57Z** 退出 0，含选窗/停止 12 项、原输入八项、父/子坐标断言，所有自有窗口关闭。报告实际 **DPI 120**；不是多屏/其他 DPI、真实物理输入/锁屏或业务软件验收。只使用自有合成窗口，控件选择/停止由原生消息驱动，未操作用户已有应用。

Agent `go vet`、Windows 完整二进制/帮助输出及 Linux amd64 完整客户端交叉编译通过，Linux 未执行。仍使用本项目 Go 1.27.3，无全局 PATH/安装变更。常驻八进程和配置保持，Device Control 仍未启动，Agent 未绑定或安装到实际设备，未发布云端。Ego 空间 7 本轮不存在后按原授权恢复为 **8**，工作流原登录态可读；未把浏览器列表当 Windows 客户端验收。

下一批按 **4.2.239** 接屏幕专用策略、生效回执和工具名/能力检测，再验证实际控制服务→TCP→客户端并做有限场景结果检查，随后录制回放。原文件读取策略不扩权，完整目标保持。入口 http://127.0.0.1:5173/ai/device-control。

## 屏幕策略 v1、隔离截图与独立客户端 TCP 验证

当前协议见 [Windows 屏幕策略](device-screen-policy-protocol.md)。本机授权产生不含窗口身份的实例编号，服务端只能确认更小范围；次数不重置，失败更新/新版本使旧策略或旧截图失效，重连需重新本机授权。屏幕模式初始化失败不回退原文件/命令工具；原读取策略仍只允许 `fs_read`。目前没有持久发布/API/Web/员工执行入口，正式 MCP 继续屏蔽屏幕工具，不能把协商当作生产可用。

独立 Go CLI 首次跨进程取图失败，改为有私有管道、一次性编号和 2 秒期限的截图子进程，使用 `PrintWindow` 后只返回裁切客户区。主进程等待超时/取消的自有子进程退出，目标身份不放到命令行或网络字段。状态窗摆到目标外侧，满屏时留任务栏入口，避免自身遮挡引发命中保护。真实业务软件和多屏/其他 DPI 仍单独验收。

本批普通客户端 **74 passed / 6 skipped**，Control 全单测 **96 passed / 4 skipped / 38 warnings**。原生组合 `.tmp/screen-policy-final-native.jsonl` 于 **2026-10-26T04:36:41Z** 五项通过，包括三组原生窗口、独立 CLI/TCP 和明确停驻子进程监督；目标 DPI 120。最终参数约束后 `.tmp/screen-policy-rpc-accepted.jsonl` 于 **04:43:41Z** 再通过 15 项，报告/PNG 在 `.tmp/native-dev/screen-policy-accepted/`。TCP 对端是合成本机测试端，未启动真实 Device Control 服务。期限报告 `.tmp/native-dev/screen-policy-final/worker-timeout-report.json` 为 **2002ms**，确认自有进程已退出；不冒充真实业务应用挂起。

Agent Go Vet、Control compileall、三个新文件目标格式/Flake8和两个生产模块目标 Mypy 通过。统一 Control 全工程检查器缺失仍退出 1。一次 Go 链接内存失败后，在任务子进程使用 128MiB/GOGC20/单并发串行成功；未改全局设置、暂停常驻服务或重放业务。没有模型/迁移变更，也未提交、推送、上线。

下一批按计划 **4.2.240** 补持久发布与撤销、API/Web 管理、同连接业务验证和员工/工作流固定范围，再完成真实 HTTP/MCP/TCP 链路。入口 http://127.0.0.1:5173/ai/device-control；完整目标保持。

## 屏幕策略内部发布与撤销

2026-10-26：Device Control 新增迁移 `0011_device_screen_policy` 与内部 `/v1/devices/{device_id}/screen-grant`、`screen-policy`、`screen-policy/revoke`，沿既有企业/设备签名边界管理当前连接的本机授权交集。期望范围/版本和撤销历史持久保存，连接确认不持久恢复；数据库重启或重连后不会自动恢复屏幕授权。文件与屏幕发布沿相同设备行锁互斥，完整显式模式切换、正式 API/Web、业务验证与员工/工作流绑定继续；正式 MCP 屏幕发现/调用仍关闭。

最终 `.tmp/native-dev/screen-publication-final/report.json` 退出 0，源码测试前后 SHA-256 一致。全 Control 回归 **112 passed / 10 skipped / 37 warnings**，另在随机本机 PostgreSQL schema 内 **6 项通过**：11 个实际迁移升级函数、首次/撤销竞争、文件/屏幕互斥、读库与 ASGI HTTP、空表降升/非空降级拒绝；每个自建 schema 删除后核对不存在。数据库专项的身份和 TCP 回复为夹具，不是完整真实服务链路。新文件目标格式/Flake8、五个目标生产模块 Mypy 和 compileall 通过；全工程检查器缺失、只看已跟踪 diff 的迁移/同步技能限制、常用 CLI 未配置仍保留。

未修改常驻环境、业务表或迁移版本，未重启八服务、绑定设备、提交/推送或上线。Device Control 仍未常驻运行。后续按开发计划 **4.2.241** 接完整模式切换、页面和真实链路。查看 http://127.0.0.1:5173/ai/device-control；本批没有新增可见表单，不能用页面未变化判断后端没有实现。
