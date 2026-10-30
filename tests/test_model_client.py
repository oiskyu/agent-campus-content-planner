import json
import pytest

from src.model_client import OpenAICompatModel, resolve_mode, select_model
from src.mock_model import MockChatModel


def test_default_mode_is_mock(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    assert resolve_mode() == "mock"
    assert isinstance(select_model("mock"), MockChatModel)


def test_cli_model_overrides_env(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "api")
    assert resolve_mode("mock") == "mock"
    assert resolve_mode(None, mock_flag=True) == "mock"


def test_api_without_key_explains_optional_boundary(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="仓库默认和 pytest 走 mock"):
        select_model("api")


def test_api_client_builds_openai_payload_without_live_call(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def read(self) -> bytes:
            body = {
                "choices": [
                    {
                        "message": {
                            "content": "先检索素材。",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "search_materials",
                                        "arguments": '{"theme": "春季上新"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
            return json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=30.0):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("src.model_client.urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatModel(api_key="sk-test", base_url="https://example.test/v1", model="demo")
    from langchain_core.messages import HumanMessage, SystemMessage

    message = client.invoke([SystemMessage(content="sys"), HumanMessage(content="主题：春季上新")])
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["body"]["model"] == "demo"
    assert captured["body"]["tools"]
    assert message.tool_calls[0]["name"] == "search_materials"
    assert message.tool_calls[0]["args"]["theme"] == "春季上新"
