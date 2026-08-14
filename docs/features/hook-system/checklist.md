# YCode Hook 系统 Checklist

> 本清单只验证功能性实验范围内的可观察行为，不执行压力、性能、长时间稳定性、复杂
> 故障注入、多平台矩阵或真实付费 API 验证。

## 配置与规则

- [ ] 从项目子目录启动时加载最近的 `.ycode/hooks.yaml`；文件不存在时使用空规则并正常
  启动。（验证：运行 `tests/unit/hooks/test_config.py` 的发现和缺失配置场景）
- [ ] YAML 无法解析或顶层结构非法时，整份配置不加载但应用继续启动。（验证：运行配置
  文件级错误测试并检查返回空规则和诊断）
- [ ] 单条规则非法时只跳过该规则；重复 ID 保留第一条，其余合法规则保持声明顺序。
  （验证：运行逐条降级和重复 ID 单元测试）
- [ ] 配置诊断包含路径、规则序号、可识别的规则 ID 和具体字段错误。（验证：断言
  HookDiagnostic 的可观察字段）
- [ ] 已启用 Shell 或 HTTP 动作时，启动终端只显示一次外部操作风险提示。（验证：运行
  App 单元测试和真实终端启动场景）
- [ ] `enabled` 省略时默认为 `true`；`enabled: false` 的规则不求值条件、不执行动作、
  不产生权限决定。（验证：运行模型与 HookRuntime 禁用规则测试）
- [ ] 权限决定用于错误事件、权限动作异步执行、Reminder 异步执行、Reminder 用于
  `session.end` 等非法组合，只导致对应规则被跳过。（验证：运行规则组合约束测试）

## 条件、上下文与模板

- [ ] 无条件、`all` 和 `any` 规则产生约定结果；同一规则不能同时声明 `all` 与 `any`。
  （验证：运行 `tests/unit/hooks/test_models.py` 和 `test_matching.py`）
- [ ] `exact`、`glob`、`regex` 和包装正向匹配器的 `not` 能匹配代表性输入。（验证：运行
  四种操作符参数化单元测试）
- [ ] 点路径能访问嵌套对象和数组元素；字段缺失时，包括 `not`，都不匹配。（验证：运行
  Missing、JSON null、嵌套数组测试）
- [ ] 生命周期事件上下文包含公共事件、项目、会话字段及相应消息、任务、工具、路径、
  结果、压缩和错误字段。（验证：运行 `tests/unit/hooks/test_context.py`）
- [ ] 工具执行前上下文使用权限硬检查产生的规范化参数，存在 `path` 时提供
  `file.path`。（验证：运行工具上下文与权限准备集成单元测试）
- [ ] `{{ field.path }}` 对字符串、布尔值、数值、对象和数组进行单遍稳定替换，缺失变量
  变为空串，不执行表达式或替换结果中的新模板。（验证：运行模板单元测试）

## 动作执行

- [ ] Shell 动作以项目根目录为默认 cwd，通过独立 Shell 执行，不触发工具权限或递归
  Hook。（验证：运行 Shell 执行器 cwd 测试及 Agent 集成调用次数断言）
- [ ] Shell 正常输出、非零退出、启动错误和超时均转成统一动作结果，且主流程继续。
  （验证：运行 Shell 成功、失败和短超时单元测试）
- [ ] 同步 `tool.before_execute` Shell 能解析 `allow`、`deny`、`ask` 和原因；非法 JSON 或
  非法字段不形成动态决定。（验证：运行 Shell 权限输出测试）
- [ ] HTTP 支持 GET、POST、PUT、PATCH、DELETE，以及模板化请求头、文本 body 和 JSON
  字符串叶子。（验证：使用 httpx MockTransport 运行方法与请求体测试）
- [ ] HTTP 2xx 为成功，非 2xx、请求错误和超时为失败；响应不改变权限或模型上下文。
  （验证：运行 HTTP 状态与结果隔离测试）
- [ ] Reminder 生成包含规则 ID、事件名和 XML 转义正文的 `<system-reminder>`。
  （验证：运行 Reminder 执行器与 Prompt 模型标签测试）
- [ ] Agent 动作不创建子 Agent，只产生包含规则 ID 的“子 Agent Hook 尚未实现”终端
  通知。（验证：运行占位执行器及 HookNoticeEvent 终端渲染测试）

## 运行状态、异步与错误隔离

- [ ] 每条规则初始 `executed == false`；事件和条件匹配后、动作启动前变为 `true`。
  （验证：使用可观察假执行器断言启动时的运行时状态）
- [ ] `once: true && executed: true` 时不再触发；动作失败、超时或取消不恢复状态；重建
  HookRuntime 后状态重置。（验证：运行 once 成功、失败和重建测试）
- [ ] `once: false` 的规则可重复执行，即使 `executed` 已为 `true`。（验证：连续分发同一
  事件并断言执行次数）
- [ ] 异步 Shell/HTTP 启动后立即返回，不阻塞 Agent 主流程。（验证：运行受控 Event 的
  后台任务测试）
- [ ] 应用关闭先触发 `session.end`，再等待后台任务最多 3 秒并取消剩余任务。（验证：
  运行受控后台任务和关闭顺序测试）
- [ ] 条件、模板和动作异常只记录带事件、规则和动作信息的有界日志，不中断 Agent，也
  不触发新的 `agent.error`。（验证：运行 caplog 错误隔离和日志截断测试）

## 权限结合

- [ ] PermissionEngine `prepare()` 在 Hook 前完成工具参数校验、路径规范化、危险命令及
  plan-only 检查。（验证：运行 `tests/unit/security/test_engine.py` 两阶段测试）
