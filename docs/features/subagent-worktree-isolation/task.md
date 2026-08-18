# 子 Agent Worktree 隔离实现任务

## 文档状态

- 对应 Spec：`docs/features/subagent-worktree-isolation/spec.md`
- 对应 Plan：`docs/features/subagent-worktree-isolation/plan.md`
- 当前状态：已批准（2026-08-18）
- 交付方式：按依赖顺序一次性完成全部任务，不拆分产品阶段
- 实现开始条件：本文件与后续 `checklist.md` 均获明确批准

## 执行规则

1. 严格按任务编号和依赖顺序实施；前置任务验证通过后再进入下一项。
2. 每完成一个任务，先运行该任务列出的验证命令并观察实际结果；失败时先修复再继续。
3. 只实现已批准 Spec 和 Plan，不增加主 Agent 切换、Fork 隔离、自动整合、远端 Git、OpenAI 适配或生产级可靠性工程。
4. Git 测试使用本地临时仓库；Provider 使用 Fake Provider；不得调用真实付费 API、真实远端服务或网络。
5. 不修改或清理用户已有的无关工作区变更，不创建 Git commit。
6. PowerShell 命令正文不作为操作系统级沙箱验证对象；只验证已批准的文件/搜索工具、命令 `cwd` 和提示词边界。
7. 最终按 `checklist.md` 执行格式、静态、编译、完整测试和真实终端验收。

## Task 1：建立配置、角色隔离、领域模型与安全命名

**依赖：** 无

**目标：** 建立 Worktree 功能的严格输入模型、安全名称和可逆分支映射，不产生 Git 或文件系统副作用。

**实现内容：**

- [ ] 新增 `WorktreeConfig`，包含 `copy_files`、`ignored_file_globs`、`link_directories` 和默认 24 小时 TTL。
- [ ] 对配置路径执行严格类型、重复项和明显绝对/越界形式校验；更新 `.ycode/config.example.yaml`。
- [ ] 新增 `SubagentIsolation.NONE / WORKTREE` 并加入 `SubagentRoleConfig`。
- [ ] 扩展角色 Frontmatter Loader：`isolation` 省略时为 `none`，非法值只禁用对应角色。
- [ ] 扩展 `run_subagent` 工具与运行时参数：可选 `isolation` 接受 `none/worktree`，并按“工具参数 → 角色定义 → `none`”解析。
- [ ] 建立生命周期、owner、record、lease、状态摘要、删除判定和任务 Worktree 摘要等 Provider 无关模型。
- [ ] 实现 `WorktreeName` 严格校验：字符集、段数、长度、空段、`.`/`..`、反斜杠、Windows 保留名、段尾点和连续 `--`。
- [ ] 实现基于角色与任务 ID 的系统命名、有限冲突后缀和 `ycode/<flat-name>` 可逆分支映射。
- [ ] 增加配置、角色字段、领域模型、名称合法性和映射往返单元测试。

**涉及文件：**

- `ycode/config/models.py`
- `ycode/config/__init__.py`
- `.ycode/config.example.yaml`
- `ycode/subagents/models.py`
- `ycode/subagents/loader.py`
- `ycode/subagents/__init__.py`
- `ycode/worktrees/__init__.py`
- `ycode/worktrees/models.py`
- `ycode/worktrees/naming.py`
- `tests/unit/config/test_models.py`
- `tests/unit/config/test_loader.py`
- `tests/unit/subagents/test_models.py`
- `tests/unit/subagents/test_loader.py`
- `tests/unit/worktrees/test_models.py`
- `tests/unit/worktrees/test_naming.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/config/test_models.py tests/unit/config/test_loader.py tests/unit/subagents/test_models.py tests/unit/subagents/test_loader.py tests/unit/worktrees/test_models.py tests/unit/worktrees/test_naming.py
.venv\Scripts\python.exe -m ruff check ycode/config ycode/subagents/models.py ycode/subagents/loader.py ycode/worktrees/models.py ycode/worktrees/naming.py tests/unit/config tests/unit/subagents/test_models.py tests/unit/subagents/test_loader.py tests/unit/worktrees
```

