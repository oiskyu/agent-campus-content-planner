# 内容任务规划 Agent 编写计划

题号：2  
题目：内容任务规划 Agent  
运行方式：CLI  
编排：LangGraph 提供循环、状态和 checkpoint；每一步动作由模型按 ReAct 选择

## 1. 结论：用 ReAct，workflow 只做骨架

本题用 ReAct 更好。

审核要看的是：模型看见成本超限、工具错误或部分成功之后，自己调整计划、改选方案或停止。这条链路必须是「模型选择动作 → 执行工具 → 观察结果 → 模型决定下一步或结束」。

Workflow 适合固定工序，例如检索完一定去生成脚本。把「估价 12 大于预算 10，就改选轻量方案」写进边上的条件分支，只能证明流程按预设走完，达不到动态决策要求。自动重试和预录决策日志同样不能算完成。

决策权在模型侧，LangGraph 只做执行骨架。正式表述见 `docs/02-solution.md`。

- 模型选择工具、参数、是否从 `standard` 改选 `lite`、停止或再试一次。
- 路由只判断有没有工具调用，以及是否达到步数上限。
- 费用累加、素材校验、超预算拒绝保存、草稿幂等，由工具和 `data/` 执行。
- 节点自动重试不作为动态决策。
- `--model api` 的 Trace 才证明模型会改下一步。默认 Mock 只验证流程。

LangGraph 使用状态图、消息累加和步数路由。`SqliteSaver` 只用于加分项里的跨进程续跑。不做界面、多 Agent、线上部署和生产级 Runtime。

## 2. 目标和范围

帮助运营人员在预算和截止时间内完成一份内容活动方案。

输入：主题、预算、截止时间。材料全部自编，使用模拟费用单位。

交付：每次运行给出同一份结果，字段见第 7 节。内容为任务计划、步骤状态、工具结果、费用合计、已保存方案或未完成原因。只保存草稿，不接真实发布。已保存方案包含方案正文，不只是草稿 ID。

建议投入约 3 小时，先完成核心链路。实际用时和未完成部分记入 `docs/05-retrospective.md`。

## 3. 谁做什么

| 事项 | 承担者 |
| --- | --- |
| 下一步调用哪个工具、参数是什么 | 模型 |
| 看见标准方案合计 12 且预算为 10 后，改选轻量方案或停止 | 模型 |
| 看见工具失败 JSON 后，重试该工具或停止 | 模型 |
| 预算或截止时间缺失时停止，不编造缺项 | 模型 |
| 价目相加、工期比较、脚本只引用已检索事实 | 工具与 `data/` |
| 超预算、缺估价、工期不够时拒绝保存 | `save_plan` |
| 同一主题 + 方案 + 预算只保留一份草稿 | `save_plan` |
| 成功步骤保留，失败步骤保持失败 | 图状态 |
| 步数到达 8 仍未完成时返回 `incomplete` | workflow 路由 |

预设骨架可以是「通常先检索，再写脚本，再估价，再保存」。这句话写在系统提示里。真正走到哪一步，仍由模型根据上一轮工具结果决定。

## 4. 合成数据

放在 `data/`。评审只克隆仓库，应能用这些文件复算费用。

`materials.json`  
四到六条素材。每条包含 ID、主题、可引用事实、平台、制作耗时。事实只写素材里真有的内容。

`cost_table.json`

| 项目 | standard | lite |
| --- | --- | --- |
| 素材使用 | 3 | 3 |
| 脚本 | 4 | 2 |
| 剪辑 | 5 | 3 |
| 合计 | 12 | 8 |
| 耗时 | 6 小时 | 3 小时 |

`estimate_cost` 读表相加，不把 12 和 8 写成与分项无关的常数。

`rules.json`

- 没有预算或截止时间，禁止保存。
- 合计大于预算，禁止保存，并返回缺口。
- 方案耗时大于剩余截止时间，禁止保存。
- 脚本只能使用已检索素材中的事实。
- 未检索到的素材不能写脚本；未成功估价不能保存。
- 重复保存返回已有草稿。
- 工具调用上限 8 次。

`requests.json`  
五组验收输入，另加一组预算小到两套方案都不可行的输入，以及一组失败后续跑输入。

## 5. 四个工具

只实现这四个工具。方案编号 `standard` 和 `lite` 写在工具说明中。费用只从 `estimate_cost` 返回。

