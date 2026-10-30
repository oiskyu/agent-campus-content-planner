import inspect
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.catalog import load_requests
from src.checkpoint import memory_checkpointer
from src.graph import build_graph, initial_state, route_after_agent, route_after_tools
from src.mock_model import MockChatModel


def _run(case: str, drafts: Path, thread_id: str, saver=None):
    request = load_requests()[case]
    checkpointer = saver or memory_checkpointer()
    graph = build_graph(MockChatModel(), checkpointer)
    result = graph.invoke(initial_state(request, str(drafts)), {"configurable": {"thread_id": thread_id}})
    return result["output"], result["trace"]


def test_router_does_not_encode_budget_choices():
    source = inspect.getsource(route_after_agent) + inspect.getsource(route_after_tools)
    assert "budget" not in source
    assert "lite" not in source
    assert "standard" not in source


def test_normal_saves_standard_plan(tmp_path: Path):
    output, trace = _run("normal", tmp_path / "drafts.json", "normal")
    assert output["status"] == "completed"
    assert output["saved_plan"]["plan_id"] == "standard"
    assert output["saved_plan"]["total"] == 12
    assert output["total_cost"] == 12
    assert output["saved_plan"]["total"] == sum(output["saved_plan"]["items"].values())
    assert output["incomplete_reason"] is None
    assert [step["tool"] for step in trace["steps"]] == [
        "search_materials",
        "generate_script",
        "estimate_cost",
        "save_plan",
    ]
    assert trace["steps"][-1]["next_action"] == "finish"


def test_missing_budget_and_deadline_do_not_save(tmp_path: Path):
    missing_budget, _ = _run("missing-info", tmp_path / "a.json", "budget")
    assert missing_budget["status"] == "incomplete"
    assert missing_budget["incomplete_reason"] == "missing_budget"
    assert missing_budget["saved_plan"] is None

    request = {
        "theme": "春季上新",
        "budget": 20,
        "deadline_hours": None,
    }
    graph = build_graph(MockChatModel(), memory_checkpointer())
    missing_deadline = graph.invoke(
        initial_state(request, str(tmp_path / "b.json")),
        {"configurable": {"thread_id": "deadline"}},
    )["output"]
    assert missing_deadline["incomplete_reason"] == "missing_deadline"
    assert missing_deadline["saved_plan"] is None


def test_tool_failure_keeps_earlier_success(tmp_path: Path):
    output, trace = _run("tool-failure", tmp_path / "drafts.json", "fail")
    assert output["status"] == "incomplete"
    assert output["incomplete_reason"] == "tool_failed"
    assert output["saved_plan"] is None
    statuses = {record["tool"]: record["status"] for record in output["step_records"]}
    assert statuses["search_materials"] == "success"
    assert statuses["generate_script"] == "success"
    assert statuses["estimate_cost"] == "failed"
    assert output["tool_results"][-1]["code"] == "estimate_cost"
    assert output["tool_results"][-1]["ok"] is False
    assert trace["steps"][-1]["next_action"] == "finish"


def test_budget_overrun_switches_to_lite(tmp_path: Path):
    output, trace = _run("budget-overrun", tmp_path / "drafts.json", "over")
    assert output["status"] == "completed"
    assert output["saved_plan"]["plan_id"] == "lite"
    assert output["total_cost"] == 8
    assert output["saved_plan"]["items"] == {"素材使用": 3, "脚本": 2, "剪辑": 3}
    estimated = [step for step in trace["steps"] if step["tool"] == "estimate_cost"]
    assert estimated[0]["arguments"]["plan_id"] == "standard"
    assert estimated[0]["result_summary"] == "合计 12"
    assert estimated[0]["next_action"] == "generate_script"
    assert "轻量" in estimated[0]["decision"]
    assert estimated[1]["arguments"]["plan_id"] == "lite"
    assert estimated[1]["next_action"] == "save_plan"


def test_no_feasible_plan_reports_gap(tmp_path: Path):
    output, _ = _run("no-feasible-plan", tmp_path / "drafts.json", "gap")
    assert output["status"] == "incomplete"
    assert output["incomplete_reason"] == "no_feasible_plan"
    assert output["gap"] == 3
    assert output["total_cost"] == 8
    assert output["saved_plan"] is None


def test_duplicate_run_returns_same_draft(tmp_path: Path):
    drafts = tmp_path / "drafts.json"
    first, _ = _run("budget-overrun", drafts, "first")
    second, _ = _run("duplicate", drafts, "second")
    assert first["saved_plan"]["draft_id"] == second["saved_plan"]["draft_id"]
    assert second["tool_results"][-1]["message"] == "草稿已存在"
    assert len(load_draft_keys(drafts)) == 1


def test_max_steps_stops_with_partial_success(tmp_path: Path):
    output, trace = _run("max-steps", tmp_path / "drafts.json", "limit")
    assert output["status"] == "incomplete"
    assert output["incomplete_reason"] == "max_steps"
    assert output["saved_plan"] is None
    assert len(output["step_records"]) == 2
    assert [record["status"] for record in output["step_records"]] == ["success", "success"]
    assert trace["steps"][-1]["next_action"] == "finish"
    assert "上限" in trace["steps"][-1]["decision"]


def test_rerunning_same_thread_without_resume_keeps_checkpoint_history(tmp_path: Path):
    saver = memory_checkpointer()
    drafts = tmp_path / "drafts.json"
    failed, _ = _run("tool-failure", drafts, "same-thread", saver)
    assert failed["incomplete_reason"] == "tool_failed"
    again, _ = _run("normal", drafts, "same-thread", saver)
    assert any(record["status"] == "failed" for record in again["step_records"])
    fresh, _ = _run("normal", drafts, "fresh-thread", saver)
    assert fresh["status"] == "completed"
    assert fresh["saved_plan"]["plan_id"] == "standard"
    assert all(record["status"] == "success" for record in fresh["step_records"])


def test_resume_retries_failed_estimate_and_saves(tmp_path: Path):
    saver = memory_checkpointer()
    request = load_requests()["tool-failure"]
    graph = build_graph(MockChatModel(), saver)
    config = {"configurable": {"thread_id": "resume-1"}}
    first = graph.invoke(initial_state(request, str(tmp_path / "drafts.json")), config)
    assert first["output"]["incomplete_reason"] == "tool_failed"
    assert any(record["status"] == "failed" for record in first["output"]["step_records"])

    second = graph.invoke({"messages": [HumanMessage(content="继续完成失败步骤")]}, config)
    output = second["output"]
    assert output["status"] == "completed"
    assert output["saved_plan"]["plan_id"] == "standard"
    assert output["saved_plan"]["total"] == 12
    estimate_statuses = [record["status"] for record in output["step_records"] if record["tool"] == "estimate_cost"]
    assert estimate_statuses == ["failed", "success"]
    assert output["step_records"][0]["status"] == "success"


def load_draft_keys(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
