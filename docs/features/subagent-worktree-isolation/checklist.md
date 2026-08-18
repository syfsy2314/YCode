# 子 Agent Worktree 隔离最终验收清单

## 文档状态

- 对应 Spec：`docs/features/subagent-worktree-isolation/spec.md`
- 对应 Plan：`docs/features/subagent-worktree-isolation/plan.md`
- 对应 Task：`docs/features/subagent-worktree-isolation/task.md`
- 当前状态：已执行验收（2026-08-18；完整回归存在 1 项用户原有文件状态导致的失败）
- 使用时机：全部开发任务完成后逐项执行并记录实际结果

## 验收规则

1. 只有实际执行并观察到结果后才能勾选，不根据代码阅读、Mock 配置或预期推断通过。
2. 任一必选项失败时先修复并重新验证；不得以剩余时间或实现成本为由跳过。
3. Git 场景使用本地临时仓库和本地引用，Provider 使用 Fake Provider，不调用真实付费 API、真实远端服务或网络。
4. 不进行压力测试、性能基准、长时间稳定性、大规模并发、复杂竞态、故障注入或多平台矩阵。
5. 真实终端验收使用 PTY 或等价交互终端；不能只调用内部 Python 方法替代用户流程。
6. 最终报告必须列出实际命令、通过/失败/跳过数量、未执行项目及原因。

## A. 文档、范围与代码边界

- [ ] A1. 实现只覆盖已批准 Spec、Plan 和 Task，没有增加未批准的产品行为。
- [ ] A2. `spec.md`、`plan.md`、`task.md` 和本清单之间不存在已知冲突或未记录偏差。
- [ ] A3. 新增 Python 标识符使用英文，新增注释使用简洁中文，并遵循仓库既有结构和风格。
- [ ] A4. 用户开发前已有的无关工作区变更未被覆盖、移动或清理。
- [ ] A5. 本期只接入 Anthropic；OpenAI 的 Agent、Session、命令和应用装配行为未增加 Worktree 分支。
- [ ] A6. 没有主 Agent Worktree 切换、手动 create/enter/exit、Fork Worktree 或子 Agent 嵌套。
- [ ] A7. 没有自动 commit、merge、rebase、cherry-pick、冲突解决、集成分支或合并队列。
- [ ] A8. 没有自动 fetch、pull、push、远端分支创建、托管平台调用或其他隐式网络操作。
- [ ] A9. 没有任务恢复、周期后台清理、文件监听、持久化调度、分布式锁、监控或操作系统级沙箱。
- [ ] A10. 没有执行压力、性能、长稳、大规模并发、复杂故障注入、多平台矩阵或真实付费 API 验证。

## B. 隔离声明与失败降级（AC1）

- [ ] B1. 项目角色 `isolation: none` 可用，省略 `isolation` 时默认行为与当前普通子 Agent 相同。
- [ ] B2. 项目角色 `isolation: worktree` 可用，并保存在任务派发时的不可变角色快照中。
- [ ] B3. `isolation` 未知值、错误类型或未知 Frontmatter 字段只使对应角色不可用，不阻止其他角色或应用启动。
- [ ] B4. `run_subagent` Schema 包含可选 `isolation`，只接受 `none/worktree`；非法值只拒绝本次调用。
- [ ] B5. 工具参数优先于角色定义，两处都省略时使用本地工作区；工具可为定义式同步、异步及无角色 Fork 请求 Worktree，也可用 `none` 覆盖角色默认隔离。
- [ ] B6. 任意子 Agent 仍不能调用 `run_subagent` 创建嵌套子 Agent。
- [ ] B7. Git 不可用、仓库无初始 commit 或 Worktree 创建失败时，子任务不会登记为 running 或静默共享主仓库。
- [ ] B8. 隔离失败结果包含明确原因和一次性 fallback token，主 Agent 会先结束当前回合并询问用户。
- [ ] B9. 同一父回合内携带 token 重试被拒绝；未获得新的用户回合时不能自动降级。
- [ ] B9. 用户在后续回合明确允许后，完全相同的 session、角色、任务和模式可以共享执行一次。
- [ ] B10. token 的参数改变、跨会话、重复使用、未知值或进程恢复后使用均被拒绝。