- [ ] Hook `allow` 跳过项目 deny/allow/ask、会话授权和权限模式；无 Hook 决定时保持
  原有权限策略结果。（验证：运行 Hook allow 覆盖项目 deny 和兼容入口测试）
- [ ] 即使 Hook 返回 `allow`，无效参数、路径越界、危险命令和 plan-only 禁止仍被拒绝。
  （验证：对四类硬拒绝运行 Hook allow 参数化测试）
- [ ] Hook `ask` 能把原本自动允许的调用升级为人工审批，并且每个工具调用最多审批一次、
  不写入会话授权。（验证：运行审批事件数量与 PermissionSession 授权数断言）
- [ ] Hook `deny` 阻止工具副作用，将原因作为结构化工具错误反馈给模型，并停止后续执行前
  规则。（验证：运行 runtime 短路测试和 Agent 集成测试）
- [ ] 多条执行前规则按声明顺序及 `deny > ask > allow > none` 汇总；动态决定优先于本规则
  固定决定，动态失败时正确回退。（验证：运行权限组合参数化测试）

## Agent Loop 与会话生命周期

- [ ] `session.start/end`、`turn.start/end`、`message.before_send/after_receive`、
  `tool.before_execute/after_execute`、`context.compacted` 和 `agent.error` 在约定节点触发。
  （验证：运行 Hook 事件记录器的 AgentLoop 与 ChatSession 单元测试）
- [ ] 一个用户 Agent 任务只有一个 turn ID；内部多次模型请求分别触发消息事件，流式增量
  不触发 Hook。（验证：使用多轮 FakeProvider 响应断言事件序列）
- [ ] 正常完成、取消、错误和轮数上限分别产生正确 `turn.end.status`；错误时
  `agent.error` 先于 `turn.end`。（验证：运行四种终态顺序测试）
- [ ] 自动和手动上下文压缩均在成功激活结果后触发一次 `context.compacted`。（验证：运行
  AgentLoop 自动压缩与 ChatSession `/compact` 测试）
- [ ] 工具真实执行成功或失败均触发一次 `tool.after_execute`；执行前被拒绝的调用不触发。
  （验证：区分真实 ToolExecutor 记录和 denied_results 记录）
- [ ] Reminder 只进入下一次模型请求并在取出后清除；`message.before_send` 产生的 Reminder
  进入当前请求，不写入 ChatSession 历史。（验证：检查 FakeProvider 收到的 supplements
  和最终会话历史）
- [ ] HookNoticeEvent 能到达 TerminalUI，但不会成为 ChatMessage 或会话提交内容。
  （验证：运行 Agent、Session 和 Terminal 单元测试）
- [ ] 隔离 Skill AgentLoop 和 OpenAI PlainChatRunner 不创建或触发 HookRuntime。
  （验证：运行 App 装配测试及现有 OpenAI 回归测试）

## 集成场景

- [ ] 场景一：模型发起工具调用，before Hook 返回 `deny`，工具没有副作用，模型收到拒绝
  原因后生成安全替代回复。（验证：运行
  `.venv\Scripts\python.exe -m pytest -q tests/integration/test_hook_agent_flow.py -k deny`）
- [ ] 场景二：before Hook 返回 `ask`，终端只审批一次；用户允许后工具执行并产生 after
  事件。（验证：运行集成审批场景）
- [ ] 场景三：工具执行后生成 Reminder，下一次模型请求包含该 Reminder，随后队列清空且
  历史不包含 Reminder。（验证：运行集成 Reminder 场景）
- [ ] 场景四：一个禁用 Hook 和一个 `once: true` Hook 同时配置，禁用项从不运行，once 项
  只启动一次。（验证：运行集成状态场景）

## 编译、检查与回归

- [ ] Hook 及相邻模块单元测试全部通过。（验证：运行
  `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks tests/unit/security tests/unit/prompt tests/unit/agent tests/unit/session tests/unit/ui tests/unit/test_app.py`）
- [ ] Anthropic Hook 集成测试全部通过。（验证：运行
  `.venv\Scripts\python.exe -m pytest -q tests/integration/test_hook_agent_flow.py`）
- [ ] 代码格式检查通过。（验证：运行 `.venv\Scripts\python.exe -m ruff format --check .`）
- [ ] 静态检查通过。（验证：运行 `.venv\Scripts\python.exe -m ruff check .`）
- [ ] Python 编译检查通过。（验证：运行
  `.venv\Scripts\python.exe -m compileall -q ycode tests`）
- [ ] 完整测试套件通过，现有权限、工具、上下文、会话、Anthropic 和 OpenAI 回归无新增
  失败。（验证：运行 `.venv\Scripts\python.exe -m pytest -q`）

## 真实终端端到端

- [ ] 在临时项目配置 Hook 后启动真实 YCode 终端，观察到一次外部操作风险提示。
  （验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k hook`）
- [ ] 真实终端中触发 before Hook 的 ask/deny，观察到单次审批、拒绝原因回填和 Agent 后续
  调整。（验证：同一 ConPTY 场景检查终端输出和受控工具副作用）
- [ ] 退出终端时执行 session.end，后台 Hook 不使进程无限挂起。（验证：同一场景在测试
  时限内正常退出）
- [ ] 如果当前环境不支持 ConPTY，端到端测试按现有约定明确标记 skip，并在验收报告中
  列为未实际验证，不将其报告为通过。

## 明确不执行的生产级验证

- [ ] 验收报告明确说明未执行压力测试、性能基准、长期稳定性、大规模并发、复杂故障
  注入、多平台矩阵和真实付费 API。（验证：最终报告列出范围排除项）
