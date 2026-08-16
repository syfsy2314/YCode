# 子 Agent 系统最终验收清单

## 文档状态

- 对应 Spec：`docs/features/subagent-system/spec.md`
- 对应 Plan：`docs/features/subagent-system/plan.md`
- 对应 Task：`docs/features/subagent-system/task.md`
- 当前状态：已批准（2026-08-16）
- 使用时机：全部开发任务完成后逐项执行并记录实际结果

## 验收规则

1. 只有实际执行并观察到结果后才能勾选，不根据代码阅读或预期推断通过。
2. 任一必选项失败时，先修复并重新验证；不得以剩余时间或实现成本为由跳过。
3. 测试只使用本地替身和测试凭据，不调用真实付费 API。
4. 不进行压力测试、性能基准、长时间稳定性、大规模并发、复杂故障注入或多平台矩阵。
5. 最终报告必须列出实际运行命令、通过数量、失败项目、未执行项目及原因。

## A. 文档、范围与代码边界

- [ ] A1. 实现只覆盖已批准的 Spec、Plan 和 Task，没有增加未批准的产品行为。
- [ ] A2. `spec.md`、`plan.md`、`task.md` 和本清单之间不存在已知冲突。
- [ ] A3. 新增 Python 标识符使用英文，新增注释使用简洁中文，并遵循仓库现有结构。
- [ ] A4. 本期只实现 Anthropic 子 Agent 和 Fork 行为，没有修改 OpenAI 子 Agent 协议路径。
- [ ] A5. 没有加入任务持久化、跨进程恢复、排队、优先级、自动重试、分布式调度或生产监控。
- [ ] A6. 没有加入角色热重载、额外角色搜索路径、角色市场、`verify`、`general` 或子 Agent 嵌套。
- [ ] A7. 没有实现同步/异步运行中转换、超时转后台或快捷键转后台。
- [ ] A8. 没有迁移或重写现有隔离 Skill Runner、MCP 协议、工具 Scheduler/Executor 核心模型和会话持久化格式。
- [ ] A9. 用户在开发前已有的无关工作区变更未被覆盖或清理。

## B. 统一工具与角色系统（AC1、AC2）

- [ ] B1. `run_subagent` Schema 包含必填非空 `task`、可选 `role`、可选 `mode`，且 `mode` 只接受 `sync`、`async`。
- [ ] B2. 工具 Schema 不把当前角色名写入动态枚举，角色增删不改变 Schema。
- [ ] B3. 指定角色且省略 `mode` 时创建定义式同步任务；显式 `async` 时创建定义式异步任务。
- [ ] B4. 省略角色时创建 Fork 异步任务；显式 `sync` 返回明确参数错误。
- [ ] B5. 空任务、非法模式、不存在或不可用角色均返回明确错误，且不创建运行任务。
- [ ] B6. 同步或异步在创建后不可改变，长时间同步任务不会自动转异步。
- [ ] B7. 只扫描项目根目录 `.ycode/agents/*.md`，不扫描用户目录、父目录或嵌套目录。
- [ ] B8. 合法 Markdown + YAML Frontmatter 角色能应用 name、description、正文、model、allowed-tools、denied-tools、max-rounds 和 permission。
- [ ] B9. 文件名/名称不一致、未知字段、未知模型、未知工具、名单重叠、非法轮次和空正文会使对应角色不可用。
- [ ] B10. 单个损坏角色不会阻止其他角色、主 Agent 或应用启动。
- [ ] B11. 规范化重名的项目角色全部不可用，项目角色不能覆盖内置角色。
- [ ] B12. 内置 `explore`、`plan` 可用且始终只读；不存在内置 `verify` 或 `general`。
- [ ] B13. 角色在启动时生成不可变快照，运行中修改文件不会改变已创建任务。

## C. Fork 请求与 Prompt Cache（AC3）

