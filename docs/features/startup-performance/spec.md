# YCode 启动性能优化 Spec

> 状态：已批准

## 背景

YCode 当前在模块加载时同时导入 Anthropic 与 OpenAI Provider，即使 YAML 顶层 `active`
只选择其中一个，也会承担两套 SDK 的冷启动成本。应用还会在显示终端 UI 前等待所有启用的
MCP Server 完成连接或超时，远程 MCP 不可达时会直接阻塞用户输入。

## 目标

- 只导入 `active` 对应的 Provider 实现和 SDK。
- 让普通 MCP 在后台启动，不再阻塞 UI 首屏和输入。
- 将 MCP 默认启动超时从 10 秒缩短为 5 秒，同时保留 YAML 显式覆盖。
- 保持改动聚焦，不引入新的 MCP 调度机制。

## 功能需求

- F1：Provider 工厂加载时不预先导入具体 Provider；创建 Provider 时根据已解析的
  `active` 协议延迟导入并实例化对应实现。
- F2：应用为全部 `enabled: true` 的 MCP 启动后台初始化任务，不等待任务完成就进入
  Terminal UI；`enabled: false` 的 Server 不创建连接任务。
- F3：UI 首屏摘要显示正在后台连接的 MCP 数量；后台完成时不主动插入终端消息。连接期间
  `/mcp` 显示现有 `starting` 状态；连接成功后注册工具并供后续 Agent 回合使用，连接失败
  或超时后显示现有 `unavailable` 状态，最终结果由用户通过 `/mcp` 查询。
- F4：单个 MCP 启动失败不得中断 UI、普通对话或内置工具；后台失败不得向终端输出异常
  堆栈。
- F5：应用退出时取消尚未完成的 MCP 后台初始化并关闭已经创建的连接，不等待剩余的完整
  启动超时。
- F6：`startup_timeout_seconds` 的默认值改为 5 秒；YAML 显式值继续优先，不强制覆盖。
- F7：配置示例和当前项目 YAML 增加注释，说明默认值为 5 秒；当前显式配置的 10 秒保持
  不变。

## 非功能需求

- N1：使用事件顺序而非固定毫秒数验证 UI 不受慢 MCP 阻塞，避免测试依赖机器性能。
- N2：在全新 Python 子进程中验证未激活 Provider 的 SDK 没有被导入。
- N3：MCP 后台任务由应用生命周期明确持有和关闭，不遗留任务、子进程或 HTTP Client。
- N4：错误信息继续脱敏，不显示 API Key、Header、环境变量值或原始异常堆栈。
- N5：不新增运行依赖或开发依赖。
- N6：使用本地 Provider 与 MCP 替身验证，不访问真实 Context7 或付费模型 API。

## 不做的事

- 不删除 OpenAI 支持，也不修改 OpenAI Provider 内部实现或扩展其专用测试场景。
- 不支持同时激活多个 Provider、故障切换或多模型路由。
- 不为 Provider 增加 `enabled` 字段。
- 不实现 MCP `alwaysLoad`、按需等待工具、自动重试或配置热重载。
- 不把后台注册的新 MCP 工具加入已经开始的 Agent 回合；只对后续回合生效。
- 不重新设计 `/mcp` 界面或状态模型。
- 不修改当前 YAML 中显式设置的 10 秒超时。

## 验收标准

- AC1（F1）：在全新 Python 进程中以 Anthropic 配置创建 Provider 后，Anthropic 实现已
  加载，而 `openai` SDK 和 OpenAI Provider 模块未加载。
- AC2（F2）：使用受控阻塞的 MCP 替身启动应用时，UI 已进入运行状态，而 MCP 初始化仍在
  后台等待；禁用的 MCP 不创建连接。
- AC3（F3、F4）：UI 首屏显示后台连接数量，后台完成时不打断输入或流式输出；通过 `/mcp`
  可观察到 `starting → ready` 或 `starting → unavailable`。成功后工具对下一 Agent 回合
  可见，失败后普通对话和内置工具仍可使用。
- AC4（F5）：MCP 尚未完成时退出，后台初始化被取消且资源关闭，没有未处理任务警告，
  退出不等待剩余启动超时。
- AC5（F6）：省略 `startup_timeout_seconds` 时配置结果为 5 秒；显式设置 10 秒时仍为
  10 秒。
- AC6（F7）：配置示例和当前项目 YAML 均说明默认值为 5 秒，当前显式 10 秒值没有改变。
- AC7（N4、N5、N6）：错误输出不泄露秘密，无新增依赖，相关单元测试和本地端到端测试
  全部通过。
