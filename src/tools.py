"""标准 Agent 工具：名称、描述、JSON Schema、校验、超时、权限、统一返回。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from src.catalog import dump_json, load_json, load_materials, load_rules, quote_plan
from src.schemas import (
    CostItems,
    EstimateCostData,
    EstimateCostInput,
    GenerateScriptData,
    GenerateScriptInput,
    MaterialRecord,
    SavePlanInput,
    SavedPlanData,
    SearchMaterialsData,
    SearchMaterialsInput,
    ToolMeta,
    json_parameters,
    tool_payload,
)

logger = logging.getLogger(__name__)
ToolResult = tuple[dict[str, Any], dict[str, Any]]
FORBIDDEN_TOOLS = {"publish", "release", "deploy"}

HINTS = {
    "search_materials": {
        "ok": "检索成功后，把返回的 material_ids 传给 generate_script。",
        "empty": "未找到素材。请核对 theme 是否与素材表完全一致，不要编造素材。",
        "invalid": "search_materials 需要字符串参数 theme，且必须与素材表中的主题一致。",
    },
    "generate_script": {
        "ok": "脚本已生成。下一步对同一个 plan_id 调用 estimate_cost。",
        "missing_material": "请先调用 search_materials，再把返回的素材 ID 原样传入 material_ids。",
        "invalid": "generate_script 需要 material_ids（字符串数组）和 plan_id（standard 或 lite）。",
    },
    "estimate_cost": {
        "ok": "请比较 data.total 与预算、data.hours 与截止时间，再决定保存或改选 lite。",
        "unavailable": "估价暂时失败。可对同一 plan_id 再调用一次 estimate_cost，不要把本步记为成功。",
        "invalid": "estimate_cost 只接受 plan_id，取值 standard 或 lite。本工具不判断预算。",
    },
    "save_plan": {
        "ok": "草稿已保存。停止调用工具，不要声称已发布。",
        "duplicate": "返回的是已有草稿。不要再次保存，不要新建第二份。",
        "missing_budget": "用户未提供预算。不要编造预算，停止保存。",
        "missing_deadline": "用户未提供截止时间。不要编造截止时间，停止保存。",
        "need_script": "请先对该 plan_id 调用 generate_script。",
        "need_estimate": "请先对该 plan_id 调用 estimate_cost，且必须成功。",
        "over_budget": "不要保存超预算方案。可改选 lite 重新生成脚本并估价，或停止并说明缺口。",
        "over_deadline": "方案耗时超过截止时间，不要保存。可改选耗时更短的 lite，或停止。",
        "denied": "save_plan 只能写草稿，没有发布权限。",
        "need_confirm": "该工具需要人工确认。等待确认后再调用，不要自行发布。",
        "invalid": "save_plan 需要 theme、plan_id、budget、deadline_hours；budget 与 deadline_hours 必须是数字。",
    },
}


def canonical_number(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def draft_key(theme: str, plan_id: str, budget: float) -> str:
    return f"{theme}|{plan_id}|{canonical_number(budget)}"


def load_drafts(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    payload = load_json(file_path)
    return payload if isinstance(payload, dict) else {}


def _policy(name: str) -> dict[str, Any]:
    tools = load_rules().get("tools") or {}
    defaults = {"permission": "read", "timeout_ms": 2000, "max_calls": 4, "require_confirmation": False}
    merged = dict(defaults)
    merged.update(tools.get(name) or {})
    return merged


def _meta(name: str, call_count: int, latency_ms: int, hint: str, call_id: str = "") -> ToolMeta:
    policy = _policy(name) if name in {"search_materials", "generate_script", "estimate_cost", "save_plan"} else {
        "permission": "none",
        "timeout_ms": 0,
        "max_calls": 0,
        "require_confirmation": False,
    }
    return ToolMeta(
        tool=name,
        call_id=call_id,
        latency_ms=latency_ms,
        call_count=call_count,
        max_calls=int(policy["max_calls"]),
        timeout_ms=int(policy["timeout_ms"]),
        permission=str(policy["permission"]),
        hint=hint,
    )


def _fail(
    code: str,
    message: str,
    hint: str,
    state: dict[str, Any],
    latency_ms: int = 0,
    call_id: str = "",
) -> ToolResult:
    counts = dict(state.get("tool_call_counts") or {})
    count = int(counts.get(code, 0))
    meta_name = code if code in {"search_materials", "generate_script", "estimate_cost", "save_plan"} else code
    return tool_payload(False, code, message, meta=_meta(meta_name, count, latency_ms, hint, call_id)), {}


def _ok_payload(code: str, message: str, data: BaseModel, hint: str, state: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    counts = dict(state.get("tool_call_counts") or {})
    count = int(counts.get(code, 0))
    return tool_payload(True, code, message, data, _meta(code, count, latency_ms, hint))


def search_materials(theme: str, state: dict[str, Any]) -> ToolResult:
    matches = [item for item in load_materials() if item["theme"] == theme]
    if not matches:
        return _fail("search_materials", "未找到该主题的素材", HINTS["search_materials"]["empty"], state)
    retrieved = dict(state.get("retrieved") or {})
    materials = []
    for item in matches:
        retrieved[item["id"]] = item
        materials.append(
            MaterialRecord(
                id=item["id"],
                facts=list(item["facts"]),
                platform=item["platform"],
                hours=item["hours"],
            )
        )
    payload = _ok_payload(
        "search_materials",
        "检索完成",
        SearchMaterialsData(materials=materials),
        HINTS["search_materials"]["ok"],
        state,
        0,
    )
    return payload, {"retrieved": retrieved}


def generate_script(material_ids: list[str], plan_id: str, state: dict[str, Any]) -> ToolResult:
    if quote_plan(plan_id) is None:
        return _fail("generate_script", f"未知方案 {plan_id}", HINTS["generate_script"]["invalid"], state)
    retrieved = state.get("retrieved") or {}
    missing = [material_id for material_id in material_ids if material_id not in retrieved]
    if missing:
        return _fail(
            "generate_script",
            f"素材 {missing[0]} 尚未检索",
            HINTS["generate_script"]["missing_material"],
            state,
        )
    facts: list[str] = []
    for material_id in material_ids:
        facts.extend(retrieved[material_id]["facts"])
    if plan_id == "lite":
        facts = facts[:2]
    script = "。".join(facts) + "。"
    scripts = dict(state.get("scripts") or {})
    scripts[plan_id] = {"material_ids": list(material_ids), "facts": facts, "script": script}
    payload = _ok_payload(
        "generate_script",
        "脚本已生成",
        GenerateScriptData(plan_id=plan_id, material_ids=list(material_ids), script=script),
        HINTS["generate_script"]["ok"],
        state,
        0,
    )
    return payload, {"scripts": scripts}


def estimate_cost(plan_id: str, state: dict[str, Any]) -> ToolResult:
    quote = quote_plan(plan_id)
    if quote is None:
        return _fail("estimate_cost", f"未知方案 {plan_id}", HINTS["estimate_cost"]["invalid"], state)
    fail_tools = list(state.get("fail_tools") or [])
    consumed = list(state.get("fail_consumed") or [])
    inject_first_fail = bool((load_rules().get("failure_inject") or {}).get("estimate_cost_first_fail"))
    if inject_first_fail and "estimate_cost" in fail_tools and "estimate_cost" not in consumed:
        consumed.append("estimate_cost")
        payload, _ = _fail(
            "estimate_cost",
            "成本估算服务暂时不可用",
            HINTS["estimate_cost"]["unavailable"],
            state,
        )
        return payload, {"fail_consumed": consumed}
    estimates = dict(state.get("estimates") or {})
    items = CostItems.model_validate(quote["items"])
    estimates[plan_id] = {"items": items.model_dump(), "total": quote["total"], "hours": quote["hours"]}
    payload = _ok_payload(
        "estimate_cost",
        "估价完成",
        EstimateCostData(plan_id=plan_id, items=items, total=quote["total"], hours=quote["hours"]),
        HINTS["estimate_cost"]["ok"],
        state,
        0,
    )
    return payload, {"estimates": estimates}


def save_plan(
    theme: str,
    plan_id: str,
    budget: float | None,
    deadline_hours: float | None,
    state: dict[str, Any],
) -> ToolResult:
    state_budget = state.get("budget")
    state_deadline = state.get("deadline_hours")
    if state_budget is None or budget is None:
        return _fail("save_plan", "无法保存：缺少预算", HINTS["save_plan"]["missing_budget"], state)
    if state_deadline is None or deadline_hours is None:
        return _fail("save_plan", "无法保存：缺少截止时间", HINTS["save_plan"]["missing_deadline"], state)
    scripts = state.get("scripts") or {}
    if plan_id not in scripts:
        return _fail("save_plan", "无法保存：该方案尚未生成脚本", HINTS["save_plan"]["need_script"], state)
    estimates = state.get("estimates") or {}
    if plan_id not in estimates:
        return _fail("save_plan", "无法保存：该方案尚未成功估价", HINTS["save_plan"]["need_estimate"], state)
    quote = estimates[plan_id]
    limit_budget = min(float(budget), float(state_budget))
    limit_deadline = min(float(deadline_hours), float(state_deadline))
    total = quote["total"]
    hours = quote["hours"]
    if total > limit_budget:
        gap = total - limit_budget
        return _fail(
            "save_plan",
            f"无法保存：费用 {canonical_number(total)} 超过预算 {canonical_number(limit_budget)}，缺口 {canonical_number(gap)}",
            HINTS["save_plan"]["over_budget"],
            state,
        )
    if hours > limit_deadline:
        return _fail(
            "save_plan",
            f"无法保存：耗时 {hours} 小时超过截止时间 {canonical_number(limit_deadline)} 小时",
            HINTS["save_plan"]["over_deadline"],
            state,
        )

    key = draft_key(state.get("theme") or theme, plan_id, float(state_budget))
    drafts_path = Path(state["drafts_path"])
    drafts = load_drafts(str(drafts_path))
    if key in drafts:
        existing = SavedPlanData.model_validate(drafts[key])
        payload = _ok_payload("save_plan", "草稿已存在", existing, HINTS["save_plan"]["duplicate"], state, 0)
        return payload, {"saved_plan": existing.model_dump()}
    script_info = scripts[plan_id]
    draft_model = SavedPlanData(
        draft_id=f"draft-{len(drafts) + 1:03d}",
        plan_id=plan_id,
        theme=state.get("theme") or theme,
        material_ids=list(script_info["material_ids"]),
        script=script_info["script"],
        items=CostItems.model_validate(quote["items"]),
        total=total,
        hours=hours,
    )
    draft = draft_model.model_dump()
    drafts[key] = draft
    dump_json(drafts_path, drafts)
    payload = _ok_payload("save_plan", "方案已保存为草稿", draft_model, HINTS["save_plan"]["ok"], state, 0)
    return payload, {"saved_plan": draft}


@dataclass
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., ToolResult]
    permission: str
    timeout_ms: int
    max_calls: int
    require_confirmation: bool

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": json_parameters(self.input_model),
        }

    def invoke(self, arguments: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        call_id = uuid4().hex[:8]
        counts = dict(state.get("tool_call_counts") or {})
        used = int(counts.get(self.name, 0)) + 1
        counts[self.name] = used
        count_delta = {"tool_call_counts": counts}

        if self.name in FORBIDDEN_TOOLS or self.permission == "publish":
            logger.warning("tool_denied name=%s call_id=%s", self.name, call_id)
            payload, _ = _fail(
                self.name,
                "没有发布权限，只能保存草稿",
                HINTS["save_plan"]["denied"],
                {**state, **count_delta},
                call_id=call_id,
            )
            return payload, count_delta
        if self.require_confirmation and not state.get("confirmed"):
            payload, _ = _fail(
                self.name,
                "需要人工确认后才能调用该工具",
                HINTS["save_plan"]["need_confirm"],
                {**state, **count_delta},
                call_id=call_id,
            )
            return payload, count_delta
        if used > self.max_calls:
            payload, _ = _fail(
                self.name,
                f"{self.name} 已达到调用上限 {self.max_calls} 次，请根据已有结果结束或改用其他工具",
                "不要重复调用该工具，根据已有观察结束或改选其他工具。",
                {**state, **count_delta},
                call_id=call_id,
            )
            return payload, count_delta

        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            fields = "、".join(str(item["loc"][0]) for item in exc.errors())
            hint = HINTS.get(self.name, {}).get("invalid", "请按工具 parameters 补全字段。")
            payload, _ = _fail(
                self.name,
                f"参数缺少或类型不符：{fields}",
                hint,
                {**state, **count_delta},
                call_id=call_id,
            )
            logger.info("tool_invalid name=%s call_id=%s fields=%s", self.name, call_id, fields)
            return payload, count_delta

        def _run() -> ToolResult:
            if self.name == "search_materials":
                return self.handler(parsed.theme, state)
            if self.name == "generate_script":
                return self.handler(parsed.material_ids, parsed.plan_id, state)
            if self.name == "estimate_cost":
                return self.handler(parsed.plan_id, state)
            return self.handler(parsed.theme, parsed.plan_id, parsed.budget, parsed.deadline_hours, state)

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                payload, delta = pool.submit(_run).result(timeout=self.timeout_ms / 1000)
        except FutureTimeout:
            logger.error("tool_timeout name=%s call_id=%s timeout_ms=%s", self.name, call_id, self.timeout_ms)
            fail, _ = _fail(
                self.name,
                f"{self.name} 超过 {self.timeout_ms} 毫秒未返回，请稍后重试同一步，不要当作成功",
                "本步失败。可重试一次，已成功步骤保留。",
                {**state, **count_delta},
                call_id=call_id,
            )
            return fail, count_delta
        except Exception as exc:
            logger.exception("tool_error name=%s call_id=%s", self.name, call_id)
            fail, _ = _fail(
                self.name,
                f"{self.name} 执行异常：{exc}。请检查参数后重试一次，不要把本步记为成功",
                "本步失败。修正参数后可重试一次。",
                {**state, **count_delta},
                call_id=call_id,
            )
            return fail, count_delta

        latency_ms = int((time.perf_counter() - started) * 1000)
        if payload.get("meta"):
            payload["meta"]["latency_ms"] = latency_ms
            payload["meta"]["call_count"] = used
            payload["meta"]["call_id"] = call_id
        logger.info(
            "tool_done name=%s call_id=%s ok=%s latency_ms=%s",
            self.name,
            call_id,
            payload.get("ok"),
            latency_ms,
        )
        merged = dict(count_delta)
        merged.update(delta)
        return payload, merged


def _build_tools() -> dict[str, AgentTool]:
    specs = [
        ("search_materials", "按主题检索合成素材，返回素材 ID、可引用事实、平台和制作耗时。", SearchMaterialsInput, search_materials),
        ("generate_script", "用已检索到的素材 ID 生成指定方案的脚本。方案编号只能是 standard 或 lite。", GenerateScriptInput, generate_script),
        ("estimate_cost", "按价目表返回方案的分项费用、合计和耗时。不判断是否超过预算。", EstimateCostInput, estimate_cost),
        ("save_plan", "在该方案的脚本和估价都已成功后保存草稿。相同主题、方案和预算再次保存时返回已有草稿。没有发布权限。", SavePlanInput, save_plan),
    ]
    registry: dict[str, AgentTool] = {}
    for name, description, model, handler in specs:
        policy = _policy(name)
        registry[name] = AgentTool(
            name=name,
            description=description,
            input_model=model,
            handler=handler,
            permission=str(policy["permission"]),
            timeout_ms=int(policy["timeout_ms"]),
            max_calls=int(policy["max_calls"]),
            require_confirmation=bool(policy["require_confirmation"]),
        )
    return registry


TOOLS = _build_tools()
TOOL_DEFINITIONS = [tool.definition() for tool in TOOLS.values()]
TOOL_SCHEMAS = [{"type": "function", "function": spec} for spec in TOOL_DEFINITIONS]


def dispatch(name: str, arguments: dict[str, Any], state: dict[str, Any]) -> ToolResult:
    if name in FORBIDDEN_TOOLS:
        payload, _ = _fail(name, "没有发布权限，只能保存草稿", HINTS["save_plan"]["denied"], state)
        return payload, {}
    tool = TOOLS.get(name)
    if tool is None:
        payload, _ = _fail(
            name or "unknown",
            f"未知工具 {name}。可用工具：search_materials、generate_script、estimate_cost、save_plan",
            "只调用已绑定的四个工具，不要调用发布类工具。",
            state,
        )
        return payload, {}
    return tool.invoke(arguments, state)
