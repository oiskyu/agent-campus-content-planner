# 内容任务规划 Agent 方案

题号 2。运行方式为 CLI。编排使用 LangGraph 的状态图、消息累加和步数路由。决策按 ReAct 进行：模型选择动作，工具执行，模型看到返回后再决定下一步或结束。

## 设计取舍

ReAct 负责决策，LangGraph 只做执行骨架。

模型根据上一轮工具返回决定四件事：调用哪个工具、参数是什么、是否从 `standard` 改选 `lite`、停止还是再试一次。路由函数不读取预算、方案名或失败码。`src/graph.py` 里的路由只判断两件事：最近一条消息有没有工具调用，以及工具次数是否达到 `max_steps`。达到上限时进入 `finalize`，返回 `status=incomplete`、`incomplete_reason=max_steps`。

固定规则放在工具和 `data/` 中，不放在图的边上：

- `estimate_cost` 按 `cost_table.json` 把分项相加，返回 `total` 和 `hours`，不判断是否超预算。
- `generate_script` 只使用已经检索到的素材事实。
- `save_plan` 拒绝缺预算、缺截止时间、未估价、超预算和超工期，并按「主题 + 方案 + 预算」返回已有草稿。

不把「估价大于预算就改选 lite」写成条件边。节点自动重试不会把失败观察交给模型，不作为动态决策。系统提示只说明常用顺序和约束；真正的下一步来自当次工具返回。

`--model api` 跑出的 Trace 才能证明模型会改下一步。默认 `--model mock` 是按已有观察选择下一步的确定性策略，只验证同一条循环和工具协议。

## 必做与加分

必做是七组验收，每组留下 `examples/<用例>/input.json`、`output.json`、`trace.json`。

| 标记 | 用例 | 目录 | 结果 |
| --- | --- | --- | --- |
| 必做 | 正常完成 | `examples/normal/` | `completed`，标准方案合计 12 |
| 必做 | 缺预算或截止时间 | `examples/missing-info/` | `incomplete`，`missing_budget` 或 `missing_deadline`，无草稿 |
| 必做 | 工具失败 | `examples/tool-failure/` | 估价失败保持 `failed`，已成功的检索和脚本保留，无草稿 |
| 必做 | 预算超限 | `examples/budget-overrun/` | 先看到标准方案 12，再保存轻量方案，合计 8 |
| 必做 | 无可行方案 | `examples/no-feasible-plan/` | `incomplete`，`no_feasible_plan`，`gap` 为 3 |
| 必做 | 重复执行 | `examples/duplicate/` | 再次运行返回同一个 `draft_id` |
| 必做 | 步数上限 | `examples/max-steps/` | `incomplete`，`max_steps`，已完成步骤保留 |

正常完成和预算超限是两条必交路径。两次都出现过标准方案合计 12：一次未超过预算并保存，一次超出后改选 `lite`。改选必须出现在 Trace 的下一次工具调用里。

工具失败作为必做，允许模型看到失败 JSON 后停止。同一次运行中再次调用该工具，并在 `step_records` 里同时保留 `failed` 与后续 `success`，是加分项。跨进程使用 `SqliteSaver` 和 `--resume --thread-id` 也是加分项。`SqliteSaver` 来自 LangGraph，只用于这条续跑链路。

## 输出约定

`input.json` 保存本次主题、预算、截止时间和步数上限。`output.json` 与 `trace.json` 由 `finalize` 从同一次状态写出。`docs/03-evaluation.md` 按路径引用这些文件。

`output.json` 使用同一组字段：

| 交付要求 | 字段 |
| --- | --- |
| 任务计划 | `task_plan` |
| 步骤状态 | `step_records[].status`，取值为 `success` 或 `failed` |
| 工具结果 | `tool_results`。评审核对 `ok`、`code`、`message`、`data` |
| 费用合计 | `total_cost` |
| 已保存方案 | `saved_plan`，含草稿 ID、方案、素材、脚本、分项、合计、耗时 |
| 未完成原因 | `incomplete_reason` 与 `gap`。未保存时 `saved_plan` 为 `null` |

`saved_plan.total` 等于分项之和，并等于 `total_cost`。失败步骤保持 `failed`。工具返回里的 `error` 和 `meta` 是给模型的修正提示和调用信息，不替代上述字段。

`trace.json` 的每一步包含工具名、参数、返回摘要、后续动作和一句依据。后续动作是下一次实际工具调用，或 `finish`。不记录模型内部思维。

## 模型入口

CLI 用 `--model` 切换，默认 `mock`。

| 入口 | 命令 | 用途 |
| --- | --- | --- |
| Mock | `python -m src.main --case budget-overrun` | 离线、确定性，不需要密钥。验证循环、工具和输出字段 |
| API | `python -m src.main --model api --case budget-overrun` | OpenAI 兼容接口。七组 `examples/` 已用该入口写出 Trace |

密钥放在本机环境变量，仓库只提交 `.env.example`。没有密钥时不要用 Mock 结果代替 API Trace。必做里的「正常完成」和「预算超限」两份决策记录，以 `--model api` 的实际输出为准。
