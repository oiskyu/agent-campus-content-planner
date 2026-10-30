# 04 - AI 使用（AI Usage）

本文档记录本题开发中 AI 辅助的范围、个人承担的部分，以及如何验证生成结果。评审可对照仓库文件与 `examples/` 中的 Trace。

## AI 辅助的范围

编码智能体按当次对话生成并修改了项目代码、合成数据、测试、`examples/` 运行结果和文档。`pytest` 默认走 Mock。七组验收 Trace 已用 `--model api` 对 OpenAI 兼容接口跑过。

AI 生成过的内容：

- `src/`：四个工具、LangGraph 循环、Mock 与 OpenAI 兼容入口、CLI、输出与 Trace
- `data/`：素材、价目、规则、入口请求
- `tests/`：工具、图、契约与模型入口测试
- `examples/`：七组 `input.json`、`output.json`、`trace.json`（由 `python -m src.main --case ...` 跑出，不是手写）
- `README.md` 与 `docs/01` 至 `05`

生成结果不默认可信，以下决策和纠正由候选人给出，代码按这些要求改过。

## 候选人承担的部分

选题与范围。从四道题中选定第 2 题。运行方式定为 CLI，编排用 LangGraph 做循环骨架，决策按 ReAct：模型选动作，工具执行，模型再决定下一步或结束。不做界面、多 Agent、线上部署。材料自编，交付物是可运行的 GitHub 仓库。

决策边界。模型选择工具、是否从标准方案改到轻量方案、失败后停止还是再试。费用相加、事实校验、能否保存、草稿幂等放在工具里。路由只判断有没有工具调用、是否到达步数上限，不把「合计大于预算就改 lite」写成边上的条件。

提示词架构。系统提示词是 Agent 说明，不是工具清单。规定六段结构：角色、任务边界、行为规则、工具使用、输出格式、少量示例。结束回复用中文，不规定固定结果 JSON。初稿曾把提示词改成工具 Schema，已否定并改回该架构。正文见 `src/prompts.py`。

工具架构。提示词与工具定义分开。每个工具必须有唯一名称、说明和 JSON Schema 参数，并带校验、超时、权限、统一返回和可给模型改下一步的错误提示，不能只写一段文字描述。实现见 `src/tools.py`。

数据形态。入口与合成数据按指定字段编写：素材含 `id`、`theme`、`facts`、`platform`、`hours`；价目分项相加得到 12 和 8，禁止在代码里写死这两个数；规则含约束和 `failure_inject`；入口请求收成 7 条，只保留 `case_name`、`theme`、`budget`、`deadline_hours`，缺失写 `null`，不再每条重复 `fail_tools` 和 `max_steps`。

模型入口。默认本地 Mock，离线、不需要密钥。另留 OpenAI 兼容 API，用 `--model` 切换。没有密钥时不准用 Mock 结果冒充真实模型决策。

验收与本地操作。要求 Trace 由 Agent 跑出来，不要手写。本地用本机 Python 和 `pytest`，不必 conda，不必把测试发到云端。提交方式是 GitHub 仓库加题号和 commit SHA。命令行按用例名复用检查点、把失败步骤叠进后续用例时，要求改成日常验收开新会话。

文档。要求按考核栏目写 `docs/01` 到 `05`，并按本人实际分工改 AI 使用记录，不套用其他仓库的贡献表述。

【在此补上本人实际用时，以及还改过、但上文未列到的细节。用时同步写在 `docs/05-retrospective.md`。】

## 验证方式

- `pytest`：当前 27 项通过。核对分项求和、拒绝保存、失败步骤仍为 `failed`、预算 10 时从 12 改选 8、缺口 3、重复草稿、步数上限、工具契约和入口解析。
- 命令行：七组均用 `--model api` 跑过。`normal` 保存标准方案 12；`missing-info` 缺预算停止；`tool-failure` 第一次估价失败后模型再次调用并保存 12；`budget-overrun` 看到 12 后改选 lite 保存 8；`no-feasible-plan` 缺口 3；`duplicate` 仍返回 `draft-002`；`max-steps` 两步后停止。
- Trace：预算 20 在估价 12 后保存；预算 10 在估价 12 后改选 lite；估价失败后下一步再次调用 `estimate_cost`。
- 复现注意：`duplicate` 须接在 `budget-overrun` 之后；日常不要带 `--thread-id`。

## 边界声明

- 不提交真实密钥。仓库只有 `.env.example`。
- Mock 验证流程和工具协议。七组 `examples/` 以 api Trace 为准。密钥不进仓库。
