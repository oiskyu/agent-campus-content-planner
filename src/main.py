"""命令行入口。默认 mock；--model api 为可选的 OpenAI 兼容分支。"""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.catalog import ROOT, dump_json, load_requests
from src.checkpoint import memory_checkpointer, sqlite_checkpointer
from src.graph import build_graph, initial_state
from src.model_client import resolve_mode, select_model


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def write_result(directory: Path, output_name: str, trace_name: str, output: dict, trace: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    dump_json(directory / output_name, output)
    dump_json(directory / trace_name, trace)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="内容任务规划 Agent")
    parser.add_argument("--request", default="data/requests.json", help="入口请求文件")
    parser.add_argument("--case", default="normal", help="requests.json 中的用例名")
    parser.add_argument(
        "--model",
        choices=("mock", "api"),
        default=None,
        help="模型入口。默认 mock。api 为 OpenAI 兼容接口，仓库未对真实服务验证",
    )
    parser.add_argument("--mock", action="store_true", help="等同 --model mock")
    parser.add_argument("--resume", action="store_true", help="按 thread_id 继续失败步骤")
    parser.add_argument("--thread-id", default=None, help="checkpoint 线程编号")
    parser.add_argument("--drafts", default="runs/drafts.json", help="草稿库路径")
    parser.add_argument("--checkpoint", default="runs/checkpoints.sqlite", help="SQLite checkpoint 路径")
    return parser.parse_args()


def main() -> None:
    load_env_file(ROOT / ".env")
    args = parse_args()
    try:
        mode = resolve_mode(args.model, args.mock)
        model = select_model(mode)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.resume:
        if not args.thread_id:
            raise SystemExit("续跑需要 --thread-id")
        graph = build_graph(model, sqlite_checkpointer(args.checkpoint))
        config = {"configurable": {"thread_id": args.thread_id}}
        result = graph.invoke({"messages": [HumanMessage(content="继续完成失败步骤")]}, config)
        directory = ROOT / "examples" / "resume-retry"
        write_result(directory, "resume-output.json", "resume-trace.json", result["output"], result["trace"])
    else:
        requests = load_requests(Path(args.request))
        if args.case not in requests:
            known = "、".join(requests)
            raise SystemExit(f"未知用例 {args.case}。可选：{known}")
        request = requests[args.case]
        thread_id = args.thread_id or f"{args.case}-{uuid.uuid4().hex[:8]}"
        checkpointer = sqlite_checkpointer(args.checkpoint) if args.thread_id else memory_checkpointer()
        graph = build_graph(model, checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(initial_state(request, args.drafts), config)
        directory = ROOT / "examples" / args.case
        dump_json(directory / "input.json", request)
        write_result(directory, "output.json", "trace.json", result["output"], result["trace"])
    output = result["output"]
    draft_id = None if output.get("saved_plan") is None else output["saved_plan"].get("draft_id")
    print(f"model={mode} thread_id={config['configurable']['thread_id']} 已写入 {directory}")
    print(
        f"status={output['status']} total_cost={output['total_cost']} "
        f"draft_id={draft_id} incomplete_reason={output['incomplete_reason']} gap={output['gap']}"
    )


if __name__ == "__main__":
    main()
