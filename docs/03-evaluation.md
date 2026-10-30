# 评估

七组 `examples/` 均由 `--model api`（OpenAI 兼容接口）跑出。密钥只放本机环境变量，不进仓库。`duplicate` 接在已保存的预算 10 轻量草稿之后。草稿库在 `runs/`，不提交。

`pytest` 当前 27 项通过。合计等于分项之和，失败步骤保持 `failed`，重复保存的 `draft_id` 相同，`max-steps` 的原因是 `max_steps`。

## 对照

| 用例 | 文件 | 预期 | 本次实际 |
| --- | --- | --- | --- |
| 正常完成 | `examples/normal/output.json` | `completed`，标准方案合计 12 | `status=completed`，`saved_plan.plan_id=standard`，`draft_id=draft-001`，`total_cost=12`。分项为素材使用 3、脚本 4、剪辑 5。素材 M01，脚本为「春季新品折扣力度8折。活动周期7天。线下门店同步参与。」 |
| 缺预算 | `examples/missing-info/output.json` | `incomplete`，`missing_budget`，无草稿 | 只完成检索 M01。`incomplete_reason=missing_budget`，`saved_plan=null`，`total_cost=null` |
| 工具失败 | `examples/tool-failure/output.json`、`examples/tool-failure/trace.json` | 估价失败保持 `failed`；必做允许随后停止 | 主题「618大促」，素材 M02。第一次 `estimate_cost` 为 `failed`，`message` 为「成本估算服务暂时不可用」。真实模型下一步再次调用 `estimate_cost`，第二次成功合计 12 并保存 `draft-003`。步骤表同时保留 `failed` 与 `success`。这是加分项「同一次运行内续跑」 |
| 预算超限 | `examples/budget-overrun/trace.json` | 先看到 12，再保存轻量方案 8 | 标准方案估价后，下一步是 `generate_script`，依据是合计 12 超过预算 10。随后轻量方案合计 8 并保存。`draft_id=draft-002`，`total_cost=8`，`status=completed`。轻量脚本只保留前两条事实：「春季新品折扣力度8折。活动周期7天。」 |
| 无可行方案 | `examples/no-feasible-plan/output.json` | `no_feasible_plan`，缺口 3，无草稿 | 主题「会员日」，素材 M03。标准方案 12、轻量方案 8 都超过预算 5。`incomplete_reason=no_feasible_plan`，`total_cost=8`，`gap=3.0`，`saved_plan=null` |
| 重复执行 | `examples/duplicate/output.json` | 与预算 10 那次是同一个草稿 | `saved_plan.draft_id` 仍是 `draft-002`。最后一次 `save_plan` 的 `message` 为「草稿已存在」，`ok` 为 true |
| 步数上限 | `examples/max-steps/output.json` | 未完成，已完成步骤保留 | 主题「周年庆」，素材 M04。只留下检索和生成脚本两步，都是 `success`。`incomplete_reason=max_steps`，`saved_plan=null`，`total_cost=null`。Trace 最后一句是「工具调用已达到上限，停止执行。」 |

`examples/normal/output.json` 里，标准方案 12 未超过预算 20，下一步是保存。`examples/budget-overrun/trace.json` 里，同一次工具返回的 12 超过预算 10，下一步改成生成轻量脚本。两条路径都出现过标准方案合计 12，随后动作不同。

## 复现

```bash
python -m src.main --model api --case normal
python -m src.main --model api --case missing-info
python -m src.main --model api --case tool-failure
python -m src.main --model api --case budget-overrun
python -m src.main --model api --case no-feasible-plan
python -m src.main --model api --case duplicate
python -m src.main --model api --case max-steps
```

无密钥时去掉 `--model api`，走 mock。mock 下 `tool-failure` 看到失败后停止，不会在同一次运行里再估价。

单独重跑 `duplicate` 前，草稿库里需要已经有「春季上新 + lite + 预算 10」。本次样例是先跑 `budget-overrun` 再跑 `duplicate`。清空 `runs/drafts.json` 后只跑 `duplicate`，会新建草稿，不能用来核对 `draft-002`。

`max-steps` 的请求文件没有步数字段。运行这条用例时，程序把上限收成 2。其余用例读 `data/rules.json` 的 `max_tool_calls`，当前是 8。

## 未覆盖与限制

- 缺截止时间没有单独的 `examples/` 目录。`save_plan` 会拒绝，原因是 `missing_deadline`。这条由 `tests/test_graph.py` 用临时请求覆盖，不在七组入口里。
- 跨进程 `--resume` 需要第一次在失败后结束。真实模型在同一次运行里就会再估一次，因此这次没有单独的 `examples/resume-retry/`。同一次运行内的续跑见 `examples/tool-failure/`。
- `rules.json` 的 `constraints` 字符串没有注入系统提示词。对应限制写在工具里，提示词是另一份手写说明。
- `gap` 在文件中是 `3.0`，含义是 8 减 5。
- 重复执行的工具消息是「草稿已存在」。Trace 结束句仍是「草稿已保存，结束。」核对是否重复，以 `draft_id` 和 `message` 为准。
- 工具调用编号含随机片段，Trace 不是逐字节固定的。稳定的是工具顺序、参数、摘要和下一步。
- 七组 `examples/` 均以 `--model api` 的 Trace 为准。Mock 仍可用于无密钥复现流程。
