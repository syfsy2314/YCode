# YCode Hook 系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `ycode/hooks/__init__.py` | Hook 公共导出 |
| 新建 | `ycode/hooks/models.py` | 事件、规则、条件、动作、诊断和执行结果模型 |
| 新建 | `ycode/hooks/config.py` | 项目配置发现与逐条容错加载 |
| 新建 | `ycode/hooks/context.py` | 生命周期事件上下文构造 |
| 新建 | `ycode/hooks/matching.py` | 点路径解析与条件匹配 |
| 新建 | `ycode/hooks/template.py` | 简单模板替换与 XML 转义 |
| 新建 | `ycode/hooks/executors.py` | 四类动作执行器 |
| 新建 | `ycode/hooks/runtime.py` | 规则分发、状态、权限、Reminder 和后台任务 |
| 新建 | `ycode/hooks/logging.py` | Hook 日志与有界摘要 |
| 修改 | `ycode/security/engine.py` | 拆分权限硬检查与普通策略 |
| 修改 | `ycode/security/models.py` | PermissionPreparation 数据模型 |
| 修改 | `ycode/security/__init__.py` | 导出新增权限接口 |
| 修改 | `ycode/prompt/models.py` | system-reminder 请求补充类型 |
| 修改 | `ycode/agent/events.py` | HookNoticeEvent |
| 修改 | `ycode/agent/__init__.py` | 导出 Hook 通知事件 |
| 修改 | `ycode/agent/loop.py` | Agent 生命周期、消息和工具 Hook 触发 |
| 修改 | `ycode/session/chat.py` | 会话起止及手动压缩 Hook |
| 修改 | `ycode/ui/terminal.py` | HookNoticeEvent 展示 |
| 修改 | `ycode/app.py` | Hook 配置、运行时和关闭顺序装配 |
| 新建 | `tests/unit/hooks/__init__.py` | Hook 单元测试包 |
| 新建 | `tests/unit/hooks/test_models.py` | 配置模型测试 |
| 新建 | `tests/unit/hooks/test_config.py` | 配置发现、降级和诊断测试 |
| 新建 | `tests/unit/hooks/test_matching.py` | 点路径与匹配测试 |
| 新建 | `tests/unit/hooks/test_template.py` | 模板替换测试 |
| 新建 | `tests/unit/hooks/test_context.py` | 事件上下文测试 |
| 新建 | `tests/unit/hooks/test_executors.py` | Shell、HTTP、Reminder、Agent 动作测试 |
| 新建 | `tests/unit/hooks/test_runtime.py` | 运行时状态、权限和异步任务测试 |
| 修改 | `tests/unit/security/test_engine.py` | 两阶段权限与兼容入口测试 |
| 修改 | `tests/unit/prompt/test_models.py` | system-reminder 标签测试 |
| 修改 | `tests/unit/agent/test_contracts.py` | HookNoticeEvent 契约测试 |
| 修改 | `tests/unit/agent/test_loop.py` | Agent Loop Hook 时序和工具集成测试 |
| 修改 | `tests/unit/session/test_chat.py` | 会话起止及手动压缩 Hook 测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | HookNoticeEvent 渲染测试 |
| 修改 | `tests/unit/test_app.py` | Anthropic 装配与 OpenAI 回归测试 |
| 新建 | `tests/integration/test_hook_agent_flow.py` | 拦截、Reminder 和 Agent 调整集成流程 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 真实终端 Hook 场景 |

## T1：定义 Hook 配置与结果模型

**文件：** `ycode/hooks/models.py`、`tests/unit/hooks/__init__.py`、
`tests/unit/hooks/test_models.py`  
**依赖：** 无

**步骤：**

1. 定义 HookEventName、HttpMethod、HookPermissionDecision 和 HookActionStatus。
2. 定义正向匹配器、`not` 匹配器、`all/any` 条件组及四种动作判别联合。
3. 定义 HookRule，包含 `enabled`、`once`、`async`、`timeout_seconds` 和固定权限决定。
4. 校验动作必填字段、正则语法及事件、异步、Reminder、权限决定的组合约束。
5. 定义 HookDiagnostic、HookEvent、HookActionResult 和 HookDispatchResult。
6. 添加代表性合法、非法、默认值和禁用规则测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_models.py`，
预期全部通过。

## T2：实现配置发现与逐条容错加载

**文件：** `ycode/hooks/config.py`、`tests/unit/hooks/test_config.py`  
**依赖：** T1

**步骤：**

1. 实现从起始目录向上发现最近 `.ycode/hooks.yaml`。
2. 使用安全 YAML 加载并校验顶层 `hooks` 列表。
3. 按声明顺序逐条校验规则，跳过非法规则并保留合法规则。
4. 重复 ID 保留第一条，并为后续重复项生成带序号和 ID 的诊断。
5. 文件缺失返回空规则；文件级错误返回空规则和诊断，不抛出启动异常。
6. 为已启用 Shell/HTTP 动作生成一次外部操作风险标记。
7. 测试最近配置发现、文件错误、单条错误、重复 ID、禁用规则和风险提示。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_config.py`，
预期全部通过。

