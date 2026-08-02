# YCode MCP 客户端与延迟工具加载 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | 声明 MCP SDK、HTTP、JSON Schema 和 dotenv 依赖 |
| 新建 | `.env.example` | 提供可提交的敏感变量名示例 |
| 修改 | `.ycode/config.example.yaml` | 增加 stdio、HTTP 和 `enabled` 示例 |
| 修改 | `README.md` | 说明 MCP、`.env`、权限和 `/mcp` 使用方式 |
| 修改 | `ycode/config/discovery.py` | 从配置路径确定项目根 |
| 新建 | `ycode/config/environment.py` | `.env`、EnvironmentResolver、SecretRedactor |
| 新建 | `ycode/config/mcp.py` | MCP 配置模型、逐项校验与错误隔离 |
| 修改 | `ycode/config/models.py` | 接受顶层 MCP 原始配置和加载结果 |
| 修改 | `ycode/config/loader.py` | 接入项目 `.env` 与 MCP 两阶段解析 |
| 修改 | `ycode/config/__init__.py` | 导出新增配置接口 |
| 新建 | `ycode/tools/arguments.py` | Pydantic 与 JSON Schema 参数适配器 |
| 修改 | `ycode/tools/contracts.py` | 通用参数契约、延迟标记和任务上下文 |
| 修改 | `ycode/tools/executor.py` | 统一参数校验与 MCP 超时错误码 |
| 修改 | `ycode/tools/registry.py` | 延迟定义过滤与稳定注册顺序 |
| 新建 | `ycode/tools/exposure.py` | 当前任务的发现状态 |
| 新建 | `ycode/tools/builtin/tool_search.py` | 本地 ToolSearch 工具 |
| 修改 | `ycode/tools/builtin/*.py` | 六个内建工具迁移到 Pydantic 参数适配器 |
| 修改 | `ycode/tools/builtin/__init__.py` | 导出 ToolSearch |
| 修改 | `ycode/tools/__init__.py` | 导出新增通用工具契约 |
| 新建 | `ycode/mcp/__init__.py` | MCP 客户端公共导出 |
| 新建 | `ycode/mcp/models.py` | 发现、状态和错误数据结构 |
| 新建 | `ycode/mcp/naming.py` | 工具名规范化与冲突检测 |
| 新建 | `ycode/mcp/connection.py` | 单 Server 传输、调用、重连和关闭 |
| 新建 | `ycode/mcp/manager.py` | 多 Server 启动、注册、状态和生命周期 |
| 新建 | `ycode/mcp/tool.py` | MCPToolWrapper 与结果转换 |
| 修改 | `ycode/security/models.py` | plan-only 白名单、警告和审批能力 |
| 修改 | `ycode/security/config.py` | 暂不可用 MCP 规则警告和参数校验 |
| 修改 | `ycode/security/engine.py` | plan-only MCP 强制审批 |
| 修改 | `ycode/security/__init__.py` | 导出新增安全结构 |
| 修改 | `ycode/prompt/models.py` | 增加 TOOL_CATALOG supplement |
| 修改 | `ycode/prompt/runtime.py` | 构造稳定的延迟工具 reminder |
| 修改 | `ycode/agent/events.py` | 增加 McpStatusEvent |
| 修改 | `ycode/agent/loop.py` | 每轮刷新、发现状态和隐藏工具拦截 |
| 修改 | `ycode/agent/__init__.py` | 导出状态事件 |
| 修改 | `ycode/session/chat.py` | 实现 `/mcp` 本地命令 |
| 新建 | `ycode/ui/mcp_status.py` | 启动摘要和状态表 |
| 修改 | `ycode/ui/input_box.py` | 支持禁止会话授权的审批界面 |
| 修改 | `ycode/ui/terminal.py` | 渲染 MCP 状态与启动摘要 |
| 修改 | `ycode/app.py` | 完整装配和异常路径资源关闭 |
| 新建 | `tests/unit/config/test_environment.py` | `.env` 与脱敏测试 |
| 新建 | `tests/unit/config/test_mcp.py` | MCP 配置测试 |
| 新建 | `tests/unit/tools/test_arguments.py` | 参数适配器测试 |
| 新建 | `tests/unit/tools/test_exposure.py` | 任务级发现状态测试 |
| 新建 | `tests/unit/tools/test_tool_search.py` | ToolSearch 测试 |
| 新建 | `tests/unit/mcp/test_models.py` | MCP 状态模型测试 |
| 新建 | `tests/unit/mcp/test_naming.py` | 名称规范化测试 |
| 新建 | `tests/unit/mcp/test_connection.py` | 连接状态机测试 |
| 新建 | `tests/unit/mcp/test_manager.py` | Manager 隔离和注册测试 |
| 新建 | `tests/unit/mcp/test_tool.py` | Wrapper 与结果转换测试 |
| 修改 | `tests/unit/config/*.py` | 更新配置加载返回值和兼容性断言 |
| 修改 | `tests/unit/tools/*.py` | 更新通用参数契约与 Registry 断言 |
| 修改 | `tests/unit/security/*.py` | 更新安全加载结果和 plan-only MCP 测试 |
| 修改 | `tests/unit/agent/test_loop.py` | 延迟工具跨轮次和防绕过测试 |
| 修改 | `tests/unit/session/test_chat.py` | `/mcp` 命令测试 |
| 修改 | `tests/unit/ui/*.py` | 状态表和审批选项测试 |
| 修改 | `tests/unit/test_app.py` | Anthropic MCP 装配及无配置回归 |
| 新建 | `tests/support/mcp_stdio_server.py` | 可控 stdio MCP 测试 Server |
| 新建 | `tests/support/mcp_http_server.py` | 可控 Streamable HTTP 测试 Server |
| 新建 | `tests/integration/test_mcp_stdio.py` | stdio 集成验证 |
| 新建 | `tests/integration/test_mcp_http.py` | HTTP JSON/SSE 集成验证 |
| 新建 | `tests/integration/test_mcp_protocol_fallback.py` | 新旧协议自动兼容验证 |
| 新建 | `tests/integration/test_mcp_agent_flow.py` | ToolSearch 到调用的 Agent 流程验证 |
| 修改 | `tests/e2e/test_terminal_chat.py` | Windows PTY MCP 完整场景 |

