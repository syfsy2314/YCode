# YCode 工具权限安全系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `ycode/security/__init__.py` | 安全系统公共导出 |
| 新建 | `ycode/security/models.py` | 权限模式、规则、决策和会话状态 |
| 新建 | `ycode/security/config.py` | 项目安全配置发现、加载与校验 |
| 新建 | `ycode/security/powershell.py` | PowerShell 语法解析和危险命令分类 |
| 新建 | `ycode/security/engine.py` | 工具参数规范化、规则匹配和统一判定 |
| 修改 | `ycode/tools/contracts.py` | 增加 UNKNOWN 工具访问分类 |
| 修改 | `ycode/tools/__init__.py` | 更新工具与安全相关公共导出 |
| 修改 | `ycode/tools/scheduler.py` | 合并权限拒绝结果并串行调度 UNKNOWN |
| 修改 | `ycode/agent/contracts.py` | AgentTurn 审批响应状态 |
| 修改 | `ycode/agent/events.py` | 工具审批和权限模式事件 |
| 修改 | `ycode/agent/__init__.py` | 导出新增 Agent 事件 |
| 修改 | `ycode/agent/loop.py` | 工具批次权限检查、审批暂停和权限动态补充 |
| 修改 | `ycode/session/chat.py` | 权限命令和审批选择转交 |
| 修改 | `ycode/ui/header.py` | 启动权限模式展示 |
| 修改 | `ycode/ui/input_box.py` | 双模式状态和阻塞式审批输入 |
| 修改 | `ycode/ui/terminal.py` | 审批事件、权限命令事件和输入监听切换 |
| 修改 | `ycode/app.py` | 安全配置与运行组件装配 |
| 新建 | `tests/unit/security/*.py` | 模型、配置、命令检查和权限引擎测试 |
| 修改 | `tests/unit/tools/*.py` | UNKNOWN 与拒绝结果调度测试 |
| 修改 | `tests/unit/agent/*.py` | 审批状态、阻塞顺序、回填和动态补充测试 |
| 修改 | `tests/unit/session/test_chat.py` | 权限命令和审批转交测试 |
| 修改 | `tests/unit/ui/*.py` | 权限状态与三选一审批测试 |
| 修改 | `tests/unit/test_app.py` | Anthropic 安全组件装配测试 |
| 修改 | `tests/integration/test_anthropic_stream.py` | 权限链路与模型回填集成测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | Windows ConPTY 审批场景 |

## T1：权限模型与 UNKNOWN 分类

**依赖：** 无  
**文件：** `ycode/security/models.py`、`ycode/security/__init__.py`、
`ycode/tools/contracts.py`、`ycode/tools/__init__.py`、
`tests/unit/security/test_models.py`、`tests/unit/tools/test_contracts.py`

1. 定义权限模式、权限动作、审批选择、参数匹配、规则、配置、权限主题和决策。
2. 实现 PermissionSession 的模式切换、授权查询、授权写入和清除。
3. 使用冻结结构保存规范化参数和会话键，校验非法类型和空字段。
4. 为 ToolAccess 增加 UNKNOWN，并更新公共导出。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_models.py tests/unit/tools/test_contracts.py -q
```

## T2：项目安全配置

**依赖：** T1  
**文件：** `ycode/security/config.py`、`tests/unit/security/test_config.py`

1. 从指定起点向上发现最近的 `.ycode/security.yaml`。
2. 文件缺失时返回内置 default 模式和空规则。
3. 使用安全 YAML 加载并校验 matcher、动作、模式、重复 ID 和额外字段。
4. 结合 ToolRegistry 校验工具名和参数名；错误转换为明确 ConfigError。
5. 验证项目配置只加载一次，不读取用户目录全局配置。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_config.py -q
```

## T3：PowerShell 语法解析通道

**依赖：** T1  
**文件：** `ycode/security/powershell.py`、`tests/unit/security/test_powershell.py`