## C. 安全创建、命名与快速恢复（AC2）

- [ ] C1. 每个隔离任务自动得到唯一 Worktree 和唯一 `ycode/` 临时分支，没有手动创建入口。
- [ ] C2. Worktree 的基准是创建时主 Agent 当前 HEAD。
- [ ] C3. 主仓库 staged、modified 和 untracked 内容不出现在新 Worktree。
- [ ] C4. 所有数据目录位于主仓库 `.ycode/worktrees/agents/`，管理记录位于受管 `.state` 目录。
- [ ] C5. 从有效或疑似受管 Worktree 内启动 Anthropic YCode 会被拒绝并提示返回主仓库。
- [ ] C6. 逻辑名称只来源于角色和任务 ID，不读取任务正文或模型输出。
- [ ] C7. 名称字符、分段、长度、`.`/`..`、反斜杠、Windows 保留名、段尾点和连续 `--` 限制全部生效。
- [ ] C8. 逻辑名称与 `ycode/<flat-name>` 分支映射确定、可逆且无碰撞。
- [ ] C9. 目录、记录或来源不明分支冲突时自动换后缀有限重试，不接管、覆盖或删除冲突对象。
- [ ] C10. 创建使用主 HEAD，Git worktree 在任务活动期带有 owner reason 的 lock。
- [ ] C11. 有效的同 owner 完整记录和 `.git` 指针对应时，快速恢复能返回正确 HEAD。
- [ ] C12. 快速恢复路径实际未调用 Git 子进程。
- [ ] C13. 记录缺失、损坏、半初始化、路径/分支/HEAD 不匹配或未知 ref 后端时保留现场并报错。

## D. 环境初始化与 Git Hooks（AC3）

- [ ] D1. 初始化规则只从主仓库 `.ycode/config.yaml` 的 `worktrees` 段读取，子 Worktree 不复制该配置。
- [ ] D2. `copy_files` 可复制配置列出的普通本地文件到相同相对路径。
- [ ] D3. 显式源缺失、目标已存在、类型错误或复制失败时不覆盖目标，只产生告警并继续。
- [ ] D4. `ignored_file_globs` 只复制 Git 实际忽略、规范化后仍在主仓库内的普通文件。
- [ ] D5. 绝对 Glob、`..`、越界结果、非忽略文件、目录、symlink/Junction 目录和目录外目标不会被复制。
- [ ] D6. 单个 Glob 匹配失败不阻止其他匹配或子 Agent 启动。
- [ ] D7. `link_directories` 在 Linux/macOS 创建目录 symlink，在 Windows 创建 Junction。
- [ ] D8. 依赖源缺失、目标已存在或链接失败只产生告警且不覆盖目标。
- [ ] D9. 主仓库未配置 `core.hooksPath` 时，主/子 Git 实际使用同一默认 Hooks 目录。
- [ ] D10. 主仓库配置相对或绝对 `core.hooksPath` 时，隔离子 Agent 的 Git 命令实际使用同一规范化目录。
- [ ] D11. Hooks 配置通过子进程环境覆盖实现，没有修改主仓库共享 Git 配置或主 Agent Runner 环境。
- [ ] D12. Hooks 路径无法可靠确认一致时，任务不启动并回滚本次创建。
- [ ] D13. Worktree、管理记录和 Hooks 关键步骤失败会回滚；回滚失败时记录 interrupted 并保留现场。
- [ ] D14. 复制、Glob 和依赖链接失败属于 best-effort，只汇总告警。
- [ ] D15. 告警、日志和任务结果不包含被复制文件正文、密钥、环境变量值或 Git 配置覆盖值。

## E. 路径驱动工具与进程 cwd（AC4）

