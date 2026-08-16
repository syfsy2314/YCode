# 子 Agent 系统实现任务

## 文档状态

- 对应 Spec：`docs/features/subagent-system/spec.md`
- 对应 Plan：`docs/features/subagent-system/plan.md`
- 当前状态：已批准（2026-08-16）
- 交付方式：按依赖顺序一次性完成全部任务，不拆分产品阶段
- 实现开始条件：本文件与后续 `checklist.md` 均获明确批准

## 执行规则

1. 严格按任务编号和依赖顺序实施；前置任务验证通过后再进入下一项。
2. 每完成一个任务，先运行该任务列出的验证命令并观察实际结果；失败时先修复再继续。
3. 只实现 Spec 和 Plan 已批准的内容，不增加 OpenAI 适配、任务持久化、排队、重试或生产级可靠性工程。
4. 测试使用 Fake Provider、本地工具与本地 Hook，不调用真实付费 API。
5. 不修改用户现有的无关工作区变更。
6. 最终按 `checklist.md` 执行格式、静态、编译、完整测试和真实终端验收。

## Task 1：扩展请求模型与 Anthropic Fork 缓存前缀

**依赖：** 无

**目标：** 为 Fork 提供不会改写父请求前缀的 continuation 区域，并让 Anthropic 对话前缀具备可观察的 Prompt Cache breakpoint。

**实现内容：**

- [ ] 在 `AgentModelRequest` 增加默认空的 `continuation_messages`，补齐导出、复制和相等性行为。
- [ ] 更新 Token 估算、上下文管理、摘要/外部化路径，使 continuation 被计入且不会静默丢失。
- [ ] 更新 Fake Provider，使测试能够记录完整请求和分类缓存 Token。
- [ ] 调整 Anthropic 序列化顺序为 tools → stable system → messages → supplements → continuation。
- [ ] 在最后一个可复用的历史消息或 supplement 上设置 conversation cache breakpoint，同时保留现有工具和稳定 system 缓存行为。
- [ ] 保持 OpenAI Provider 不变；普通请求 continuation 为空时行为不变。
- [ ] 增加单元测试，覆盖普通请求兼容、序列化顺序、请求复制、Token 估算、cache breakpoint 和 continuation 保留。

**涉及文件：**

- `ycode/core/provider.py`
- `ycode/context/manager.py`
- `ycode/context/artifacts.py`
- `ycode/context/tokens.py`
- `ycode/providers/anthropic.py`
- `tests/support/fake_provider.py`
- `tests/unit/core/test_contracts.py`
- `tests/unit/context/test_manager.py`
- `tests/unit/context/test_artifacts.py`
- `tests/unit/context/test_tokens.py`
- `tests/unit/providers/test_anthropic.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/core/test_contracts.py tests/unit/context/test_manager.py tests/unit/context/test_artifacts.py tests/unit/context/test_tokens.py tests/unit/providers/test_anthropic.py
.venv\Scripts\python.exe -m ruff check ycode/core/provider.py ycode/context ycode/providers/anthropic.py tests/support/fake_provider.py tests/unit/core/test_contracts.py tests/unit/context tests/unit/providers/test_anthropic.py
```

**覆盖：** F14–F18、F24、N1、N2、N5、AC3、AC13。

## Task 2：建立配置、任务模型与角色目录

**依赖：** Task 1

**目标：** 建立 Provider 无关的子 Agent 基础模型，并完成内置角色和项目角色的启动时发现与严格校验。

**实现内容：**

- [ ] 新增 `SubagentConfig`、创建模式、运行模式、状态、角色、调用、错误、用量、任务视图和通知模型。
- [ ] 在现有配置加载路径加入 `max_concurrent` 与异步工具白名单校验，默认并发为 4。
- [ ] 实现 Markdown + YAML Frontmatter 解析、名称规范化和严格字段校验。
- [ ] 校验文件名、正文、模型、工具、名单重叠、权限值和正整数 `max-rounds`；默认轮次为 10。
- [ ] 实现内置优先、项目角色规范化重名全部失效、单个错误角色隔离。
- [ ] 增加 `explore.md`、`plan.md`、`fork.md` 资源并配置 package data；仅前两个进入角色目录。
- [ ] 增加模型、配置、loader 和 catalog 单元测试。

