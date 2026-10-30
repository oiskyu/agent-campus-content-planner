# 内容任务规划 Agent

题号 2。命令行 ReAct 循环：模型选工具，工具返回结果，模型再决定下一步或结束。路由只看有没有工具调用和是否到达步数上限。设计取舍、必做验收和加分续跑见 `docs/02-solution.md`。

## 两套模型入口

| 入口 | 切换 | 说明 |
| --- | --- | --- |
| **mock（默认）** | `--model mock` 或省略 | 本地确定性策略，离线、不需要密钥。同一输入得到同一条路径。只验证流程和工具协议，不能证明真实模型会改方案。 |
| **api（可选）** | `--model api` | OpenAI 兼容 Chat Completions。七组 `examples/` 已用该入口跑过。缺密钥会直接退出。 |

```bash
python -m src.main --case budget-overrun
python -m src.main --model mock --case budget-overrun
python -m src.main --model api --case budget-overrun
```

`--mock` 仍可用，等同 `--model mock`。`AGENT_MODEL=api` 也会切换，命令行 `--model` 优先。

## 环境

使用本机已有的 Python 3.11 或更高版本即可，不必单独建 conda 环境。LangGraph 负责循环；默认 mock 不访问网络。

```bash
pip install -r requirements.txt
copy .env.example .env
```

默认不必填写密钥。只有 `--model api` 才需要 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。

## 本地测试

在项目根目录执行。先跑测试，再按用例跑 Agent。`input.json`、`output.json`、`trace.json` 都由这次运行写入 `examples/<用例>/`，不要手写。`trace.json` 记录的是这次实际走过的工具顺序。

```bash
pytest
python -m src.main --case normal
python -m src.main --case missing-info
python -m src.main --case tool-failure
python -m src.main --case budget-overrun
python -m src.main --case no-feasible-plan
python -m src.main --case duplicate
python -m src.main --case max-steps
```

`duplicate` 要接在 `budget-overrun` 后面，并共用 `runs/drafts.json`，才会返回已有草稿。日常验收不要带 `--thread-id`，每次会开一条新会话，避免叠进上次的失败步骤。

最近一次 `pytest -v` 的完整输出存在 `examples/test-results.txt`，27 项全部通过。

失败后的续跑使用同一个线程编号。第一次跑 `tool-failure`，第二次只追加续跑，不要再传原始请求：

```bash
python -m src.main --case tool-failure --thread-id demo-1
python -m src.main --resume --thread-id demo-1
```

草稿在 `runs/drafts.json`，checkpoint 在 `runs/checkpoints.sqlite`。这两个文件不提交。

## 评审导航

| 检查项 | 入口 |
| --- | --- |
| 业务目标与成功标准 | `docs/01-requirements.md` |
| 调用流程、动态决策、技术取舍 | `docs/02-solution.md` |
| 测试预期、实际结果、失败与限制 | `docs/03-evaluation.md` |
| AI 辅助范围与个人贡献 | `docs/04-ai-usage.md` |
| 最难问题、学习收获、下一步 | `docs/05-retrospective.md` |
| 预算超限改选 lite（参考实例 12→8） | `examples/budget-overrun/trace.json` |
| 失败后同运行内重试且保留 failed（加分） | `examples/tool-failure/trace.json` |
| 无可行方案返回缺口 | `examples/no-feasible-plan/output.json` |
| 重复执行返回同一草稿 | `examples/duplicate/output.json` |
| 步数上限返回未完成 | `examples/max-steps/output.json` |
| 自动化测试记录 | `examples/test-results.txt` |

## 提交信息

- 题号：2（内容任务规划 Agent）
- 运行方式：CLI。默认 mock 离线、无需密钥即可复现；`--model api` 为 OpenAI 兼容入口
- 评审可复现：`pip install -r requirements.txt` 后按「本地测试」执行，结果与 `examples/` 一致（`call_id`、`latency_ms` 等随机字段除外）
- 仓库不含真实密钥，`.env.example` 只列变量名；`runs/` 为本地运行产物，不提交

