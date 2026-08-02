# YCode MCP 客户端与延迟工具加载 Checklist

> 每一项都通过运行代码、自动化测试或真实交互观察来验证，聚焦外部行为和安全边界。

## 配置、`.env` 与敏感信息（AC1）

- [x] 项目根 `.env` 中的变量可以为活动 Anthropic API Key 提供值，YCode 能正常启动。
  （验证：只在 `.env` 设置测试 Key，运行配置加载及应用启动测试，期望 Provider 收到该
  值且启动成功。）
- [x] `.env` 变量可以用于 stdio Server 的显式 env，子进程能读取对应值。
  （验证：运行 stdio 集成测试，让测试 Server 返回是否收到变量，期望收到但响应和状态中
  不出现原值。）
- [x] `.env` 变量可以用于 HTTP Header，包括 `Bearer ${TOKEN}` 形式的嵌入式插值。
  （验证：运行 HTTP 集成测试，期望测试 Server 收到完整 Header，终端和状态中不出现
  Token。）
- [x] 系统环境和 `.env` 同名时使用系统环境值。
  （验证：同时设置两个不同值运行配置测试，期望解析结果为系统环境值。）
- [x] 读取 `.env` 不会向 `os.environ` 写入新变量，也不会递归展开 `.env` 内部引用。
  （验证：运行环境解析单元测试，对比调用前后进程环境并检查嵌套引用结果。）
- [x] 缺少 `.env` 时仍可使用明文或系统环境配置正常启动。
  （验证：在无 `.env` 临时项目运行配置和应用测试，期望启动成功。）
- [x] `.env` 无法读取、不是 UTF-8 或语法错误时以配置错误阻止启动。
  （验证：分别构造三类文件运行配置测试，期望非零启动结果和不含文件内容的错误。）
- [x] `enabled` 未声明时 Server 默认启用。
  （验证：省略字段运行配置及 Manager 测试，期望执行连接和发现。）
- [x] `enabled: false` 时不解析该 Server 的敏感变量、不连接、不发现、不注册工具。
  （验证：禁用条目引用不存在变量并配置不可达端点，期望仍显示 disabled，连接和发现计数
  均为零。）
- [x] 顶层 YAML 或 `mcp_servers` 结构损坏会阻止启动，单个 Server 字段或变量错误只停用
  该 Server。（验证：运行配置隔离测试，期望全局错误退出，而同列表中的其他有效 Server
  仍可用。）
- [x] API Key、stdio env、HTTP Header 值不会出现在异常、stderr、启动摘要、`/mcp`、
  日志或 Agent 工具结果中。（验证：使用唯一哨兵秘密运行单元、集成和 PTY 测试，对全部
  捕获输出执行字符串搜索，期望零命中。）
- [x] 未配置 `mcp_servers` 时配置加载、六个内建工具和启动界面保持原行为。
  （验证：运行无 MCP 的现有配置、应用、Agent 和 PTY 测试，期望无 ToolSearch、MCP
  reminder 或摘要。）

## stdio 与 Streamable HTTP（AC2）

- [x] stdio Server 能被启动、完成工具发现并返回工具调用结果。
  （验证：运行 `tests/integration/test_mcp_stdio.py`，期望真实子进程场景通过。）
- [x] 同一 stdio Server 的多次调用复用一个子进程和连接。
  （验证：测试 Server 记录启动 PID 和次数，连续调用后期望只有一个 PID、一次启动。）
- [x] stdio stdout 只作为协议通道，stderr 被持续排空且不会阻塞子进程。
  （验证：让 Server 产生大量 stderr 后调用工具，期望调用完成、状态可用且输出已脱敏。）
- [x] Streamable HTTP 能处理普通 JSON 响应。
  （验证：运行 HTTP 集成测试的 JSON 场景，期望发现和调用完成。）
- [x] Streamable HTTP 能处理请求级 SSE 响应。
  （验证：运行 HTTP 集成测试的 SSE 场景，期望流式响应被组合为正确调用结果。）
- [x] HTTP 自定义 Header 能正确发送但不会出现在 `/mcp` 或错误中。
  （验证：测试 Server 断言 Header，随后查询状态并注入 HTTP 错误，搜索输出中无 Header
  值。）
- [x] HTTP 调用复用底层 Client 和连接池。
  （验证：连续调用同一端点，测试连接标识或连接创建计数保持复用。）
