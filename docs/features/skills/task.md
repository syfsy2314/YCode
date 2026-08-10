# YCode Agent Skills Tasks

> 状态：修订已批准（2026-08-10）

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `ycode/skills/__init__.py` | 导出 Skill 公共类型和服务 |
| 新建 | `ycode/skills/models.py` | Skill 配置、快照、诊断、目录和任务作用域模型 |
| 新建 | `ycode/skills/loader.py` | `SKILL.md` 读取、解析、校验和工具名称映射 |
| 新建 | `ycode/skills/catalog.py` | 项目 Skill 扫描、冲突处理、候选状态和热更新 |
| 新建 | `ycode/skills/runtime.py` | 共享激活、调用栈、分支作用域、工具策略和提交 |
| 新建 | `ycode/skills/context.py` | 隔离 Skill 的 summary、recent、none 上下文 |
| 新建 | `ycode/skills/isolated.py` | 临时 Anthropic Agent 执行和最终交接 |
| 新建 | `ycode/skills/installer.py` | 多来源 HTTPS 解析、下载、校验和原子安装 |
| 新建 | `ycode/skills/commands.py` | Skill 管理命令和动态命令定义 |
| 新建 | `ycode/tools/builtin/load_skill.py` | `load_skill` 工具 |
| 新建 | `ycode/tools/builtin/install_skill.py` | `install_skill` 工具 |
| 修改 | `ycode/tools/builtin/__init__.py` | 导出 Skill 工具 |
| 修改 | `ycode/tools/contracts.py` | 工具执行上下文携带 Skill 任务作用域 |
| 修改 | `ycode/tools/registry.py` | 装配 Skill 工具所需注册入口 |
| 修改 | `ycode/agent/contracts.py` | Skill 感知的可选任务接口和取消边界 |
| 修改 | `ycode/agent/loop.py` | 每轮刷新 Skill Prompt、工具集合和预批准 |
| 修改 | `ycode/prompt/models.py` | Skill 目录与指令补充类型 |
| 修改 | `ycode/prompt/runtime.py` | 聚合目录和共享 SOP |
| 修改 | `ycode/config/loader.py` | 延迟解析命名 Anthropic Provider 配置 |
| 修改 | `ycode/context/manager.py` | `/clear` 清空摘要和上下文运行状态 |
| 修改 | `ycode/security/engine.py` | Skill 预批准和安装强制审批 |
| 修改 | `ycode/commands/contracts.py` | UI Skill 管理与调用接口 |
| 修改 | `ycode/commands/registry.py` | 原子替换动态命令 |
| 修改 | `ycode/commands/builtin.py` | 注册 `/skills` 和 `/clear` |
| 修改 | `ycode/session/models.py` | Skill 状态记录和恢复快照字段 |
| 修改 | `ycode/session/codec.py` | Skill 状态记录编解码 |
| 修改 | `ycode/session/manager.py` | Skill 状态事务写入与加载 |
| 修改 | `ycode/session/chat.py` | 显式调用、管理、清空、恢复和任务提交 |
| 修改 | `ycode/session/__init__.py` | 导出 Skill 会话状态记录 |
| 修改 | `ycode/ui/terminal.py` | Skill 命令桥接与事件显示 |
| 修改 | `ycode/mcp/manager.py` | MCP 就绪后的 Skill 依赖重校验回调 |
| 修改 | `ycode/app.py` | Anthropic Skill 功能整体装配 |
| 新建 | `.ycode/skills/commit/SKILL.md` | 普通共享提交 Skill 示例 |
| 新建 | `.ycode/skills/review/SKILL.md` | recent=5 的隔离审查 Skill 示例 |
| 新建 | `.ycode/skills/test/SKILL.md` | none 上下文的隔离测试 Skill 示例 |
| 新建 | `tests/unit/skills/test_models.py` | Skill 模型单元测试 |
| 新建 | `tests/unit/skills/test_loader.py` | 标准格式、扩展和诊断单元测试 |
| 新建 | `tests/unit/skills/test_catalog.py` | 扫描、冲突和 reload 单元测试 |
| 新建 | `tests/unit/skills/test_runtime.py` | 激活、嵌套、工具策略和事务单元测试 |
| 新建 | `tests/unit/skills/test_context.py` | 隔离上下文策略单元测试 |
| 新建 | `tests/unit/skills/test_isolated.py` | 隔离执行生命周期单元测试 |
| 新建 | `tests/unit/skills/test_installer.py` | 下载和 ZIP 安装单元测试 |
| 新建 | `tests/unit/skills/test_commands.py` | 管理与动态命令单元测试 |
| 新建 | `tests/unit/tools/test_skill_tools.py` | 两个 Skill 工具的契约测试 |
| 新建 | `tests/integration/test_skill_agent_flow.py` | 共享、隔离、嵌套和权限集成测试 |
| 新建 | `tests/integration/test_skill_sessions.py` | 激活、clear、恢复和热更新集成测试 |
| 新建 | `tests/integration/test_skill_install.py` | 受控多来源 HTTPS 安装集成测试 |
| 新建 | `tests/support/https_zip_server.py` | PTY 验证使用的受控本地 HTTPS 来源服务 |
| 新建 | `tests/support/certs/localhost.crt` | 本地 HTTPS 测试证书 |
| 新建 | `tests/support/certs/localhost.key` | 本地 HTTPS 测试私钥 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 单一真实 PTY Skill 核心流程 |
| 修改 | 现有相关 `tests/unit/**` | Agent、命令、Prompt、权限、会话、UI 和装配回归测试 |