## T3：实现点路径和条件匹配

**文件：** `ycode/hooks/matching.py`、`tests/unit/hooks/test_matching.py`  
**依赖：** T1

**步骤：**

1. 定义 Missing 哨兵并实现对象键、数组索引的点路径读取。
2. 实现 `exact`、大小写敏感 Glob 和正则搜索匹配。
3. 实现 `not`，保证缺失字段始终不匹配。
4. 实现无条件、`all` 和 `any` 条件计算。
5. 测试嵌套对象、数组、JSON `null`、缺失字段和四种操作符。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_matching.py`，
预期全部通过。

## T4：实现 Hook 模板替换

**文件：** `ycode/hooks/template.py`、`tests/unit/hooks/test_template.py`  
**依赖：** T3

**步骤：**

1. 实现 `{{ field.path }}` 占位符识别和单遍替换。
2. 实现字符串、布尔值、数值、对象和数组的稳定文本转换。
3. 缺失字段替换为空串，不再次解析替换值中的模板文本。
4. 提供 System Reminder 正文的 XML 文本转义辅助函数。
5. 测试嵌套变量、缺失变量、稳定 JSON、单遍替换和 XML 特殊字符。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_template.py`，
预期全部通过。

## T5：实现生命周期事件上下文工厂

**文件：** `ycode/hooks/context.py`、`tests/unit/hooks/test_context.py`  
**依赖：** T1

**步骤：**

1. 实现公共事件、项目和会话字段构造。
2. 实现 turn、message、tool、context.compacted 和 agent.error 事件工厂。
3. 将工具规范化参数、结果和元数据转换为冻结 JSON。
4. 存在规范化 `path` 时补充 `file.path`。
5. 消息只暴露角色和完整文本，不暴露 Thinking 或供应商内部字段。
6. 测试各类代表性事件字段和不适用字段缺失行为。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_context.py`，
预期全部通过。

## T6：增加 Hook 日志辅助

**文件：** `ycode/hooks/logging.py`、`tests/unit/hooks/test_runtime.py`  
**依赖：** T1

**步骤：**

1. 建立 Hook 专用标准库 logger。
2. 实现字符串和结构化值的有界日志摘要。
3. 提供包含事件、规则、动作和结果的统一日志函数。
4. 添加日志字段和截断行为测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_runtime.py -k log`，
预期相关测试通过。

## T7：增加 System Reminder 补充类型

**文件：** `ycode/prompt/models.py`、`tests/unit/prompt/test_models.py`  
**依赖：** 无

**步骤：**

1. 新增值为 `system-reminder` 的 SupplementKind。
2. 保持其为请求级 SystemSupplement，不加入会话持久状态。
3. 验证 `tagged_content` 生成准确 `<system-reminder>` 标签。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/prompt/test_models.py`，
预期全部通过。

## T8：实现 Reminder 与 Agent 兼容执行器

**文件：** `ycode/hooks/executors.py`、`tests/unit/hooks/test_executors.py`  
**依赖：** T1、T4、T7

**步骤：**

1. 定义统一 HookActionExecutor 协议。
2. 实现 Reminder 模板渲染、XML 转义和 SystemSupplement 结果。
3. 保留 Agent 兼容动作并静默完成，不创建子 Agent 或返回遗留占位通知。
4. 确保执行器异常转换为统一失败结果。
5. 测试 Reminder 标签、一次性内容和 Agent 未实际启动行为。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_executors.py -k "reminder or agent"`，预期相关测试通过。

## T9：实现同步 Shell 执行及权限输出

**文件：** `ycode/hooks/executors.py`、`tests/unit/hooks/test_executors.py`  
**依赖：** T1、T4、T6