- [x] 没有使用废弃 HTTP+SSE transport，也没有启动 OAuth 登录流程。
  （验证：运行传输构造单元测试和 HTTP 集成测试，期望仅创建 Streamable HTTP Client，
  无浏览器、回调端口或 OAuth 交互。）

## 协议自动兼容与客户端能力（AC3）

- [x] 面对 2026-07-28 Server 时优先使用现代协议并记录实际版本。
  （验证：运行协议兼容集成测试，期望 `protocol_version` 为 `2026-07-28` 且 Server 收到
  discover 路径。）
- [x] 面对不支持 discover 的 Server 时自动回退并完成 2025-11-25 initialize 握手。
  （验证：运行旧版协议场景，期望无需修改配置即可发现和调用工具。）
- [x] 新旧协议 Server 使用相同配置结构，不接受或要求用户配置协议版本。
  （验证：用同一配置模型分别连接两类 fixture，期望均成功；包含 version 字段时按额外
  字段规则拒绝。）
- [x] 客户端不声明 roots、sampling 或 elicitation 能力。
  （验证：测试 Server 读取客户端能力，期望三项均不存在。）
- [x] Server 发起未支持的客户端请求时被安全拒绝，YCode 不执行模型代理或工作区暴露。
  （验证：运行协议回退测试的反向请求场景，期望稳定协议错误且无 Provider/文件访问。）
- [x] 进度、日志和工具变化通知不会导致 YCode 崩溃。
  （验证：测试 Server 在调用期间发送通知，期望调用正常完成或产生受控错误。）

## 多 Server、降级启动与目录发现（AC4、AC5）

- [x] 多个已启用 Server 并发连接和发现，启动耗时不等于各自超时之和。
  （验证：两个延迟 Server 同时启动并计时，期望总耗时接近最长单项而非相加。）
- [x] 多个 Server 的工具都能被独立发现和调用，响应不会跨 Server 串交。
  （验证：让两个 Server 返回不同哨兵结果，分别调用公开工具名，期望结果对应正确。）
- [x] 禁用 Server 不参与成功/失败计数，并在状态中显示 disabled。
  （验证：混合 ready、failed、disabled 配置查询摘要和 `/mcp`，期望三个计数准确。）
- [x] 一个 Server 配置错误、超时、退出或返回非法协议消息时，其他 Server 与内建工具
  继续可用。（验证：运行 Manager 隔离及两种传输异常测试，期望只有目标状态失败。）
- [x] 所有 MCP Server 均失败时，Anthropic Agent 仍能使用六个内建工具启动。
  （验证：配置全部不可达 Server 运行应用测试，期望 UI 启动且内建工具可执行。）
- [x] 启动摘要准确显示成功、失败、未启用数量和脱敏原因。
  （验证：渲染混合状态报告，对比表格内容与连接实际状态。）
- [x] tools/list 的所有分页结果都进入固定目录。
  （验证：测试 Server 返回至少三页工具，期望工具数和公开名称完整。）
- [x] 重复 cursor、分页中途失败不会造成无限循环，也不会注册不完整目录。
  （验证：运行分页异常测试，期望目标 Server 快速失败、工具数为零。）
- [x] 单个 SDK 可解析但 JSON Schema 无效的工具被排除，同一 Server 其他工具仍注册。
  （验证：一页返回一个有效和一个无效工具，期望状态工具数为一并报告无效原因。）
- [x] 运行期间工具列表变化通知不会修改当前 Registry；重启后才看到变化。
  （验证：启动后发送变化通知并再次生成模型工具列表，期望不变；重启并重新发现后变化
  生效。）
- [x] 远端名称按统一规则转换为 `mcp_<server>_<tool>`。
  （验证：运行名称单元测试，覆盖 camelCase、PascalCase、连字符、点和大小写。）
- [x] 相同配置和目录重复启动会产生相同公开名称和注册顺序。
  （验证：随机化异步完成顺序多次启动，比较完整名称序列，期望完全一致。）
- [x] 两个远端名称规范化冲突时双方都不注册，并显示具体冲突。
  （验证：配置 `find-item` 与 `find.item`，期望两者均缺失且状态问题包含原名。）
- [x] MCP 工具不能覆盖内建工具、ToolSearch 或其他 Server 已注册名称。
  （验证：注入重复公开名，期望后注册工具被排除、原工具仍可调用。）
- [x] 公开名称调用最终发送的是 Server 的原始远端工具名。
  （验证：测试 Server 记录 tools/call name，期望收到原名而不是 `mcp_` 名称。）