## T1：定义 Skill 核心模型

**文件：** `ycode/skills/models.py`、`ycode/skills/__init__.py`、`tests/unit/skills/test_models.py`  
**依赖：** 无

**步骤：**
1. 定义执行模式、上下文策略、调用来源和诊断严重级别枚举。
2. 定义 `SkillConfig`、`SkillSnapshot`、`SkillProblem`、`SkillCatalogEntry` 和目录候选模型。
3. 定义 `SkillInvocation`、`SkillCallFrame`、分支 `SkillTaskScope`、共享授权状态和调用结果。
4. 对集合、路径、模式组合和稳定排序执行必要的构造校验，并导出公共类型。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_models.py`，期望模型默认值、不变性和非法组合测试通过。

## T2：实现标准 `SKILL.md` 解析

**文件：** `ycode/skills/loader.py`、`tests/unit/skills/test_loader.py`  
**依赖：** T1

**步骤：**
1. 读取文件字节，严格分离 YAML frontmatter 与 Markdown 正文并计算 SHA-256。
2. 校验标准字段、名称长度和字符规则、父目录一致性及 metadata 字符串映射。
3. 将读取、YAML 和字段错误转换为 error 级诊断，不向外泄露无关路径或环境信息。

**验证：** 运行 loader 单元测试，期望最小合法 Skill 生成共享快照，损坏 YAML、缺字段和名称错误产生明确诊断。

## T3：解析 YCode 执行扩展

**文件：** `ycode/skills/loader.py`、`tests/unit/skills/test_loader.py`  
**依赖：** T2

**步骤：**
1. 解析 execution mode、model、context、recent turns、visible tools 和 argument hint。
2. 应用标准 Skill 的共享、当前模型、当前上下文和继承工具默认值。
3. 拒绝共享模式指定模型、隔离模式缺上下文、recent 缺合法回合数等无效组合。

**验证：** 运行 loader 单元测试，期望所有合法组合得到确定配置，非法组合标记不可用。

## T4：实现工具名称与 `allowed-tools` 解析

**文件：** `ycode/skills/loader.py`、`tests/unit/skills/test_loader.py`  
**依赖：** T3

**步骤：**
1. 实现 Anthropic 常见名称到 YCode 工具名称的固定映射。
2. 校验 visible tools 和普通 allowed-tools 属于当前已知工具，且预批准是可见工具子集。
3. 对 `Bash(git:*)` 等参数级表达式生成 warning、忽略其授权，但保留同字段中的普通工具授权。

**验证：** 运行 loader 单元测试，期望映射、未知普通工具错误和参数表达式安全降级行为通过。

## T5：实现项目目录扫描

**文件：** `ycode/skills/catalog.py`、`tests/unit/skills/test_catalog.py`  
**依赖：** T4

**步骤：**
1. 只枚举 `.ycode/skills/` 的直接子目录和其中准确命名的 `SKILL.md`。
2. 按规范化名称稳定排序，同时保留可用与不可用条目。
3. 提供名称查询、简要目录和扫描整体失败错误。

**验证：** 运行 catalog 单元测试，期望松散文件、嵌套目录被忽略，合法与损坏 Skill 相互隔离。

## T6：实现冲突检测与事务式 reload

**文件：** `ycode/skills/catalog.py`、`tests/unit/skills/test_catalog.py`  
**依赖：** T5

**步骤：**
1. 检测内置命令冲突和同范围规范化重名，将冲突各方标记不可用。
2. 实现完整候选状态生成与一次提交，扫描过程失败时保留旧目录。
3. 实现既有条目的调用时重读；失败时保留目录与有效快照，新增和重命名仍等待全量 reload。

**验证：** 运行 catalog 单元测试，期望冲突无随机覆盖、失败不提交、单项热更新不发现新目录。

## T7：增加 Skill Prompt 补充

**文件：** `ycode/prompt/models.py`、`ycode/prompt/runtime.py`、`tests/unit/prompt/test_models.py`、`tests/unit/prompt/test_runtime.py`  
**依赖：** T1、T5

**步骤：**
1. 增加 Skill 目录与 Skill 指令补充类型及稳定排序位置。
2. 将未激活目录格式化为只含名称和说明的单一补充。
3. 将共享快照正文按名称聚合为单一 SOP 补充，并支持替换和清空。

**验证：** 运行 Prompt 相关单元测试，期望未激活正文不出现、多个共享 SOP 顺序稳定且可移除。

## T8：实现动态命令原子替换

**文件：** `ycode/commands/registry.py`、`tests/unit/commands/test_registry.py`  
**依赖：** 无

**步骤：**
1. 区分内置定义与 Skill 动态定义。
2. 在临时索引中完整校验候选后原地替换动态部分。
3. 保持内置优先、帮助顺序和 completion entries 的确定性。

**验证：** 运行 registry 单元测试，期望替换后同一 Registry 实例可见新命令，冲突候选不改变旧状态。

## T9：生成管理命令和动态 Skill 命令

**文件：** `ycode/skills/commands.py`、`ycode/commands/contracts.py`、`ycode/commands/builtin.py`、`tests/unit/skills/test_commands.py`、`tests/unit/commands/test_builtin.py`  
**依赖：** T6、T8

**步骤：**
1. 扩展 `UIController` 的 Skill 列表、详情、停用、reload、clear 和显式调用方法。
2. 注册 `/skills` 子命令和 `/clear`，并生成每个可用 Skill 的 AI 动态命令。
3. 保持原始参数、argument hint、帮助和补全行为，查看帮助不触发 Skill。

**验证：** 运行命令相关单元测试，期望管理命令参数校验、原始参数转发和动态帮助通过。

## T10：实现 recent 隔离上下文

**文件：** `ycode/skills/context.py`、`tests/unit/skills/test_context.py`  
**依赖：** T1

**步骤：**
1. 识别普通用户消息与工具结果 user-role 消息。
2. 从尾部提取最近 N 个完整用户回合。
3. 保证 Assistant 工具调用与对应工具结果不被拆分。

**验证：** 运行 context 单元测试，期望 recent=1/5、历史不足和带工具回合均返回完整边界。

## T11：实现 summary 与 none 隔离上下文

**文件：** `ycode/skills/context.py`、`tests/unit/skills/test_context.py`  
**依赖：** T10

**步骤：**
1. 使用现有 `ConversationCompactor` 基于当前摘要和全部已提交历史产生临时摘要。
2. 实现 none 策略为空历史，三种策略都原样追加当前任务。
3. 让摘要操作响应取消且不修改主 `ContextManager` 状态。

**验证：** 运行 context 单元测试，期望三种策略输入准确，取消和摘要失败不改变主上下文替身。

## T12：支持解析命名 Anthropic Provider

**文件：** `ycode/config/loader.py`、`tests/unit/config/test_loader.py`  
**依赖：** 无

**步骤：**
1. 提取复用现有环境变量展开和 Provider 校验的命名条目解析入口。
2. 只允许隔离 Skill 引用已有 Anthropic 配置，拒绝不存在或 OpenAI 条目。
3. 保持活动配置和未引用配置的现有懒校验行为。

**验证：** 运行 config loader 单元测试，期望命名 Anthropic 条目可解析，OpenAI 和缺失名称被拒绝且现有测试不回归。

## T13：增加会话 Skill 状态记录模型与编解码

**文件：** `ycode/session/models.py`、`ycode/session/codec.py`、`ycode/session/__init__.py`、`tests/unit/session/test_models.py`、`tests/unit/session/test_codec.py`  
**依赖：** T1

**步骤：**
1. 定义带 `covered_turn_id` 的 `SkillStateRecord`，并为 `SessionSnapshot` 增加默认空激活名称。
2. 在当前格式版本内增加记录编码和解码分支。
3. 保持旧会话记录可以加载为空 Skill 状态。

**验证：** 运行 session model/codec 单元测试，期望新记录往返一致，旧记录兼容且非法名称集合被拒绝。

## T14：事务写入和加载 Skill 会话状态

**文件：** `ycode/session/manager.py`、`tests/unit/session/test_manager.py`  
**依赖：** T13

**步骤：**
1. 扩展 `commit_turn()`，在同一次可回滚追加中写入候选 Skill 状态。
2. 增加当前已提交会话的独立状态更新，用于停用和 reload 自动停用。
3. 加载时只应用已提交回合覆盖的最新 Skill 状态，并保留损坏尾记录告警规则。

**验证：** 运行 session manager 单元测试，期望成功写入、模拟存储失败回滚、旧会话和最新状态恢复通过。

## T15：实现共享 Skill 运行状态

**文件：** `ycode/skills/runtime.py`、`tests/unit/skills/test_runtime.py`  
**依赖：** T6、T14

**步骤：**
1. 管理正式共享快照、主分支候选快照和稳定 SOP 顺序。
2. 实现任务开始、成功提交、失败丢弃、停用和清空。
3. 重复加载已激活共享 Skill 时更新快照但不重复注入。

**验证：** 运行 runtime 单元测试，期望两个共享 Skill 的生命周期、热更新、存储失败和 clear 行为通过。

## T16：实现调用帧、分支和嵌套限制

**文件：** `ycode/skills/runtime.py`、`tests/unit/skills/test_runtime.py`  
**依赖：** T15

**步骤：**
1. 压入和弹出包含调用时快照的 `SkillCallFrame`。
2. 拒绝循环和第四层调用，并为隔离 Agent 创建复制调用栈的分支作用域。
3. 让隔离分支的共享激活在分支结束时丢弃，主分支候选不受影响。

**验证：** 运行 runtime 单元测试，期望两层、三层、循环、第四层和隔离分支不泄漏行为通过。

## T17：计算工具可见性与任务预批准

**文件：** `ycode/skills/runtime.py`、`tests/unit/skills/test_runtime.py`  
**依赖：** T16

**步骤：**
1. 计算当前模式基础工具、共享白名单并集和当前调用帧工具视图。
2. 在顶层任务共享获批的普通工具集合，但不继承历史共享 Skill 的 allowed-tools。
3. 在任务成功、失败和取消后清空预批准。

**验证：** 运行 runtime 单元测试，期望白名单继承、并集、plan-only 收窄和授权生命周期通过。

## T18：实现 `load_skill` 工具契约

**文件：** `ycode/tools/contracts.py`、`ycode/tools/builtin/load_skill.py`、`ycode/tools/builtin/__init__.py`、`tests/unit/tools/test_skill_tools.py`  
**依赖：** T17

**步骤：**
1. 让 `ToolContext` 可选携带当前 Skill 分支作用域。
2. 定义 `load_skill` 的 name 和可选 arguments 参数，设置为读取类且始终可发现。
3. 委托 `SkillRuntime` 调用并返回共享激活结果或隔离最终交接，不自行保存状态。

**验证：** 运行 Skill 工具单元测试，期望参数校验、缺少作用域、共享结果和隔离交接结果通过。

## T19：接入 Skill 权限规则

**文件：** `ycode/security/engine.py`、`tests/unit/security/test_engine.py`  
**依赖：** T17、T18

**步骤：**
1. 在底层拒绝之后使用任务预批准把普通 ASK 决定转为 ALLOW。
2. 对自动或嵌套 `load_skill` 的有效 allowed-tools 生成包含 Skill 名称和工具列表的审批。
3. 保证 plan-only、显式拒绝、工作区和命令安全结果不能被预批准覆盖。

**验证：** 运行 security engine 单元测试，期望 ASK 降级、DENY 不变、自动激活审批和拒绝后父任务继续条件通过。

## T20：让 AgentLoop 每轮应用 Skill 状态

**文件：** `ycode/agent/contracts.py`、`ycode/agent/loop.py`、`tests/unit/agent/test_contracts.py`、`tests/unit/agent/test_loop.py`  
**依赖：** T7、T17、T19

**步骤：**
1. 普通任务开始时创建主 Skill 作用域，并把它传入工具上下文。
2. 每轮模型请求前重新生成 Skill 目录、共享 SOP 和实际工具定义。
3. 在正常完成后暴露候选共享状态，在失败、限制和取消路径清理作用域。

**验证：** 运行 Agent 单元测试，期望 `load_skill` 后下一轮看到 SOP，工具列表变化，所有终止路径清除预批准。

## T21：创建隔离 Agent 装配器

**文件：** `ycode/skills/isolated.py`、`tests/unit/skills/test_isolated.py`  
**依赖：** T11、T12、T20

**步骤：**
1. 根据当前或命名 Anthropic 配置创建临时 Provider 与独立 Prompt 运行状态。
2. 复用工具、安全配置和工作区服务，但不创建 SessionManager、主 ContextManager 或记忆更新器。
3. 为隔离任务应用自身 SOP、上下文策略、模式和分支作用域。

**验证：** 运行 isolated 单元测试，期望当前/命名模型选择和依赖装配准确，OpenAI 引用不进入运行。

## T22：完成隔离执行生命周期

**文件：** `ycode/skills/isolated.py`、`tests/unit/skills/test_isolated.py`  
**依赖：** T21

**步骤：**
1. 消费临时 Agent 事件但只返回最终 Assistant 交接文本。
2. 在完成、失败和取消路径关闭 Provider、子任务和分支授权。
3. 不返回 Thinking、工具过程或中间消息，也不回滚已经发生的工作区修改。

**验证：** 运行 isolated 单元测试，期望成功只返回交接，失败/取消无历史输出且资源关闭。

## T23：实现显式共享 Skill 会话流程

**文件：** `ycode/session/chat.py`、`ycode/commands/contracts.py`、`ycode/ui/terminal.py`、`tests/unit/session/test_chat.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T9、T15、T20