- [ ] E1. 创建、运行两个隔离任务并结束后，YCode 进程级 cwd 与启动前完全相同。
- [ ] E2. 主 Agent、普通子 Agent和每个隔离子 Agent持有明确、不可由模型覆盖的绝对 workspace。
- [ ] E3. 隔离 Agent 的 Read/Write/Edit/Glob/Grep/RunCommand、Git 状态和环境信息都作用于自己的 Worktree。
- [ ] E4. 模型使用相对路径时以自己的 Worktree 为根正确解析。
- [ ] E5. 命令 `cwd` 可选择自己 Worktree 内的相对子目录，不能改变 workspace 根。
- [ ] E6. 绝对路径、`..`、symlink、Junction 或直接物理挂载路径不能越界到主仓库或其他 Worktree。
- [ ] E7. 配置依赖链接只能从登记的 Worktree 逻辑入口访问，解析结果必须仍位于登记源目录。
- [ ] E8. `.ycode/memory` 只读虚拟挂载可由 Read/Glob/Grep 使用，Write/Edit 和命令 cwd 被拒绝。
- [ ] E9. 未启用策略或挂载的普通 WorkspacePathResolver 保持原有解析行为和错误契约。

## F. 项目上下文、Hooks 与独占访问（AC5）

- [ ] F1. 隔离子 Agent使用派发时已验证的角色快照，运行中修改角色文件不改变任务。
- [ ] F2. 隔离子 Agent从自己的 Worktree 重新加载对应版本的 `YCODE.md` 和 `@include` 文件。
- [ ] F3. 每个隔离任务都从主仓库重新读取 Memory，且任务完成时不调用 MemoryUpdater。
- [ ] F4. 可信路径说明位于原始任务正文之前，包含父/子绝对路径映射；原始正文逐字不变。
- [ ] F5. 任务中的父仓库绝对路径按可信映射解释为子 Worktree 中相同相对路径，不直接访问父仓库。
- [ ] F6. 隔离子 Agent触发现有回合、消息、工具、上下文压缩和 Agent 错误 Hooks。
- [ ] F7. 隔离子 Agent不重复触发 session start/end。
- [ ] F8. 隔离任务复用已验证 Hook 规则，但 `once`、Reminder、后台任务和运行状态与父 Agent及其他任务独立。
- [ ] F9. Shell Hook 的执行路径是对应 Worktree；普通子 Agent仍共享现有 HookRuntime 和会话级 `once`。
- [ ] F10. 活动 Worktree owner 是准确的 session ID + task ID；PID 只作为辅助存活信息。
- [ ] F11. 另一个会话或 Agent通过文件工具直接访问活动 Worktree 时被拒绝。
- [ ] F12. Glob/Grep 从主仓库父级搜索时会排除活动 Worktree，不会读取其中内容。
- [ ] F13. 另一个会话或 Agent把活动 Worktree 或其子目录作为命令 cwd 时被拒绝。
- [ ] F14. 活动记录缺失、损坏或无法读取时，候选受管目录访问 fail-closed。
- [ ] F15. 主 Agent提示词明确禁止 PowerShell 命令正文访问活动 Worktree，并要求整合前取得用户授权。
- [ ] F16. 验收没有把任意 PowerShell 命令正文路径访问声称为操作系统级硬隔离。
- [ ] F17. Worktree 进入 retained/interrupted 后活动访问门解除，主 Agent在用户授权后可以显式检查。

## G. 完成判定、结果与整合边界（AC6、AC7）

- [ ] G1. completed、failed、cancelled 和 limit_reached 都会关闭子运行时并执行 Worktree 完成检查。
- [ ] G2. 相对基准无新 commit 且无 staged、modified、untracked 时，Worktree、临时分支和记录自动删除。
- [ ] G3. 存在任一新 commit、staged、modified 或 untracked 时，Worktree 和分支进入 retained。
- [ ] G4. 失败、取消或达到轮次上限但留下变更时同样 retained，不以任务状态覆盖 Git 判定。
- [ ] G5. Git 或身份检查无法可靠完成时保留目录，并在摘要中显示未知状态和阻止原因。
- [ ] G6. 同步结果、异步通知和 `/tasks` 详情都包含名称、绝对路径、分支、基准和当前 HEAD。
- [ ] G7. 结果包含工作区分类状态、基准后 commit、diff stat、upstream、未推送和初始化/检查告警。
- [ ] G8. 任务结果不自动附带完整 diff；主 Agent只能在用户授权后按需检查。
- [ ] G9. 未提交成果不会被系统自动 commit，保留在其 Worktree 中。
- [ ] G10. 两个子 Agent完成后没有自动 merge、rebase、cherry-pick 或主分支修改。
- [ ] G11. 用户授权后主 Agent可依次检查和整合；无法判断的冲突不会被系统自动解决。

