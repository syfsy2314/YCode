# YCode 启动性能优化 Tasks

> 状态：已批准

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `ycode/providers/factory.py` | 根据活动协议延迟导入 Provider |
| 修改 | `ycode/config/mcp.py` | MCP 默认启动超时 |
| 修改 | `ycode/mcp/models.py` | 后台连接状态统计 |
| 修改 | `ycode/mcp/connection.py` | 启动中连接取消 |
| 修改 | `ycode/mcp/manager.py` | 后台启动任务及生命周期 |
| 修改 | `ycode/app.py` | 非阻塞 MCP 装配 |
| 修改 | `ycode/ui/mcp_status.py` | 首屏后台连接摘要 |
| 修改 | `.ycode/config.example.yaml` | 默认超时说明 |
| 修改 | `.ycode/config.yaml` | 默认值与显式覆盖注释 |
| 修改 | `tests/unit/providers/test_factory.py` | Provider 导入隔离 |
| 修改 | `tests/unit/config/test_mcp.py` | 超时配置测试 |
| 修改 | `tests/unit/mcp/test_models.py` | 状态统计测试 |
| 修改 | `tests/unit/mcp/test_connection.py` | 连接取消测试 |
| 修改 | `tests/unit/mcp/test_manager.py` | Manager 后台生命周期测试 |
| 修改 | `tests/unit/test_app.py` | 应用启动事件顺序测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | 首屏摘要测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | Windows PTY 非阻塞 MCP 场景 |

## T1：实现 Provider 按活动协议延迟导入

**文件：** `ycode/providers/factory.py`、`tests/unit/providers/test_factory.py`  
**依赖：** 无

**步骤：**

1. 删除模块顶层的具体 Provider 导入和类对象映射。
2. 在 `create_provider()` 内按 `ProviderProtocol` 分支局部导入并创建对应实现。
3. 保持未知协议的现有安全错误语义。
4. 使用全新 Python 子进程创建 Anthropic Provider，验证 `openai` 和
   `ycode.providers.openai` 未进入 `sys.modules`。
5. 保留现有工厂路由测试，不修改 OpenAI Provider 内部实现或增加专用行为测试。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/providers/test_factory.py
```

期望工厂路由与 Anthropic 导入隔离测试全部通过。

## T2：修改 MCP 默认超时并补充 YAML 注释

**文件：** `ycode/config/mcp.py`、`.ycode/config.example.yaml`、`.ycode/config.yaml`、
`tests/unit/config/test_mcp.py`  
**依赖：** 无

**步骤：**

1. 将 `startup_timeout_seconds` 默认值从 10 秒改为 5 秒。
2. 验证省略字段得到 5 秒，显式 10 秒仍得到 10 秒。
3. 在示例 YAML 中注明默认值为 5 秒。
4. 在当前 YAML 的显式 10 秒字段旁注明其覆盖默认值，不改变字段值。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/config/test_mcp.py
```

期望默认值、显式覆盖和现有独立条目校验测试全部通过。

## T3：增加后台连接统计与首屏摘要

**文件：** `ycode/mcp/models.py`、`ycode/ui/mcp_status.py`、
`tests/unit/mcp/test_models.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** 无

**步骤：**

1. 为状态报告增加 `starting_count`，统计 `STARTING` 与 `RECONNECTING`。
2. 首屏摘要在计数大于零时显示“后台连接 N”。
3. 保持 ready、failed、disabled 和安全警告统计内容。
4. 测试零个、一个和多个后台连接状态的摘要。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/mcp/test_models.py tests/unit/ui/test_terminal.py -k "mcp or summary or header"
```

期望状态计数与终端首屏摘要测试全部通过。

## T4：实现启动中 MCP Connection 的快速关闭

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`  
**依赖：** 无

**步骤：**

1. 在关闭前保存连接原状态。
2. `STARTING` 或 `RECONNECTING` 状态关闭时取消所有者任务。
3. `READY` 状态继续使用关闭事件正常结束。
4. 所有路径继续关闭 transport、HTTP Client、stderr sink 和活动工具任务。
5. 使用受控阻塞 transport 验证关闭不等待剩余启动超时且保持幂等。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/mcp/test_connection.py -k "close or cancel or startup"
```