**步骤：**
1. 增加显式 Skill 调用入口，先显示原始命令，再调用时重读并建立候选共享状态。
2. 构造固定展开任务文本，区分原始参数和无参数，不修改 SOP。
3. 成功时把回合和候选 Skill 状态一起持久化；读取、执行或存储失败时不提交候选状态。

**验证：** 运行 ChatSession 和 TerminalUI 单元测试，期望显示文本、模型文本、历史内容和共享提交边界准确。

## T24：实现显式隔离 Skill 会话流程

**文件：** `ycode/session/chat.py`、`ycode/ui/terminal.py`、`tests/unit/session/test_chat.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T22、T23

**步骤：**
1. 显式隔离调用直接运行 `IsolatedSkillRunner`，不启动普通主 Agent 回合。
2. 只把展开后的用户任务和最终交接作为完整主会话回合保存。
3. 将取消信号传给隔离任务，失败或取消时不提交主历史。

**验证：** 运行 ChatSession 和 TerminalUI 单元测试，期望内部事件不渲染、不持久化，只显示并保存最终交接。

## T25：实现 `/skills` 管理与动态刷新

**文件：** `ycode/skills/commands.py`、`ycode/session/chat.py`、`ycode/commands/registry.py`、`ycode/ui/terminal.py`、`tests/unit/skills/test_commands.py`、`tests/unit/session/test_chat.py`  
**依赖：** T9、T15、T23

**步骤：**
1. 输出来源、可用/激活状态、元数据、执行配置、warning 和 error 原因。
2. 实现停用和全量 reload，先持久化自动停用候选，再一次更新目录、Prompt 和动态命令。
3. 确保管理命令不调用模型、不进入历史。

**验证：** 运行 Skill command 和 ChatSession 单元测试，期望列表、详情、停用、reload 及失败保留旧状态通过。

## T26：实现 `/clear`

**文件：** `ycode/commands/builtin.py`、`ycode/session/chat.py`、`ycode/context/manager.py`、`tests/unit/commands/test_builtin.py`、`tests/unit/session/test_chat.py`、`tests/unit/context/test_manager.py`  
**依赖：** T25

**步骤：**
1. 清空历史、摘要、共享 Skill、临时授权和 Prompt 模式状态，并开始新空会话。
2. 将模式重置为 agent，同时保留权限配置模式、目录、MCP 和项目记忆。
3. 活动任务期间拒绝 clear；命令本身不进入历史且旧会话文件保留。

**验证：** 运行相关单元测试，期望 clear 前后全部状态边界和旧会话可加载行为通过。

## T27：恢复会话共享 Skill

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T14、T15、T26

**步骤：**
1. 恢复前为历史、摘要和共享 Skill 分别生成候选。
2. 按会话名称重读当前磁盘 Skill，跳过删除、改名和不可用项并收集告警。
3. 一次提交恢复状态，不恢复隔离 Skill、旧 SOP 或任务级预批准。

**验证：** 运行 ChatSession 单元测试，期望有效恢复、失效告警、当前文件热内容和恢复失败不改变当前会话通过。

## T28：实现来源识别与受限 HTTPS 获取

**文件：** `ycode/skills/installer.py`、`tests/unit/skills/test_installer.py`  
**依赖：** T1

**步骤：**
1. 把用户 URL 分类为直接 ZIP、skills.sh 详情页、GitHub tree 或原始 `SKILL.md`，拒绝
   其他普通网页和不合法形态。
2. 校验 HTTPS、无 URL 凭据和公开地址规则，并对重定向、API 地址及解析出的下载地址
   重复校验。
3. 使用可注入异步客户端按本次安装累计流式字节数，超过 30 MB 立即失败，并在成功、
   网络失败和取消路径关闭响应、清理临时文件。

**验证：** 运行 installer 单元测试，使用内存 HTTP 替身验证四类 URL 分类、HTTP/URL
错误、解析后私有地址、累计超限和取消清理。

## T29：实现 skills.sh 与 GitHub tree 目录解析

**文件：** `ycode/skills/installer.py`、`tests/unit/skills/test_installer.py`  
**依赖：** T28

**步骤：**
1. 从 skills.sh 详情页 URL 提取 owner、repo 和 Skill slug，通过公开 GitHub 元数据定位
   唯一匹配的 `SKILL.md` 父目录，拒绝缺失或歧义结果。
2. 解析 GitHub tree 的 owner、repo、ref 和目录路径；ref 含斜杠时按最长有效 ref 候选
   确定边界。
3. 只递归下载目标目录的普通文件，保留相对资源结构，拒绝 symlink、submodule、绝对路径、
   目录越界和非公开下载地址。

**验证：** 运行 installer 单元测试，覆盖两个来源的合法目录、嵌套资源、斜杠 ref、匹配
缺失/歧义、链接、submodule 和累计超限。

## T30：统一构造来源并完成原子安装与目录刷新

**文件：** `ycode/skills/installer.py`、`ycode/skills/catalog.py`、`tests/unit/skills/test_installer.py`  
**依赖：** T6、T28、T29

**步骤：**
1. 保留直接 ZIP 的安全解压规则；为原始 `SKILL.md` 按 frontmatter name 构造只含该文件
   的目录，并把四类来源统一为同一暂存目录模型。
2. 在 `.ycode/skills/` 同一文件系统暂存，统一用 SkillLoader 校验，拒绝名称不一致、覆盖
   同名目录和半完成候选，再使用原子重命名提交安装。
3. 安装后提交新 Catalog、Prompt 和动态命令但不激活；依赖缺失的合法 Skill 显示
   “已安装但不可用”。

**验证：** 运行 installer 单元测试，期望四类来源成功安装到
`.ycode/skills/<frontmatter-name>/`；原始来源只有 `SKILL.md`；同名、候选失败和取消均无
半安装目录。

## T31：实现 `install_skill` 工具与强制审批

**文件：** `ycode/tools/builtin/install_skill.py`、`ycode/tools/builtin/__init__.py`、`ycode/security/engine.py`、`tests/unit/tools/test_skill_tools.py`、`tests/unit/security/test_engine.py`  
**依赖：** T19、T30

**步骤：**
1. 把参数定义为 `source_url`，描述四类支持来源和“调用工具自动触发审批”，并委托
   Installer。
2. 在 agent 模式始终发起显示用户原始 URL 的人工审批，在 plan-only 中不暴露。
3. 确保全局 allow 和 Skill 预批准不能跳过安装审批。

**验证：** 运行 Skill 工具和权限单元测试，期望参数 Schema、模型触发描述、批准、拒绝、
plan-only 和四类安装结果消息通过。

## T32：装配 Anthropic Skill 功能

**文件：** `ycode/tools/registry.py`、`ycode/tools/__init__.py`、`ycode/agent/__init__.py`、`ycode/prompt/__init__.py`、`ycode/config/__init__.py`、`ycode/mcp/manager.py`、`ycode/app.py`、`tests/unit/test_app.py`、`tests/unit/mcp/test_manager.py`、`tests/unit/tools/test_registry.py`  
**依赖：** T7、T12、T20、T22、T25、T27、T31

**步骤：**
1. 按 Plan 顺序装配 Catalog、两个工具、安全引擎、Runtime、隔离运行器、AgentLoop 和 ChatSession。
2. 首次扫描更新启动告警、Prompt 和动态命令；MCP 工具就绪后触发依赖重校验。
3. 保持 OpenAI 路径不创建、不扫描、不注册 Skill 组件。

**验证：** 运行 app、MCP manager 和 registry 单元测试，期望 Anthropic 组件齐全，OpenAI 装配保持原状。

## T33：添加三个普通示例 Skill

**文件：** `.ycode/skills/commit/SKILL.md`、`.ycode/skills/review/SKILL.md`、`.ycode/skills/test/SKILL.md`、`tests/unit/skills/test_catalog.py`  
**依赖：** T6

**步骤：**
1. 编写共享 `commit` SOP，只描述通用提交流程。
2. 编写 recent=5 的隔离 `review` SOP 和 context=none 的隔离 `test` SOP。
3. 通过通用 Catalog 加载并验证删除目录后 reload 会移除命令，不添加名称专用代码。

**验证：** 运行 catalog 单元测试并用 Loader 读取三个真实样例，期望模式和上下文配置准确。

## T34：验证共享、工具和权限集成

**文件：** `tests/integration/test_skill_agent_flow.py`  
**依赖：** T32、T33

**步骤：**
1. 用 Fake Provider 验证启动目录、自动 load、显式共享调用和同回合下一轮 SOP 注入。
2. 验证多个共享 Skill 的工具并集、plan-only 收窄和参数级授权 warning。
3. 验证自动 allowed-tools 审批、拒绝后父任务继续、预批准不覆盖底层拒绝。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_agent_flow.py`，期望共享和权限场景全部通过。