## T1：声明并安装运行依赖

**文件：** `pyproject.toml`
**依赖：** 无

**步骤：**

1. 增加 `mcp>=2,<3`、`httpx2>=2.5.0`、`jsonschema>=4.20.0` 和
   `python-dotenv>=1.0.0`。
2. 不添加 `mcp[cli]`，不改变现有开发依赖。
3. 按项目现有方式把新增运行依赖安装进 `.venv`。

**验证：** 运行
`.venv\Scripts\python.exe -c "import dotenv, httpx2, jsonschema, mcp"`，期望退出码为 0；
运行 `.venv\Scripts\python.exe -m pytest tests/unit/config/test_models.py -q`，期望现有配置
测试通过。

## T2：实现项目根解析

**文件：** `ycode/config/discovery.py`、`tests/unit/config/test_discovery.py`
**依赖：** 无

**步骤：**

1. 增加根据实际配置路径解析项目根的纯函数。
2. `.ycode/config.yaml` 返回 `.ycode` 的父目录；其他显式 YAML 返回文件所在目录。
3. 保留现有最近配置发现逻辑和错误消息。
4. 增加自动发现、显式标准路径和显式自定义路径测试。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config/test_discovery.py -q`，期望全部用例通过，
且现有配置发现行为不变。

## T3：实现严格 `.env` 与环境变量解析

**文件：** `ycode/config/environment.py`、`tests/unit/config/test_environment.py`
**依赖：** T1、T2

**步骤：**

1. 使用 UTF-8 和 `python-dotenv` 实现项目 `.env` 读取，缺失文件返回空映射。
2. 把无法读取、编码错误和解析错误转换为不含文件内容的 `ConfigError`。
3. 实现 `EnvironmentResolver.resolve()` 的系统环境优先级。
4. 实现一个或多个 `${VARIABLE}` 的字符串插值，缺失变量只报告变量名。
5. 禁用 `.env` 内部递归插值，确认不会修改 `os.environ`。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config/test_environment.py -q -k "dotenv or resolver or interpolate"`，
期望缺失文件、系统优先、嵌入式插值、语法错误和环境不污染用例通过。

## T4：实现统一敏感值脱敏

**文件：** `ycode/config/environment.py`、`tests/unit/config/test_environment.py`
**依赖：** T3

**步骤：**

1. 实现 `SecretRedactor.add()`，忽略空字符串并安全提取 `SecretStr`。
2. 实现文本中多个已知密钥的确定性替换。
3. 实现 JSON 标量、数组和对象的递归脱敏。
4. 确保对象字符串表示和错误路径不输出原始秘密。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config/test_environment.py -q -k redactor`，期望文本、
嵌套 JSON、重叠秘密和空值用例通过，输出中只出现 `[REDACTED]`。

## T5：定义 MCP 配置模型

**文件：** `ycode/config/mcp.py`、`ycode/config/models.py`、
`tests/unit/config/test_mcp.py`
**依赖：** T3

**步骤：**

1. 定义 stdio 与 Streamable HTTP 判别联合模型和默认超时。
2. 校验 Server snake_case 名称、command、HTTP/HTTPS URL、正数超时、env 名称和 Header
   换行。
3. 定义 `McpConfigIssue`、`McpConfigSet` 和 `LoadedAppConfig`。
4. 允许 `AppConfig` 接受可选顶层 `mcp_servers` 原始列表，保持无配置默认值。
5. 增加有效配置和所有字段约束测试。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config/test_mcp.py -q -k "model or defaults or validation"`，
期望两种传输和默认值用例通过，非法字段均产生稳定位置说明。

## T6：实现逐 Server 错误隔离与 `enabled`

**文件：** `ycode/config/mcp.py`、`tests/unit/config/test_mcp.py`
**依赖：** T4、T5

**步骤：**