期望启动取消、正常关闭和现有连接状态测试全部通过。

## T5：让 McpManager 持有后台启动生命周期

**文件：** `ycode/mcp/manager.py`、`tests/unit/mcp/test_manager.py`  
**依赖：** T4

**步骤：**

1. 增加唯一 `_start_task` 和幂等 `start_background()`。
2. 让 `start()` 启动或复用同一任务并等待完成。
3. 保留连接并行启动、工具注册、冲突报告和稳定状态顺序。
4. 提供一次性启动完成回调，用于应用刷新安全警告；安全消费回调异常。
5. `close()` 取消未完成启动任务并关闭全部连接。
6. 测试立即返回、共享任务、成功注册、失败隔离、回调和取消关闭。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/mcp/test_manager.py
```

期望 Manager 现有注册测试和新增后台生命周期测试全部通过。

## T6：在 Anthropic 应用装配中后台启动 MCP

**文件：** `ycode/app.py`、`tests/unit/test_app.py`  
**依赖：** T3、T5

**步骤：**

1. 创建 Manager 后不再 `await manager.start()`。
2. 有启用 MCP 时提前注册 `ToolSearchTool`。
3. 完成权限、AgentLoop 和 ChatSession 装配后调用 `start_background()`，随即运行 UI。
4. 注册完成回调重新计算 MCP 相关安全警告并更新 Manager 状态报告。
5. 保持禁用、无效和不可用 MCP 不阻止 Anthropic Agent 使用内置工具。
6. 使用受控 Manager 断言 UI 运行早于 MCP 完成，关闭通过现有 Session → Runner → Manager
   链路完成。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_app.py
```

期望 Anthropic 应用装配、事件顺序和现有资源关闭测试全部通过。

## T7：执行 MCP 与命令状态集成回归

**文件：** `tests/unit/mcp/`、`tests/integration/test_mcp_*.py`、
`tests/unit/ui/test_terminal.py`  
**依赖：** T2–T6

**步骤：**

1. 验证 stdio、HTTP、协议回退与 Agent 工具调用保持通过。
2. 验证后台完成后下一 Agent 回合可发现工具，已经开始的回合不动态注入。
3. 验证 `/mcp` 在连接中、成功和失败状态下显示正确。
4. 验证后台完成没有主动终端输出或未处理任务警告。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/mcp tests/unit/ui/test_terminal.py tests/integration/test_mcp_agent_flow.py tests/integration/test_mcp_http.py tests/integration/test_mcp_protocol_fallback.py tests/integration/test_mcp_stdio.py
```

期望 MCP 核心、终端状态和本地集成测试全部通过。

## T8：完成 Windows PTY 非阻塞启动场景

**文件：** `tests/e2e/test_terminal_chat.py`  
**依赖：** T6、T7

**步骤：**

1. 使用本地慢 MCP Server，让连接完成时间晚于 UI 首屏。
2. 观察输入框和“后台连接 1”摘要先出现。
3. 在连接期间执行 `/mcp`，观察 `starting`。
4. 等待本地 Server 就绪后再次执行 `/mcp`，观察 `ready` 与工具数量。
5. 验证后台完成没有插入通知，退出无堆栈且不访问真实 API。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k mcp_background_startup
```

期望真实 Windows PTY 的 MCP 后台启动场景通过。

## T9：执行完整质量检查

**文件：** 全部本功能文件  
**依赖：** T1–T8

**步骤：**

1. 运行格式、静态和编译检查。
2. 运行完整测试套件，修复本功能引入的回归。
3. 检查命令框架、会话和 Anthropic 端到端流程保持通过。
4. 确认没有新增依赖、OpenAI Provider 内部改动或真实网络测试。
5. 确认工作区已有未提交改动未被覆盖或清理。

**验证：**

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条质量命令全部通过。

## 执行顺序

```text
T1 ───────────────────────────────┐
T2 ────────────────────────┐      │
T3 ────────────────┐       │      │
T4 → T5 → T6 ──────┴→ T7 → T8 ───┴→ T9
```

T1、T2、T3 和 T4 相互独立；T5 依赖连接可取消能力，T6 依赖 Manager 生命周期和状态摘要，
随后统一进行集成、PTY 与全量验证。
