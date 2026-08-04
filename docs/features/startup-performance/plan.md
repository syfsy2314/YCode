# YCode 启动性能优化 Plan

> 状态：已批准

## 架构概览

本功能由 Provider 延迟加载、MCP 后台生命周期和启动状态展示三部分组成。

Provider 工厂不再在模块顶层导入具体实现，而是在 `create_provider()` 根据已解析的协议
执行局部导入。YAML 顶层 `active` 仍由现有配置加载器解析，工厂只接收最终选中的
`ProviderConfig`。

`McpManager` 新增由自身持有的后台启动任务。应用完成内置工具、权限、Agent 和会话装配后
启动该任务，但不等待任务完成，随后立即进入 Terminal UI。`McpConnection` 在启动阶段收到
关闭请求时取消连接所有者任务，避免退出继续等待启动超时。

Tool Registry 保持现有可变注册结构。Agent 每个新回合都会从注册表创建工具快照，因此
后台连接成功后注册的 MCP 工具自然对后续回合可见，不加入已经开始的回合。

## 核心接口

### Provider 工厂

公共接口保持不变：

```python
def create_provider(config: ProviderConfig) -> ChatProvider: ...
```

内部按 `ProviderProtocol` 分支局部导入 `AnthropicProvider` 或 `OpenAIProvider`。不使用动态
插件发现、字符串模块路径或全局 Provider 类映射。

### McpManager

```python
class McpManager:
    def start_background(self) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
```

内部新增：

```python
_start_task: asyncio.Task[None] | None
_startup_callbacks: list[Callable[[], None]]
```

- `start_background()` 幂等创建启动任务并立即返回。
- `start()` 复用同一个任务并等待完成，保留现有直接调用语义。
- 启动任务继续使用 `TaskGroup` 并行连接全部启用 Server。
- 完成回调只用于刷新依赖已注册 MCP 工具的安全警告；回调异常转换为安全状态，不泄漏到
  事件循环。
- `close()` 先取消未完成的启动任务，再关闭全部连接。

### McpConnection

`close()` 继续幂等。关闭前状态为 `STARTING` 或 `RECONNECTING` 时取消 `_owner_task`；状态为
`READY` 时设置 `_close_requested`，让所有者任务正常退出。两条路径最终都关闭 transport、
HTTP Client 和 stderr sink。

### MCP 状态报告

`McpStatusReport` 增加只读统计：

```python
@property
def starting_count(self) -> int: ...
```

统计 `STARTING` 和 `RECONNECTING` 状态。首屏摘要在该值大于零时显示“后台连接 N”；后台
完成时不主动向终端打印消息，最终明细通过 `/mcp` 查询。

## 模块交互

### 应用启动

```text
run_app()
→ 解析 active Provider
→ create_provider() 只导入对应 SDK
→ 创建内置 Tool Registry
→ 创建 McpManager
→ 有启用 MCP 时提前注册 ToolSearchTool
→ 使用当前注册表读取安全配置并创建 PermissionEngine
→ 创建 AgentLoop、ChatSession 与 TerminalUI
→ McpManager.start_background()
→ TerminalUI.run() 立即显示首屏和输入框
```

### MCP 后台完成

```text
McpManager 后台并行连接
→ 成功连接并发现工具
→ 按现有规则注册 MCPToolWrapper
→ 运行安全警告刷新回调
→ 状态变为 ready
→ 不主动打印完成消息
→ 下一 Agent 回合读取更新后的 Tool Registry
```

连接失败或超时由 `McpConnection` 转换为 `unavailable`，不影响 Manager 的其他连接和 UI。

### 应用退出

```text
TerminalUI 退出
→ ChatSession.close()
→ AgentLoop.close()
→ McpManager.close()
→ 取消未完成的 Manager 启动任务
→ 取消仍在 STARTING / RECONNECTING 的连接所有者任务
→ 正常关闭 READY 连接
→ Provider.close()
```

## 文件组织

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `ycode/providers/factory.py` | 按协议局部导入 Provider |
| 修改 | `ycode/config/mcp.py` | MCP 默认启动超时改为 5 秒 |
| 修改 | `ycode/mcp/models.py` | 后台连接数量统计 |
| 修改 | `ycode/mcp/manager.py` | 后台启动任务、完成回调和关闭协调 |
| 修改 | `ycode/mcp/connection.py` | 启动中连接的快速取消 |
| 修改 | `ycode/app.py` | 后台启动 MCP 并立即进入 UI |
| 修改 | `ycode/ui/mcp_status.py` | 首屏后台连接摘要 |
| 修改 | `.ycode/config.example.yaml` | 默认超时注释 |
| 修改 | `.ycode/config.yaml` | 默认值与显式覆盖注释 |
| 修改 | `tests/unit/providers/test_factory.py` | Provider 导入隔离测试 |
| 修改 | `tests/unit/config/test_mcp.py` | 默认值和显式覆盖测试 |
| 修改 | `tests/unit/mcp/test_models.py` | 后台连接统计测试 |
| 修改 | `tests/unit/mcp/test_manager.py` | 后台启动、幂等和取消测试 |
| 修改 | `tests/unit/mcp/test_connection.py` | 启动中关闭测试 |
| 修改 | `tests/unit/test_app.py` | UI 与 MCP 启动事件顺序测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | 首屏摘要测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 本地慢 MCP 非阻塞启动场景 |

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Provider 加载 | 协议分支内局部导入 | 最小改动即可避免未激活 SDK 加载 |
| MCP 任务所有权 | `McpManager` 持有 | 统一启动、异常消费、取消和关闭 |
| 现有 `start()` | 等待同一后台任务 | 保持直接调用者和现有测试语义 |
| UI 通知 | 首屏显示连接数，完成后静默 | 避免异步输出破坏输入和流式渲染 |
| 最终状态 | `/mcp` 主动查询 | 复用现有状态表格，不增加事件系统 |
| 工具生效 | 下一 Agent 回合 | 复用现有回合工具快照 |
| 安全警告 | 后台完成后刷新一次 | 避免连接中的 MCP 永久标记为不可用 |
| 超时 | 默认 5 秒、显式值优先 | 缩短默认失败窗口并保持配置兼容 |
| 测试 | 新子进程和本地受控慢 MCP | 不依赖模块缓存、机器速度或真实网络 |

## 测试策略

- 在全新 Python 子进程创建 Anthropic Provider，检查 `sys.modules` 不包含 OpenAI SDK 与
  Provider 模块。
- 用事件控制的连接替身验证 `start_background()` 立即返回、`start()` 可等待同一任务，
  `close()` 能取消启动。
- 在应用测试中让 MCP 启动阻塞，断言 UI 已运行；释放 MCP 后断言后续回合可看到工具。
- 验证首屏摘要显示后台连接数量，后台完成不产生主动终端消息。
- Windows PTY 使用本地慢 MCP Server 验证输入框先于连接完成出现，并通过 `/mcp` 查询
  最终状态。
- 最终运行格式、静态、编译和完整测试；不访问真实 Context7 或真实模型。