成功和失败使用同一层 JSON。`code` 固定为工具名，`message` 是给模型看的说明。

失败示例：

```json
{"ok": false, "code": "estimate_cost", "message": "成本估算服务暂时不可用"}
```

成功示例：

```json
{"ok": true, "code": "estimate_cost", "message": "估价完成", "data": {"plan_id": "standard", "items": {"素材使用": 3, "脚本": 4, "剪辑": 5}, "total": 12, "hours": 6}}
```

| 工具 | 作用 | 失败 code 与典型 message |
| --- | --- | --- |
| `search_materials(theme)` | 按主题返回素材 ID 和事实 | `search_materials`：未找到该主题的素材 |
| `generate_script(material_ids, plan_id)` | 只用已检索事实生成脚本 | `generate_script`：素材尚未检索或不存在 |
| `estimate_cost(plan_id)` | 返回分项、合计、耗时 | `estimate_cost`：成本估算服务暂时不可用 |
| `save_plan(theme, plan_id, budget, deadline_hours)` | 通过规则后写入草稿 | `save_plan`：缺预算、超预算、缺估价或工期不够 |

错误在工具内部转换为上述 JSON，不抛出图外。这样失败会进入步骤表和 checkpoint，已成功步骤不会被清掉。

`estimate_cost` 的失败注入只对当前请求的第一次调用生效，第二次返回正常合计。这为「同一次运行中的续跑」留出口。

`save_plan` 的幂等键是「主题 + 方案 ID + 预算」。第二次保存返回原来的草稿 ID，并在 `message` 中说明草稿已存在。

## 6. LangGraph 编排

三个节点：

```text
START → agent → 路由
                 ├─ 模型发出工具调用，且未满 8 步 → tools → agent
                 ├─ 模型不再调用工具 → finalize → END
                 └─ 步数用尽 → finalize（status=incomplete）→ END
```

- `agent`：把目标、规则摘要、工具列表和历史结果交给模型。模型返回下一次工具调用，或给出结束结论。
- `tools`：执行模型点名的工具，追加步骤记录后回到 `agent`。
- `finalize`：按第 7 节的固定结构写出 `output.json`。成功时 `saved_plan` 带上方案正文；未完成时 `saved_plan` 为 `null`，并写明 `incomplete_reason`。

路由只判断两件事：有没有工具调用，步数是否到达上限。预算比较放在工具返回和模型的下一轮选择里。

状态字段：`messages`、`steps`、`max_steps`、主题、预算、截止时间、`step_records`、`status`、未完成原因。`step_records` 中每步单独标记 `success` 或 `failed`。

参考实例在图上的走法：

1. 模型调用 `search_materials("春季上新")`，得到 M01。
2. 模型调用 `generate_script(["M01"], "standard")`。
3. 模型调用 `estimate_cost("standard")`，得到合计 12。
4. 预算为 10 时，模型改选 `estimate_cost("lite")` 或先生成轻量脚本再估价，得到 8。
5. 模型调用 `save_plan`，工具确认 8 不超过 10 且工期足够，返回草稿 ID。
6. 模型停止，`finalize` 返回 `completed`。

第 4 步必须出现在 Trace 的工具调用里，不能由路由代替。

## 7. 输出结果

每组运行都写出三个文件。`input.json` 是本次请求。`output.json` 是交付结果。`trace.json` 是决策记录。`docs/03-evaluation.md` 引用这些路径，不另写一套口头结论。

`output.json` 固定为下面的结构。成功和未完成使用同一组字段。

```json
{
  "status": "completed",
  "task_plan": {
    "theme": "春季上新",
    "budget": 10,
    "deadline_hours": 24,
    "selected_plan_id": "lite",
    "material_ids": ["M01"],
    "script": "依据 M01 事实写成的轻量脚本",
    "plan_steps": ["检索素材", "生成轻量脚本", "估价", "保存草稿"]
  },
  "step_records": [
    {
      "step": 1,
      "tool": "search_materials",
      "arguments": {"theme": "春季上新"},
      "status": "success",
      "result_summary": "找到 M01"
    }
  ],
  "tool_results": [
    {
      "ok": true,
      "code": "search_materials",
      "message": "检索完成",
      "data": {"material_ids": ["M01"]}
    }
  ],
  "total_cost": 8,
  "saved_plan": {
    "draft_id": "draft-001",
    "plan_id": "lite",
    "theme": "春季上新",
    "material_ids": ["M01"],
    "script": "依据 M01 事实写成的轻量脚本",
    "items": {"素材使用": 3, "脚本": 2, "剪辑": 3},
    "total": 8,
    "hours": 3
  },
  "incomplete_reason": null,
  "gap": null
}
```