## 初始延迟暴露与 ToolSearch（AC6、AC7）

- [x] MCP 工具完整定义存在于 Registry，但首轮模型请求不包含其描述和 Schema。
  （验证：检查首轮 AgentModelRequest 与 Registry，期望 Registry 可查、request tools
  不可见。）
- [x] 首轮 system reminder 只包含当前模式可搜索 MCP 名称和必要说明，并稳定排序。
  （验证：比较 reminder 文本，期望无描述、无 Schema、无秘密且名称按序排列。）
- [x] 配置 MCP 时内建工具和 ToolSearch 在首轮正常可见。
  （验证：检查首轮工具定义，期望六个内建工具及 `tool_search` 存在。）
- [x] 未配置 MCP 时不额外注册 ToolSearch，不改变原六工具列表。
  （验证：运行无配置 Agent 请求测试，期望工具名序列与原版本一致。）
- [x] 增加大量复杂 MCP Schema 不会让首轮模型工具 Schema 体积线性增加。
  （验证：分别使用少量和大量远端 Schema，比较首轮 tools 序列化大小，期望只增加名称
  reminder 的有限文本。）
- [x] ToolSearch 只接受本地可搜索名称，不发起连接、tools/list 或 tools/call。
  （验证：记录全部 MCP 请求计数，执行 ToolSearch 后期望计数不变。）
- [x] ToolSearch 结果只包含名称、最多 160 字符的简短描述和加载状态。
  （验证：使用长描述和复杂 Schema，检查 ToolResult，期望描述截断且无 Schema。）
- [x] ToolSearch 新激活工具返回 loaded，重复搜索返回 already_loaded。
  （验证：同任务搜索同名两次，比较状态并确认发现集合只包含一项。）
- [x] 不存在或当前模式不可搜索的名称统一返回 not_found，不泄露隐藏目录。
  （验证：在 plan-only 猜测非白名单工具名，期望 not_found 且 reminder/结果无额外信息。）
- [x] ToolSearch 完成后的下一次模型请求包含被激活工具完整 Schema，无需新用户消息。
  （验证：运行多轮 Agent 测试，检查第二轮 request tools。）
- [x] 模型随后可以按普通 `mcp_*` 名称调用已激活工具。
  （验证：Agent 集成流程完成搜索、审批和远端调用，期望最终回答包含远端结果。）
- [x] 同一模型批次中搜索并直接调用隐藏工具时返回 `tool_not_discovered`，远端无副作用。
  （验证：分别测试 ToolSearch 在前和在后两种顺序，远端调用计数都保持零。）

## 任务级状态与缓存稳定性（AC8、AC13）

- [x] 已激活工具在同一用户任务的所有后续模型轮次保持可见。
  （验证：执行至少三轮且只搜索一次，期望第二、三轮工具定义相同。）
- [x] 任务正常完成后发现状态被丢弃。
  （验证：完成任务后提交新消息，期望新任务首轮不包含上一任务 MCP Schema。）
- [x] 任务失败、取消和达到轮数上限后发现状态同样被丢弃。
  （验证：分别触发三种终态并启动下一任务，期望首轮均重新隐藏。）
- [x] 清空发现状态不删除 Registry 工具、不关闭连接、不重新发现目录。
  （验证：任务结束前后比较 Registry、连接标识和 tools/list 次数，期望不变。）
- [x] 当前任务未发现新工具时，连续模型请求的工具定义内容与顺序保持一致。
  （验证：序列化连续请求 tools 并逐字节比较，期望相同。）
- [x] 首次发现新工具后，下一轮只增加该工具，不重排原有工具。
  （验证：比较激活前后工具名序列，期望保持原顺序并增加对应项。）
- [x] 新的可见集合稳定后，后续请求继续生成相同定义和顺序。
  （验证：比较激活后的连续两轮 tools，期望完全相同。）
- [x] ToolSearch 不会重复激活或重复添加同一个工具定义。
  （验证：重复搜索后检查 discovered 集合和模型工具名，期望各只有一项。）
- [x] 两个并行或连续用户任务之间不共享 discovered_tools。
  （验证：创建独立 AgentTurn/Exposure 实例，分别激活不同工具，期望互不可见。）

## 参数、结果与错误转换（AC9）

- [x] MCP 参数不符合远端 JSON Schema 时在远端 tools/call 前返回 `invalid_arguments`。
  （验证：提交缺字段、错类型和嵌套错误参数，期望远端调用计数为零且错误路径正确。）
