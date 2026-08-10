# YCode Agent Skills Checklist

> 状态：修订已批准（2026-08-10）
>
> 每项通过运行代码或观察行为验证；只做功能性验收，不扩展生产级验证。

## 标准格式、发现与渐进加载

- [ ] **C1（AC1）最小标准 Skill 可发现并使用默认配置。** 在临时项目创建只含合法 `name`、`description` 和正文的 `.ycode/skills/sample/SKILL.md`，启动目录扫描后显示为可用、共享模式、当前模型、当前上下文、继承工具且无预批准。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_loader.py tests/unit/skills/test_catalog.py`，观察最小格式用例通过。）
- [ ] **C2（AC1）非法标准格式产生明确不可用原因。** 分别使用目录名不匹配、非法名称、缺少必填字段、损坏 frontmatter 和松散单文件，合法 Skill 不受影响。（验证：运行 loader/catalog 单元测试，观察每个非法条目有稳定 error，松散文件不被扫描。）
- [ ] **C3（AC2）YCode 扩展配置正确生效。** 共享/隔离模式、命名模型、summary/recent/none、recent turns、visible tools 和 argument hint 均得到预期配置。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_loader.py tests/unit/config/test_loader.py`。）
- [ ] **C4（AC2）无效扩展组合不会猜测默认值。** 共享模式指定模型、隔离模式缺上下文、recent 缺少合法回合数、缺失模型或未知普通工具时条目不可用。（验证：运行 loader/config 单元测试，观察对应错误文本。）
- [ ] **C5（AC3）扫描故障相互隔离且结果确定。** 同时放置合法、损坏、工具缺失、模型缺失、内置命令冲突和规范化重名 Skill，合法项仍可用，冲突项没有扫描顺序覆盖。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_catalog.py`。）
- [ ] **C6（AC3）单项无效不阻止普通 Anthropic 对话。** 使用包含不可用 Skill 的项目启动应用并完成一轮 Fake Provider 对话。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/test_app.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C7（AC4）未激活时只披露名称和说明。** 检查首次模型请求包含可用 Skill 的名称与说明，不包含正文、scripts、references 或 assets 内容。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/prompt/test_runtime.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C8（AC4）激活后下一轮加载完整 SOP。** Agent 调用 `load_skill` 后的下一次模型请求包含调用时快照正文，未引用随附资源仍不进入上下文。（验证：运行 Skill Agent 集成测试并检查 Fake Provider 捕获请求。）

## 共享与隔离执行

- [ ] **C9（AC5）多个共享 Skill 持续且稳定。** 激活两个共享 Skill 后，当前任务后续轮次和后续用户回合都按名称顺序携带两份 SOP。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_runtime.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C10（AC5）压缩不删除共享 SOP，停用会移除影响。** 执行上下文压缩后 SOP 仍存在；停用一个 Skill 后下一回合只包含另一个，并同步更新工具集合。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_sessions.py`。）
- [ ] **C11（AC6）summary 隔离上下文准确。** 隔离 Agent 收到由现有摘要和全部已提交历史生成的最新临时摘要以及当前任务，主 ContextManager 不被提交。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_context.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C12（AC6）recent 和 none 上下文准确。** recent=N 只携带最近 N 个完整用户回合且不拆散工具调用与结果；none 不携带任何旧历史；两者都原样携带当前任务。（验证：运行 context 单元测试和 Skill Agent 集成测试。）
- [ ] **C13（AC6）隔离主会话只接收最终交接。** 显式调用只提交展开后的用户任务和最终交接；自动调用只把交接放入父 Agent 的工具结果，内部 Thinking、工具过程和中间消息均不回流。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_isolated.py tests/unit/session/test_chat.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C14（AC7）隔离失败和取消不生成子会话。** 成功、失败和取消后不存在独立可恢复会话，临时 Provider、模型流和授权均被清理；已完成的工作区修改不自动回滚。（验证：运行 isolated 单元测试和隔离集成测试。）
- [ ] **C15（AC7）命名模型仅影响隔离 Anthropic Skill。** 未声明模型时继承活动配置；合法命名 Anthropic 配置被使用；共享、缺失和 OpenAI 配置引用被拒绝。（验证：运行 config loader、isolated 和 app 单元测试。）

## 调用、工具与权限

