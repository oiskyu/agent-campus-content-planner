"""四个工具的标准定义：name、description、parameters、returns。执行在 tools.dispatch。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PlanId = Literal["standard", "lite"]
ToolCode = Literal["search_materials", "generate_script", "estimate_cost", "save_plan"]


class SearchMaterialsInput(BaseModel):
    theme: str = Field(description="活动主题，必须与素材表中的 theme 完全一致")


class GenerateScriptInput(BaseModel):
    material_ids: list[str] = Field(description="本次 search_materials 已返回的素材 ID")
    plan_id: PlanId = Field(description="standard 为标准方案，lite 为轻量方案")


class EstimateCostInput(BaseModel):
    plan_id: PlanId = Field(description="要估价的方案，standard 或 lite。本工具不比较预算")


class SavePlanInput(BaseModel):
    theme: str = Field(description="活动主题，与用户输入一致")
    plan_id: PlanId = Field(description="已经成功生成脚本并成功估价的方案")
    budget: float = Field(description="用户给出的预算，单位是模拟单位")
    deadline_hours: float = Field(description="距离截止时间剩余的小时数")


class MaterialRecord(BaseModel):
    id: str = Field(description="素材 ID")
    facts: list[str] = Field(description="可写入脚本的事实，不能增删改")
    platform: str = Field(description="适用平台")
    hours: float = Field(description="素材制作耗时，小时")


class SearchMaterialsData(BaseModel):
    materials: list[MaterialRecord]


class GenerateScriptData(BaseModel):
    plan_id: PlanId
    material_ids: list[str]
    script: str = Field(description="只由已检索事实拼成的脚本")


class CostItems(BaseModel):
    素材使用: int
    脚本: int
    剪辑: int


class EstimateCostData(BaseModel):
    plan_id: PlanId
    items: CostItems
    total: int = Field(description="分项之和，不含是否超预算的判断")
    hours: int = Field(description="方案耗时，小时")


class SavedPlanData(BaseModel):
    draft_id: str
    plan_id: PlanId
    theme: str
    material_ids: list[str]
    script: str
    items: CostItems
    total: int
    hours: int


class ToolMeta(BaseModel):
    tool: str
    call_id: str = ""
    latency_ms: int = 0
    call_count: int = 0
    max_calls: int = 0
    timeout_ms: int = 0
    permission: str = "read"
    hint: str = Field(description="给模型的下一步修正提示")


class ToolError(BaseModel):
    code: str
    message: str
    hint: str = ""


class ToolResultModel(BaseModel):
    ok: bool
    code: str = Field(description="工具名")
    message: str
    data: SearchMaterialsData | GenerateScriptData | EstimateCostData | SavedPlanData | dict[str, Any] | None = None
    error: ToolError | None = None
    meta: ToolMeta | None = None


def json_parameters(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }


def tool_payload(
    ok: bool,
    code: str,
    message: str,
    data: BaseModel | dict[str, Any] | None = None,
    meta: ToolMeta | dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta_dump = meta.model_dump() if isinstance(meta, ToolMeta) else meta
    payload: dict[str, Any] = {
        "ok": ok,
        "code": code,
        "message": message,
        "data": None if data is None else (data.model_dump() if isinstance(data, BaseModel) else data),
        "error": None,
        "meta": meta_dump,
    }
    if not ok:
        hint = ""
        if isinstance(meta, ToolMeta):
            hint = meta.hint
        elif isinstance(meta, dict):
            hint = str(meta.get("hint") or "")
        payload["error"] = {"code": code, "message": message, "hint": hint}
    return payload