**步骤：**

1. 使用平台默认 Shell 创建独立子进程，并固定项目根目录为默认 cwd。
2. 捕获 stdout/stderr，应用规则超时并清理超时进程。
3. 仅为同步 `tool.before_execute` 解析单个 JSON 权限对象。
4. 支持 `allow`、`deny`、`ask` 和原因；拒绝额外控制字段。
5. 非零退出、非法 JSON、超时和启动异常返回隔离的失败结果。
6. 测试 cwd、普通输出、动态权限、非法输出和短超时。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_executors.py -k shell`，
预期相关测试通过。

## T10：实现 HTTP 动作执行器

**文件：** `ycode/hooks/executors.py`、`tests/unit/hooks/test_executors.py`  
**依赖：** T1、T4、T6

**步骤：**

1. 使用注入的 httpx.AsyncClient 执行五种允许方法。
2. 渲染 URL、请求头、文本 body 和 JSON 字符串叶子节点。
3. 将 2xx 转为成功，非 2xx、请求错误和超时转为失败。
4. 响应仅进入有界日志摘要，不产生权限或 Reminder。
5. 使用 MockTransport 测试方法、模板、两类 body 和响应状态。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_executors.py -k http`，
预期相关测试通过。

## T11：实现运行时分发和规则状态

**文件：** `ycode/hooks/runtime.py`、`tests/unit/hooks/test_runtime.py`  
**依赖：** T1、T3、T5、T6、T8、T9、T10

**步骤：**

1. 构造保持声明顺序的 RuntimeHookRule 列表。
2. 实现事件名、`enabled`、`once/executed` 和条件门禁顺序。
3. 条件匹配后、动作启动前设置 `executed = true`。
4. 串行执行同步动作，隔离单条规则异常并继续后续规则。
5. 收集 Reminder 和通知，提供原子 take-and-clear 接口。
6. 测试禁用、条件未命中、once、失败不恢复和 Reminder 一次性消费。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_runtime.py -k "dispatch or enabled or once or reminder"`，预期相关测试通过。

## T12：实现运行时权限汇总与短路

**文件：** `ycode/hooks/runtime.py`、`tests/unit/hooks/test_runtime.py`  
**依赖：** T11

**步骤：**

1. 实现动态权限优先于单条规则固定权限。
2. 动态执行失败时回退到固定权限或无决定。
3. 按 `deny > ask > allow > none` 汇总多条规则。
4. `deny` 后立即停止后续执行前规则。
5. 非 `tool.before_execute` 事件不产生权限结果。
6. 测试优先级、固定回退、deny 短路和原因选择。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_runtime.py -k permission`，
预期相关测试通过。

## T13：实现异步任务与退出收尾

**文件：** `ycode/hooks/runtime.py`、`tests/unit/hooks/test_runtime.py`  
**依赖：** T11

**步骤：**

1. 为异步 Shell/HTTP 创建后台任务并立即返回主流程。
2. 保存任务集合并在完成回调中记录结果和移除任务。
3. `close()` 停止接收普通新事件，等待任务最多 3 秒。
4. 取消期限内未完成任务，并关闭共享 HTTP 客户端。
5. 测试非阻塞执行、once 在启动前消费、正常收尾和取消收尾。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks/test_runtime.py -k "async or close"`，预期相关测试通过。

## T14：补齐 Hook 包公共导出

**文件：** `ycode/hooks/__init__.py`  
**依赖：** T2、T5、T11、T13

**步骤：**

1. 导出配置加载、事件工厂所需模型、HookRuntime 和公共决定类型。
2. 保持内部执行器和实现辅助不进入公共表面。
3. 验证包可以独立导入且无循环依赖。

**验证：** 运行 `.venv\Scripts\python.exe -c "import ycode.hooks"`，预期退出码为 0。

## T15：拆分权限硬检查与普通策略

**文件：** `ycode/security/models.py`、`ycode/security/engine.py`、
`ycode/security/__init__.py`、`tests/unit/security/test_engine.py`  
**依赖：** 无

**步骤：**

1. 增加 PermissionPreparation，携带规范化 PermissionSubject 和可选硬拒绝。
2. 将工具查找、参数与路径规范化、危险命令和访问/plan-only 检查移入 `prepare()`。
3. 将项目规则、Skill 特殊审批、会话授权和权限模式移入 `evaluate_policy()`。
4. 保持项目 deny 属于普通策略，可被 Hook 明确决定替代。
5. 保留 `evaluate()` 兼容入口，组合两个阶段并保持无 Hook 时的现有结果。
6. 扩充测试，验证硬拒绝、规范化 subject、普通策略及旧入口回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/security`，预期全部通过。