- [x] JSON Schema 在发现时编译，本地 `$ref` 能正常验证。
  （验证：运行 Schema 单元测试，期望 `#/$defs/...` 场景通过。）
- [x] 外部 URL 或文件 `$ref` 不会产生网络或文件读取。
  （验证：为解析器设置会失败的网络/文件探针，构造外部引用，期望受控拒绝且探针计数
  为零。）
- [x] 六个内建工具迁移后参数 Schema、校验和执行结果保持不变。
  （验证：运行全部现有 tools 单元测试并对比已知 Schema 快照。）
- [x] 文本 MCP 结果按远端顺序返回 Agent。
  （验证：Server 返回多个 TextContent，期望 ToolResult 文本顺序一致。）
- [x] structuredContent 同时以可读 JSON 和结构化 metadata 返回。
  （验证：调用结构化测试工具，期望两处数据语义相同且键值完整。）
- [x] 文本和结构化结果同时存在时两者均保留。
  （验证：运行混合结果测试，期望固定分隔文本和 structured metadata 都存在。）
- [x] 图片和音频只返回类型/MIME 摘要，不包含 Base64。
  （验证：使用唯一 Base64 哨兵运行结果测试，期望摘要存在、哨兵零命中。）
- [x] Resource、ResourceLink 和其他未支持内容不复制正文，只返回安全摘要。
  （验证：提供含正文的测试内容，期望 ToolResult 只含类型、URI/MIME。）
- [x] MCP tool error、协议错误、连接错误、超时和无效结果可通过稳定错误码区分。
  （验证：逐一注入错误，期望分别得到 `mcp_tool_error`、`mcp_protocol_error`、
  `mcp_connection_error`、`mcp_timeout`、`mcp_invalid_result`。）

## 权限与 plan-only（AC10）

- [x] MCP annotations 无论声明只读、幂等或无破坏性，工具分类始终为 UNKNOWN。
  （验证：发现带不同 annotations 的工具，检查本地定义和调度行为均为 UNKNOWN。）
- [x] Agent 模式下未配置规则的 MCP 调用触发人工审批。
  （验证：运行 Agent 集成流程，期望 tools/call 前出现 ToolApprovalRequested。）
- [x] MCP UNKNOWN 工具串行执行，不与相邻读取工具并发穿过写入屏障。
  （验证：记录多个工具开始/结束时间，期望 MCP 调用按 UNKNOWN 串行位置执行。）
- [x] 用户拒绝审批时不发送远端 tools/call。
  （验证：选择拒绝后检查远端调用计数为零，并收到 permission_denied ToolResult。）
- [x] Agent 模式项目 allow/deny 规则可以按规范化 `mcp_*` 名称生效。
  （验证：分别配置 allow 与 deny，期望一个免本次询问执行、另一个不调用远端。）
- [x] plan-only 默认 reminder、ToolSearch 和模型工具列表均不暴露 MCP 工具。
  （验证：空白名单进入 `/plan` 运行 Agent 请求，期望无 MCP 名称和 Schema。）
- [x] plan-only 本地白名单只允许指定 MCP 名称被搜索和激活。
  （验证：配置一个允许、一个未允许工具，期望只有允许项出现在 reminder 并可 loaded。）
- [x] plan-only 白名单不会把 MCP 工具改成 READ，执行仍为 UNKNOWN 且串行。
  （验证：激活白名单工具后检查定义分类和调度时间线。）
- [x] plan-only 每次 MCP 调用都强制审批，项目 allow、`/permission allow` 和会话授权均
  不能绕过。（验证：连续调用两次并设置三种授权来源，期望每次都出现审批。）
- [x] plan-only MCP 审批界面只显示“拒绝”和“本次允许”，不提供会话允许。
  （验证：渲染对应 PermissionDecision，期望无选项 3 且按键 3 无效。）
- [x] plan-only 项目 deny 规则仍可以直接阻止白名单 MCP 工具。
  （验证：白名单与 deny 同时配置，期望不出现远端调用。）
- [x] 安全配置引用暂时不可用、禁用或启动失败的 `mcp_*` 工具时只显示警告并继续启动。
  （验证：配置三类引用运行应用，期望启动成功并显示脱敏警告。）
- [x] 未知非 MCP 工具、已注册工具的未知参数字段和非法白名单名称仍阻止启动。
  （验证：分别构造配置错误，期望 ConfigError 和非零启动结果。）