**覆盖：** F1–F2、F5、F8–F10、F12、F31–F32、F41、N2、N5、N15、AC1–AC2、AC8、AC10。

## Task 2：实现管理记录、磁盘互斥与纯文件系统 HEAD 读取

**依赖：** Task 1

**目标：** 建立不信任磁盘输入的持久化层，并完成快速恢复所需的纯文件系统身份核验。

**实现内容：**

- [ ] 按 `.ycode/worktrees/.state/records/agents/` 布局实现严格版本化 JSON codec。
- [ ] 读取记录后从合法逻辑名称重新推导路径和分支，拒绝记录中自定义的越界或不匹配路径。
- [ ] 使用同目录临时文件、刷新和原子替换写入记录，不输出本地文件正文或环境值。
- [ ] 实现短时 `mutation.lock`：排他创建、owner 信息、PID 存活辅助判断和未知状态 fail-closed。
- [ ] 实现只扫描受管记录命名空间的 list/get/save/delete 接口，不递归遍历主仓库。
- [ ] 实现 linked worktree `.git` 指针、反向 `gitdir`、`commondir`、loose ref 和 `packed-refs` 的纯文件系统 HEAD 读取。
- [ ] 快速恢复只接受同 owner、已完成初始化且所有字段匹配的记录；半初始化、损坏和来源不明现场保持不变。
- [ ] 使用 Git runner 替身断言快速恢复读取路径不会启动 Git 子进程。

**涉及文件：**

- `ycode/worktrees/store.py`
- `ycode/worktrees/git.py`
- `ycode/worktrees/models.py`
- `tests/unit/worktrees/test_store.py`
- `tests/unit/worktrees/test_head_reader.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_store.py tests/unit/worktrees/test_head_reader.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees/store.py ycode/worktrees/git.py ycode/worktrees/models.py tests/unit/worktrees/test_store.py tests/unit/worktrees/test_head_reader.py
```

**覆盖：** F7、F11、F31–F34、F40、F42、N2、N4、N10、N13–N14、AC2、AC8、AC10–AC11。

## Task 3：实现 Git Worktree 客户端与安全状态判定

**依赖：** Task 1、Task 2

**目标：** 用显式路径和稳定 porcelain 格式封装创建、状态、推送判断和删除所需的全部本地 Git 行为。

**实现内容：**

- [ ] 实现参数数组形式的异步 Git runner，所有调用使用显式 `git -C`，不修改进程 cwd。
- [ ] 实现仓库根、初始 commit、主 HEAD、分支存在性和 Git 可用性检查。
- [ ] 实现 `worktree add --lock --reason`、`worktree list --porcelain -z`、unlock、remove 和临时分支删除。
- [ ] 严格解析 worktree porcelain，交叉验证路径、分支、HEAD 和记录身份。
- [ ] 严格解析 `status --porcelain=v2 --branch -z`，区分 staged、modified 和 untracked。
- [ ] 查询 `<base>..HEAD` 新 commit、`git diff --stat <base>`、upstream 和新 commit 的本地可达性。
- [ ] 实现普通删除决策表：active、dirty、无 upstream 新 commit、未推送或未知状态均拒绝；clean 且无新 commit 不要求 upstream。
- [ ] 确认所有查询和清理路径不执行 fetch、push、pull 或其他网络操作。
- [ ] 使用本地临时 Git 仓库覆盖 detached/branch、clean/dirty、无 upstream、已推送引用和命令失败场景。

**涉及文件：**

- `ycode/worktrees/git.py`
- `ycode/worktrees/models.py`
- `tests/unit/worktrees/test_git_parsing.py`
- `tests/unit/worktrees/test_git_client.py`
- `tests/unit/worktrees/test_safety.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_git_parsing.py tests/unit/worktrees/test_git_client.py tests/unit/worktrees/test_safety.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees/git.py ycode/worktrees/models.py tests/unit/worktrees/test_git_parsing.py tests/unit/worktrees/test_git_client.py tests/unit/worktrees/test_safety.py
```