- [ ] C1. Fork 使用产生当前工具调用的实际父模型请求快照。
- [ ] C2. Fork 前缀不包含父模型尚未完成的 assistant `run_subagent` 工具调用。
- [ ] C3. 首次 Fork 请求逐项保留父请求的模型、工具定义及顺序、稳定 system、历史消息和 supplements。
- [ ] C4. Fork 强制工作规范和具体任务只追加在 `continuation_messages`，不改写继承前缀。
- [ ] C5. 强制规范禁止继续创建子 Agent、主动对话和请求确认，并要求直接使用工具完成任务。
- [ ] C6. 强制规范要求结果正文约 1000 汉字以内，并使用“结论、证据、风险/待办”结构。
- [ ] C7. Anthropic 线序列化顺序为 tools → stable system → messages → supplements → continuation。
- [ ] C8. 父请求最后一个可复用历史消息或 supplement 带有 conversation cache breakpoint，Fork 保留相同断点。
- [ ] C9. 在满足缓存条件的受控测试中，首次 Fork 请求记录 cache read Token。
- [ ] C10. 缓存条件不满足时 Fork 仍可执行，不被误判为任务失败。
- [ ] C11. 首次 Fork 新增任务导致上下文超限时明确失败，不压缩或改写父前缀。
- [ ] C12. Fork 后续轮次只追加或压缩 continuation，不改写继承前缀。

## D. 执行循环、结果与 Token（AC4）

- [ ] D1. 子 Agent 从参数任务直接开始，不等待 UI 用户输入。
- [ ] D2. 模型返回工具调用时执行工具并继续，普通工具错误作为工具结果返回模型继续处理。
- [ ] D3. 模型只返回正常文本且无工具调用时进入 `completed`。
- [ ] D4. 模型同时返回文本和工具调用时不会提前结束，执行工具后继续循环。
- [ ] D5. 模型既无文本也无工具调用时进入 `failed`，错误码为 `empty_result`。
- [ ] D6. 达到最大轮次时进入 `limit_reached` 并保留最后一段可用文本。
- [ ] D7. Provider、内容过滤和输出上限等异常停止进入 `failed`。
- [ ] D8. 用户、父回合或任务命令取消时进入 `cancelled`。
- [ ] D9. 定义式使用角色 `max-rounds`，省略为 10；Fork 固定使用默认 10。
- [ ] D10. `result` 使用最后一条 assistant 文本，不要求模型生成 JSON，也不机械截断。
- [ ] D11. 同步结果、异步详情和通知均包含 task_id、status、creation_mode、role、result、usage、started_at、finished_at、error。
- [ ] D12. 每个任务独立累计输入、输出、cache creation、cache read Token，父 Agent 统计不包含子任务用量。

## E. 状态隔离、共享与工具安全（AC5、AC6）

- [ ] E1. 父 Agent 与每个子 Agent 的消息历史互不修改。
- [ ] E2. 父子权限会话、allow-once 和临时授权记录相互隔离。
- [ ] E3. 父子上下文压缩状态、文件读取状态、Reminder 和 Token 统计相互隔离。
- [ ] E4. 父子共享项目文件系统、工具执行基础设施、Hook 引擎和已建立的外部连接。
- [ ] E5. Fork 借用父 Provider，不为同配置重复创建连接，也不由子循环关闭借用实例。
- [ ] E6. 定义式指定命名模型时复用会话级 Anthropic Provider；池拥有的实例只关闭一次。
- [ ] E7. Fork 保留父工具定义可见性，执行基础集合来自父当前有效工具集，包括允许的标准写入和命令工具。
- [ ] E8. 所有子 Agent 在执行前无条件拒绝 `run_subagent`，即使该工具仍对 Fork 可见。
- [ ] E9. 所有子 Agent 在执行前拒绝会安装、加载或激活运行时能力的扩权 Skill 工具。
- [ ] E10. 定义式角色白名单先收窄基础集合，黑名单随后排除，黑名单不能被权限模式覆盖。
- [ ] E11. 异步任务叠加全局异步白名单；外部/MCP 工具还必须在继承集合和显式白名单中。
- [ ] E12. Fork 继承父当前权限模式的值，但使用空白权限记录。
- [ ] E13. 定义式取父当前模式与角色 permission 中更严格者，角色不能提升父权限。
- [ ] E14. 父 Agent 为 plan-only 时，全部定义式和 Fork 子 Agent 都受不可覆盖的只读上限。
- [ ] E15. 工作区、命令安全、全局禁止、角色黑名单、异步白名单和 Hook `DENY` 均在工具执行前生效。
- [ ] E16. 子 Agent 中任意最终 `ASK` 都自动转为拒绝工具结果，不显示审批 UI、不暂停、不自动批准。
- [ ] E17. 策略或权限拒绝不会直接终止任务，模型可以换用其他方案。