## H. 持久化、多会话与会话恢复（AC8）

- [ ] H1. 每条记录跨进程保存名称、路径、分支、基准、HEAD、状态、时间、告警、session、task 和进程信息。
- [ ] H2. 记录生命周期至少能观察到 creating、active、retained、interrupted。
- [ ] H3. 首次用户回合在写入会话文件前获得稳定预留 session ID，Worktree owner 不为空或临时替换。
- [ ] H4. 预留 ID 不创建空会话文件；成功提交沿用该 ID，`/clear` 清除，restore 激活目标 ID。
- [ ] H5. 两个会话可以同时持有不同 Worktree，同一个 Worktree 不能同时被两个 session/task 持有。
- [ ] H6. 可能仍存活的其他进程 owner 保持 active 并被跳过；不能只靠 PID 等值认领。
- [ ] H7. 明确失去 owner 的 active 记录在下次启动时变为 interrupted 并保留。
- [ ] H8. 异常退出后的模型请求、工具调用、子 Agent和任务结果不会恢复。
- [ ] H9. `/tasks` 重启后仍只展示当前进程任务，不从 Worktree 记录重建。
- [ ] H10. `--continue` 恢复最近会话，`/resume <session-id>` 恢复指定会话。
- [ ] H11. `/resume` 无参数仍返回原用法错误，没有新增 `--resume`。
- [ ] H12. 会话恢复只提示其 retained/interrupted 名称，不启动子 Agent或改动 Worktree。

## I. 状态查询与安全删除（AC9）

- [ ] I1. `/worktree list` 展示名称、生命周期、分支、owner、最后活动时间和上次阻止原因。
- [ ] I2. list 主要读取持久化摘要，不为所有记录执行重型 Git 状态检查。
- [ ] I3. `/worktree status <name>` 展示路径、分支、基准、HEAD、owner、生命周期和工作区状态。
- [ ] I4. status 展示 upstream、未推送 commit、时间、初始化告警和删除阻止原因。
- [ ] I5. status 只读取本地 Git 与远端跟踪引用，不执行 fetch 或网络操作。
- [ ] I6. 普通 delete 同时删除 Worktree、对应 YCode 临时分支和管理记录。
- [ ] I7. active、dirty、身份未知、Git 检查失败和占用未知时普通 delete 拒绝且现场不变。
- [ ] I8. 有新 commit 但没有 upstream 时，普通 delete 视为无法确认已推送并拒绝。
- [ ] I9. 有新 commit 且仍有 commit 不被本地 upstream 包含时，普通 delete 拒绝。
- [ ] I10. 没有新 commit 且工作区干净时，即使没有 upstream 也允许普通 delete。
- [ ] I11. 新 commit 全部被本地 upstream 包含且工作区干净时允许普通 delete，不访问网络。
- [ ] I12. `--force` 仍拒绝 active 和身份不明对象。
- [ ] I13. 可 force 候选先显示 staged/modified/untracked、新 commit、未推送和 diff stat 风险摘要。
- [ ] I14. force 只要求一次明确交互确认；取消后不调用删除 Git 命令或修改记录。
- [ ] I15. 确认 force 后只删除预览中身份完全相同的受管 Worktree 与临时分支。

## J. 过期清理与命令范围（AC10）

