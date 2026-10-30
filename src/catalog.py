"""读取合成素材、价目、规则和入口请求。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_materials() -> list[dict[str, Any]]:
    return load_json(DATA_DIR / "materials.json")


def load_cost_table() -> dict[str, Any]:
    return load_json(DATA_DIR / "cost_table.json")


def load_rules() -> dict[str, Any]:
    return load_json(DATA_DIR / "rules.json")


def load_requests(path: Path | None = None) -> dict[str, dict[str, Any]]:
    payload = load_json(path or DATA_DIR / "requests.json")
    if not isinstance(payload, list):
        raise ValueError("requests.json 应为用例数组")
    cases: dict[str, dict[str, Any]] = {}
    for item in payload:
        name = item["case_name"]
        if name in cases:
            raise ValueError(f"重复用例 {name}")
        cases[name] = item
    return cases


def quote_plan(plan_id: str) -> dict[str, Any] | None:
    table = load_cost_table()
    plan = table.get(plan_id)
    if plan is None:
        return None
    items = dict(plan["items"])
    total = sum(items.values())
    if "total" in plan and plan["total"] != total:
        raise ValueError(f"{plan_id} 的 total 与分项之和不一致")
    return {
        "plan_id": plan_id,
        "items": items,
        "total": total,
        "hours": plan["hours"],
    }