**覆盖：** F6、F10、F16、F25–F28、F36–F40、N4–N5、N7、AC2、AC5–AC6、AC9、AC11。

## Task 4：实现 Worktree 环境初始化与 Git Hooks 继承

**依赖：** Task 1–Task 3

**目标：** 在子任务启动前完成 Hooks 关键校验和三类 best-effort 环境初始化。

**实现内容：**

- [ ] 实现主仓库有效 `core.hooksPath` 解析，以及默认 Hooks 路径的主/子一致性校验。
- [ ] 扩展 `PowerShellCommandRunner`，支持不可变、显式合并的子进程环境覆盖且保持默认行为不变。
- [ ] 为自定义 Hooks 路径生成隔离子 Agent 专用 Git 配置环境，供命令 Runner 和 Git 客户端共同使用。
- [ ] 实现 `copy_files`：普通文件、同相对目标、不跟随越界链接、目标存在不覆盖、失败告警。
- [ ] 实现 `ignored_file_globs`：安全展开、普通文件、`git check-ignore` 确认、不跟随目录链接、不覆盖和逐项告警。
- [ ] 实现 `link_directories`：Linux/macOS 目录 symlink、Windows Junction、固定脚本与独立参数传递。
- [ ] 确保告警只含相对路径和错误类别，不含文件正文、密钥或环境变量值。
- [ ] 区分 Hooks/记录等关键失败与复制/链接 best-effort 失败，为 Manager 回滚提供结构化结果。
- [ ] 增加默认/自定义 Hooks、复制、Glob、Junction、不覆盖、越界和脱敏测试。

**涉及文件：**

- `ycode/worktrees/initialize.py`
- `ycode/worktrees/git.py`
- `ycode/tools/command.py`
- `tests/unit/worktrees/test_initializer.py`
- `tests/unit/worktrees/test_hooks.py`
- `tests/unit/tools/test_command.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_initializer.py tests/unit/worktrees/test_hooks.py tests/unit/tools/test_command.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees/initialize.py ycode/worktrees/git.py ycode/tools/command.py tests/unit/worktrees/test_initializer.py tests/unit/worktrees/test_hooks.py tests/unit/tools/test_command.py
```

**覆盖：** F12–F17、F18、N2、N4、N6–N7、AC3–AC4、AC11。

## Task 5：扩展路径解析并实现活动目录访问保护

**依赖：** Task 1、Task 2

**目标：** 为隔离运行时提供受控挂载和操作类型，并阻止主工作区工具进入其他任务的活动 Worktree。

**实现内容：**

- [ ] 为 `WorkspacePathResolver` 增加 read/write/search/command-cwd 操作类型、可选路径策略和受控挂载。
- [ ] 先校验工作区内词法路径，再校验真实目标；保留现有绝对路径、`..`、symlink 和 Junction 越界拒绝。
- [ ] 实现依赖目录的登记挂载：只允许通过 Worktree 内逻辑入口落到已登记物理源。
- [ ] 实现 `.ycode/memory` 的只读虚拟挂载：Read/Glob/Grep 可用，Write/Edit/命令 cwd 拒绝。
- [ ] 实现 `WorktreeAccessGuard`：进入受管候选时只读取对应记录，活动状态拒绝，记录缺失/损坏/未知时 fail-closed。
- [ ] 修改 Glob/Grep，使从父目录搜索时动态排除活动 Worktree 子树；无法可靠生成排除集合时拒绝覆盖受管根的搜索。
- [ ] 修改文件、搜索和命令工具按操作类型调用 Resolver；模型仍不能修改工作区根。
- [ ] 保持无策略、无挂载 Resolver 的现有行为和错误码兼容。
- [ ] 增加直接路径、父级搜索、链接绕过、只读 Memory、依赖挂载和普通路径回归测试。

**涉及文件：**

