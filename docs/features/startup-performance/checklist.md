# YCode 启动性能优化 Checklist

> 状态：已批准  
> 每项均通过运行代码、测试或观察真实终端行为验证。

## Provider 延迟导入

- [ ] **AC1——Anthropic 导入隔离：** 在全新 Python 子进程创建 Anthropic Provider 后，
  Anthropic 实现已加载，`openai` SDK 和 `ycode.providers.openai` 均未出现在
  `sys.modules`。（验证：运行 Provider 工厂子进程测试）

- [ ] **单一活动 Provider：** YAML 顶层 `active` 仍只选择一个 Provider，工厂没有同时
  创建多个 Provider，也没有新增 `enabled`、故障切换或动态发现机制。（验证：运行现有
  配置与工厂测试并检查装配入口）

- [ ] **OpenAI 边界：** 本功能未修改 OpenAI Provider 内部实现或增加其专用测试场景。
  （验证：检查 Git diff）

## MCP 后台启动与状态

- [ ] **AC2——UI 不阻塞：** 本地慢 MCP 尚未完成初始化时，Terminal UI 已显示首屏并进入
  可输入状态。（验证：运行受控应用事件顺序测试和 Windows PTY 场景）

- [ ] **AC2——禁用 Server：** `enabled: false` 的 MCP 不创建连接或后台任务，并继续显示
  `disabled`。（验证：运行 Manager 与应用参数化测试）

- [ ] **AC3——首屏摘要：** 有 MCP 正在连接时首屏显示“后台连接 N”，而不是误报为全部
  成功或全部失败。（验证：运行状态模型和终端摘要测试）

- [ ] **AC3——静默完成：** MCP 后台成功或失败时不主动插入终端消息，不打断当前输入和
  流式输出；用户通过 `/mcp` 查询最终结果。（验证：检查 DummyOutput 和 PTY 输出）

- [ ] **AC3——状态转换：** `/mcp` 可以观察 `starting → ready` 或
  `starting → unavailable`，并显示最终工具数量或安全错误摘要。（验证：运行 Manager、
  Terminal UI 和 PTY 测试）

- [ ] **AC3、AC4——后续回合工具：** MCP 成功后注册的工具对下一 Agent 回合可见，不加入
  已经开始的回合；失败后普通对话和内置工具仍可使用。（验证：运行本地 MCP Agent 集成
  测试）

- [ ] **AC4——快速退出：** MCP 仍在启动时退出会取消后台任务并关闭连接，不等待剩余启动
  超时，也不遗留子进程、HTTP Client 或未处理任务警告。（验证：运行连接、Manager 和
  应用关闭测试）

## 超时配置与安全边界

- [ ] **AC5——默认与覆盖：** 省略 `startup_timeout_seconds` 得到 5 秒；显式配置 10 秒
  仍得到 10 秒。（验证：运行 MCP 配置单元测试）

- [ ] **AC6——YAML 说明：** 示例 YAML 和当前项目 YAML 均注明默认值为 5 秒，当前显式
  10 秒字段保持不变。（验证：检查两个 YAML 文件）

- [ ] **AC7——安全失败：** MCP 后台失败不输出异常堆栈、API Key、Header 或环境变量值，
  单个 Server 失败不影响其他 Server。（验证：运行错误注入和脱敏测试）

- [ ] **无新增依赖：** `pyproject.toml` 没有新增运行或开发依赖。（验证：检查 Git diff）

## 质量与端到端

- [ ] **格式检查通过。**（验证：`.venv\Scripts\python.exe -m ruff format --check .`）
- [ ] **静态检查通过。**（验证：`.venv\Scripts\python.exe -m ruff check .`）
- [ ] **编译检查通过。**（验证：`.venv\Scripts\python.exe -m compileall -q ycode tests`）
- [ ] **完整测试通过。**（验证：`.venv\Scripts\python.exe -m pytest -q`）

- [ ] **Windows PTY 完整场景：** 使用本地慢 MCP 启动 YCode，观察首屏和“后台连接 1”先
  出现；连接期间 `/mcp` 显示 `starting`，完成后显示 `ready` 与工具数量；后台完成没有
  插入通知，正常退出无堆栈且测试没有访问真实 API。（验证：运行
  `mcp_background_startup` E2E 场景）
