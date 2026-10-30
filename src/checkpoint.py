"""同一次进程用内存检查点；跨命令续跑用 SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def memory_checkpointer() -> Any:
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver
    return InMemorySaver()


def sqlite_checkpointer(path: str) -> Any:
    from langgraph.checkpoint.sqlite import SqliteSaver

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(file_path), check_same_thread=False)
    return SqliteSaver(connection)
