from pathlib import Path

from src.tools import TOOL_DEFINITIONS, TOOLS, AgentTool, dispatch, search_materials


def _state(tmp_path: Path, **overrides):
    state = {
        "theme": "春季上新",
        "budget": 20,
        "deadline_hours": 24,
        "fail_tools": [],
        "fail_consumed": [],
        "retrieved": {},
        "scripts": {},
        "estimates": {},
        "tool_call_counts": {},
        "drafts_path": str(tmp_path / "drafts.json"),
    }
    state.update(overrides)
    return state


def test_each_tool_has_name_description_and_json_schema():
    names = {item["name"] for item in TOOL_DEFINITIONS}
    assert names == {"search_materials", "generate_script", "estimate_cost", "save_plan"}
    for spec in TOOL_DEFINITIONS:
        assert spec["description"]
        assert spec["parameters"]["type"] == "object"
        assert "properties" in spec["parameters"]
        assert "required" in spec["parameters"]
        tool = TOOLS[spec["name"]]
        assert tool.permission in {"read", "write_draft"}
        assert tool.timeout_ms > 0
        assert tool.max_calls >= 1


def test_dispatch_returns_unified_envelope_with_hint(tmp_path: Path):
    payload, _ = dispatch("estimate_cost", {"plan_id": "standard"}, _state(tmp_path, fail_tools=["estimate_cost"]))
    assert payload["ok"] is False
    assert payload["code"] == "estimate_cost"
    assert payload["data"] is None
    assert payload["error"]["hint"]
    assert payload["meta"]["tool"] == "estimate_cost"
    assert payload["meta"]["call_id"]
    assert "再调用一次" in payload["error"]["hint"]


def test_invalid_arguments_tell_model_which_fields_to_fix(tmp_path: Path):
    payload, _ = dispatch("generate_script", {"plan_id": "full"}, _state(tmp_path))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "generate_script"
    assert "plan_id" in payload["message"] or "material_ids" in payload["message"]
    assert "standard 或 lite" in payload["error"]["hint"]


def test_unknown_and_publish_tools_are_denied(tmp_path: Path):
    unknown, _ = dispatch("publish", {}, _state(tmp_path))
    assert unknown["ok"] is False
    assert "没有发布权限" in unknown["message"] or "未知工具" in unknown["message"]
    assert unknown["error"]["hint"]


def test_max_calls_stops_repeated_use(tmp_path: Path):
    state = _state(tmp_path)
    last = None
    for _ in range(5):
        last, delta = dispatch("search_materials", {"theme": "春季上新"}, state)
        state.update(delta)
    assert last is not None
    assert last["ok"] is False
    assert "调用上限" in last["message"]


def test_confirmation_gate_blocks_until_confirmed(tmp_path: Path):
    gated = AgentTool(
        name="save_plan",
        description="保存草稿",
        input_model=TOOLS["save_plan"].input_model,
        handler=TOOLS["save_plan"].handler,
        permission="write_draft",
        timeout_ms=2000,
        max_calls=3,
        require_confirmation=True,
    )
    payload, _ = gated.invoke(
        {"theme": "春季上新", "plan_id": "lite", "budget": 10, "deadline_hours": 24},
        _state(tmp_path),
    )
    assert payload["ok"] is False
    assert "人工确认" in payload["message"]
    assert "等待确认" in payload["error"]["hint"]


def test_successful_search_includes_data_and_meta(tmp_path: Path):
    payload, _ = dispatch("search_materials", {"theme": "春季上新"}, _state(tmp_path))
    assert payload["ok"] is True
    assert payload["error"] is None
    assert payload["data"]["materials"][0]["id"] == "M01"
    assert payload["meta"]["permission"] == "read"
    found, _ = search_materials("春季上新", _state(tmp_path))
    assert found["ok"] is True