## T16：增加 HookNoticeEvent 与终端展示

**文件：** `ycode/agent/events.py`、`ycode/agent/__init__.py`、
`ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T1

**步骤：**

1. 定义包含非空消息的供应商无关 HookNoticeEvent。
2. 加入 AgentEvent 联合并从 agent 包导出。
3. TerminalUI 将事件渲染为 `hook: <message>`。
4. 测试事件校验和终端输出，不写入对话历史。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py tests/unit/agent/test_contracts.py`，预期全部通过。

## T17：接入任务和模型消息 Hook

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`  
**依赖：** T5、T7、T14、T16

**步骤：**

1. AgentLoop 接受可选 HookRuntime，并为每个用户任务生成 turn ID。
2. 在实际 Agent 任务开始时触发 `turn.start`。
3. 在上下文准备后触发 `message.before_send`，消费 Reminder 并追加到当前 supplements。
4. 完整 assistant_message 组装后触发 `message.after_receive`，流式增量不触发。
5. 将 Hook notices 转为 HookNoticeEvent。
6. 测试无 Hook 兼容、事件顺序、每次模型请求触发和 Reminder 一次性注入。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_loop.py -k "hook and (turn or message or reminder)"`，预期相关测试通过。

## T18：接入上下文压缩、错误与任务结束 Hook

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`  
**依赖：** T17

**步骤：**

1. 自动压缩成功时触发 `context.compacted`。
2. 将 completed、cancelled、error 和 limit_reached 终态收口到统一辅助路径。
3. 错误终态先触发 `agent.error`，再触发 `turn.end(status=error)`。
4. 其他终态触发对应状态的 `turn.end`。
5. Hook 自身失败只记日志，不递归触发 `agent.error`。
6. 测试四种终态、错误顺序和自动压缩事件。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_loop.py -k "hook and (compact or error or end or cancel or limit)"`，预期相关测试通过。

## T19：接入工具执行前 Hook 与权限合并

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`  
**依赖：** T12、T15、T17

**步骤：**

1. 每个工具调用先执行 PermissionEngine.prepare。
2. 硬检查通过后使用规范化参数触发 `tool.before_execute`。
3. 将 Hook allow/deny/ask 转成现有 PermissionDecision。
4. Hook 无决定时调用 evaluate_policy；allow/ask/deny 时跳过普通策略。
5. Hook ask 设置为仅本次批准，并复用现有单一审批槽。
6. Hook deny 生成现有结构化拒绝工具结果并反馈模型。
7. 测试 allow 覆盖项目 deny、硬边界不可覆盖、ask 仅审批一次及 deny 回填。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_loop.py -k "hook and permission"`，预期相关测试通过。

## T20：接入工具执行后 Hook

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`  
**依赖：** T19

**步骤：**

1. 在调度前保存 denied_results 位置集合。
2. ScheduledToolCompleted 不在拒绝集合时触发 `tool.after_execute`。
3. 工具真实返回成功或错误均触发一次 after 事件。
4. 硬拒绝、权限拒绝和 Hook 拒绝不触发 after 事件。
5. 测试真实失败与预生成拒绝的区分。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_loop.py -k "hook and tool_after"`，预期相关测试通过。

## T21：接入 ChatSession 会话与手动压缩 Hook

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T5、T14、T16、T18

**步骤：**

1. ChatSession 接受可选 HookRuntime、会话 ID 和配置诊断。
2. 提供启动入口触发 `session.start`，将通知合并到 startup_warnings。
3. 手动压缩成功激活后触发 `context.compacted`。
4. `close()` 在关闭 AgentLoop 前触发 `session.end`，随后关闭 HookRuntime。
5. session.end Agent 兼容动作不通过关闭通知通道输出占位信息。
6. 测试会话起止顺序、手动压缩、重复 close 和无 Hook 兼容。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k hook`，
预期相关测试通过。

## T22：装配 Anthropic Hook 运行时

**文件：** `ycode/app.py`、`tests/unit/test_app.py`  
**依赖：** T2、T8–T16、T21

**步骤：**

1. Anthropic 路径加载 Hook 配置并创建共享 HTTP 客户端、执行器和 HookRuntime。
2. 将 HookRuntime 只注入主 AgentLoop 和 ChatSession，不注入隔离 Skill AgentLoop。
3. 将配置诊断和外部操作提示合入 startup_warnings。
4. 在 UI 启动前触发 session.start。
5. 保证 ChatSession 关闭路径先 session.end 和 Hook 收尾，再关闭 Agent/Provider 资源。
6. OpenAI 路径保持不加载、不创建、不触发 Hook。
7. 测试 Anthropic 装配、空配置、非法配置降级、关闭顺序和 OpenAI 回归。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/test_app.py`，预期全部通过。