- `ycode/worktrees/access.py`
- `ycode/tools/paths.py`
- `ycode/tools/builtin/read_file.py`
- `ycode/tools/builtin/write_file.py`
- `ycode/tools/builtin/edit_file.py`
- `ycode/tools/builtin/glob.py`
- `ycode/tools/builtin/grep.py`
- `ycode/tools/builtin/run_command.py`
- `tests/unit/worktrees/test_access.py`
- `tests/unit/tools/test_paths.py`
- `tests/unit/tools/test_file_tools.py`
- `tests/unit/tools/test_search_tools.py`
- `tests/unit/tools/test_command.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_access.py tests/unit/tools/test_paths.py tests/unit/tools/test_file_tools.py tests/unit/tools/test_search_tools.py tests/unit/tools/test_command.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees/access.py ycode/tools/paths.py ycode/tools/builtin tests/unit/worktrees/test_access.py tests/unit/tools
```

**覆盖：** F18–F20、F21、F24、N2–N4、N8–N9、AC4–AC5、AC11。

## Task 6：实现 WorktreeManager 完整生命周期

**依赖：** Task 1–Task 5

**目标：** 统一实现创建、恢复、终态处置、安全删除、异常识别和过期清理。

**实现内容：**

- [ ] 实现项目级 `WorktreeManager` 及 `WorktreeLease`，生成每进程唯一实例 ID。
- [ ] 实现 acquire：取主 HEAD、生成名称、记录 `creating`、Git 创建并锁定、关键初始化、best-effort 初始化、记录 `active`。
- [ ] 目录或分支冲突时有限换名重试，不接管、覆盖或删除已有对象。
- [ ] 创建目标已存在时调用 Task 2 的纯文件系统恢复；仅恢复同 owner、完整初始化记录。
- [ ] 关键步骤失败只回滚本次身份匹配的目录和分支；回滚失败写入 `interrupted` 并保留。
- [ ] 实现 finalize：所有子 Agent 终态均检查 commit、dirty 和 diff；无变更删除，有变更 `retained`，未知状态保留并记录阻止原因。
- [ ] 实现 list/status、普通删除预检、force 风险预览和经确认的强制删除；active 与身份不明对象不可 force。
- [ ] 实现 cleanup：命名/元数据、占用/TTL、dirty/未推送三层检查，单项失败继续并汇总告警。
- [ ] 实现启动协调：活 owner 保留，明确失效 owner 标记 `interrupted`，随后只处理过期安全候选。
- [ ] 实现从受管 Worktree 内启动的路径检测和保守拒绝结果。
- [ ] 保证 Manager 不执行 commit、merge、rebase、cherry-pick、fetch、push 或网络操作。

**涉及文件：**

- `ycode/worktrees/manager.py`
- `ycode/worktrees/models.py`
- `ycode/worktrees/formatting.py`
- `ycode/worktrees/__init__.py`
- `tests/unit/worktrees/test_manager_create.py`
- `tests/unit/worktrees/test_manager_finalize.py`
- `tests/unit/worktrees/test_manager_delete.py`
- `tests/unit/worktrees/test_manager_cleanup.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_manager_create.py tests/unit/worktrees/test_manager_finalize.py tests/unit/worktrees/test_manager_delete.py tests/unit/worktrees/test_manager_cleanup.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees tests/unit/worktrees
```

**覆盖：** F4–F11、F17、F24–F34、F36–F43、N2–N7、N10、N13–N14、AC1–AC3、AC5–AC6、AC8–AC11。

## Task 7：建立稳定会话归属与一次性共享降级协议

**依赖：** Task 1、Task 6

**目标：** 让第一回合创建的 Worktree 也有稳定 session owner，并在隔离失败后强制跨用户回合取得一次性共享执行授权。

**实现内容：**

