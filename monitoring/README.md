# 企业微信客服持久状态监控

本目录的 Prometheus 规则采集 `tgo-platform` 的持久状态，Alertmanager 将告警发送到运营邮箱。它是可选的监控部署配置；仅添加文件不会启动服务或发送邮件。正式启用前，先在隔离环境检查目标服务、指标、告警状态及邮箱投递。

## 在部署机准备私密配置

1. 确认 `tgo-platform` 使用的 `SAAS_INTERNAL_TOKEN`，准备运营收件邮箱、发件邮箱、SMTP 服务器地址、端口、登录用户名和授权密码。脚本要求输入同一个内部服务令牌，否则 Prometheus 抓取会被拒绝。不要把这些值发到聊天或写入仓库。
2. 在项目根目录执行 `python scripts/monitoring/generate_alertmanager_config.py`，按提示输入。密码和内部令牌使用无回显输入。脚本生成 `data/monitoring/alertmanager.yml`、`saas-internal-token`、`smtp-password`；`data/` 已被 Git 忽略。已有文件不会被覆盖。Linux 部署机将目录权限限制为 `0700`，其中的文件设为 `0644`，让以非 root 用户运行的容器可读取文件挂载；不要将文件移出这个私密目录。Windows 部署机还需由运维核查目录 ACL；POSIX 权限设置不替代 Windows ACL。[Docker Compose 的文件来源密钥使用 bind mount，不能靠 `uid`、`gid`、`mode` 自动改写文件权限](https://docs.docker.com/reference/compose-file/services/#secrets)。
3. 启用前运行 `docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring config --quiet`，再用对应版本的 `promtool check config monitoring/prometheus.yml`、`promtool check rules monitoring/rules/wecom.yml` 和 `amtool check-config data/monitoring/alertmanager.yml` 检查配置。检查输出不得包含凭证。
4. 在已准备好基础服务的部署环境中执行 `docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring up -d tgo-prometheus tgo-alertmanager`。Prometheus 和 Alertmanager 管理端仅绑定本机回环地址，默认分别为 `http://127.0.0.1:19090` 和 `http://127.0.0.1:19093`；外部访问需走现有受控运维入口。

Prometheus 在 `/targets` 核对 `tgo-platform-wecom` 为 UP，在 `/alerts` 核对规则加载及状态。用隔离数据触发一次非客户可见的测试告警，核对运营邮箱收到触发与恢复邮件，并记录时间、Alertmanager 投递状态及处理人。**未完成实测投递前，不宣称生产告警可用。** 告警规则只使用固定状态标签，不包含客户、会话或企业标识。

`TGOWeComMediaNoticeNeedsReview` 表示失败提示的发送结果未知或需人工核查。运维应先检查企业微信实际发送结果；不要直接重发，避免客户收到重复提示。其他阈值按实际队列基线调整，不能把示例值视为生产 SLA。

## 回退与限制

停止这两个可选容器即可停止新采集与邮件通知：`docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring stop tgo-prometheus tgo-alertmanager`。不要删除命名卷或 `data/monitoring`，以便保留历史和凭证并排查问题。若要改收件人或 SMTP，先备份本机私密文件，再在维护窗口替换并重启 Alertmanager；变更凭证时同时核对 `SAAS_INTERNAL_TOKEN` 一致性。

当前规则只覆盖企业微信客服消息、媒体任务和媒体失败提示的持久状态，不覆盖所有业务链路，也不替代数据库恢复、真实微信回调和人工兜底演练。