1. 使用固定 PowerShell 解析脚本从 stdin 读取待检查命令。
2. 输出规范化的命令、参数、管道关系和语法错误 JSON。
3. 使用隐藏、无配置、非交互的短生命周期进程，并设置短超时。
4. 将启动失败、超时、非零退出、非法 JSON 和 PowerShell 语法错误转换为硬拒绝。
5. 验证待检查命令不会被解析进程执行。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_powershell.py -q -k "parser or failure"
```

## T4：危险命令分类

**依赖：** T3  
**文件：** `ycode/security/powershell.py`、`tests/unit/security/test_powershell.py`

1. 规范化已确认的 PowerShell、系统命令和常见别名。
2. 实现大范围删除、磁盘破坏、远程内容直接执行、动态或编码执行、系统启动与关机、
   高破坏性 Git，以及危险权限和所有权接管分类。
3. 按命令元素和管道关系判断，不依赖原始字符串包含关系。
4. 为每个类别增加典型命令、大小写或空白变体、别名或管道变体及安全反例。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_powershell.py -q
```

## T5：安全参数规范化与规则匹配

**依赖：** T1、T2  
**文件：** `ycode/security/engine.py`、`tests/unit/security/test_engine.py`

1. 使用 Registry 参数模型校验工具调用参数。
2. 为六个内置工具生成规范化参数、工具特定会话键和审批摘要。
3. 使用 WorkspacePathResolver 解析真实路径；允许工作区内链接，拒绝越界或不可解析
   链接，并使用真实相对路径匹配规则。
4. 实现 exact、普通字符串 Glob、路径 Glob 和有序首次命中。
5. 将审批预览限制为 2 KiB，正文截断时给出标记；命令保持完整。
6. 未知分类工具使用完整规范化参数作为会话键。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_engine.py -q -k "normalize or match or preview"
```

## T6：统一权限判定

**依赖：** T4、T5  
**文件：** `ycode/security/engine.py`、`tests/unit/security/test_engine.py`

1. 按硬规则、allowed_access、会话允许、项目规则和权限模式的固定顺序判定。
2. 实现 strict、default、allow 及 UNKNOWN 的默认行为。
3. 确保危险命令和 plan-only 不会被会话或项目 allow 覆盖。
4. 为 allow、deny、ask 生成稳定 reason_code 和安全用户消息。
5. 将内部异常转换为拒绝，不执行工具且不泄露异常细节。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/security/test_engine.py -q
```

## T7：Scheduler 合并权限结果

**依赖：** T1  
**文件：** `ycode/tools/scheduler.py`、`tests/unit/tools/test_scheduler.py`

1. 扩展 Scheduler 输入，使其接收按原始位置保存的预生成拒绝结果。
2. 拒绝位置产生 ToolExecutionRecord，但不调用 ToolExecutor。
3. 允许位置继续使用现有连续读取并发和写入屏障。
4. UNKNOWN 按非读取工具串行执行。
5. 验证完成、取消和回填位置保持稳定。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/tools/test_scheduler.py -q
```

## T8：Agent 审批状态与事件

**依赖：** T1  
**文件：** `ycode/agent/contracts.py`、`ycode/agent/events.py`、
`ycode/agent/__init__.py`、`tests/unit/agent/test_contracts.py`

1. 增加 ToolApprovalRequested 和权限模式相关 AgentEvent。
2. 为 AgentTurn 增加当前审批选择提交入口。
3. AgentTurnStream 同一时刻只允许一个待审批请求。
4. 实现开始审批、提交选择、消费选择和取消清理。
5. 无待审批、重复提交和异常状态采用安全错误。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_contracts.py -q
```

## T9：AgentLoop 权限接入

**依赖：** T6、T7、T8  
**文件：** `ycode/agent/loop.py`、`tests/support/fake_provider.py`、
`tests/unit/agent/test_loop.py`

