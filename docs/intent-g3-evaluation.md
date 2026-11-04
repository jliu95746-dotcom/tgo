# G3 意图分类离线评测

本工具用于按原计划的真实业务指标评测 `tgo-ai` 意图分类结果。默认只校验标签样本；`--predictions` 读取已经保存的分类结果，不创建模型请求；只有 `--run` 才逐条调用模型，可能消耗企业 AI 额度。工具与合成样本测试通过，不等于 G3 已验收。

## 1. 准备样本

由业务人员从历史会话中选取 **200～500 条**，先移除姓名、邮箱、电话、订单号、地址、支付信息和其他可识别内容，人工审定意图及是否必须转人工。覆盖 `IntentName` 的全部 12 类，也要包含投诉、主动转人工和其他人工判定的敏感场景。样本放在仓库忽略的 `.tmp/` 或经批准的受控目录；不要提交 Git，不要把原始会话放入命令参数、日志或本报告。

文件格式为 UTF-8 JSONL，每行一个 JSON 对象。例如以下**合成**样本，仅展示字段结构，不满足运行要求：

```json
{"case_id":"sample-001","expected_intent":"product_inquiry","requires_handoff":false,"classification_input":{"user_text":"这款包是什么材质？"}}
```

- `case_id` 唯一，只能包含英文字母、数字、点、下划线、短横线。
- `expected_intent` 使用 `app.schemas.intent.IntentName` 的固定英文标识。
- `requires_handoff` 为人工审定的布尔值；`complaint` 与 `human_handoff` 标签本身也纳入敏感样本。
- `classification_input` 与现有 `IntentClassificationInput` 一致，可按需提供 `user_text`、`asr_text`、`ocr_text` 或 `vlm_text`。媒体文本仍须脱敏。
- 每行只允许声明的字段；重复编号、未知字段、无内容、非 UTF-8 或超过工具限制的数据集会被拒绝。工具复用现有敏感信息规则，对 `case_id` 和四类样本文本检查常见的完整手机号、身份证号、带标签的支付账号、邮箱和地址；发现时整批拒绝，错误只说明行号，不输出原文。`--run` 在第一条模型请求前再次检查整批样本，防止直接调用运行器绕过文件校验。此检查不会自动脱敏，也不能识别所有姓名、地址写法或其他可识别内容；数据提供者仍须先人工检查。

## 2. 校验与运行

在 `E:/智能客服系统/repos/tgo-ai` 下运行，以下路径与 UUID 均为占位示例：

```powershell
& .\.venv\Scripts\python.exe -m app.evaluation.intent_g3 --input E:\智能客服系统\.tmp\intent-g3-cases.jsonl
```

校验成功只输出 `validated_cases` 和 `dataset_sha256`，即原始文件字节的 SHA-256。校验模式不执行 200 条和类别覆盖的正式闸门，也不调用模型。

如果已有历史消息的分类结果，先从员工有权读取的消息分析结果中，按人工标签样本的 `case_id` 对齐并投影为仅含以下字段的 JSONL。`case_id` 用脱敏编号，不使用原微信消息 ID 或客户标识；不要导出 `entities`、客户正文或其他分析字段：

```json
{"case_id":"sample-001","intent":"product_inquiry","confidence":0.96,"recommended_route":"auto_reply","need_human":false,"taxonomy_version":"v1","classifier_version":"tgo-ai-intent-v1","policy_version":"customer-service-routing-v1"}
```

```powershell
& .\.venv\Scripts\python.exe -m app.evaluation.intent_g3 `
  --input E:\智能客服系统\.tmp\intent-g3-cases.jsonl `
  --predictions E:\智能客服系统\.tmp\intent-g3-predictions.jsonl
```

离线模式按 `case_id` 对齐，不依赖文件行顺序；只接受两份文件中完全一致的样本编号，并要求所有预测具有同一个分类器版本和路由策略版本。重复、缺失、未知字段或版本混用均拒绝。报告增加预测文件 SHA-256、分类器版本和路由策略版本；不会输出样本编号或客户内容。**文件来自真实历史结果及人工复核标签时**才能作为该版本已观察结果的指标证据；还须另行确认这些历史结果使用的 Provider/模型和渠道范围，不能从分类器版本推断模型身份。离线模式不产生新的 SaaS 额度消耗。

需要重新调用模型时，仅在获授权的**非计量测试环境**通过受控本地环境设置 `TGO_INTENT_EVAL_INTERNAL_KEY`。不要把令牌写入脚本、仓库或聊天记录。SaaS 计量环境还要求每条请求有有效的额度预约和租约，并完成结算；当前 `--run` 不具备这套流程，不能用于计量项目的正式评测。

```powershell
$projectId = "已批准的测试项目 UUID"
$providerId = "该项目的 Provider UUID"
$modelName = "已批准的模型标识"
& .\.venv\Scripts\python.exe -m app.evaluation.intent_g3 `
  --input E:\智能客服系统\.tmp\intent-g3-cases.jsonl `
  --run `
  --base-url http://127.0.0.1:8081 `
  --project-id $projectId `
  --provider-id $providerId `
  --model $modelName
```

远程服务地址必须使用 HTTPS；仅本机回环地址允许 HTTP。运行前检查样本不少于 200 条、12 类都有样本且有敏感样本；任一不满足即退出，不创建模型请求。实际调用每条只执行一次；任一 HTTP、解析或鉴权错误都停止整次评测，不把部分预测当作通过，也不打印响应正文。退出码 `0` 表示本次指标全部达标，`1` 表示完成运行但指标未达标，`2` 表示输入、配置或请求失败。

## 3. 指标及验收边界

| 指标 | 计算口径 | 原计划目标 |
|---|---|---:|
| 高置信度意图精确率 | `confidence ≥ 0.85` 的预测中，意图等于人工标签的比例 | ≥ 95% |
| 整体意图 Macro-F1 | 固定 12 类的 F1 等权平均；没有样本支持的类别不能过闸 | ≥ 0.85 |
| 敏感意图转人工 | 人工标记需转人工，或标签为投诉/主动转人工的样本，同时满足 `need_human=true` 与 `recommended_route=human_handoff` 的比例 | 100% |

报告包含样本总数、三项汇总指标、各类别支持数/精确率/召回率/F1、未达标问题代码及数据集哈希。实时模式报告模型标识；离线模式报告预测文件哈希及分类器/策略版本。报告不含客户文本、密钥或 Provider 响应。验收时另记录代码版本、测试项目、Provider、模型、渠道范围、运行或观察时间及业务标签复核人；不要把含客户文本的数据集放入 Git。G3 还包括 ASR/OCR/VLM 的真实 Provider、媒体处理失败兜底及语音/图片 P95；这份意图评测报告无法代替那些验收。