## F. 同步、异步、并发与通知（AC7、AC8）

- [ ] F1. 同步任务由父工具调用持续等待统一终态结果，不因时间阈值转换模式。
- [ ] F2. 异步任务立即返回 task ID 和 `running`，主对话可继续处理。
- [ ] F3. 同步与异步任务登记在同一个会话级管理器，使用统一状态和统计。
- [ ] F4. 默认合并并发上限为 4，配置为其他正整数时生效。
- [ ] F5. 达到上限时新任务立即失败且不排队；任务进入终态后立即释放名额。
- [ ] F6. 管理器记录任务参数、模式、角色、状态、结果/错误、分类 Token 和起止时间。
- [ ] F7. 异步任务进入终态后只生成一次结构化通知；同步任务不生成异步通知。
- [ ] F8. 通知不会取消、修改或重启正在流式传输的父模型请求。
- [ ] F9. 当前父回合还有下一次请求时，在该请求边界注入；否则保留到下一次用户消息触发的请求。
- [ ] F10. 通知不会主动启动新父回合，UI 提示不等同于模型通知注入。
- [ ] F11. 多个通知按完成时间排序并批量注入，每个任务最多注入一次。
- [ ] F12. 已注入通知只保留在当前父回合后续轮次，不进入未来回合。

## G. Hook 行为（AC9）

- [ ] G1. 子 Agent 触发现有回合、消息、工具、上下文压缩和 Agent 错误事件。
- [ ] G2. 子 Agent 不重复触发 session start 和 session end。
- [ ] G3. 子 Agent Hook 上下文包含 task ID、defined/fork、角色和 sync/async。
- [ ] G4. 父子共享 Hook 规则、执行器和会话级 once 命中状态。
- [ ] G5. 父 Agent 与每个子任务使用独立 Reminder scope，任何一方都不能消费其他 scope 的 Reminder。
- [ ] G6. 子任务终态后清理自己的 Reminder scope。
- [ ] G7. Hook `DENY` 保持拒绝，Hook `ASK` 在子 Agent 中自动拒绝。

## H. 命令、取消与会话生命周期（AC10–AC12）

- [ ] H1. `/tasks` 列出当前会话全部任务，并展示 ID、状态、创建模式、角色、运行时长和总 Token。
- [ ] H2. `/tasks <id>` 展示任务参数、完整结果/错误、分类 Token 和起止时间。
- [ ] H3. `/tasks stop <id>` 能取消唯一匹配的运行任务并标记 `cancelled`。
- [ ] H4. 空列表、无匹配、多匹配和终态任务停止均返回明确提示，不误取消其他任务。
- [ ] H5. `/tasks` 命令不写入模型历史，也不触发模型请求。
- [ ] H6. ESC 和 Ctrl+C 都取消当前父回合，并级联取消该回合创建且仍在运行的同步、异步任务。
- [ ] H7. 不存在活动父回合时，ESC/Ctrl+C 不会清除此前正常回合的后台任务。
- [ ] H8. 取消新父回合不会影响此前已经正常结束父回合遗留的异步任务。
- [ ] H9. clear 当前会话会取消全部当前会话任务并清除记录和待注入通知。
- [ ] H10. restore 其他会话前会取消并清理原当前会话任务。
- [ ] H11. exit 会取消运行任务并按设计顺序关闭子 Provider、主 Provider 和共享资源。
- [ ] H12. 程序重启后不会恢复旧任务或历史任务记录。
- [ ] H13. 取消不会声称回滚子 Agent 已经完成的文件系统修改。

## I. 兼容性与错误隔离（AC13、AC14）

- [ ] I1. 未调用子 Agent 时，主对话请求、流式输出、工具、权限、上下文、Skill、Hook、MCP、命令和会话行为保持正常。
- [ ] I2. 普通请求的 `continuation_messages` 为空，Anthropic 新序列化不会改变既有消息语义。
- [ ] I3. 核心角色、任务和管理模型不依赖 Anthropic SDK 类型。
- [ ] I4. 一个子任务失败不会阻止主 Agent 或其他合法子任务继续运行。
- [ ] I5. 管理器尚未绑定时调用工具返回明确未就绪错误，不导致应用崩溃。
- [ ] I6. 所有功能测试使用 Fake Provider、本地 Hook、工具和 MCP 替身，不调用真实付费 API。
- [ ] I7. 验证范围未扩展到压力、性能、长稳、大规模并发、复杂竞态、故障注入或多平台矩阵。