字段对应关系：

| 交付要求 | 字段 |
| --- | --- |
| 任务计划 | `task_plan`：主题、预算、截止时间、选用方案、素材、脚本、计划步骤 |
| 步骤状态 | `step_records[].status`，取值为 `success` 或 `failed` |
| 工具结果 | `tool_results`，沿用工具的 `ok`、`code`、`message`、`data` |
| 费用合计 | `total_cost`。尚未估价时为 `null` |
| 已保存方案 | `saved_plan`：草稿 ID、方案编号、素材、脚本、分项、合计、耗时 |
| 未完成原因 | `status=incomplete`，`saved_plan=null`，`incomplete_reason` 必填 |

未完成时的原因只用这些值：

| incomplete_reason | 含义 | gap |
| --- | --- | --- |
| `missing_budget` | 没有预算 | `null` |
| `missing_deadline` | 没有截止时间 | `null` |
| `tool_failed` | 工具失败后模型停止，失败步骤保持 `failed` | `null` |
| `no_feasible_plan` | 标准方案与轻量方案都超过预算 | 最便宜方案合计减预算。预算 5 时，缺口为 3 |
| `max_steps` | 工具调用达到上限仍未保存 | `null` |

`saved_plan.total` 必须等于 `items` 三项之和，并等于 `total_cost`。未保存时不写草稿 ID，也不能把 `status` 标成 `completed`。

`trace.json` 记录模型可见的决策过程，不记录内部思维：

```json
{
  "steps": [
    {
      "step": 4,
      "tool": "estimate_cost",
      "arguments": {"plan_id": "lite"},
      "result_summary": "合计 8，未超过预算 10",
      "next_action": "save_plan",
      "decision": "轻量方案在预算内，下一步保存草稿"
    }
  ]
}
```

`trace.json` 的每一步都能在 `output.json` 的 `step_records` 和 `tool_results` 里找到同一次调用。后续动作必须是下一次真实的工具调用，或 `finish`。

## 8. 验收与 Trace（必做）

每组输入运行后，把结果放到 `examples/<用例>/`：

- `input.json`
- `output.json`
- `trace.json`

Trace 记录步骤、工具参数、返回摘要、后续动作和一句决策依据。不记录模型内部思维过程。

| 用例 | 目录 | 输入 | output.json 的关键结果 |
| --- | --- | --- | --- |
| 正常完成 | `examples/normal/` | 预算 20，截止 24 小时 | `status=completed`。`saved_plan.plan_id=standard`，`items` 合计与 `total_cost` 都是 12 |
| 缺预算或截止时间 | `examples/missing-info/` | 预算或截止时间为空 | `status=incomplete`，`incomplete_reason` 为 `missing_budget` 或 `missing_deadline`，`saved_plan=null` |
| 工具失败 | `examples/tool-failure/` | 预算 20，估价第一次固定失败 | 检索和脚本为 `success`，估价为 `failed`。`tool_results` 中含 `code=estimate_cost` 的失败 JSON。`saved_plan=null`，`incomplete_reason=tool_failed` |
| 预算超限 | `examples/budget-overrun/` | 预算 10，截止 24 小时 | 先看到标准方案 12，再保存轻量方案。`saved_plan.plan_id=lite`，`total_cost=8`，`status=completed` |
| 无可行方案 | `examples/no-feasible-plan/` | 预算 5 | 两套方案都超预算。`status=incomplete`，`incomplete_reason=no_feasible_plan`，`gap=3`，`saved_plan=null` |
| 重复执行 | `examples/duplicate/` | 将预算 10 的请求再运行一次 | 两份 `saved_plan.draft_id` 相同。草稿库只有 1 条 |
| 步数上限 | `examples/max-steps/` | 与正常完成相同，但本次 `max_steps=2` | 只保留前两步的成功结果。`status=incomplete`，`incomplete_reason=max_steps`，`saved_plan=null` |

正常完成和预算超限是两条必须提交的执行路径。两次工具都返回过标准方案合计 12：一次未超过预算，模型保存；一次超出 2，模型改选 `lite`。

`docs/03-evaluation.md` 按上表逐条引用文件，至少包括：