1. 对 `mcp_servers` 每个条目独立校验，生成有效配置或 `McpConfigIssue`。
2. 检测重复名称并使全部同名条目无效。
3. 已启用条目展开 env/Header 并登记敏感值；缺失变量只停用该条目。
4. 禁用条目只校验非敏感结构，不解析 `${VARIABLE}`。
5. 确保 issue 只含索引、名称、错误码和脱敏消息。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config/test_mcp.py -q -k "isolation or duplicate or enabled or secret"`，
期望一个坏条目不影响其他条目，禁用条目缺少变量也保持 disabled。

## T7：接入总配置加载链

**文件：** `ycode/config/loader.py`、`ycode/config/models.py`、
`ycode/config/__init__.py`、`tests/unit/config/test_loader.py`、
`tests/unit/config/test_models.py`
**依赖：** T2、T3、T6

**步骤：**

1. 让 `load_config()` 返回 `LoadedAppConfig` 并保留活动 Provider 的两阶段校验。
2. Anthropic 路径从项目 `.env` 解析活动 API Key 和 MCP 配置。
3. OpenAI 路径保持现有 OS 环境变量行为，不解析或启动 MCP。
4. 顶层 YAML、`.env` 和活动 Provider 错误继续全局失败；单 MCP 条目错误保存在结果中。
5. 更新公共导出和所有现有配置单元测试。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/config -q`，期望全部通过；测试必须证明无
`mcp_servers` 时返回空集合且旧 Provider 配置行为不变。

## T8：实现通用工具参数适配器基础

**文件：** `ycode/tools/arguments.py`、`tests/unit/tools/test_arguments.py`
**依赖：** T1

**步骤：**

