"""模型入口。默认 Mock；api 为 OpenAI 兼容 Chat Completions。密钥只放本机环境变量。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from src.mock_model import MockChatModel
from src.tools import TOOL_SCHEMAS

MODES = ("mock", "api")


def resolve_mode(cli_mode: str | None = None, mock_flag: bool = False) -> str:
    if mock_flag and cli_mode == "api":
        raise ValueError("不能同时指定 --mock 与 --model api")
    if mock_flag:
        return "mock"
    if cli_mode:
        mode = cli_mode.strip().lower()
        if mode not in MODES:
            raise ValueError(f"未知模型入口 {cli_mode}，可选：mock、api")
        return mode
    env_mode = os.environ.get("AGENT_MODEL", "mock").strip().lower() or "mock"
    if env_mode not in MODES:
        raise ValueError(f"AGENT_MODEL 只能是 mock 或 api，当前为 {env_mode}")
    return env_mode


def select_model(mode: str = "mock"):
    if mode == "mock":
        return MockChatModel()
    if mode == "api":
        return OpenAICompatModel.from_env()
    raise ValueError(f"未知模型入口 {mode}")


class OpenAICompatModel:
    """OpenAI 兼容 Chat Completions。只写了对接逻辑，默认测试不调用真实接口。"""

    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float = 90.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._tools = TOOL_SCHEMAS

    @classmethod
    def from_env(cls) -> OpenAICompatModel:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "api 入口需要 OPENAI_API_KEY。仓库默认和 pytest 走 mock；"
                "该分支只写了对接逻辑，未随仓库对真实服务验证。"
            )
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        return cls(api_key=api_key, base_url=base_url, model=model)

    def bind_tools(self, tools: list[Any]) -> OpenAICompatModel:
        self._tools = tools
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        payload = {
            "model": self.model,
            "temperature": 0,
            "tools": self._tools,
            "tool_choice": "auto",
            "messages": [_to_openai_message(item) for item in messages],
        }
        body = _post_json(f"{self.base_url}/chat/completions", payload, self.api_key, self.timeout_s)
        choice = ((body.get("choices") or [{}])[0].get("message")) or {}
        content = choice.get("content") or ""
        tool_calls = []
        for item in choice.get("tool_calls") or []:
            function = item.get("function") or {}
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            tool_calls.append(
                {
                    "name": function.get("name"),
                    "args": arguments,
                    "id": item.get("id") or f"call_{len(tool_calls)}",
                    "type": "tool_call",
                }
            )
        return AIMessage(content=content, tool_calls=tool_calls)


def _to_openai_message(message: BaseMessage) -> dict[str, Any]:
    role = getattr(message, "type", "")
    if isinstance(message, SystemMessage) or role == "system":
        return {"role": "system", "content": str(message.content)}
    if role == "human":
        return {"role": "user", "content": str(message.content)}
    if isinstance(message, ToolMessage) or role == "tool":
        return {
            "role": "tool",
            "tool_call_id": getattr(message, "tool_call_id", ""),
            "content": str(message.content),
        }
    tool_calls = getattr(message, "tool_calls", None) or []
    payload: dict[str, Any] = {"role": "assistant", "content": str(message.content or "")}
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ]
    return payload


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI 兼容接口 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI 兼容接口不可达：{exc.reason}") from exc