## T35：验证隔离与嵌套集成

**文件：** `tests/integration/test_skill_agent_flow.py`  
**依赖：** T34

**步骤：**
1. 验证 summary、recent、none 隔离输入和仅返回最终交接。
2. 验证两层、三层、循环和第四层调用，以及隔离分支共享 SOP 不泄漏。
3. 验证隔离成功、失败和取消均不保存子会话或内部消息。

**验证：** 重新运行 Skill Agent 集成测试，期望隔离、嵌套和取消场景通过。

## T36：验证会话、命令和热更新集成

**文件：** `tests/integration/test_skill_sessions.py`  
**依赖：** T27、T32、T33

**步骤：**
1. 验证激活、停用、上下文压缩、恢复和 `/clear` 的 Skill 状态。
2. 验证调用时正文热更新、无效更新保留快照、新增/删除等待 reload。
3. 验证 `/skills`、帮助、补全和动态冲突在 reload 后同步变化。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_sessions.py`，期望事务和命令场景通过。

## T37：验证受控安装集成

**文件：** `tests/integration/test_skill_install.py`  
**依赖：** T31、T32

**步骤：**
1. 使用可控 HTTPS 客户端替身分别提供直接 ZIP、skills.sh、GitHub tree 和原始
   `SKILL.md`，验证安装后立即更新目录、帮助和补全且不激活。
2. 验证来源解析失败、解析后私有地址、下载错误、累计超限、非法路径、链接、submodule、
   多个顶层目录、歧义 Skill 和同名目标不留残余。
3. 验证目录来源保留随附资源、原始来源只含 `SKILL.md`，以及结构合法但工具或模型缺失时
   安装成功并显示不可用原因。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_install.py`，期望核心安装行为通过。