**涉及文件：**

- `ycode/subagents/__init__.py`
- `ycode/subagents/models.py`
- `ycode/subagents/loader.py`
- `ycode/subagents/catalog.py`
- `ycode/subagents/resources/explore.md`
- `ycode/subagents/resources/plan.md`
- `ycode/subagents/resources/fork.md`
- `ycode/config/models.py`
- `ycode/config/loader.py`
- `ycode/config/__init__.py`
- `pyproject.toml`
- `tests/unit/subagents/test_models.py`
- `tests/unit/subagents/test_loader.py`
- `tests/unit/subagents/test_catalog.py`
- `tests/unit/config/test_models.py`
- `tests/unit/config/test_loader.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents/test_models.py tests/unit/subagents/test_loader.py tests/unit/subagents/test_catalog.py tests/unit/config/test_models.py tests/unit/config/test_loader.py
.venv\Scripts\python.exe -m ruff check ycode/subagents/models.py ycode/subagents/loader.py ycode/subagents/catalog.py ycode/config tests/unit/subagents tests/unit/config
```

**覆盖：** F6–F13、F41、N7、N8、N13、AC2。

## Task 3：隔离 Hook Reminder 并补充子任务上下文

**依赖：** Task 2

**目标：** 在共享 Hook 规则、执行器和会话级 once 状态的同时，让父 Agent 与每个子 Agent 独立消费 Reminder。

**实现内容：**

- [ ] 将 Hook Reminder 存储改为按 `scope_id` 隔离，主 Agent 使用稳定 main scope。
- [ ] 为 dispatch 和 reminder 提取增加 scope 参数，并提供任务结束时的 scope 清理。
- [ ] 扩展 Hook 上下文，使子任务可携带 task ID、创建模式、角色和运行模式。
- [ ] 保留全部既有事件类型和 once 共享语义，不改变普通主 Agent Hook 行为。
- [ ] 增加父子 Reminder 隔离、多个子任务隔离、once 共享和上下文字段测试。

**涉及文件：**

- `ycode/hooks/runtime.py`
- `ycode/hooks/context.py`
- `ycode/hooks/__init__.py`
- `tests/unit/hooks/test_runtime.py`
- `tests/unit/hooks/test_context.py`
- `tests/integration/test_hook_agent_flow.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_runtime.py tests/unit/hooks/test_context.py tests/integration/test_hook_agent_flow.py
.venv\Scripts\python.exe -m ruff check ycode/hooks tests/unit/hooks tests/integration/test_hook_agent_flow.py
```

**覆盖：** F50–F54、N2、N3、AC9。

## Task 4：为 AgentLoop 增加可复用运行扩展

**依赖：** Task 1、Task 3

**目标：** 以小范围重构支持 seeded turn、实际请求快照、执行前策略、运行时通知和独立运行作用域。

**实现内容：**

- [ ] 增加 `AgentLoopOptions`、`AgentRequestSnapshot`、`AgentToolScope`、通知源、工具策略和 owner turn 控制协议。
- [ ] 为每个父回合生成稳定 `turn_id`，在最终请求发送前保存不可变快照。
- [ ] 在上下文准备前提取异步通知，并把通知固定在当前回合后续轮次，避免进入未来回合。
- [ ] 在现有 PermissionEngine 和 Hook 之前调用可选 `AgentToolPolicy`；未配置时保持原流程。
- [ ] 支持 seeded turn，使独立循环可以从定义式空历史或 Fork 请求种子运行，不读取 UI 输入。
- [ ] 支持子 Agent 非交互权限语义：后续权限或 Hook 的 `ASK` 可由运行选项转换为拒绝工具结果。
- [ ] 保持 Provider 所有权可配置，借用 Provider 的子循环不得自行关闭它。
- [ ] 更新 Agent 事件、导出和单元测试，覆盖快照时机、通知边界、策略顺序、seeded turn、取消和既有主流程兼容。