- [ ] J1. 只有 `agents/...` 受管记录参与过期清理，其他目录、Git worktree 和分支不被扫描或删除。
- [ ] J2. 默认 TTL 为 24 小时，配置为其他正整数小时后生效。
- [ ] J3. TTL 从最后生命周期/管理活动计算；任务结束和 status 查询会更新最后活动时间。
- [ ] J4. active 候选即使超过 TTL 也不被清理。
- [ ] J5. 清理依次检查命名/元数据、占用/过期、dirty/未推送，任何未知结论都保留。
- [ ] J6. 只有全部检查通过的过期候选才同时删除 Worktree、临时分支和记录。
- [ ] J7. 单个候选检查或删除失败时保留该候选、汇总告警并继续其他候选。
- [ ] J8. Anthropic 启动时只执行一次 interrupted 协调和 cleanup，不阻止应用展示其余非致命告警。
- [ ] J9. `/worktree cleanup` 立即执行相同安全流程，没有 force 行为。
- [ ] J10. 等待超过 TTL 或一个模拟清理周期后没有后台扫描、定时器或文件监听发生。
- [ ] J11. `/help` 只展示 list、status、delete、cleanup，不出现 create、enter、exit、认领或导入。
- [ ] J12. 非法 `/worktree` 参数返回稳定用法错误，不调用模型、不写入历史。

## K. 安全失败与兼容性（AC11）

- [ ] K1. 角色、任务、配置和磁盘元数据中的路径均不能指定 workspace 根或越过受管边界。
- [ ] K2. 记录路径从合法名称重新推导，不直接信任 JSON 中的绝对路径。
- [ ] K3. 删除前同时验证记录、规范化路径、Git worktree 身份、分支和 owner。
- [ ] K4. 创建冲突只换名，不覆盖、删除、移动或认领已有目录、记录或分支。
- [ ] K5. 记录、Git、Hooks、占用或删除安全无法确认时均保留现场并返回明确错误。
- [ ] K6. 初始化、状态和错误输出没有泄露复制文件正文、密钥或环境变量值。
- [ ] K7. 主 Agent、普通子 Agent和 Fork 未启用 Worktree 时，文件系统、Registry、权限、Context 和 Hook once 语义不变。
- [ ] K8. 普通子 Agent继续共享主 HookRuntime；只有 Worktree 隔离子 Agent拥有独立 Hook 状态。
- [ ] K9. Provider、MCP 连接及无路径共享基础设施没有按 Worktree 重复创建或被子任务错误关闭。
- [ ] K10. Skill、MCP、Memory、Session、命令和现有工具回归测试通过。
- [ ] K11. OpenAI 现有流式、Session 和应用测试通过，且没有 Worktree 用户入口变化。
- [ ] K12. 核心 Worktree 模型、Store、Git 客户端和 Manager 不依赖 Anthropic SDK 类型。

## L. 真实终端协作流程（AC12）

- [ ] L1. 在真实终端由主 Agent分别通过角色默认值和工具 `isolation: worktree` 创建两个隔离子 Agent。
- [ ] L2. 两个任务显示不同逻辑名称、绝对路径和临时分支。
- [ ] L3. 两个子 Agent能独立修改同一相对文件而不互相覆盖或观察中间状态。
- [ ] L4. 两个任务的通知包含各自 Worktree 摘要，留下变更时均正确 retained。
- [ ] L5. 活动期间主 Agent文件工具、搜索遍历和命令 cwd 不能进入对应目录。
- [ ] L6. 真实流程前后进程 cwd 保持不变。
- [ ] L7. 系统不会在没有用户授权时自动检查完整 diff、提交或整合。
- [ ] L8. 用户明确授权后，主 Agent可以检查两个 retained 成果并按指示整合。
- [ ] L9. 冲突或无法判断的整合不会自动解决，会交还用户决定。
- [ ] L10. `/worktree list/status` 在真实终端展示与 Git 实际状态一致的信息。
- [ ] L11. 安全删除或确认 force 后，对应目录、临时分支和记录消失。
- [ ] L12. 整个流程中主仓库未授权内容没有被子 Agent或 Manager 修改。

## M. 验证边界（AC13）