## T38：更新现有回归测试

**文件：** 现有 Agent、Prompt、命令、权限、会话、工具、UI、MCP 和应用单元/集成测试  
**依赖：** T32

**步骤：**
1. 更新受新增可选参数、命令和工具定义影响的既有断言与测试替身。
2. 验证 Anthropic Thinking、工具循环、MCP、权限、plan-only、压缩、恢复和记忆行为不变。
3. 保持 OpenAI 测试仅验证原有功能，不添加 Skill 期望。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/unit tests/integration`，期望全部单元与集成测试通过。

## T39：执行真实 PTY 核心流程

**文件：** `tests/support/https_zip_server.py`、`tests/support/certs/localhost.crt`、`tests/support/certs/localhost.key`、`tests/e2e/test_terminal_chat.py`  
**依赖：** T33–T38

**步骤：**
1. 在现有 Windows PTY 测试框架中覆盖 `/skills`、显式共享调用、自动调用、隔离交接和停用。
2. 使用本地测试证书启动受控 HTTPS 来源服务，至少以 skills.sh 详情页形态覆盖一次完整
   安装，并覆盖 reload、`/clear` 和安全退出的单一主流程；其他来源由确定性集成测试覆盖。
3. 只检查用户可见核心行为，不增加多终端、多平台、压力或长期运行场景。

**验证：** 运行 `.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py`，期望 Skill PTY 场景及原有终端场景通过。

## T40：执行功能性质量检查

**文件：** 本功能全部新增和修改文件  
**依赖：** T39

**步骤：**
1. 运行格式、静态、编译和完整测试命令。
2. 修正本功能引入的失败，不扩展到性能、压力、故障注入、全面安全审计或真实付费 API。
3. 记录各命令实际结果，供 checklist 最终验收使用。

**验证：** 依次运行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条命令均退出码为 0。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6
 │                    ├→ T7
 │                    ├→ T8 → T9
 ├→ T10 → T11        ├→ T15 → T16 → T17 → T18 → T19 → T20
 ├→ T12              │                                  ├→ T21 → T22
 └→ T13 → T14        │                                  └→ T23 → T24
                      └────────────────────────────────────→ T25 → T26 → T27

T28 → T29 → T30 → T31

T7 + T12 + T20 + T22 + T25 + T27 + T31 → T32
T6 → T33
T32 + T33 → T34 → T35
T27 + T32 + T33 → T36
T31 + T32 → T37
T32 → T38
T33–T38 → T39 → T40
```

## 任务自检

- Plan 中的 Loader、Catalog、Runtime、Context、Isolated Runner、Installer、工具、命令、会话、权限、Prompt 和装配组件均有对应任务。
- F1–F23 和 N1–N15 均映射到实现或验证任务。
- 每项任务包含具体文件、依赖、步骤和可执行验证。
- 执行依赖无循环；OpenAI Skill 适配不在任何任务中。
- 验证仅覆盖功能性主路径和 Spec 明确失败路径，不包含生产级验证。