1. 定义 `ToolArguments` Protocol 和 `ToolArgumentValidationError`。
2. 定义稳定的参数错误详情结构。
3. 实现 `PydanticToolArguments` 的 Schema、字段名、校验和映射转换。
4. 把 Pydantic `ValidationError` 转换为统一错误，不包含原始输入值。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_arguments.py -q -k pydantic`，期望
Schema、字段名、合法参数和脱敏错误详情用例通过。

## T9：迁移现有工具契约到参数适配器

**文件：** `ycode/tools/contracts.py`、`ycode/tools/builtin/*.py`、
`ycode/tools/builtin/__init__.py`、`ycode/tools/__init__.py`、
`tests/unit/tools/test_contracts.py`、`tests/unit/tools/test_file_tools.py`、
`tests/unit/tools/test_search_tools.py`
**依赖：** T8

**步骤：**

1. 把 `ToolDefinition.arguments_model` 替换为 `arguments`，增加 `defer_loading` 和
   `timeout_error_code`。
2. 移除 `Tool` 泛型的 `BaseModel` 上界。
3. 六个内建工具改用 `PydanticToolArguments`，保持名称、Schema、access 和 timeout。
4. 更新公共导出和契约测试。
5. 确认内建工具执行参数仍是原 Pydantic 类型。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_contracts.py tests/unit/tools/test_file_tools.py tests/unit/tools/test_search_tools.py -q`，
期望全部通过，六个内建工具 Schema 与迁移前一致。

## T10：迁移执行器参数校验

**文件：** `ycode/tools/executor.py`、`tests/unit/tools/test_executor.py`
**依赖：** T9

**步骤：**

1. 使用 `definition.arguments.validate()` 替代直接 Pydantic 校验。
2. 把统一参数错误详情写入现有 `invalid_arguments` 结果。
3. 超时时使用 `definition.timeout_error_code`，内建工具仍返回 `timeout`。
4. 保留取消透传、ToolError 和内部错误隔离。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_executor.py -q`，期望合法调用、参数
失败、超时、取消和异常转换全部通过。

## T11：迁移现有安全引擎到参数适配器

**文件：** `ycode/security/config.py`、`ycode/security/engine.py`、
`tests/unit/security/test_config.py`、`tests/unit/security/test_engine.py`
**依赖：** T9

**步骤：**

1. 安全配置字段校验改用 `definition.arguments.field_names`。
2. PermissionEngine 规范化改用统一 validate/to_mapping。
3. 保留现有路径解析、危险命令、规则优先级和会话授权行为。
4. 更新测试构造的 ToolDefinition。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/security -q`，期望现有硬安全、plan-only、
规则匹配和失败关闭行为全部通过。

## T12：实现 JSON Schema 参数适配器

**文件：** `ycode/tools/arguments.py`、`tests/unit/tools/test_arguments.py`
**依赖：** T8

**步骤：**

1. 实现 `JsonSchemaToolArguments`，根据 `$schema` 选择 Validator，默认 2020-12。
2. 在构造期执行 `check_schema()` 并缓存 Validator。
3. 支持本地 fragment `$ref`，明确禁止外部 URL 和文件资源读取。
4. 只接受 JSON object 参数并返回 `FrozenJsonObject`。
5. 稳定排序验证错误并最多保留前 20 项。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_arguments.py -q -k json_schema`，期望
合法 Schema、本地引用、外部引用拒绝、嵌套路径和错误上限用例通过，测试期间无网络或
文件读取。

## T13：实现 MCP 名称规范化

**文件：** `ycode/mcp/naming.py`、`tests/unit/mcp/test_naming.py`
**依赖：** T9

**步骤：**

1. 实现 camel/Pascal 边界、连字符、点、非 ASCII 字符、大小写和下划线归一化。
2. 生成 `mcp_<server>_<normalized_tool>` 并通过 ToolDefinition 名称约束。
3. 实现规范化为空和同 Server 冲突双方排除。
4. 提供稳定排序的成功映射和冲突问题列表。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_naming.py -q`，期望大小写、连字符、
点、空结果、冲突双方和重复运行确定性用例通过。

## T14：定义 MCP 发现与状态模型

**文件：** `ycode/mcp/models.py`、`ycode/mcp/__init__.py`、
`tests/unit/mcp/test_models.py`
**依赖：** T7、T12、T13

**步骤：**

1. 定义连接状态枚举、错误摘要、Server 状态和总状态报告。
2. 定义 `McpToolDescriptor`、`McpDiscoveryResult` 和状态 Provider Protocol。
3. 实现成功、失败和禁用计数属性，保持配置顺序。
4. 对状态消息进行类型和非空约束，禁止保存敏感配置字段。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_models.py -q`，期望状态转换所需结构、
计数和不可变性用例通过。

## T15：实现 MCP 结果转换器

**文件：** `ycode/mcp/tool.py`、`tests/unit/mcp/test_tool.py`
**依赖：** T4、T14

**步骤：**

1. 按顺序转换 TextContent，并把 structuredContent 同时写入可读文本和 metadata。
2. 图片、音频、Resource 和 ResourceLink 只生成类型、MIME、URI 摘要。
3. 禁止整体序列化 SDK 内容对象，确保 Base64 和未支持正文不进入结果。
4. 处理 `is_error` 和无可见内容结果。
5. 对文本及结构化元数据应用 SecretRedactor。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_tool.py -q -k converter`，期望文本、
结构化数据、混合内容、二进制摘要、错误和密钥脱敏用例通过，结果中不存在测试 Base64。

## T16：实现两种 SDK Transport 构造

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T1、T6、T14

**步骤：**

1. stdio 使用 `StdioServerParameters` 和 `stdio_client()`，显式传入配置 env。
2. 实现有界脱敏 stderr sink，不把原始 stderr 直接打印到终端。
3. HTTP 使用自有 `httpx2.AsyncClient` 和 `streamable_http_client()`。
4. 把 Header、follow redirects 和读写超时放入 HTTP Client，不使用废弃 SSE transport。
5. transport 构造本身不执行连接。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py -q -k transport`，期望
stdio 参数、最小环境、HTTP Header、超时和 stderr 脱敏用例通过。

## T17：实现连接所有权任务与初始生命周期

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T16

**步骤：**

1. 创建长期所有权任务，在同一任务中进入和退出 Client/transport 上下文。
2. 使用 `Client(mode="auto")` 且不注册 roots、sampling 或 elicitation 回调。
3. 实现 STARTING、READY、UNAVAILABLE、CLOSING、CLOSED 状态更新。
4. 初始连接失败转换为脱敏错误摘要。
5. `close()` 可重复调用并等待同一关闭过程。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py -q -k "owner or lifecycle or close"`，
期望上下文进入退出各一次、状态顺序正确、重复关闭无异常。

## T18：实现分页发现与启动超时

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T12、T17

**步骤：**

1. 在 `startup_timeout_seconds` 内完成连接、协商和全部 tools/list 页。
2. 按页保存工具，并以 `next_cursor is None` 结束。
3. 检测重复 cursor，任一页失败时丢弃该 Server 的不完整目录。
4. 保存实际协议版本；忽略运行期工具变化通知。
5. 把 SDK 可解析但 Schema 无效的单工具问题保存在发现结果中。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py -q -k "discovery or pagination or startup_timeout"`，
期望多页、重复 cursor、页错误、单工具无效和超时用例通过。

## T19：实现工具调用、取消与单终态

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T17

**步骤：**

1. 实现 `call_tool()`，在 READY Client 上只发送一次 SDK 调用。
2. 跟踪在途 task 和 completion future，完成、超时、取消只允许一个终态。
3. 调用者取消时取消对应 SDK task 并等待传播，不取消其他 Server。
4. 关闭时拒绝新调用并取消全部在途任务。
5. 不自动重试任何已发送调用。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py -q -k "call or cancel or terminal"`，
期望成功、用户取消、关闭竞争、迟到完成和一次发送断言通过。

## T20：实现断线标记与后续调用重连

**文件：** `ycode/mcp/connection.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T18、T19

**步骤：**

1. 把传输/连接异常转换为 `mcp_connection_error` 并标记 DISCONNECTED。
2. 当前失败调用不关闭后重发，也不自动返回新连接结果。
3. 下一独立调用在发送前请求所有权任务建立全新 `Client(mode="auto")`。
4. 重连不执行 tools/list，不修改已缓存发现结果。
5. 重连失败保持 DISCONNECTED；关闭期间不允许重连。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py -q -k reconnect`，期望当前
调用只发送一次、下一调用重连、目录未刷新、重复重连失败和关闭竞争用例通过。

## T21：实现 MCPToolWrapper 与错误分类

**文件：** `ycode/mcp/tool.py`、`tests/unit/mcp/test_tool.py`
**依赖：** T10、T12、T15、T20

**步骤：**

1. 使用 `McpToolDescriptor` 创建 UNKNOWN、deferred、`mcp_timeout` 定义。
2. Wrapper 接收冻结 JSON，使用原始远端名调用所属 Connection。
3. 转换正常结果、MCP tool error、协议错误、连接错误和无效结果。
4. 不读取或采用远端安全 annotations，不实现自动重试。
5. 确保公开名不会写入远端 tools/call 的 name 字段。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_tool.py -q`，期望定义属性、原名调用、
结果转换和全部稳定错误码用例通过。

## T22：实现 Manager 状态目录与并发启动

**文件：** `ycode/mcp/manager.py`、`tests/unit/mcp/test_manager.py`
**依赖：** T6、T14、T18

**步骤：**

1. 为有效、禁用和配置无效条目按配置顺序创建状态项。
2. 使用 TaskGroup 并发启动所有已启用有效 Connection。
3. 单 Server 失败只更新 UNAVAILABLE，不中断其他任务或抛出全局异常。
4. 实现 snapshot 的可用、失败、禁用计数和最近错误。
5. 实现 Manager 的幂等并发关闭。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_manager.py -q -k "status or concurrent or isolation or close"`，
期望并发耗时、配置顺序、单失败、全失败、禁用和重复关闭用例通过。

## T23：实现发现结果适配、冲突处理与注册

**文件：** `ycode/mcp/manager.py`、`ycode/mcp/naming.py`、
`tests/unit/mcp/test_manager.py`
**依赖：** T13、T21、T22

**步骤：**

1. 所有发现任务完成后按配置顺序处理工具，而非按完成顺序。
2. 编译 Schema、生成公开名并排除单工具错误。
3. 对规范化冲突双方、Registry 已有名称和跨 Server 冲突生成明确问题。
4. 只注册全部检查通过的 MCPToolWrapper，保存公开名到远端名反向映射。
5. 状态工具数只统计有效注册工具；重连不重复注册。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_manager.py -q -k "register or collision or deterministic or invalid_tool"`，
期望注册顺序、冲突双方排除、内建保护和部分工具失败用例通过。

## T24：实现 Registry 延迟定义过滤

**文件：** `ycode/tools/registry.py`、`tests/unit/tools/test_registry.py`
**依赖：** T9

**步骤：**

1. 给 `definitions()` 增加 `exposed_deferred` 参数。
2. 非延迟工具继续按 allowed_access 过滤。
3. 延迟工具仅在明确暴露集合中返回；允许 plan-only 显式暴露 UNKNOWN。
4. 保持 Registry 注册顺序，不对集合迭代排序。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_registry.py -q`，期望内建过滤、初始
隐藏、显式暴露、UNKNOWN plan-only 和稳定顺序用例通过。

## T25：实现任务级 ToolExposureSession

**文件：** `ycode/tools/exposure.py`、`ycode/tools/contracts.py`、
`tests/unit/tools/test_exposure.py`
**依赖：** T24

**步骤：**

1. 实现 searchable_names、discovered_tools、activate、exposed_names 和 clear。
2. 名称输入去重并稳定排序，不可搜索名称返回 `not_found`。
3. 已发现名称返回 `already_loaded`，不重复改变集合。
4. 给 ToolContext 增加可选 exposure 前向类型，避免运行时循环依赖。
5. 验证两个实例完全隔离，clear 不影响 Registry。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_exposure.py -q`，期望激活、重复、
不可见、排序、隔离和清空用例通过。

## T26：实现本地 ToolSearch

**文件：** `ycode/tools/builtin/tool_search.py`、`ycode/tools/builtin/__init__.py`、
`ycode/tools/__init__.py`、`tests/unit/tools/test_tool_search.py`
**依赖：** T25

**步骤：**

1. 定义精确 `tool_names` 参数模型和 READ、非延迟工具定义。
2. 从 ToolContext 获取当前 exposure，从本地 Registry 读取名称和描述。
3. 返回按名称排序的 loaded/already_loaded/not_found。
4. 描述折叠空白并限制 160 字符，结果不包含 input_schema。
5. exposure 缺失时返回稳定内部上下文错误，不访问网络。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/tools/test_tool_search.py -q`，期望本地激活、短
描述、无 Schema、无远端调用和缺失上下文用例通过。

## T27：实现延迟工具 System Reminder

**文件：** `ycode/prompt/models.py`、`ycode/prompt/runtime.py`、
`tests/unit/prompt/test_models.py`、`tests/unit/prompt/test_runtime.py`
**依赖：** T25

**步骤：**

1. 增加 `SupplementKind.TOOL_CATALOG`。
2. 接受稳定排序的 searchable_names，生成只含名称和最小说明的 request supplement。
3. 空目录不生成 supplement。
4. 工具激活后 reminder 内容不变化，不包含描述、Schema 或秘密。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/prompt -q`，期望现有模式 reminder 不回归，
新目录稳定、空目录省略且不含 Schema。

## T28：在 AgentLoop 建立任务状态并逐轮刷新定义

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`
**依赖：** T24、T25、T26、T27

**步骤：**

1. 每条 `_run()` 创建独立空 ToolExposureSession 和携带它的 ToolContext。
2. Agent 模式目录包含全部延迟工具；plan-only 使用注入的白名单交集。
3. 把 definitions 计算移动到每次模型请求之前。
4. ToolSearch 执行后的下一轮加入新 Schema，无需新用户消息。
5. 在 finally 中覆盖完成、错误、取消和轮数上限的 clear。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q -k "deferred or exposure or next_round or reset"`，
期望首轮隐藏、下一轮出现、同任务保持、跨任务清空和取消清空用例通过。

## T29：实现模型请求快照与隐藏调用防绕过

**文件：** `ycode/agent/loop.py`、`tests/unit/agent/test_loop.py`
**依赖：** T28

**步骤：**

1. 每个模型请求保存 definitions 对应的 `advertised_names` 不可变快照。
2. 权限判定前预扫描延迟工具调用，隐藏位置生成 `tool_not_discovered`。
3. 权限循环跳过预拒绝位置，Scheduler 仍为每个调用 ID 产生结果。
4. 同批 ToolSearch 与隐藏 MCP 调用必须拒绝后者且不触发远端副作用。
5. 下一模型轮使用更新后的 exposure 正常暴露。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/agent/test_loop.py -q -k "not_discovered or same_batch or advertised"`，
期望两种批次顺序都拒绝隐藏调用、无审批且下一轮可用。

## T30：扩展安全配置与 MCP 警告

**文件：** `ycode/security/models.py`、`ycode/security/config.py`、
`ycode/security/__init__.py`、`tests/unit/security/test_models.py`、
`tests/unit/security/test_config.py`
**依赖：** T11、T23

**步骤：**

1. 增加 `PlanOnlySecurityConfig`、`SecurityConfigWarning` 和
   `SecurityConfigLoadResult`。
2. 解析精确 `plan_only.allow_mcp_tools`，拒绝非法或重复名称。
3. 未注册 `mcp_*` 规则或白名单项产生警告并继续；未知非 MCP 工具继续失败。
4. 已注册工具按 arguments.field_names 严格验证规则参数；不可用 MCP 暂不验证参数。
5. 更新所有调用点使用 load result 中的 config 和 warnings。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/security/test_models.py tests/unit/security/test_config.py -q`，
期望默认值、有效白名单、MCP 警告、非 MCP 失败和参数字段严格校验通过。

## T31：实现 plan-only MCP 每次强制审批

**文件：** `ycode/security/engine.py`、`ycode/security/models.py`、
`ycode/agent/loop.py`、`tests/unit/security/test_engine.py`、
`tests/unit/agent/test_loop.py`
**依赖：** T29、T30

**步骤：**

1. `evaluate()` 增加 plan_only 参数并识别已暴露、白名单内、UNKNOWN 延迟工具。
2. 保留项目 DENY；把项目 ALLOW、session grant 和 allow 权限模式降为 ASK。
3. 特例决策设置 `allow_session=False` 和稳定 reason_code。
4. Agent 模式现有 UNKNOWN 规则、审批和会话授权保持不变。
5. plan-only 非白名单或未发现调用继续由现有边界拒绝。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/security/test_engine.py tests/unit/agent/test_loop.py -q -k "plan_only and mcp"`，
期望每次 ASK、DENY 优先、ALLOW 不绕过、串行分类和默认不可见用例通过。

## T32：调整审批 UI 的会话授权选项

**文件：** `ycode/ui/input_box.py`、`tests/unit/ui/test_input_box.py`
**依赖：** T31

**步骤：**

1. 根据 `PermissionDecision.allow_session` 渲染两个或三个选项。
2. 禁止会话授权时不绑定键 3，只允许拒绝和本次允许。
3. 保留 Ctrl+C、普通工具审批和窄终端行为。
4. AgentLoop 防御性处理不允许的 ALLOW_SESSION，不保存 grant。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/ui/test_input_box.py tests/unit/agent/test_loop.py -q -k "approval or allow_session"`，
期望 plan-only MCP 不显示/不保存会话授权，普通审批仍有三个选项。

## T33：实现 `/mcp` 会话事件与命令

**文件：** `ycode/agent/events.py`、`ycode/agent/__init__.py`、
`ycode/session/chat.py`、`tests/unit/session/test_chat.py`
**依赖：** T14、T22

**步骤：**

1. 定义只携带脱敏 `McpStatusReport` 的 `McpStatusEvent`。
2. ChatSession 接受可选 McpStatusProvider。
3. 精确、大小写不敏感识别 `/mcp`；`/mcp xxx` 仍作为普通消息。
4. 命令只产生 UserMessageEvent 和状态事件，不创建 AgentTurn、不修改历史。
5. 没有状态提供者时返回 `mcp_unavailable` 且不调用模型。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/session/test_chat.py -q -k mcp`，期望状态快照、
无 Provider 调用、无历史、大小写和非精确命令用例通过。

## T34：实现 MCP 启动摘要与状态表

**文件：** `ycode/ui/mcp_status.py`、`ycode/ui/terminal.py`、
`tests/unit/ui/test_terminal.py`、`tests/unit/ui/test_header.py`
**依赖：** T33

**步骤：**

1. 用 Rich 构造可用/失败/未启用启动摘要和 Server 状态表。
2. 列固定为 Server、Transport、State、Tools、Recent error，按报告顺序显示。
3. TerminalUI 渲染 McpStatusEvent；无 MCP 配置时不增加启动区域。
4. 状态输出不包含 URL、command、Header、env 或原始异常。
5. 为窄终端和配置索引占位名称增加测试。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/ui/test_terminal.py tests/unit/ui/test_header.py -q -k mcp`，
期望摘要计数、状态列、窄终端和敏感字段排除用例通过。

## T35：完成应用装配与资源关闭

**文件：** `ycode/app.py`、`ycode/agent/loop.py`、`tests/unit/test_app.py`
**依赖：** T7、T23、T26、T30、T31、T34

**步骤：**

1. 按 Plan 顺序装配 Provider、内建 Registry、条件 ToolSearch、Manager、安全配置和 UI。
2. 只有 Anthropic 且存在 MCP 条目时创建 Manager；无配置保持六个工具和旧 UI。
3. 全部 disabled 时提供状态但不建立 Connection。
4. AgentLoop.close 先关闭 Manager 再关闭 Provider。
5. 中途配置/安全/UI 异常也关闭已创建的 Manager 和 Provider，不遗留资源。
6. OpenAI 继续 PlainChatRunner，`/mcp` 不调用模型。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/session/test_chat.py -q`，期望
无配置、部分失败、全失败、全禁用、中途异常和 OpenAI 回归用例通过。

## T36：补充示例配置和使用文档

**文件：** `.env.example`、`.ycode/config.example.yaml`、`README.md`
**依赖：** T7、T30、T33

**步骤：**

1. `.env.example` 只写变量名和无效占位值，不包含真实凭据。
2. config 示例加入一个 stdio、一个 HTTP 和一个 disabled Server。
3. README 说明项目根、系统环境优先、`enabled`、两种 timeout 和 Header 插值。
4. README 说明 `mcp_*` 名称、ToolSearch 下一轮生效、plan-only 白名单和每次审批。
5. README 说明 `/mcp`、重启更新目录、不支持 OAuth/Resources/Prompts/OpenAI MCP。

**验证：** 运行 `rg -n "mcp_servers|enabled|tool_search|/mcp|allow_mcp_tools" README.md .env.example .ycode/config.example.yaml`，
期望所有用户入口均有说明；运行
`.venv\Scripts\python.exe -m pytest tests/unit/config -q`，期望示例相关配置测试通过。

## T37：建立可控 stdio MCP 测试 Server

**文件：** `tests/support/mcp_stdio_server.py`
**依赖：** T1

**步骤：**

1. 使用官方 SDK 创建仅提供工具的 stdio Server fixture。
2. 提供文本、结构化结果、错误、慢调用、分页目录和进程退出控制。
3. 支持把调用次数、接收参数和关闭状态写入测试专用临时路径。
4. stdout 只输出协议消息，诊断写 stderr，禁止真实网络和外部凭据。

**验证：** 直接运行 fixture 的自检模式，期望进程启动后可控退出且 stdout 无非协议文本；
运行 `.venv\Scripts\python.exe -m compileall -q tests/support/mcp_stdio_server.py`，期望无错误。

## T38：完成 stdio 真实集成测试

**文件：** `tests/integration/test_mcp_stdio.py`、`tests/support/mcp_stdio_server.py`
**依赖：** T20、T23、T37

**步骤：**

1. 通过真实子进程验证自动协议协商、发现和文本/结构化调用。
2. 验证配置 env 送达但不会出现在状态、stderr 或结果中。
3. 验证同一 Server 多次调用只创建一个进程。
4. 验证调用取消、调用超时和正常关闭后子进程退出。
5. 验证非法 stdout/提前退出只影响该 Server。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/integration/test_mcp_stdio.py -q`，期望全部通过，测试结束
后不存在 fixture 子进程或后台读取任务。

## T39：完成 Streamable HTTP JSON/SSE 集成测试

**文件：** `tests/support/mcp_http_server.py`、`tests/integration/test_mcp_http.py`
**依赖：** T20、T23

**步骤：**

1. 创建本地临时端口 Streamable HTTP Server，支持普通 JSON 和请求级 SSE 响应。
2. 验证自定义 Header 与嵌入式变量正确发送且状态输出脱敏。
3. 验证 HTTP Client 连接复用、调用取消、超时和关闭。
4. 验证异常状态、Content-Type 和损坏 SSE 只影响对应请求/Server。
5. 明确断言未使用废弃 HTTP+SSE transport。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/integration/test_mcp_http.py -q`，期望 JSON、请求级
SSE、Header、连接复用、异常隔离和资源关闭用例通过。

## T40：验证 2026 与 2025 协议自动兼容

**文件：** `tests/integration/test_mcp_protocol_fallback.py`、
`tests/support/mcp_stdio_server.py`、`tests/support/mcp_http_server.py`
**依赖：** T38、T39

**步骤：**

1. 提供接受 2026-07-28 server/discover 的现代场景。
2. 提供拒绝 discover 并接受 2025-11-25 initialize 的旧版场景。
3. 使用完全相同的 YCode Server 配置验证自动选择，不允许配置版本号。
4. 断言实际 protocol_version，且 roots/sampling/elicitation 能力未声明。
5. Server 发起未支持请求时确认 SDK 安全拒绝且 YCode 不崩溃。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/integration/test_mcp_protocol_fallback.py -q`，期望现代直连、
旧版回退、能力为空和不支持请求用例通过。

## T41：验证异步匹配、迟到响应和断线恢复

**文件：** `tests/integration/test_mcp_stdio.py`、
`tests/integration/test_mcp_http.py`、`tests/unit/mcp/test_connection.py`
**依赖：** T20、T38、T39

**步骤：**

1. 并发发出可区分参数的请求，让 Server 乱序返回并断言 ID 对应正确。
2. 注入迟到、未知 ID、重复响应和损坏消息，确认不误交付、不崩溃。
3. 让当前调用在可能已执行后断线，断言不自动重试。
4. 下一独立调用触发重连并成功，调用计数证明没有重复副作用。
5. 取消、超时、连接关闭和正常响应竞争时断言只产生一个结果。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/mcp/test_connection.py tests/integration/test_mcp_stdio.py tests/integration/test_mcp_http.py -q -k "concurrent or late or unknown_id or duplicate or reconnect or race"`，
期望全部竞争与恢复断言通过。

## T42：完成延迟工具 Agent 集成流程

**文件：** `tests/integration/test_mcp_agent_flow.py`
**依赖：** T29、T31、T35、T38

**步骤：**

1. 使用 Fake Anthropic Provider 和真实 stdio MCP Server 构造完整 Agent 请求序列。
2. 首轮断言只有内建工具、ToolSearch 和名称 reminder，不含 MCP Schema。
3. ToolSearch 后下一轮只增加被激活工具，结果历史不含 Schema。
4. 审批前远端调用计数为零，允许一次后完成调用并生成最终回答。
5. 覆盖同批绕过、跨任务清空、plan-only 默认隐藏和白名单每次审批。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/integration/test_mcp_agent_flow.py -q`，期望延迟暴露、
权限顺序、任务隔离和最终回答场景全部通过。

## T43：补齐应用级降级与无配置回归

**文件：** `tests/unit/test_app.py`、`tests/unit/agent/test_loop.py`、
`tests/unit/providers/test_openai.py`
**依赖：** T35、T42

**步骤：**

1. 验证无 `mcp_servers` 时仍只有六个内建工具，无 ToolSearch、reminder 和摘要。
2. 验证一个、全部 Server 失败时 Anthropic Agent 和内建工具仍启动。
3. 验证全部 disabled 不创建连接但 `/mcp` 可显示状态。
4. 验证安全配置暂不可用 MCP 引用只警告。
5. 验证 OpenAI PlainChatRunner 不创建 MCP 组件且现有请求完全不变。

**验证：** 运行
`.venv\Scripts\python.exe -m pytest tests/unit/test_app.py tests/unit/agent/test_loop.py tests/unit/providers/test_openai.py -q`，
期望降级、无配置和 OpenAI 回归用例全部通过。

## T44：完成 Windows PTY 端到端场景

**文件：** `tests/e2e/test_terminal_chat.py`、`tests/support/mcp_stdio_server.py`
**依赖：** T34、T35、T38、T42

**步骤：**

1. 配置真实终端测试项目、`.env`、Anthropic 测试响应和 stdio MCP Server。
2. 验证启动摘要与 `/mcp` 状态。
3. 让模型依次调用 ToolSearch、下一轮 MCP 工具并等待本次审批。
4. 模拟用户选择本次允许，验证远端结果和最终回答。
5. `/exit` 后验证终端恢复、MCP 子进程退出且敏感值未出现在屏幕。

**验证：** 在 Windows 运行
`.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k mcp`，期望真实 PTY 场景
通过且无残留子进程。

## T45：执行完整验收前验证

**文件：** 全部本功能文件和现有测试
**依赖：** T36、T40、T41、T42、T43、T44

**步骤：**

1. 运行格式检查并修正格式。
2. 运行静态检查并修正新增告警。
3. 运行编译检查。
4. 运行完整测试，确认现有和新增场景全部通过。
5. 检查工作树，只保留本功能相关文件且不创建 Git commit。

**验证：** 依次运行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条命令退出码均为 0；完整测试包含 stdio、Streamable HTTP、两代协议、延迟加载、
plan-only、断线恢复和 Windows PTY 场景。

## 执行顺序

```text
T1 → T3 → T4
 │    └──────→ T5 → T6
 └→ T8 → T9 → T10 → T11
             └──────→ T12 → T13 → T14 → T15

T2 → T3
T2 + T6 → T7

T14 + T16 → T17 → T18
                  └→ T19 → T20 → T21
T18 + T14 → T22 → T23

T9 → T24 → T25 → T26
              └→ T27
T26 + T27 → T28 → T29
T11 + T23 → T30 → T31 → T32

T14 + T22 → T33 → T34
T7 + T23 + T26 + T30 + T31 + T34 → T35 → T36

T1 → T37 → T38
T20 + T23 → T39
T38 + T39 → T40 → T41
T29 + T31 + T35 + T38 → T42 → T43
T34 + T35 + T38 + T42 → T44
T36 + T40 + T41 + T42 + T43 + T44 → T45
```

可并行分支：

- T2 与 T8 可并行。
- T13、T15 与 T16 在各自依赖满足后可并行。
- T24–T29 的延迟暴露分支可与 T16–T23 的连接分支部分并行。
- T37 测试 Server 可在连接实现期间提前准备。

## Plan 覆盖检查

| Plan 组件 | 对应任务 |
|---|---|
| 项目根、`.env`、变量解析、脱敏 | T2–T7 |
| 通用参数契约与 Pydantic 兼容 | T8–T11 |
| JSON Schema 与名称规范化 | T12–T13 |
| MCP 数据结构和结果转换 | T14–T15、T21 |
| stdio/HTTP 连接、发现、取消、重连 | T16–T20 |
| 多 Server Manager、注册和状态 | T22–T23 |
| 延迟目录、ToolSearch 和 reminder | T24–T27 |
| Agent 每轮刷新和防绕过 | T28–T29 |
| 安全配置与 plan-only 强制审批 | T30–T32 |
| `/mcp` 与 UI | T33–T34 |
| 应用装配、文档和无配置回归 | T35–T36、T43 |
| 两种传输和两代协议验证 | T37–T41 |
| Agent 集成和 Windows PTY | T42、T44 |
| 全量验证 | T45 |

Plan 中所有组件均至少有一个实现或验证任务。