**涉及文件：**

- `ycode/agent/contracts.py`
- `ycode/agent/events.py`
- `ycode/agent/loop.py`
- `ycode/agent/__init__.py`
- `ycode/tools/contracts.py`
- `ycode/tools/__init__.py`
- `ycode/prompt/models.py`
- `ycode/prompt/runtime.py`
- `ycode/prompt/__init__.py`
- `tests/unit/agent/test_contracts.py`
- `tests/unit/agent/test_loop.py`
- `tests/unit/tools/test_contracts.py`
- `tests/unit/prompt/test_models.py`
- `tests/unit/prompt/test_runtime.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/agent tests/unit/tools/test_contracts.py tests/unit/prompt/test_models.py tests/unit/prompt/test_runtime.py
.venv\Scripts\python.exe -m ruff check ycode/agent ycode/tools/contracts.py ycode/prompt tests/unit/agent tests/unit/tools/test_contracts.py tests/unit/prompt
```

**覆盖：** F14、F19–F21、F25、F45–F49、F50、N2、N3、N6、AC4、AC8、AC13。

## Task 5：实现子 Agent 工具策略与权限收窄

**依赖：** Task 2、Task 4

**目标：** 把防嵌套、防扩权、角色限制、异步限制和父权限上限落实为执行前硬检查。

**实现内容：**

- [ ] 实现全局拒绝 `run_subagent` 和运行时扩权 Skill 工具。
- [ ] 实现 Fork 父有效工具集合与定义式基础集合。
- [ ] 按白名单后黑名单顺序应用角色过滤，固定 `explore`、`plan` 为只读。
- [ ] 对异步任务叠加全局异步白名单；外部/MCP 工具必须显式配置且原本已继承。
- [ ] Fork 使用父权限模式值和空白权限记录；定义式取父模式与角色模式中更严格者。
- [ ] 对 plan-only 父任务应用不可覆盖的只读硬上限。
- [ ] 保证策略拒绝、权限 `ASK` 和 Hook `ASK` 都作为拒绝工具结果返回模型继续，不显示审批 UI。
- [ ] 增加工具可见但执行拒绝、父写工具可执行、角色不能提权和各层不可覆盖拒绝测试。

**涉及文件：**