- [ ] **C16（AC8）自动和显式调用都在执行前重读。** 普通任务能触发 `load_skill`；提交动态 Slash Command 能立即执行；仅补全或 `/help` 不激活。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_commands.py tests/integration/test_skill_agent_flow.py tests/integration/test_skill_sessions.py`。）
- [ ] **C17（AC8、AC9）调用时读取失败不提交任务。** 删除、损坏或改成无效配置后显式/自动调用返回明确错误，不激活、不替换旧快照、不写入新历史。（验证：运行 catalog、ChatSession 和 session 集成测试。）
- [ ] **C18（AC9）参数保持原样并与 SOP 分离。** `/<name> MiXeD  internal   spaces` 在终端显示原命令，模型和存档只记录稳定展开任务；无参数明确写为未提供；SOP 不发生替换。（验证：运行 commands、ChatSession 和 TerminalUI 单元测试。）
- [ ] **C19（AC9）热更新与全量 reload 边界正确。** 修改既有正文后再次调用使用新内容；无效修改保留旧快照；新增、删除和重命名只在 `/skills reload` 后更新目录与命令。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_sessions.py`。）
- [ ] **C20（AC10）工具白名单只能收窄并稳定合并。** 单个共享 Skill 使用自己的白名单，多个共享 Skill 取并集，未声明白名单继承当前模式原始工具。（验证：运行 runtime 单元测试和 Skill Agent 集成测试，检查每轮 ToolDefinition 名称。）
- [ ] **C21（AC10）标准工具名正确映射。** Read、Write、Edit、Bash、PowerShell、Glob、Grep、ToolSearch 与对应 YCode 名称产生相同工具集合。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_loader.py`。）
- [ ] **C22（AC10、AC11）参数级授权安全降级。** `Bash(git:*)` 显示 warning、Skill 保持可用且不获得 run_command 预批准；同字段中的普通工具授权仍生效，Git 命令可走普通权限流程。（验证：运行 loader、runtime、权限和 Skill Agent 集成测试。）
- [ ] **C23（AC11）显式调用的普通 allowed-tools 仅本任务免审批。** 显式 Skill 任务内可识别工具不重复询问，任务完成、失败或取消后同一工具重新服从普通权限；历史共享 Skill 不贡献授权。（验证：运行 runtime、security engine 和 Skill Agent 集成测试。）
- [ ] **C24（AC11）自动与嵌套预批准先询问。** 审批信息包含 Skill 名称和工具列表；同意后本任务生效，拒绝后不激活且父 Agent 可以继续。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/security/test_engine.py tests/integration/test_skill_agent_flow.py`。）
- [ ] **C25（AC12）底层权限和 plan-only 始终优先。** 预批准不能覆盖 security 拒绝、命令安全、工作区边界或 plan-only；plan-only 可见 `load_skill`，只暴露读取工具，不显示 `install_skill`，显式 Skill 不切换模式。（验证：运行 security、AgentLoop 和 Skill Agent 集成测试。）
- [ ] **C26（AC13）嵌套深度和分支隔离正确。** 两层和三层调用成功；循环和第四层返回稳定错误；重复加载共享 Skill 不重复 SOP；隔离分支内共享激活不进入主会话。（验证：运行 runtime 单元测试和隔离嵌套集成测试。）
- [ ] **C27（AC14）随附资源按需访问。** 激活时不注入资源；SOP 明确引用后，Agent 可通过现有文件或命令工具访问，并继续经过可见性、权限和工作区检查。（验证：运行 Skill Agent 集成测试中的受控资源场景。）

## 管理、会话与事务

- [ ] **C28（AC15）管理命令反映实际状态。** `/skills`、show、deactivate、reload 分别显示来源、状态、元数据、配置、warning/error 和激活变化，且不调用模型或写入历史。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/skills/test_commands.py tests/integration/test_skill_sessions.py`。）
- [ ] **C29（AC15）`/clear` 建立新的空会话。** clear 后历史、摘要、共享 Skill、临时授权为空，模式为 agent；目录、MCP、项目记忆和权限配置模式保留；旧会话仍可恢复。（验证：运行 ChatSession、ContextManager 和 session 集成测试。）
- [ ] **C30（AC16）共享 Skill 状态随会话恢复。** 恢复后按当前磁盘内容激活有效 Skill；删除、改名或不可用项跳过并告警；不恢复旧 SOP、隔离 Skill 或任务预批准。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py tests/unit/session/test_chat.py tests/integration/test_skill_sessions.py`。）
- [ ] **C31（N7、N9）状态变更先落盘后更新内存。** 模拟回合提交、停用、reload 和恢复存储失败，历史、摘要、目录、命令和共享状态均保持旧值，不出现半提交。（验证：运行 SessionManager、ChatSession、Catalog 和 Runtime 的存储失败测试。）
- [ ] **C32（N8、N10）取消和快照边界一致。** 取消安装、隔离摘要或工具调用后没有临时文件、活动授权或子任务；回合中磁盘变化不改变当前快照。（验证：运行 installer、isolated、runtime 单元测试和对应集成测试。）

## 安装、冲突与示例 Skill

