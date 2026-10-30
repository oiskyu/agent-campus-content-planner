"""把图状态整理成固定的 output.json 和 trace.json。"""

from __future__ import annotations

from typing import Any

TOOL_LABELS = {
    "search_materials": "检索素材",
    "generate_script": "生成脚本",
    "estimate_cost": "估价",
    "save_plan": "保存草稿",
}


def summarize_result(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return str(payload.get("message", "工具失败"))
    data = payload.get("data") or {}
    if "materials" in data:
        ids = "、".join(item["id"] for item in data["materials"])
        return f"找到 {ids}"
    if "total" in data:
        return f"合计 {data['total']}"
    if "draft_id" in data:
        return f"草稿 {data['draft_id']}"
    if "script" in data:
        return "脚本已生成"
    return str(payload.get("message", "完成"))


def _successful_estimates(state: dict[str, Any]) -> list[dict[str, Any]]:
    estimates = state.get("estimates") or {}
    return [item for item in estimates.values() if "total" in item]


def infer_incomplete(state: dict[str, Any]) -> tuple[str, float | None]:
    if state.get("steps", 0) >= state.get("max_steps", 0) and not state.get("saved_plan"):
        return "max_steps", None
    if state.get("budget") is None:
        return "missing_budget", None
    if state.get("deadline_hours") is None:
        return "missing_deadline", None
    totals = [item["total"] for item in _successful_estimates(state)]
    estimates = state.get("estimates") or {}
    budget = float(state["budget"])
    if "standard" in estimates and "lite" in estimates and totals and min(totals) > budget:
        return "no_feasible_plan", min(totals) - budget
    if any(record["status"] == "failed" for record in state.get("step_records") or []):
        return "tool_failed", None
    if totals and min(totals) > budget:
        return "no_feasible_plan", min(totals) - budget
    return "tool_failed", None


def build_output(state: dict[str, Any]) -> dict[str, Any]:
    saved = state.get("saved_plan")
    records = list(state.get("step_records") or [])
    retrieved = state.get("retrieved") or {}
    estimates = _successful_estimates(state)
    scripts = state.get("scripts") or {}
    if saved:
        status = "completed"
        reason = None
        gap = None
        total_cost = saved["total"]
        selected = saved["plan_id"]
        script = saved["script"]
        material_ids = list(saved["material_ids"])
    else:
        status = "incomplete"
        reason, gap = infer_incomplete(state)
        total_cost = min(item["total"] for item in estimates) if estimates else None
        selected = None
        latest_script = next(reversed(scripts.values()), None)
        script = None if latest_script is None else latest_script["script"]
        material_ids = list(retrieved.keys())
    return {
        "status": status,
        "task_plan": {
            "theme": state.get("theme"),
            "budget": state.get("budget"),
            "deadline_hours": state.get("deadline_hours"),
            "selected_plan_id": selected,
            "material_ids": material_ids,
            "script": script,
            "plan_steps": [TOOL_LABELS.get(record["tool"], record["tool"]) for record in records],
        },
        "step_records": records,
        "tool_results": list(state.get("tool_results") or []),
        "total_cost": total_cost,
        "saved_plan": saved,
        "incomplete_reason": reason,
        "gap": gap,
    }


def build_trace(state: dict[str, Any]) -> dict[str, Any]:
    records = list(state.get("step_records") or [])
    decisions = [record.get("decision", "") for record in records]
    messages = state.get("messages") or []
    final_decision = ""
    for message in reversed(messages):
        if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
            final_decision = str(message.content)
            break
    reached_limit = state.get("steps", 0) >= state.get("max_steps", 0) and not state.get("saved_plan")
    steps = []
    for index, record in enumerate(records):
        has_next = index + 1 < len(records)
        if has_next:
            next_action = records[index + 1]["tool"]
            decision = decisions[index + 1]
        elif reached_limit and state.get("status") != "completed":
            next_action = "finish"
            decision = "工具调用已达到上限，停止执行。"
        else:
            next_action = "finish"
            decision = final_decision or "模型结束。"
        steps.append(
            {
                "step": record["step"],
                "tool": record["tool"],
                "arguments": record["arguments"],
                "result_summary": record["result_summary"],
                "next_action": next_action,
                "decision": decision,
            }
        )
    return {"steps": steps}