## 超时、取消、异步关联与断线恢复（AC11、AC12）

- [x] 启动连接和分页发现使用 Server 的 startup timeout，默认值为 10 秒且可覆盖。
  （验证：使用默认和短覆盖值连接慢 Server，观察超时边界与状态错误码。）
- [x] 单次工具调用使用 tool timeout，默认值为 60 秒且可覆盖。
  （验证：使用短覆盖值调用慢工具，期望接近配置时间返回 `mcp_timeout`。）
- [x] 多个并发请求乱序返回时按 JSON-RPC ID 交付给正确调用者。
  （验证：并发发送唯一参数并让 Server 反序返回，期望每个结果与参数严格对应。）
- [x] 未知 ID、重复响应、损坏消息不会误交付或导致整个 YCode 崩溃。
  （验证：运行协议异常注入测试，期望目标调用受控失败或消息被忽略，其他 Server 可用。）
- [x] 用户取消 Agent 任务会取消正在等待的 MCP 请求。
  （验证：启动慢工具后发送取消，期望 ToolExecutionCancelled/AgentCancelled 且 Server
  客户端等待结束。）
- [x] 用户取消后，尚未执行的远端调用不会启动。
  （验证：在审批或串行队列期间取消，期望后续调用计数为零。）
- [x] 迟到响应不会进入 Agent 历史或覆盖取消/超时结果。
  （验证：取消后让 Server 返回，检查 history 和终态只保留取消结果。）
- [x] 超时、取消、正常响应和连接关闭竞争时每个调用只有一个终态。
  （验证：重复运行竞争测试，断言每个 ToolCall ID 只有一个完成/取消结果。）
- [x] 超时或取消不会自动重试工具调用。
  （验证：检查 Server 调用计数始终为一。）
- [x] stdio 子进程或 HTTP 连接在调用中断开时，当前调用返回连接错误。
  （验证：两种传输分别在接收调用后断开，期望 `mcp_connection_error`。）
- [x] 当前断线调用不会因客户端重连而重新发送。
  （验证：Server 记录副作用计数，断线后期望当前调用计数为一。）
- [x] 下一次独立调用可以建立新连接并成功执行。
  （验证：首次调用断线，第二次调用恢复 Server，期望第二次成功且状态回到 ready。）
- [x] 重连不重新执行 tools/list，不修改 Registry、公开名或当前工具目录。
  （验证：比较重连前后 list 次数、Registry 快照和名称序列，期望完全不变。）
- [x] 重连不会重新暴露全部 MCP Schema或修改当前任务发现集合。
  （验证：断线前只激活一个工具，重连后模型工具列表仍只包含该工具。）

## 状态查询与资源关闭（AC14）

- [x] `/mcp` 精确、大小写不敏感地查询状态；`/mcp xxx` 仍作为普通消息。
  （验证：运行 ChatSession 命令测试，对比三种输入的事件和 Provider 调用。）
- [x] `/mcp` 不调用模型、不创建 AgentTurn、不进入对话历史。
  （验证：查询前后比较 Provider 计数和 history，期望均不变。）
- [x] `/mcp` 显示每个 Server 的名称、传输、当前状态、有效工具数和最近错误。
  （验证：渲染 mixed report，逐列对比状态快照。）
- [x] `/mcp` 对 disabled Server 显示“未启用”，配置无合法名称时使用稳定索引占位名。
  （验证：渲染禁用和无名称配置问题，期望状态可识别。）
- [x] 连接断开、重连成功和再次失败后 `/mcp` 反映最新状态。
  （验证：逐步触发三次状态变化并每次查询，期望输出随快照更新。）
- [x] `/mcp` 和启动摘要在窄终端仍可读且不显示敏感配置字段。
  （验证：用多个 Console width 渲染，搜索 URL、command、Header、env 和秘密均无命中。）
- [x] 正常退出会关闭 HTTP Client、stdio 输入和子进程。
  （验证：完成 PTY `/exit` 后检查连接、进程和后台任务均结束。）
- [x] Agent 异常、配置后半段异常和用户取消后同样关闭已创建资源。
  （验证：分别注入异常路径，使用资源计数器确认全部退出一次。）
- [x] stdio Server 不主动退出时，关闭流程最终会终止子进程。
  （验证：使用忽略正常退出的测试 Server，期望关闭在有界时间完成且进程不存在。）