- [ ] 为 `SessionManager` 增加 session ID 预留接口；不提前创建空会话文件，提交、clear 和 restore 语义保持一致。
- [ ] 在 `ChatSession` 启动普通用户回合前完成预留，使子 Agent 工具调用能读取稳定 session ID。
- [ ] 新增内存级 fallback grant 模型，绑定 session、role、原始 task、mode、发放 turn 和随机 token。
- [ ] 隔离不可用时返回明确错误与 token，不登记运行任务，也不静默使用主仓库。
- [ ] 扩展 `run_subagent` 参数支持一次性 `shared_fallback_token`。
- [ ] 校验重试必须来自后续 parent turn、同一 session 和完全相同任务参数；成功消费后只能共享运行一次。
- [ ] 拒绝同回合、跨会话、参数改变、重复 token、未知 token 和进程恢复后的 token。
- [ ] 增加主 Agent 稳定说明，要求首次失败后先结束回合并询问用户，不自行假定授权。
- [ ] 增加首次会话失败/成功提交、clear/restore、token 发放和消费单元测试。

**涉及文件：**

- `ycode/session/manager.py`
- `ycode/session/chat.py`
- `ycode/subagents/models.py`
- `ycode/subagents/manager.py`
- `ycode/tools/builtin/run_subagent.py`
- `ycode/prompt/builder.py`
- `tests/unit/session/test_manager.py`
- `tests/unit/session/test_chat.py`
- `tests/unit/subagents/test_manager.py`
- `tests/unit/tools/test_run_subagent.py`
- `tests/unit/prompt/test_builder.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py tests/unit/session/test_chat.py tests/unit/subagents/test_manager.py tests/unit/tools/test_run_subagent.py tests/unit/prompt/test_builder.py
.venv\Scripts\python.exe -m ruff check ycode/session ycode/subagents ycode/tools/builtin/run_subagent.py ycode/prompt/builder.py tests/unit/session tests/unit/subagents/test_manager.py tests/unit/tools/test_run_subagent.py tests/unit/prompt/test_builder.py
```

**覆盖：** F4、F31、F33、F35、N8、N10、AC1、AC8、AC11。

## Task 8：实现隔离子 Agent 工作区运行时工厂

**依赖：** Task 4–Task 6

**目标：** 为每个隔离任务创建完整独立的工作区相关运行时，同时共享已批准的无路径基础设施。

**实现内容：**

- [ ] 实现 `SubagentWorkspaceFactory` 和具有明确资源所有权的运行时返回对象。
- [ ] 为隔离任务创建新的 Resolver、TextFileService、路径内置工具、Registry、Executor 和 Scheduler。
- [ ] 从主 Registry 按任务启动时快照复用 MCP 等非工作区工具；针对子 Registry 重建 `tool_search`。
- [ ] 继续注册但通过现有策略硬拒绝 `run_subagent`、Skill 安装和 Skill 加载，禁止嵌套与扩权。
- [ ] 创建独立 PermissionEngine、PermissionSession、PowerShellSafetyChecker 和带 Hooks 环境的 CommandRunner。
- [ ] 创建独立 ContextArtifactStore、ContextManager、PromptRuntimeContext、EnvironmentCollector 和 ToolContext。
- [ ] 从 Worktree 重新加载 `YCODE.md` 和引用；从主仓库新读取 Memory，并通过只读虚拟挂载提供按需读取。
- [ ] 使用主进程已验证规则创建独立 HookRuntime，项目路径指向 Worktree，跳过 session start/end。
- [ ] 明确关闭 AgentLoop、HookRuntime 和上下文临时资源，不关闭共享 Provider/MCP 连接。
- [ ] 普通子 Agent 继续走现有共享运行时路径；增加对比回归测试。

**涉及文件：**

- `ycode/worktrees/runtime.py`
- `ycode/worktrees/access.py`
- `ycode/subagents/runner.py`
- `ycode/tools/__init__.py`
- `ycode/prompt/project.py`
- `ycode/hooks/runtime.py`
- `tests/unit/worktrees/test_runtime.py`
- `tests/unit/prompt/test_project.py`
- `tests/unit/hooks/test_runtime.py`
- `tests/unit/subagents/test_runner.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees/test_runtime.py tests/unit/prompt/test_project.py tests/unit/hooks/test_runtime.py tests/unit/subagents/test_runner.py
.venv\Scripts\python.exe -m ruff check ycode/worktrees/runtime.py ycode/worktrees/access.py ycode/subagents/runner.py ycode/prompt/project.py ycode/hooks/runtime.py tests/unit/worktrees/test_runtime.py tests/unit/prompt/test_project.py tests/unit/hooks/test_runtime.py tests/unit/subagents/test_runner.py
```