## J. 真实终端端到端验收（AC15、AC16）

- [ ] J1. 在真实终端完成定义式同步任务，并看到最终统一结果。
- [ ] J2. 在真实终端创建定义式异步任务，主对话不被阻塞，任务完成后通知在下一安全请求边界生效。
- [ ] J3. 在真实终端创建 Fork 异步任务，并通过任务详情取得结果与 Token 用量。
- [ ] J4. 在真实终端使用 `/tasks`、`/tasks <id>`、`/tasks stop <id>` 完成列表、详情和终止主流程。
- [ ] J5. 在真实终端验证 ESC 取消当前父回合及其同步/异步子任务。
- [ ] J6. 在真实终端验证 Ctrl+C 具有与 ESC 相同的取消边界。
- [ ] J7. 在真实终端验证取消当前回合不会影响此前正常回合遗留的异步任务。
- [ ] J8. 在真实终端验证 clear 和 exit 会取消并清理当前会话任务。

## K. 自动化验证命令

### K1. 子 Agent 单元测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents tests/unit/tools/test_run_subagent.py
```

- [ ] 命令已实际执行。
- [ ] 结果通过；通过/失败数量已记录。

### K2. 受影响模块单元测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/agent tests/unit/config tests/unit/context tests/unit/hooks tests/unit/prompt tests/unit/providers/test_anthropic.py tests/unit/security tests/unit/session/test_chat.py tests/unit/commands tests/unit/tools tests/unit/ui/test_input_box.py tests/unit/ui/test_terminal.py tests/unit/test_app.py
```

- [ ] 命令已实际执行。
- [ ] 结果通过；通过/失败数量已记录。

### K3. 集成测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py
```

- [ ] 命令已实际执行。
- [ ] 结果通过；通过/失败数量已记录。

### K4. 真实终端 E2E

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py
```

- [ ] 命令已实际执行。
- [ ] 结果通过；通过/失败数量已记录。

### K5. 格式检查

```powershell
.venv\Scripts\python.exe -m ruff format --check .
```

- [ ] 命令已实际执行。
- [ ] 结果通过。

### K6. 静态检查

```powershell
.venv\Scripts\python.exe -m ruff check .
```

- [ ] 命令已实际执行。
- [ ] 结果通过。

### K7. 编译检查

```powershell
.venv\Scripts\python.exe -m compileall -q ycode tests
```

- [ ] 命令已实际执行。
- [ ] 结果通过。

### K8. 完整测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

- [ ] 命令已实际执行。
- [ ] 结果通过；通过/失败/跳过数量已记录。

## L. 最终结果记录

实施完成后在最终报告中记录，不预填结果：

| 验证项 | 实际命令 | 实际结果 | 备注 |
|---|---|---|---|
| 子 Agent 单元测试 | K1 | 未执行 | 实施后填写 |
| 受影响模块单元测试 | K2 | 未执行 | 实施后填写 |
| 集成测试 | K3 | 未执行 | 实施后填写 |
| 真实终端 E2E | K4 | 未执行 | 实施后填写 |
| 格式检查 | K5 | 未执行 | 实施后填写 |
| 静态检查 | K6 | 未执行 | 实施后填写 |
| 编译检查 | K7 | 未执行 | 实施后填写 |
| 完整测试 | K8 | 未执行 | 实施后填写 |

## 完成判定

以下条件必须同时满足：

- [ ] Task 1–Task 11 的实现内容全部完成，且每项局部验证实际通过。
- [ ] A–J 的功能、范围和端到端项目全部实际通过。
- [ ] K1–K8 全部实际执行并通过。
- [ ] 没有已知未报告失败、跳过的必选项或未说明的环境限制。
- [ ] 最终报告准确列出验证结果和仍存在的限制。

全部条件满足后，子 Agent 系统功能才可标记完成。