- [x] 重复调用 Connection、Manager、AgentLoop 和 ChatSession 的 close 不会重复释放或报错。
  （验证：每层连续 close 两次，期望退出码正常且上下文退出计数为一。）
- [x] 退出、取消和异常后没有遗留子进程、HTTP 连接、读取任务或未消费 Future。
  （验证：完整集成测试结束后检查进程列表、活动 task 和测试资源计数。）

## 端到端与回归（AC15）

- [x] Windows 真实终端完成：启动 → MCP 摘要 → `/mcp` → 用户任务 → ToolSearch →
  下一轮 Schema → 本次审批 → MCP 调用 → 最终回答。
  （验证：运行
  `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q -k mcp`，期望场景
  通过并观察完整输出顺序。）
- [x] Windows PTY 场景退出后终端状态恢复、子进程退出且屏幕没有测试秘密。
  （验证：E2E 结束后检查终端可继续输入、进程不存在并搜索完整捕获输出。）
- [x] 2026-07-28 至少有一个从连接到工具结果的完整自动化场景。
  （验证：运行 `tests/integration/test_mcp_protocol_fallback.py` 的 modern 场景，期望通过。）
- [x] 2025-11-25 至少有一个从自动回退到工具结果的完整自动化场景。
  （验证：运行同文件 legacy 场景，期望通过。）
- [x] stdio、Streamable HTTP JSON 和请求级 SSE 各有真实集成场景通过。
  （验证：运行 `tests/integration/test_mcp_stdio.py` 与 `test_mcp_http.py`。）
- [x] ToolSearch、审批和远端调用的 Agent 多轮集成场景通过。
  （验证：运行 `tests/integration/test_mcp_agent_flow.py`，期望最终回答正确。）
- [x] 未配置 MCP 时现有 Anthropic 工具循环、权限审批、plan-only 和六个内建工具行为
  不变。（验证：运行现有 Agent、security、tools 和 Anthropic 集成测试。）
- [x] OpenAI PlainChatRunner 的消息、配置和流式响应行为不变，没有 MCP 工具或协议适配。
  （验证：运行全部 OpenAI 单元和集成测试，期望请求结构与现有断言一致。）
- [x] 不存在 OAuth、Resources、Prompts、Completions、运行期热重载、跨任务发现持久化或
  Anthropic 原生 defer_loading 行为。（验证：运行范围测试并检查公开命令/配置 Schema，
  期望没有相关入口。）
- [x] `.env.example`、配置示例和 README 能让用户配置两种传输、enabled、权限白名单和
  `/mcp`，且示例不含真实凭据。（验证：按示例创建临时项目并运行配置加载；人工检查
  示例秘密均为无效占位值。）

## 编译、静态检查与完整测试

- [x] Python 编译检查通过。（验证：运行
  `.venv\Scripts\python.exe -m compileall -q ycode tests`，期望退出码 0。）
- [x] Ruff 格式检查通过。（验证：运行
  `.venv\Scripts\python.exe -m ruff format --check .`，期望退出码 0。）
- [x] Ruff 静态检查通过。（验证：运行
  `.venv\Scripts\python.exe -m ruff check .`，期望退出码 0。）
- [x] 全部单元、集成和端到端测试通过。（验证：运行
  `.venv\Scripts\python.exe -m pytest -q`，期望退出码 0 且没有失败或错误。）
- [x] 工作树只包含本功能文档、实现、测试和示例改动，没有临时密钥、日志、缓存或测试
  产物。（验证：检查 `git status --short` 和敏感值哨兵搜索。）

## 验收标准追溯

| 验收标准 | Checklist 章节 |
|---|---|
| AC1 | 配置、`.env` 与敏感信息 |
| AC2 | stdio 与 Streamable HTTP |
| AC3 | 协议自动兼容与客户端能力 |
| AC4 | 多 Server、降级启动与目录发现 |
| AC5 | 多 Server、降级启动与目录发现 |
| AC6 | 初始延迟暴露与 ToolSearch |
| AC7 | 初始延迟暴露与 ToolSearch |
| AC8 | 任务级状态与缓存稳定性 |
| AC9 | 参数、结果与错误转换 |
| AC10 | 权限与 plan-only |
| AC11 | 超时、取消、异步关联与断线恢复 |
| AC12 | 超时、取消、异步关联与断线恢复 |
| AC13 | 任务级状态与缓存稳定性 |
| AC14 | 状态查询与资源关闭 |
| AC15 | 端到端与回归 |

AC1 至 AC15 均有可运行或可观察的验收项。