- `ycode/subagents/policy.py`
- `ycode/subagents/models.py`
- `ycode/security/models.py`（仅在需要复用权限比较辅助函数时修改）
- `tests/unit/subagents/test_policy.py`
- `tests/unit/security/test_models.py`（仅在修改对应辅助函数时修改）

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents/test_policy.py tests/unit/security/test_models.py tests/unit/security/test_engine.py
.venv\Scripts\python.exe -m ruff check ycode/subagents/policy.py ycode/subagents/models.py ycode/security tests/unit/subagents/test_policy.py tests/unit/security
```

**覆盖：** F27–F37、N4、AC6。

## Task 6：实现 Provider 池与跑到底 Runner

**依赖：** Task 1–Task 5

**目标：** 使用独立运行状态和现有 AgentLoop 执行定义式及 Fork 子任务，生成正确终态与独立 Token 用量。

**实现内容：**

- [ ] 实现 `SubagentProviderPool`：借用父/当前 Provider，按名称延迟创建并复用 Anthropic Provider，区分关闭所有权。
- [ ] 实现定义式请求：空消息历史、基础行为、角色正文、任务和角色最大轮次。
- [ ] 实现 Fork 请求：复制父快照前缀，在 continuation 追加 `fork.md` 强制指令和任务，固定异步且最大轮次为 10。
- [ ] Fork 首轮上下文超限时明确失败；后续只允许压缩 continuation。
- [ ] 为每个任务创建独立消息、上下文、权限会话、读取状态、Hook scope 和 Token 累加器。
- [ ] 触发除 session start/end 外的既有 Agent/消息/工具/上下文/错误 Hook 事件并携带任务元数据。
- [ ] 实现 completed、failed、cancelled、limit_reached、empty_result、异常停止和最后文本选择。
- [ ] 累计输入、输出、cache creation、cache read Token，不计入父 Agent。
- [ ] 增加 Provider 所有权、定义式/Fork 种子、跑到底循环、工具错误恢复、终态和统计测试。

**涉及文件：**

- `ycode/subagents/providers.py`
- `ycode/subagents/runner.py`
- `ycode/subagents/__init__.py`
- `ycode/context/manager.py`（仅增加 continuation 压缩边界）
- `tests/unit/subagents/test_providers.py`
- `tests/unit/subagents/test_runner.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents/test_providers.py tests/unit/subagents/test_runner.py tests/unit/agent/test_loop.py tests/unit/context/test_manager.py
.venv\Scripts\python.exe -m ruff check ycode/subagents/providers.py ycode/subagents/runner.py ycode/context/manager.py tests/unit/subagents/test_providers.py tests/unit/subagents/test_runner.py
```

**覆盖：** F12、F14–F26、F27–F28、F33、F38、F50–F54、N1、N3、N5、N8、AC3–AC6、AC9。

## Task 7：实现统一任务管理、结果格式和通知

**依赖：** Task 6

**目标：** 统一管理同步与异步任务，落实并发、状态、通知、查询和取消归属。

**实现内容：**

- [ ] 实现两阶段绑定的会话级 `SubagentManager`。
- [ ] 实现调用校验：非空任务、角色存在、模式合法、定义式默认同步、Fork 强制异步。
- [ ] 为同步/异步任务统一登记 ID、参数、owner turn、状态、结果、错误、用量和时间。
- [ ] 实现默认 4 的合并并发上限、超限立即失败、不排队和终态释放名额。
- [ ] 同步任务等待 Runner 并直接返回结果；异步任务立即返回 `running` 并由 `asyncio.Task` 继续。
- [ ] 只为异步终态生成一次通知，按完成时间排序并支持安全边界批量提取。
- [ ] 实现完整 ID/唯一前缀查询、指定任务停止、按 owner turn 取消、全部取消和记录清理。
- [ ] 集中实现工具结果、任务详情、任务列表、停止提示和通知格式。
- [ ] 增加并发、状态转换、通知一次性/顺序、取消边界和统一字段测试。

**涉及文件：**

- `ycode/subagents/manager.py`
- `ycode/subagents/formatting.py`
- `ycode/subagents/models.py`
- `tests/unit/subagents/test_manager.py`
- `tests/unit/subagents/test_formatting.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents/test_manager.py tests/unit/subagents/test_formatting.py
.venv\Scripts\python.exe -m ruff check ycode/subagents/manager.py ycode/subagents/formatting.py ycode/subagents/models.py tests/unit/subagents/test_manager.py tests/unit/subagents/test_formatting.py
```

**覆盖：** F1–F5、F22–F24、F38–F49、F57–F61、N6–N9、AC1、AC4、AC7、AC8、AC11、AC12。

## Task 8：接入统一工具与应用启动路径

**依赖：** Task 2、Task 7

**目标：** 注册稳定的 `run_subagent` 工具并按批准顺序装配角色、Runner、Manager、主 Agent 和共享基础设施。

**实现内容：**

- [ ] 新增 `run_subagent` 工具，固定 `task`、可选 `role`、可选 `mode` Schema，角色名不使用动态枚举。
- [ ] 将工具访问级别设为 `READ`，调用现有安全/Hook 审批后再进入管理器。
- [ ] 把 Manager 和父请求快照/turn scope 接入工具上下文。
- [ ] 在应用启动时完成：配置 → 注册表 → 未绑定 Manager → 工具 → 安全/Hook → 角色 → Provider 池/Runner 绑定 → 主循环/会话 → MCP/UI。
- [ ] 在关闭时按：子任务 → session/Hook → MCP → 命名子 Provider → 主 Provider/上下文收尾。
- [ ] 启动扫描只读取项目 `.ycode/agents/*.md`，角色错误不阻止主应用。
- [ ] 未调用子 Agent 时保持原启动、工具、Skill、MCP 和主对话行为。
- [ ] 增加工具参数、Schema、READ 权限、两阶段绑定、角色错误隔离、启动和关闭顺序测试。

**涉及文件：**

- `ycode/tools/builtin/run_subagent.py`
- `ycode/tools/builtin/__init__.py`
- `ycode/tools/__init__.py`
- `ycode/tools/contracts.py`
- `ycode/app.py`
- `tests/unit/tools/test_run_subagent.py`
- `tests/unit/test_app.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/tools/test_run_subagent.py tests/unit/tools/test_contracts.py tests/unit/test_app.py
.venv\Scripts\python.exe -m ruff check ycode/tools/builtin/run_subagent.py ycode/tools ycode/app.py tests/unit/tools/test_run_subagent.py tests/unit/test_app.py
```

**覆盖：** F1–F6、F10、F26、F30、F31、F39、N2、N8、N9、AC1、AC2、AC6、AC13。

## Task 9：接入 `/tasks`、取消按键和会话生命周期

**依赖：** Task 7、Task 8

**目标：** 提供任务查看/终止命令，并让 ESC、Ctrl+C、clear、restore 和 exit 按批准边界清理任务。

**实现内容：**

- [ ] 实现 `/tasks`、`/tasks <id>`、`/tasks stop <id>`，直接读取管理器且不进入模型历史。
- [ ] 展示 ID、状态、模式、角色、运行时长、分类 Token、结果/错误和起止时间。
- [ ] 明确处理空列表、无匹配、多匹配、终态停止和运行任务停止。
- [ ] 让 InputBox 同时识别 ESC 和 Ctrl+C，并统一为取消当前回合。
- [ ] 取消当前 `AgentTurnStream` 后，级联取消相同 `owner_turn_id` 的同步和异步任务。
- [ ] 保证取消新回合不影响此前正常父回合遗留的异步任务。
- [ ] clear、restore 其他会话和 exit 时取消全部当前会话任务、等待基本收尾并清空记录。
- [ ] 增加命令、输入按键、父回合取消、会话边界和非阻塞完成提示测试。

**涉及文件：**

- `ycode/commands/contracts.py`
- `ycode/commands/builtin.py`
- `ycode/session/chat.py`
- `ycode/ui/input_box.py`
- `ycode/ui/terminal.py`
- `tests/unit/commands/test_builtin.py`
- `tests/unit/commands/test_contracts.py`
- `tests/unit/session/test_chat.py`
- `tests/unit/ui/test_input_box.py`
- `tests/unit/ui/test_terminal.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands tests/unit/session/test_chat.py tests/unit/ui/test_input_box.py tests/unit/ui/test_terminal.py
.venv\Scripts\python.exe -m ruff check ycode/commands ycode/session/chat.py ycode/ui/input_box.py ycode/ui/terminal.py tests/unit/commands tests/unit/session/test_chat.py tests/unit/ui/test_input_box.py tests/unit/ui/test_terminal.py
```

**覆盖：** F44–F48、F55–F61、N6、N7、N9、AC8、AC10–AC12。

## Task 10：完成跨模块集成测试

**依赖：** Task 1–Task 9

**目标：** 用本地替身验证定义式、Fork、权限、Hook、缓存、通知、隔离和生命周期的完整调用链。

**实现内容：**

- [ ] 新增定义式同步测试：空历史、角色 Prompt、工具循环和统一结果。
- [ ] 新增定义式异步测试：立即返回、后台终态、下一请求通知且只注入一次。
- [ ] 新增 Fork 异步测试：父请求前缀逐项相同、不含未完成 tool call、task 只在 continuation。
- [ ] 在受控 Fake Anthropic 场景验证 conversation cache read 用量。
- [ ] 验证 Fork 保留父写工具定义并可执行允许的写工具，同时拒绝嵌套和越权调用。
- [ ] 验证父/角色权限收窄、plan-only 只读、异步外部工具白名单和所有 `ASK` 自动拒绝。
- [ ] 验证多任务消息、权限、读取状态、上下文、Reminder 和 Token 互不串扰，Hook once 共享。
- [ ] 验证并发超限、指定停止、owner turn 取消、clear/restore/exit 收尾。

**涉及文件：**

- `tests/integration/test_subagent_flow.py`
- `tests/integration/test_hook_agent_flow.py`
- `tests/support/fake_provider.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py
.venv\Scripts\python.exe -m ruff check tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py tests/support/fake_provider.py
```

**覆盖：** AC1–AC14。

## Task 11：完成真实终端验收与全量回归

**依赖：** Task 10

**目标：** 在真实交互终端验证用户主流程和取消边界，并完成仓库级功能回归。

**实现内容：**

- [ ] 扩展终端 E2E，覆盖定义式同步、定义式异步和 Fork 异步。
- [ ] 验证异步非阻塞提示、下一安全请求通知、`/tasks` 列表/详情/停止。
- [ ] 验证 ESC、Ctrl+C 对当前父回合拥有的同步/异步任务级联取消。
- [ ] 验证此前正常父回合的异步任务不被新回合取消。
- [ ] 验证 clear 和退出取消任务并清除记录。
- [ ] 运行格式、静态、编译、完整测试和真实终端用例；只修复本功能造成的回归。
- [ ] 记录所有实际命令、通过数量、失败项目和环境限制，供 checklist 最终验收使用。

**涉及文件：**

- `tests/e2e/test_terminal_chat.py`
- 本功能前述实现和测试文件（仅修复验证发现的问题）

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

**覆盖：** N10–N13、AC15、AC16，以及 AC1–AC14 的完整回归。

## 任务依赖总览

```text
Task 1 请求/缓存 ───────────────┐
                                ├─→ Task 4 AgentLoop 扩展 ─→ Task 5 策略 ─┐
Task 2 模型/角色 ─→ Task 3 Hook ┘                                      │
Task 2 模型/角色 ────────────────────────────────────────────────────────┤
                                                                         ▼
                                                               Task 6 Runner
                                                                         │
                                                                         ▼
                                                               Task 7 Manager
                                                                         │
                                        ┌────────────────────────────────┴────┐
                                        ▼                                     ▼
                              Task 8 工具/启动                         Task 9 命令/取消
                                        └────────────────┬────────────────────┘
                                                         ▼
                                                Task 10 集成测试
                                                         │
                                                         ▼
                                                Task 11 E2E/回归
```

实际执行保持编号顺序；依赖图只用于说明设计依赖，不授权跳过前置验证。

## Spec 与验收覆盖

| 范围 | 主要任务 |
|---|---|
| F1–F13 | Task 2、Task 7、Task 8 |
| F14–F18 | Task 1、Task 4、Task 6、Task 10 |
| F19–F28 | Task 4、Task 6、Task 7 |
| F29–F37 | Task 5、Task 6、Task 10 |
| F38–F49 | Task 4、Task 7、Task 9、Task 10 |
| F50–F54 | Task 3、Task 4、Task 6、Task 10 |
| F55–F61 | Task 7、Task 9、Task 10、Task 11 |
| N1–N9 | Task 1–Task 10 |
| N10–N13 | Task 10、Task 11 |
| AC1–AC14 | Task 1–Task 10 |
| AC15–AC16 | Task 11 |

## 完成条件

全部 Task 的实现内容和局部验证均完成，Task 11 的全量命令取得实际结果，并逐项满足后续已批准 `checklist.md` 后，本功能才可标记完成。任务预算或单次执行时间不构成跳过功能或验证的理由。