## T23：补齐 Hook 包与相邻模块单元回归

**文件：** `ycode/hooks/*`、`tests/unit/hooks/*` 及相关既有单元测试  
**依赖：** T1–T22

**步骤：**

1. 运行全部 Hook 单元测试，修复模型、上下文、执行器和运行时接口不一致。
2. 运行权限、提示词、Agent、Session、UI 与 App 相邻单元测试。
3. 确认禁用规则、once、动态权限失败回退和错误隔离均有代表性测试。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/hooks tests/unit/security tests/unit/prompt tests/unit/agent tests/unit/session tests/unit/ui tests/unit/test_app.py`，预期全部通过。

## T24：实现 Anthropic Hook 集成流程测试

**文件：** `tests/integration/test_hook_agent_flow.py`  
**依赖：** T23

**步骤：**

1. 使用现有 FakeProvider 和真实 AgentLoop 组装 HookRuntime。
2. 验证 before_execute deny 生成工具错误结果，模型下一轮收到原因并调整策略。
3. 验证 ask 只产生一次审批，allow 跳过普通权限策略。
4. 验证 after_execute Reminder 只进入下一次模型请求且不写入历史。
5. 验证 `enabled: false` 不触发以及 `once: true` 只启动一次。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_hook_agent_flow.py`，预期全部通过。

## T25：增加真实终端端到端 Hook 场景

**文件：** `tests/e2e/test_terminal_chat.py`  
**依赖：** T22、T24

**步骤：**

1. 在临时项目写入最小 `.ycode/hooks.yaml` 和本地 Shell Hook。
2. 使用现有 Windows ConPTY 测试框架启动 YCode。
3. 验证启动风险提示只显示一次。
4. 触发工具调用并验证 Hook ask/deny 的用户可见行为、单次审批和拒绝原因回填。
5. 退出应用并验证 session.end 与后台收尾不阻塞终端退出。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k hook`，
预期 Hook 端到端场景通过；如果 ConPTY 环境不可用，应按现有测试约定明确 skip，而不是
报告为通过。

## T26：执行全项目质量验证

**文件：** 全项目  
**依赖：** T23–T25

**步骤：**

1. 运行格式检查并修复格式问题。
2. 运行静态检查并修复 lint 问题。
3. 运行编译检查。
4. 运行完整测试套件。
5. 再次运行 Hook 真实终端场景，记录实际结果。
6. 不执行压力、性能、长稳、复杂故障注入、多平台矩阵或真实付费 API 验证。

**验证：** 依次运行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k hook
```

预期格式、静态检查、编译和完整测试全部通过；端到端场景通过或仅因现有环境能力按约定
明确跳过。

## 执行顺序

```text
T1 ─┬─→ T2
    ├─→ T3 → T4
    └─→ T5

T7 → T8
T4 + T6 → T9、T10
T1 + T3–T10 → T11 → T12、T13 → T14

T15（可与 T1–T14 并行）
T16（可与 T2–T15 并行）

T14 + T16 → T17 → T18
T12 + T15 + T17 → T19 → T20
T14 + T16 + T18 → T21
T2 + T8–T16 + T21 → T22

T1–T22 → T23 → T24
T22 + T24 → T25
T23–T25 → T26
```

## 后续待完成

- [ ] 增加轻量 `print` 动作，允许通过模板配置自定义终端消息，例如：

  ```yaml
  action:
    type: print
    message: "Hook 已触发：{{ event.name }}"
  ```

  该动作只负责产生终端通知，不注入模型上下文、不写入会话历史、不执行 Shell，也不占用
  未来 `agent` 动作的语义。`session.start` 阶段通过启动提示展示，Agent Loop 运行期间通过
  `HookNoticeEvent` 展示。第一版仅支持同步执行，不提供异步和超时配置。