1. 注入 PermissionEngine 和 PermissionSession。
2. agent 模式允许向模型提供 READ、WRITE 和 UNKNOWN 定义，plan-only 仍只提供 READ。
3. 对每个工具批次按模型位置逐个判定；ASK 时产生审批事件并暂停。
4. 确保用户提交选择前不判断下一调用、不启动 Scheduler、不发起模型请求。
5. 处理本次允许、本会话允许和拒绝，并把拒绝结果交给 Scheduler。
6. 在每个用户任务的请求级 tool_state 补充中加入权限模式，同一工具循环复用。
7. 验证取消审批不会执行当前或后续工具。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q
```

## T10：ChatSession 权限命令与审批转交

**依赖：** T8  
**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`

1. 让 Anthropic ChatSession 持有 PermissionSession。
2. 实现 `/permission`、三档模式切换和 `/permission clear`。
3. 权限命令产生明确事件，不请求 Provider、不进入历史、不修改配置。
4. 将审批选择转交当前 AgentTurn；无活动审批时拒绝提交。
5. 验证模式切换保留会话授权，clear 只清除授权。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q
```

## T11：终端权限状态与阻塞审批

**依赖：** T8、T10  
**文件：** `ycode/ui/header.py`、`ycode/ui/input_box.py`、`ycode/ui/terminal.py`、
`tests/unit/ui/test_header.py`、`tests/unit/ui/test_input_box.py`、
`tests/unit/ui/test_terminal.py`

1. Anthropic 启动头部和输入提示同时显示任务模式与权限模式。
2. 实现只包含拒绝、本次允许、本会话允许的审批输入。
3. 显示工具、触发原因和审批摘要。
4. 处理审批时暂停普通事件推进，并保证审批输入和 Ctrl+C 监听不竞争输入设备。
5. Ctrl+C 取消审批和整个 Agent 回合；普通聊天取消行为保持不变。
6. OpenAI UI 保持原有显示和命令行为。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ui -q
```

## T12：应用装配与集成回归

**依赖：** T2、T6、T9、T10、T11  
**文件：** `ycode/app.py`、`tests/unit/test_app.py`、
`tests/integration/test_anthropic_stream.py`

1. 在内置工具注册后加载项目安全配置。
2. 创建并共享 PermissionSession、PowerShellSafetyChecker 和 PermissionEngine。
3. 只向 Anthropic AgentLoop、ChatSession 和 TerminalUI 装配安全组件。
4. 验证权限模式动态补充进入 supplements，不改变稳定 System Prompt。
5. 添加拒绝、审批后执行、拒绝回填和工具循环继续的本机模拟服务集成场景。
6. 运行 OpenAI 现有测试，确认其请求和 UI 路径未增加权限组件。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/integration/test_anthropic_stream.py tests/integration/test_openai_stream.py -q
```

## T13：Windows ConPTY 端到端审批

**依赖：** T12  
**文件：** `tests/e2e/test_terminal_chat.py`

1. 添加 default 模式下写入或命令审批后执行场景。
2. 验证拒绝、本次允许和本会话允许三个选择。
3. 验证会话允许匹配时不再询问，关键参数变化时重新询问。
4. 验证危险命令直接拒绝且不出现允许选项。
5. 验证 `/permission` 切换、clear、状态显示和不请求 Provider。
6. 验证审批等待期间没有后续工具执行，Ctrl+C 可以安全取消。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q
```

## T14：完整回归与质量检查

**依赖：** T1–T13  
**文件：** 全部相关实现、测试和功能文档

1. 运行格式检查、静态检查和编译检查。
2. 运行完整自动化测试并记录实际通过、跳过和失败数量。
3. 检查安全配置和错误输出不包含密钥、环境变量或黑名单实现细节。
4. 检查工作区差异、临时文件和安装资源状态。
5. 按 checklist.md 逐项执行最终验收。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

## 执行顺序

```text
T1 → T2 ───────────┐
 ├→ T3 → T4 ───────┤
 ├→ T5 → T6 ───────┼→ T9 ─┐
 ├→ T7 ─────────────┘      │
 └→ T8 ───────→ T10 → T11 ├→ T12 → T13 → T14
                            ┘
```