- [ ] **C33（AC17）安装始终请求审批。** `install_skill(source_url)` 在 agent 模式即使权限为 allow 也显示用户提供的原始 HTTPS URL 并等待人工选择；拒绝时不解析或下载；plan-only 不暴露该工具。工具描述明确用户给出受支持 URL 时直接调用，由调用自动触发审批。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/tools/test_skill_tools.py tests/unit/security/test_engine.py`。）
- [ ] **C34（AC17）四类公开 HTTPS 来源原子安装。** 使用 HTTP 替身分别从直接 ZIP、skills.sh 详情页、GitHub tree 单 Skill 目录和原始 `SKILL.md` 安装到 `.ycode/skills/<frontmatter-name>/`；目录来源保留随附资源，原始来源只含 `SKILL.md`；安装后立即出现在 `/skills`、帮助和补全中，但不激活或执行。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_skill_install.py`。）
- [ ] **C35（AC18）明确的来源、下载和结构失败不留残余。** 不支持的网页、skills.sh/GitHub 定位缺失或歧义、解析后私有地址、HTTP 错误、累计超 30 MB、损坏 ZIP、多个顶层目录、名称不匹配、绝对/越界路径、symlink、submodule 和同名目录均失败且无半安装目录。（验证：运行 installer 单元测试和安装集成测试。）
- [ ] **C36（AC18）合法但依赖缺失的来源仍完成安装。** 任一受支持来源解析出的 Skill 若缺失工具或命名模型，仍显示“已安装但不可用”和具体原因，不注册动态执行命令。（验证：运行安装集成测试并检查目录及管理输出。）
- [ ] **C37（AC19）内置命令和规范化冲突确定处理。** Skill 不覆盖 `/help` 等内置命令；同范围重名双方不可用；reload 整体失败保留旧目录、命令、帮助、补全和激活状态。（验证：运行 catalog、registry、commands 和 session 集成测试。）
- [ ] **C38（AC20）三个示例使用通用路径。** `commit` 为共享，`review` 为 recent=5 隔离，`test` 为 none 隔离；将相同配置放入另一个合法名称目录时产生等价行为，删除任一目录并 reload 后对应命令消失。（验证：用 Loader 扫描三个真实文件，并运行 catalog 和 session 集成测试中的复制、重命名、删除场景。）

## 回归与质量检查

- [ ] **C39（AC21、N11）真实 PTY 核心流程通过。** 在单一 Windows PTY 场景完成列表、显式共享、自动调用、隔离交接、停用、reload、clear、一次受控 skills.sh 详情页形态安装和安全退出；其他来源由 HTTP 替身集成测试覆盖，输入、帮助、补全及布局无回归。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py`。）
- [ ] **C40（AC21、N12）Anthropic 既有功能无回归。** 流式文本、Thinking、工具循环、MCP、审批、plan-only、压缩、恢复、记忆和退出整理测试通过。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit tests/integration`。）
- [ ] **C41（N1）OpenAI 路径保持原状。** OpenAI 应用不扫描 Skill、不注册 `load_skill`/`install_skill` 或动态 Skill 命令，现有 OpenAI 流测试通过。（验证：运行 `.venv\Scripts\python.exe -m pytest -q tests/unit/providers/test_openai.py tests/integration/test_openai_stream.py tests/unit/test_app.py`。）
- [ ] **C42（AC22、N14）自动化验证只使用本地替身。** Skill 测试使用临时目录、Fake Provider、HTTP 替身或受控本地 HTTPS 服务，不请求真实付费模型或不受控公网。（验证：检查测试夹具并运行全部 Skill 测试，确认无外部凭据要求。）
- [ ] **C43 格式检查通过。**（验证：运行 `.venv\Scripts\python.exe -m ruff format --check .`，期望退出码 0。）
- [ ] **C44 静态检查通过。**（验证：运行 `.venv\Scripts\python.exe -m ruff check .`，期望退出码 0。）
- [ ] **C45 编译检查通过。**（验证：运行 `.venv\Scripts\python.exe -m compileall -q ycode tests`，期望退出码 0。）
- [ ] **C46 完整测试通过。**（验证：运行 `.venv\Scripts\python.exe -m pytest -q`，期望退出码 0。）

## 验收范围边界

- [ ] **C47 未执行生产级验证。** 未增加或运行压力、性能、长时间稳定性、大规模并发、复杂故障注入、DNS 重绑定攻防、恶意样本库、全面安全审计、多平台矩阵或真实付费 API 测试。（验证：检查测试清单与最终执行记录只包含上述功能性命令和受控场景。）

## 覆盖自检

- AC1–AC4：C1–C8。
- AC5–AC7：C9–C15。
- AC8–AC14：C16–C27。
- AC15–AC16：C28–C32。
- AC17–AC20：C33–C38。
- AC21–AC22：C39–C47。
- 至少一个端到端场景：C39。