- `examples/normal/output.json`
- `examples/missing-info/output.json`
- `examples/tool-failure/output.json`
- `examples/tool-failure/trace.json`
- `examples/budget-overrun/trace.json`
- `examples/no-feasible-plan/output.json`
- `examples/duplicate/output.json`
- `examples/max-steps/output.json`

预期写在文档里，实际数字以这些文件为准。文件缺失的验收点视为未验证。

`tests/` 用断言核对同一份结构：`saved_plan.total` 等于分项之和，失败步骤的 `status` 仍是 `failed`，重复保存只有一个 `draft_id`，`max-steps` 的 `incomplete_reason` 为 `max_steps`。

默认 `--model mock` 只验证流程和工具协议。正常完成与预算超限这两条决策 Trace 必须来自 `--model api`。密钥只放本机环境变量，仓库提供 `.env.example`。API 对接已写，仓库未对真实服务验证。

## 9. Checkpoint 与重跑（加分）

必做「工具失败」可以在看到失败 JSON 后停止。已成功步骤留在 `step_records`，失败步骤保持 `failed`。

加分是失败后续跑：同一次运行内由模型再次调用该工具，或用 `SqliteSaver` 跨进程 `--resume`。`SqliteSaver` 属于 LangGraph，只在这条续跑链路上使用。

1. 同一次运行内续跑  
   第一次 `estimate_cost` 返回失败 JSON。模型下一轮再次调用它，第二次返回正常合计。步骤表保留第一次 `failed` 和第二次 `success`。

2. 两次命令续跑  
   编译图时加入 checkpointer，两次使用同一个 `thread_id`。第一次在失败后结束。第二次只追加「继续完成失败步骤」，或在图停在未完成节点时传入 `None`。不要把原始需求再传一遍，否则会从入口重新开始。  
   两次命令属于两个进程，使用 `SqliteSaver` 写入本地文件。`InMemorySaver` 只用于同一进程内的测试。需要抗进程中断时，调用参数使用 `durability="sync"`。

明确不作为主路径的策略：

- `RetryPolicy`：节点抛异常后自动重跑，模型看不到失败，不能证明动态决策。
- 时间旅行重放：从失败前的 checkpoint 执行 `invoke(None, checkpoint_config)`，会原样重放工具节点，模型没有重新选择。仅适合进程在工具节点中途崩溃。
- `interrupt`：适合中途等待人工确认。本题没有确认步骤，不使用。

## 10. 仓库与编写顺序

```text
README.md                 环境、安装、运行命令、评审入口
.env.example              所需变量；无密钥时注明 Mock 边界
requirements.txt
src/                      图、工具、CLI、模型接入
tests/                    可执行测试
examples/                 输入、输出、Trace
data/                     合成素材、价目、规则、请求
docs/01-requirements.md   用户、目标、范围、成功标准
docs/02-solution.md       调用流程、动态决策、技术取舍
docs/03-evaluation.md     测试预期、实际结果、失败与限制
docs/04-ai-usage.md       AI 辅助范围、个人修改与验证
docs/05-retrospective.md  最难问题、收获、下一步、实际用时
```

编写顺序：

1. 写 `data/` 和四个工具，先用单元测试钉住计价、拒绝保存和幂等。
2. 接上 ReAct 循环、步骤上限和失败 JSON。
3. 按第 8 节跑通七组用例，输出都符合第 7 节的字段。重点核对预算超限、无可行方案和步数上限。
4. 用真实模型生成「正常完成」和「预算超限」两条 Trace。
5. 补 Sqlite checkpoint 的失败续跑。
6. 写 README 与 `docs/01` 到 `05`，提交 GitHub。

提交物是题号 2、仓库 URL 和最终 commit SHA。不提交真实密钥或隐私数据。

## 11. 运行入口

在项目根目录用本机 Python 运行，不必单独建 conda 环境。先 `pytest`，再按下面的用例跑。每条命令结束后，程序把这次的 `input.json`、`output.json`、`trace.json` 写入 `examples/<用例>/`。这三份文件来自本次运行，不要手写。`duplicate` 接在 `budget-overrun` 后面。

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

续跑是加分项。第一次使用 `tool-failure` 和线程编号，第二次用同一个编号只追加续跑：

```bash
python -m src.main --case tool-failure --thread-id demo-1
python -m src.main --resume --thread-id demo-1
```