**覆盖：** F18–F23、N1–N4、N8–N9、N15、AC4–AC5、AC11。

## Task 9：接入子 Agent 生命周期、可信路径说明与结果摘要

**依赖：** Task 6–Task 8

**目标：** 把 Worktree acquire/runtime/finalize 接入同步和异步子 Agent 的所有路径，并保持普通任务兼容。

**实现内容：**

- [ ] `SubagentManager.start()` 在登记 running 前根据工具参数和角色快照的优先级决定是否 acquire Worktree。
- [ ] 同步和异步隔离任务都等待关键初始化完成；显式请求隔离的 Fork 使用固定 `fork` 身份创建 Worktree，最终模式为 `none` 的任务不调用 WorktreeManager。
- [ ] 扩展 runtime request/assignment，使 Runner 选择共享或 Worktree 工厂，不读取模型提供的工作区路径。
- [ ] 在隔离任务原文前注入可信 parent/worktree 映射模板，保持原始任务正文逐字不变。
- [ ] 让隔离子 Agent 的文件、搜索、命令、Git、环境和 YCode Hooks 全部使用 assignment 路径。
- [ ] 在 `finally` 中覆盖 completed、failed、cancelled、limit_reached、Runner 异常和会话清理的 finalize。
- [ ] 扩展 `SubagentTaskView`、工具结果、异步通知和 `/tasks` 详情，显示完整 Worktree 摘要与告警。
- [ ] 接通 Task 7 fallback：合法 token 仅对该次调用强制共享，任务结果标识实际未使用 Worktree。
- [ ] 主 Agent 提示词加入活动目录 PowerShell 禁止规则，以及保留成果需用户授权后检查/整合的规则。
- [ ] 保持普通子 Agent 的共享 Registry、共享 Hook once、Memory 和结果格式兼容。

**涉及文件：**

- `ycode/subagents/models.py`
- `ycode/subagents/manager.py`
- `ycode/subagents/runner.py`
- `ycode/subagents/formatting.py`
- `ycode/tools/builtin/run_subagent.py`
- `ycode/prompt/builder.py`
- `ycode/subagents/resources/`（新增可信路径说明资源时）
- `tests/unit/subagents/test_manager.py`
- `tests/unit/subagents/test_runner.py`
- `tests/unit/subagents/test_formatting.py`
- `tests/unit/tools/test_run_subagent.py`
- `tests/unit/prompt/test_builder.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents/test_manager.py tests/unit/subagents/test_runner.py tests/unit/subagents/test_formatting.py tests/unit/tools/test_run_subagent.py tests/unit/prompt/test_builder.py tests/unit/worktrees
.venv\Scripts\python.exe -m ruff check ycode/subagents ycode/tools/builtin/run_subagent.py ycode/prompt ycode/worktrees tests/unit/subagents tests/unit/tools/test_run_subagent.py tests/unit/prompt tests/unit/worktrees
```

**覆盖：** F1–F6、F18–F30、F31–F35、N1–N10、AC1、AC4–AC8、AC11。

## Task 10：接入 Slash 命令、强制确认、启动恢复与应用装配

**依赖：** Task 6、Task 9

**目标：** 完成用户可见管理入口、启动协调和会话遗留提示，并保证 OpenAI 和既有命令契约不变。

**实现内容：**

