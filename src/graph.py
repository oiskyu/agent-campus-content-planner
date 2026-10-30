"""LangGraph 循环：模型选工具，路由只判断有没有工具调用以及步数是否用尽。"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.catalog import load_rules
from src.output import build_output, build_trace, summarize_result
from src.prompts import SYSTEM_PROMPT
from src.tools import dispatch


class AgentState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    steps: int
    max_steps: int
    theme: str
    budget: float | None
    deadline_hours: float | None
    fail_tools: list[str]
    fail_consumed: list[str]
    tool_call_counts: dict[str, int]
    retrieved: dict[str, Any]
    scripts: dict[str, Any]
    estimates: dict[str, Any]
    step_records: Annotated[list[dict[str, Any]], operator.add]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    saved_plan: dict[str, Any] | None
    drafts_path: str
    status: str
    incomplete_reason: str | None
    gap: float | None
    total_cost: float | None
    task_plan: dict[str, Any]
    output: dict[str, Any]
    trace: dict[str, Any]


def route_after_agent(state: AgentState) -> str:
    message = state["messages"][-1]
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls and state.get("steps", 0) < state.get("max_steps", 0):
        return "tools"
    return "finalize"


def route_after_tools(state: AgentState) -> str:
    if state.get("steps", 0) >= state.get("max_steps", 0):
        return "finalize"
    return "agent"


def _make_agent(model: Any):
    def agent(state: AgentState) -> dict[str, Any]:
        from langchain_core.messages import SystemMessage

        response = model.invoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    return agent


def run_tools(state: AgentState) -> dict[str, Any]:
    message = state["messages"][-1]
    working: dict[str, Any] = dict(state)
    records: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    tool_messages: list[ToolMessage] = []
    decision = str(getattr(message, "content", "") or "")
    for offset, call in enumerate(message.tool_calls, start=1):
        payload, delta = dispatch(call["name"], dict(call["args"]), working)
        working.update(delta)
        payloads.append(payload)
        records.append(
            {
                "step": state.get("steps", 0) + offset,
                "tool": call["name"],
                "arguments": dict(call["args"]),
                "status": "success" if payload.get("ok") else "failed",
                "result_summary": summarize_result(payload),
                "decision": decision,
            }
        )
        tool_messages.append(
            ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call["id"])
        )
    return {
        "messages": tool_messages,
        "steps": state.get("steps", 0) + len(message.tool_calls),
        "step_records": records,
        "tool_results": payloads,
        "fail_consumed": list(working.get("fail_consumed") or []),
        "tool_call_counts": dict(working.get("tool_call_counts") or {}),
        "retrieved": dict(working.get("retrieved") or {}),
        "scripts": dict(working.get("scripts") or {}),
        "estimates": dict(working.get("estimates") or {}),
        "saved_plan": working.get("saved_plan") if working.get("saved_plan") is not None else state.get("saved_plan"),
        "drafts_path": working["drafts_path"],
    }


def finalize(state: AgentState) -> dict[str, Any]:
    output = build_output(state)
    traced_state = dict(state)
    traced_state["status"] = output["status"]
    traced_state["incomplete_reason"] = output["incomplete_reason"]
    traced_state["gap"] = output["gap"]
    traced_state["total_cost"] = output["total_cost"]
    traced_state["task_plan"] = output["task_plan"]
    traced_state["saved_plan"] = output["saved_plan"]
    traced_state["output"] = output
    trace = build_trace(traced_state)
    return {
        "status": output["status"],
        "incomplete_reason": output["incomplete_reason"],
        "gap": output["gap"],
        "total_cost": output["total_cost"],
        "task_plan": output["task_plan"],
        "saved_plan": output["saved_plan"],
        "output": output,
        "trace": trace,
    }


def build_graph(model: Any, checkpointer: Any):
    if hasattr(model, "bind_tools"):
        from src.tools import TOOL_SCHEMAS

        model = model.bind_tools(TOOL_SCHEMAS)
    builder = StateGraph(AgentState)
    builder.add_node("agent", _make_agent(model))
    builder.add_node("tools", run_tools)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "finalize": "finalize"})
    builder.add_conditional_edges("tools", route_after_tools, {"agent": "agent", "finalize": "finalize"})
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _max_steps(request: dict[str, Any]) -> int:
    if request.get("case_name") == "max-steps":
        return 2
    if request.get("max_steps") is not None:
        return int(request["max_steps"])
    return int(load_rules().get("max_tool_calls") or 8)


def _fail_tools(request: dict[str, Any]) -> list[str]:
    names = list(request.get("fail_tools") or [])
    inject = bool((load_rules().get("failure_inject") or {}).get("estimate_cost_first_fail"))
    if request.get("case_name") == "tool-failure" and inject and "estimate_cost" not in names:
        names.append("estimate_cost")
    return names


def initial_state(request: dict[str, Any], drafts_path: str) -> AgentState:
    from langchain_core.messages import HumanMessage

    from src.prompts import format_request

    return {
        "messages": [HumanMessage(content=format_request(request))],
        "steps": 0,
        "max_steps": _max_steps(request),
        "theme": request["theme"],
        "budget": request.get("budget"),
        "deadline_hours": request.get("deadline_hours"),
        "fail_tools": _fail_tools(request),
        "fail_consumed": [],
        "tool_call_counts": {},
        "retrieved": {},
        "scripts": {},
        "estimates": {},
        "step_records": [],
        "tool_results": [],
        "saved_plan": None,
        "drafts_path": drafts_path,
        "status": "running",
        "incomplete_reason": None,
        "gap": None,
        "total_cost": None,
        "task_plan": {},
        "output": {},
        "trace": {"steps": []},
    }
