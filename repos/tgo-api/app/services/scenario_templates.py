"""Setup templates describe requirements and test questions."""

from app.core.exceptions import ConflictError, NotFoundError
from app.schemas.scenario_drafts import (
    ScenarioBusiness,
    ScenarioModule,
    ScenarioTemplate,
)
from app.schemas.scenario_readiness import ScenarioId
from app.schemas.scenario_resources import ResourceKind

_definitions: list[
    tuple[ScenarioId, str, str, list[ResourceKind], list[str], list[str]]
] = [
    (
        "knowledge",
        "商品咨询客服",
        "商品咨询",
        ["model", "knowledge"],
        ["governed_knowledge_search"],
        [
            "这款包是什么材质？",
            "这款包能放平板吗？",
            "资料里没有的库存情况能确认吗？",
        ],
    ),
    (
        "logistics",
        "订单与物流客服",
        "订单物流查询",
        ["model", "connection"],
        ["scoped_order_query", "scoped_logistics_query"],
        [
            "我的订单发货了吗？",
            "物流查询失败时怎么办？",
            "能查询其他客户的订单吗？",
        ],
    ),
    (
        "aftersales",
        "售后客服",
        "售后政策咨询",
        ["model", "knowledge"],
        ["governed_knowledge_search"],
        [
            "收到商品有问题怎么退换？",
            "申请售后需要哪些材料？",
            "政策无法确认时怎么处理？",
        ],
    ),
    (
        "assistant",
        "业务助理",
        "业务资料整理",
        ["model"],
        ["business_data_access"],
        [
            "整理本周常见咨询。",
            "汇总已有售后资料。",
            "没有接入的数据可以确认吗？",
        ],
    ),
    (
        "files",
        "读取业务文件",
        "业务文件读取",
        ["device"],
        ["fs_read"],
        [
            "设备上是否有文件读取工具？",
            "所选文件是否在授权目录？",
            "设备断线后是否停止？",
        ],
    ),
    (
        "report",
        "运行报表脚本",
        "受控脚本执行",
        ["device"],
        ["shell_exec"],
        [
            "脚本和参数是否经过审核？",
            "执行结果是否符合预期？",
            "权限未生效时是否拒绝执行？",
        ],
    ),
    (
        "screen",
        "操作业务软件界面",
        "受控屏幕操作",
        ["device"],
        ["screenshot", "click", "type_text"],
        [
            "是否绑定正确窗口？",
            "锁屏或窗口变化时是否停止？",
            "用户停止后是否还会点击？",
        ],
    ),
]

_templates: dict[tuple[ScenarioModule, ScenarioId, int], ScenarioTemplate] = {}
for (
    template_id,
    title,
    profession,
    resources,
    capabilities,
    questions,
) in _definitions:
    modules: tuple[ScenarioModule, ...]
    if template_id in {"files", "report", "screen"}:
        modules = ("devices",)
    else:
        modules = ("agents", "workflows")
    for module in modules:
        if module == "workflows" and template_id == "assistant":
            continue
        _templates[(module, template_id, 1)] = ScenarioTemplate(
            id=template_id,
            module=module,
            version=1,
            title=title,
            available=(template_id != "screen" and not (
                module == "workflows" and template_id == "logistics"
            )),
            default_model_mode="not_required"
            if module == "devices"
            else "system_default",
            required_resources=resources,
            required_capabilities=capabilities,
            business_defaults=ScenarioBusiness(
                name=title,
                profession=profession,
                instructions=(
                    "根据已审核且适用于当前渠道的企业资料或成功查询结果回答。"
                    "资料不足或查询失败时说明无法确认，并引导人工处理。"
                    "涉及提交业务数据的操作须经过授权和人工确认。"
                ),
            ),
            test_questions=questions,
            recommended_workflows=[template_id]
            if module == "agents" and template_id != "assistant"
            else [],
        )


def list_templates(
    module: ScenarioModule | None = None,
) -> list[ScenarioTemplate]:
    return [
        template.model_copy(deep=True)
        for template in _templates.values()
        if module is None or template.module == module
    ]


def get_template(
    module: ScenarioModule, template_id: ScenarioId, version: int
) -> ScenarioTemplate:
    template = _templates.get((module, template_id, version))
    if template is None:
        raise NotFoundError("场景模板")
    if not template.available:
        raise ConflictError("此模板所需的业务能力尚未开放。")
    return template.model_copy(deep=True)