- [ ] 新增 `/worktree list/status/delete/cleanup` 固定子命令解析和帮助，不增加 create/enter/exit。
- [ ] 扩展 UIController、ChatSession 和 TerminalUI 的 Worktree 查询、删除和清理入口。
- [ ] 普通 delete 显示统一阻止原因；`--force` 先显示风险摘要，再调用 InputBox 一次性确认。
- [ ] force 取消后不执行 Git 或记录写入；active 和身份未知对象即使 force 也拒绝。
- [ ] 在 Anthropic 应用装配中创建项目级 Store、AccessGuard、Manager 和 WorkspaceFactory，并完成依赖关闭顺序。
- [ ] 在 Provider/Agent 运行时创建前拒绝从受管 Worktree 内启动。
- [ ] 启动时只执行一次 interrupted 协调与过期清理，把单项失败汇总为启动告警，不启动后台周期任务。
- [ ] `ChatSession.restore()` 显示所属会话遗留的 retained/interrupted 名称，但不恢复任务或 `/tasks`。
- [ ] 保持 `--continue`、`/resume <session-id>`、`/resume` 缺参、`/tasks` 和 `/clear` 现有契约。
- [ ] OpenAI 路径不装配 Worktree Manager、命令行为或会话扩展。

**涉及文件：**

- `ycode/worktrees/formatting.py`
- `ycode/commands/builtin.py`
- `ycode/commands/contracts.py`
- `ycode/session/chat.py`
- `ycode/ui/input_box.py`
- `ycode/ui/terminal.py`
- `ycode/app.py`
- `tests/unit/commands/test_builtin.py`
- `tests/unit/commands/test_contracts.py`
- `tests/unit/session/test_chat.py`
- `tests/unit/ui/test_input_box.py`
- `tests/unit/ui/test_terminal.py`
- `tests/unit/test_app.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/commands tests/unit/session/test_chat.py tests/unit/ui/test_input_box.py tests/unit/ui/test_terminal.py tests/unit/test_app.py
.venv\Scripts\python.exe -m ruff check ycode/commands ycode/session/chat.py ycode/ui/input_box.py ycode/ui/terminal.py ycode/app.py tests/unit/commands tests/unit/session/test_chat.py tests/unit/ui/test_input_box.py tests/unit/ui/test_terminal.py tests/unit/test_app.py
```

**覆盖：** F7、F31–F44、N1、N4、N8、N10、N14–N15、AC8–AC11。

## Task 11：完成跨模块功能集成测试

**依赖：** Task 1–Task 10

**目标：** 使用本地临时 Git 仓库、Fake Provider 和真实工具组件验证完整协作链路。

**实现内容：**

- [ ] 验证主 HEAD 创建且主仓库 staged、modified、untracked 不进入 Worktree。
- [ ] 验证定义式同步/异步 `worktree` 创建不同目录和分支，`none` 与 Fork 继续共享。
- [ ] 验证两个隔离任务的文件修改、Registry、权限、Context、临时资源和 Hook once/Reminder 不串用。
- [ ] 验证 Worktree `YCODE.md`、主仓库只读 Memory、可信路径映射、默认/自定义 Git Hooks。
- [ ] 验证 best-effort 告警继续运行，关键失败回滚，告警不泄露文件正文或环境值。
- [ ] 验证所有子 Agent 终态的无变更清理、有变更保留和完整结果/通知摘要。
- [ ] 验证创建冲突换名、有效快速恢复无 Git 调用、损坏或来源不明现场保留。
- [ ] 验证主/普通子 Agent 的文件与搜索工具和命令 cwd 不能进入活动 Worktree。
- [ ] 验证普通删除安全门、clean/no-upstream 例外、force 预览，以及 cleanup 单项失败继续。
- [ ] 验证异常 owner 变 interrupted、TTL 启动清理、多 Session 独占和 restore 只提示不恢复。
- [ ] 验证隔离失败不能同回合降级，后续用户确认场景中的 token 只允许原任务共享一次。
- [ ] 验证普通子 Agent、Fork、MCP、Skill、Session、Hooks、Memory、命令和 OpenAI 既有测试回归。

**涉及文件：**

