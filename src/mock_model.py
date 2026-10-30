"""离线模型。只按对话里已经出现的工具结果选择下一步，供无密钥时验证流程。"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage


def _call(name: str, arguments: dict[str, Any], decision: str) -> AIMessage:
    return AIMessage(
        content=decision,
        tool_calls=[
            {
                "name": name,
                "args": arguments,
                "id": f"call_{uuid4().hex[:8]}",
                "type": "tool_call",
            }
        ],
    )


def _finish(decision: str) -> AIMessage:
    return AIMessage(content=decision)


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    value = text.strip()
    if value == "未提供":
        return None
    return float(value)


def _field(user_text: str, label: str) -> str | None:
    matched = re.search(rf"{re.escape(label)}：(.+)", user_text)
    if matched is None:
        return None
    return matched.group(1).strip()


class MockChatModel:
    """流程替身。它的选择不能当作真实模型的决策证据。"""

    def bind_tools(self, tools: list[Any]) -> MockChatModel:
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        user_text = ""
        observations: list[tuple[dict[str, Any], dict[str, Any]]] = []
        continued = False
        pending: dict[str, Any] | None = None
        for message in messages:
            role = getattr(message, "type", "")
            content = str(message.content)
            if role == "human":
                if "继续完成失败步骤" in content:
                    continued = True
                elif not user_text:
                    user_text = content
            elif role == "ai" and getattr(message, "tool_calls", None):
                pending = message.tool_calls[0]
            elif role == "tool" and pending is not None:
                observations.append((pending, json.loads(str(message.content))))
                pending = None

        budget = _number(_field(user_text, "预算"))
        deadline = _number(_field(user_text, "截止时间（小时）"))
        theme = _field(user_text, "主题") or ""
        return self._decide(theme, budget, deadline, observations, continued)

    def _decide(
        self,
        theme: str,
        budget: float | None,
        deadline: float | None,
        observations: list[tuple[dict[str, Any], dict[str, Any]]],
        continued: bool,
    ) -> AIMessage:
        search_result: dict[str, Any] | None = None
        scripts: set[str] = set()
        estimates: dict[str, dict[str, Any]] = {}
        saved = False
        for call, payload in observations:
            name = call["name"]
            if name == "search_materials" and payload.get("ok"):
                search_result = payload
            elif name == "generate_script" and payload.get("ok"):
                scripts.add(call["args"]["plan_id"])
            elif name == "estimate_cost":
                estimates[call["args"]["plan_id"]] = payload
            elif name == "save_plan" and payload.get("ok"):
                saved = True

        if saved:
            return _finish("草稿已保存，结束。")
        if search_result is None:
            return _call("search_materials", {"theme": theme}, "先检索该主题的素材。")
        if budget is None:
            return _finish("预算未提供，停止保存。")
        if deadline is None:
            return _finish("截止时间未提供，停止保存。")

        material_ids = [item["id"] for item in search_result["data"]["materials"]]
        if "standard" not in scripts:
            return _call(
                "generate_script",
                {"material_ids": material_ids, "plan_id": "standard"},
                "已检索到素材，先生成标准方案脚本。",
            )
        standard = estimates.get("standard")
        if standard is None:
            return _call("estimate_cost", {"plan_id": "standard"}, "标准脚本已生成，下一步估价。")
        if not standard.get("ok"):
            if continued:
                return _call("estimate_cost", {"plan_id": "standard"}, "估价失败，根据续跑指令重试标准方案估价。")
            return _finish("估价失败，保留已完成步骤并停止。")

        total = standard["data"]["total"]
        if total <= budget:
            return _call(
                "save_plan",
                {"theme": theme, "plan_id": "standard", "budget": budget, "deadline_hours": deadline},
                f"标准方案合计 {canonical(total)}，未超过预算 {canonical(budget)}，保存草稿。",
            )
        if "lite" not in scripts:
            return _call(
                "generate_script",
                {"material_ids": material_ids, "plan_id": "lite"},
                f"标准方案合计 {canonical(total)}，超过预算 {canonical(budget)}，改选轻量方案并生成脚本。",
            )
        lite = estimates.get("lite")
        if lite is None or not lite.get("ok"):
            return _call("estimate_cost", {"plan_id": "lite"}, "轻量脚本已生成，重新估价。")
        lite_total = lite["data"]["total"]
        if lite_total <= budget:
            return _call(
                "save_plan",
                {"theme": theme, "plan_id": "lite", "budget": budget, "deadline_hours": deadline},
                f"轻量方案合计 {canonical(lite_total)}，未超过预算 {canonical(budget)}，保存草稿。",
            )
        gap = lite_total - budget
        return _finish(f"轻量方案合计 {canonical(lite_total)} 仍超过预算，缺口 {canonical(gap)}，停止保存。")


def canonical(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)
