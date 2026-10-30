from pathlib import Path

from src.catalog import quote_plan
from src.tools import estimate_cost, generate_script, save_plan, search_materials


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


def test_quotes_sum_to_reference_totals():
    standard = quote_plan("standard")
    lite = quote_plan("lite")
    assert standard is not None and lite is not None
    assert standard["total"] == 12
    assert standard["hours"] == 6
    assert sum(standard["items"].values()) == 12
    assert lite["total"] == 8
    assert lite["hours"] == 3
    assert sum(lite["items"].values()) == 8


def test_search_returns_spring_materials_and_misses_unknown_theme():
    found, delta = search_materials("春季上新", _state(Path(".")))
    assert found["ok"] is True
    assert found["code"] == "search_materials"
    assert [item["id"] for item in found["data"]["materials"]] == ["M01"]
    assert found["data"]["materials"][0]["platform"] == "短视频"
    assert found["data"]["materials"][0]["facts"][0] == "春季新品折扣力度8折"
    missed, _ = search_materials("不存在的主题", _state(Path(".")))
    assert missed["ok"] is False
    assert missed["code"] == "search_materials"
    assert missed["message"] == "未找到该主题的素材"
    assert "不要编造素材" in missed["meta"]["hint"]


def test_script_requires_retrieval_and_lite_keeps_two_facts(tmp_path: Path):
    state = _state(tmp_path)
    failed, _ = generate_script(["M01"], "standard", state)
    assert failed["ok"] is False
    assert failed["code"] == "generate_script"
    _, delta = search_materials("春季上新", state)
    state.update(delta)
    lite, lite_delta = generate_script(["M01"], "lite", state)
    assert lite["ok"] is True
    assert lite["data"]["script"] == "春季新品折扣力度8折。活动周期7天。"
    assert "线下门店同步参与" not in lite["data"]["script"]
    state.update(lite_delta)
    standard, _ = generate_script(["M01"], "standard", state)
    assert "线下门店同步参与" in standard["data"]["script"]


def test_estimate_fails_once_then_returns_sum(tmp_path: Path):
    state = _state(tmp_path, fail_tools=["estimate_cost"])
    failed, delta = estimate_cost("standard", state)
    assert failed["ok"] is False
    assert failed["code"] == "estimate_cost"
    assert failed["message"] == "成本估算服务暂时不可用"
    assert "再调用一次" in failed["meta"]["hint"]
    state.update(delta)
    succeeded, _ = estimate_cost("standard", state)
    assert succeeded["ok"] is True
    assert succeeded["data"]["total"] == 12
    assert succeeded["data"]["items"] == {"素材使用": 3, "脚本": 4, "剪辑": 5}


def test_save_rejects_missing_budget_over_budget_and_duplicate(tmp_path: Path):
    state = _state(tmp_path, budget=None)
    missing, _ = save_plan("春季上新", "standard", None, 24, state)
    assert missing["code"] == "save_plan"
    assert "缺少预算" in missing["message"]

    state = _state(tmp_path, budget=10)
    _, retrieved = search_materials("春季上新", state)
    state.update(retrieved)
    _, script_delta = generate_script(["M01"], "standard", state)
    state.update(script_delta)
    _, estimate_delta = estimate_cost("standard", state)
    state.update(estimate_delta)
    rejected, _ = save_plan("春季上新", "standard", 10, 24, state)
    assert rejected["ok"] is False
    assert "缺口 2" in rejected["message"]

    state["budget"] = 20
    saved, saved_delta = save_plan("春季上新", "standard", 20, 24, state)
    state.update(saved_delta)
    again, _ = save_plan("春季上新", "standard", 20, 24, state)
    assert saved["data"]["draft_id"] == again["data"]["draft_id"]
    assert again["message"] == "草稿已存在"
    assert saved["data"]["total"] == sum(saved["data"]["items"].values())


def test_save_rejects_deadline_shorter_than_plan(tmp_path: Path):
    state = _state(tmp_path, deadline_hours=2)
    _, retrieved = search_materials("春季上新", state)
    state.update(retrieved)
    _, script_delta = generate_script(["M01"], "standard", state)
    state.update(script_delta)
    _, estimate_delta = estimate_cost("standard", state)
    state.update(estimate_delta)
    rejected, _ = save_plan("春季上新", "standard", 20, 2, state)
    assert rejected["ok"] is False
    assert "超过截止时间" in rejected["message"]