- `tests/integration/test_worktree_manager.py`
- `tests/integration/test_subagent_worktree_flow.py`
- `tests/integration/test_subagent_flow.py`
- `tests/integration/test_hook_agent_flow.py`
- `tests/integration/test_memory_system.py`
- `tests/support/fake_provider.py`

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_worktree_manager.py tests/integration/test_subagent_worktree_flow.py tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py tests/integration/test_memory_system.py
.venv\Scripts\python.exe -m ruff check tests/integration/test_worktree_manager.py tests/integration/test_subagent_worktree_flow.py tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py tests/integration/test_memory_system.py tests/support/fake_provider.py
```

**覆盖：** F1–F44、N1–N11、N13–N15、AC1–AC11。

## Task 12：完成真实终端验收与全量功能回归

**依赖：** Task 11

**目标：** 在真实交互终端验证核心用户流程，并执行仓库级功能性验收。

**实现内容：**

- [ ] 扩展终端 E2E：主 Agent 创建两个隔离子 Agent，观察不同路径/分支、独立修改、通知和保留。
- [ ] 验证活动期间主 Agent 文件/搜索工具和命令 cwd 被拒绝，进程级 cwd 全程不变。
- [ ] 验证用户授权后可检查保留成果，未授权时不自动提交、合并、rebase 或 cherry-pick。
- [ ] 验证 `/worktree list/status/delete/cleanup`，包括 force 风险预览、确认和取消。
- [ ] 模拟异常退出后的 interrupted 识别和启动清理；验证没有周期后台扫描。
- [ ] 验证 `--continue`、`/resume <session-id>`、`/resume` 缺参和 `/tasks` 进程内语义。
- [ ] 验证安全删除后 Worktree 与临时分支消失，主仓库未授权内容未改变。
- [ ] 运行格式、静态、编译、完整测试和真实终端用例；只修复本功能引入的回归。
- [ ] 记录全部实际命令、结果、通过数量、失败项和环境限制，供 checklist 最终验收使用。

**涉及文件：**

- `tests/e2e/test_terminal_chat.py`
- 本功能前述实现和测试文件（仅修复验证发现的问题）

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

**覆盖：** N11–N15、AC12–AC13，以及 AC1–AC11 的完整回归。

## 任务依赖总览

```text
Task 1 配置/模型/命名
    │
    ├──→ Task 2 记录/FS HEAD ──→ Task 3 Git 状态与安全 ──→ Task 4 初始化/Hooks ─┐
    │                    │                                                   │
    │                    └────────────→ Task 5 路径隔离 ──────────────────────┤
    │                                                                        ▼
    └──────────────────────────────────────────────────────────────→ Task 6 Manager
                                                                             │
                                                        ┌────────────────────┴────────────┐
                                                        ▼                                 ▼
                                             Task 7 Session/Fallback          Task 8 运行时工厂
                                                        └────────────────────┬────────────┘
                                                                             ▼
                                                                  Task 9 子 Agent 接入
                                                                             │
                                                                             ▼
                                                                  Task 10 命令/应用装配
                                                                             │
                                                                             ▼
                                                                  Task 11 集成测试
                                                                             │
                                                                             ▼
                                                                  Task 12 E2E/全量回归
```

实际执行保持编号顺序。依赖图只说明前置关系，不授权跳过某项实现或局部验证。

## Spec 与验收覆盖

| 范围 | 主要任务 |
|---|---|
| F1–F4 | Task 1、Task 7、Task 9、Task 11 |
| F5–F11 | Task 1–Task 3、Task 6、Task 11 |
| F12–F17 | Task 1、Task 4、Task 6、Task 11 |
| F18–F24 | Task 4–Task 5、Task 8–Task 9、Task 11–Task 12 |
| F25–F30 | Task 3、Task 6、Task 9、Task 11–Task 12 |
| F31–F35 | Task 2、Task 6–Task 7、Task 9–Task 11 |
| F36–F44 | Task 3、Task 6、Task 10–Task 12 |
| N1–N5 | Task 1–Task 11 |
| N6–N10 | Task 2、Task 4、Task 6–Task 11 |
| N11–N15 | Task 1、Task 6、Task 10–Task 12 |
| AC1–AC11 | Task 1–Task 11 |
| AC12–AC13 | Task 12 |

## 完成条件

全部 Task 的实现内容和局部验证均完成，Task 12 的全量命令取得实际结果，并逐项满足后续已批准 `checklist.md` 后，本功能才可标记完成。任务预算或单次执行时间不构成跳过功能或验证的理由。
