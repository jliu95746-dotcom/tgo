"""Only curated errors may appear in persisted workflow node receipts."""


class WorkflowAIError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(self.public_message)

    @property
    def public_message(self) -> str:
        if self.status_code == 402:
            return "企业套餐或 AI 额度不足，请由管理员检查。"
        if self.status_code in {401, 403, 404}:
            return "员工已停用、权限已变更或运行授权失效，请检查员工和企业配置。"
        if self.status_code == 409:
            return "原 AI 调用已提交或结果待核验，请查看原运行记录，避免重复生成。"
        if self.status_code == 429:
            return "AI 同时运行数量已达上限，请稍后再试。"
        if self.status_code in {400, 422}:
            return "AI 员工节点输入或固定策略不完整，请补齐配置。"
        return "AI 调用结果暂无法确认，请查看原运行记录，避免重复执行。"