- [ ] M1. 创建、恢复、初始化、保护、完成、删除和清理均由本地临时 Git 仓库测试覆盖。
- [ ] M2. 子 Agent与对话流程使用 Fake Provider，不调用真实 Anthropic/OpenAI 付费 API。
- [ ] M3. Git 推送判断使用本地 refs/upstream 模拟，不创建或访问真实远端。
- [ ] M4. Windows Junction 行为在当前 Windows 环境中实际验证；其他平台只验证平台分支单元逻辑，不声称完成多平台矩阵。
- [ ] M5. CLI、强制确认、双 Agent协作和会话恢复使用真实 PTY 或等价交互终端验证。
- [ ] M6. 没有为了验收启动周期后台清理或长时间等待；通过可控时钟验证 TTL。
- [ ] M7. 没有执行未批准的压力、性能、长稳、大规模并发、复杂竞态或故障注入。

## N. 自动化验证命令

### N1. Worktree 单元测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/worktrees
```

- [x] 命令已实际执行。
- [x] 结果通过；60 passed。

### N2. 子 Agent 与工具单元测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/subagents tests/unit/tools
```

- [x] 命令已实际执行。
- [x] 结果通过；134 passed，1 skipped。

### N3. 配置、Session、Prompt、Hook、命令、UI 与应用单元测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/config tests/unit/session tests/unit/prompt tests/unit/hooks tests/unit/commands tests/unit/ui tests/unit/test_app.py
```

- [x] 命令已实际执行。
- [x] 结果通过；304 passed。

### N4. Worktree 与子 Agent 集成测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_worktree_manager.py tests/integration/test_subagent_worktree_flow.py tests/integration/test_subagent_flow.py tests/integration/test_hook_agent_flow.py tests/integration/test_memory_system.py
```

- [x] 命令已实际执行。
- [x] 结果通过；9 passed。

### N5. 真实终端 E2E

```powershell
.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py
```

- [x] 命令已实际执行。
- [x] 结果通过；25 passed。

### N6. 格式检查

```powershell
.venv\Scripts\python.exe -m ruff format --check .
```

- [x] 命令已实际执行。
- [x] 结果通过。

### N7. 静态检查

```powershell
.venv\Scripts\python.exe -m ruff check .
```

- [x] 命令已实际执行。
- [x] 结果通过（首次受 Ruff 缓存影响 panic，清理缓存后原命令通过）。

### N8. 编译检查

```powershell
.venv\Scripts\python.exe -m compileall -q ycode tests
```

- [x] 命令已实际执行。
- [x] 结果通过。

### N9. 完整测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

- [x] 命令已实际执行。
- [ ] 完整结果为 860 passed、1 failed、2 skipped；唯一失败是用户原有删除/不可访问的
  `.ycode/skills/frontend-design/SKILL.md` 导致既有 Skill catalog 测试报 `PermissionError`。
  排除该测试后为 860 passed、2 skipped、1 deselected。

## O. 最终结果记录

实施完成后填写，不预填结果：

| 验证项 | 实际命令 | 实际结果 | 备注 |
|---|---|---|---|
| Worktree 单元测试 | N1 | 60 passed | 通过 |
| 子 Agent 与工具单元测试 | N2 | 134 passed, 1 skipped | 通过；既有平台跳过 |
| 其他受影响模块单元测试 | N3 | 304 passed | 通过 |
| 集成测试 | N4 | 9 passed | 通过 |
| 真实终端 E2E | N5 | 25 passed | 通过，包含双 Worktree 场景 |
| 格式检查 | N6 | passed | 通过 |
| 静态检查 | N7 | passed | 清理 Ruff 缓存后通过 |
| 编译检查 | N8 | passed | 通过 |
| 完整测试 | N9 | 860 passed, 1 failed, 2 skipped | 失败项来自用户原有 Skill 文件删除/权限状态 |

## 完成判定

以下条件必须同时满足：

- [ ] Task 1–Task 12 的实现内容全部完成，且每项局部验证实际通过。
- [ ] A–M 的范围、功能、安全、兼容和真实终端项目全部实际通过。
- [ ] N1–N9 全部实际执行并通过。
- [ ] 没有已知未报告失败、未说明的必选跳过项或未记录的环境限制。
- [ ] 最终报告准确列出命令、数量、失败/跳过项和仍存在的限制。

全部条件满足后，子 Agent Worktree 隔离功能才可标记完成。
