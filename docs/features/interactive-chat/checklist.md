# YCode 交互式对话 Checklist

> 状态：已批准

> 每一项都通过运行命令、检查模拟服务记录或观察真实终端行为来验证。真实 Anthropic/OpenAI Key 的手动冒烟不作为自动化验收前置条件。

> 最近一次执行结果：输入框与 Thinking 缺陷修复 8/8 项通过；项目共 70/72 项通过。剩余 2 项 Git 状态检查因当前 `.git` 为空目录、项目尚未初始化为 Git 仓库而无法执行。本次输入提示区扩展的受影响项目已重新置为待验证。

> 本次四行输入提示区实现与测试代码已完成；按用户要求未运行 pytest、ConPTY 或 YCode 启动验证，相关项目保留为待验收。

> 用户消息背景板已实现；按用户要求未补充测试代码且未启动验证，AC24 保留为待验收。

## 项目与依赖

- [x] 现有 `.venv` 未被删除或重建，解释器版本不低于 Python 3.12。（验证：运行 `.venv\Scripts\python.exe --version`，并检查 `.venv\pyvenv.cfg`）
- [x] `pyproject.toml` 声明 `requires-python = ">=3.12"`、全部运行/开发依赖及 `ycode = "ycode.cli:main"`。（验证：用 `tomllib` 读取并断言对应字段）
- [x] `requirements.txt` 仅通过 `-e .[dev]` 引用项目依赖，没有维护第二套重复清单。（验证：读取文件内容并执行 `.venv\Scripts\python.exe -m pip install -r requirements.txt`）
- [x] 源码位于项目根目录 `ycode/`，不存在多余的 `src/` 层，editable install 后可以导入 `ycode`。（验证：运行 `.venv\Scripts\python.exe -c "import ycode"`）
- [ ] `.gitignore` 忽略 `.venv/`、`.ycode/config.yaml`、缓存和构建产物，但不会忽略 `.ycode/config.example.yaml`。（验证：用 `git check-ignore` 分别检查真实配置和示例配置；阻塞：当前项目尚未初始化为 Git 仓库）

## Spec 验收标准

- [x] **AC1——配置发现与进入对话：** 从包含配置的项目目录和其子目录启动时，均加载最近的 `.ycode/config.yaml` 并显示 TUI；传入 `--config` 时只使用指定文件；可以提交一条非空消息。（验证：运行配置发现单元测试和 Windows ConPTY 端到端测试）
- [x] **AC2——活动 Provider：** 多个配置中只使用 `active` 项；修改 `active` 并重启后，请求中的协议、模型和 `base_url` 随之切换。（验证：运行 `tests/unit/config`、ProviderFactory 测试，并检查本机模拟服务的请求记录）
- [x] **AC3——无效配置：** 找不到配置、显式路径不存在、无效 YAML、缺少活动项、名称缺失或重复，以及活动配置缺少必填字段或协议无效时，均显示可定位原因并以非零状态退出。（验证：运行 `tests/unit/config` 与 `tests/unit/test_cli.py`）
- [x] **AC4——API Key 两种写法：** 活动配置的明文 Key 和 `${ENV_VAR}` 均可加载；活动配置缺失环境变量时指出变量名且不进入 TUI；未激活配置缺失环境变量不影响启动。（验证：运行 `tests/unit/config/test_loader.py`）
- [x] **AC5——协议请求：** Anthropic 请求到 Messages API，OpenAI 请求到 Chat Completions API；认证、模型和消息内容正确。（验证：运行两个 `tests/integration/test_*_stream.py` 并检查模拟服务记录）
- [x] **AC6——真实流式显示：** 模拟服务延迟后续增量时，第一个文本增量在结束事件前出现在终端，未等待完整回答。（验证：运行两个 Provider 集成测试和 Windows ConPTY 流式场景）
- [x] **AC7——多轮上下文：** 第一轮完成后发送第二轮，第二次请求包含第一轮用户消息、完整回答和第二轮问题，顺序正确。（验证：运行 `tests/unit/session/test_chat.py`，并检查端到端模拟服务第二次请求）
- [x] **AC8——会话不持久化：** 正常退出不产生历史文件；重新启动后的第一条请求不包含上一次进程的消息。（验证：连续执行两次 Windows ConPTY 会话并检查临时目录及请求记录）
- [x] **AC9——启用 Claude Thinking：** `thinking: true` 产生 adaptive extended thinking 请求；Thinking 增量显示在独立纯文本区域，最终文本显示在回答区域。（验证：运行 Anthropic 单元/集成测试和 Thinking 端到端场景）
- [x] **AC10——关闭 Claude Thinking：** 未配置或设置 `thinking: false` 时，Anthropic 请求显式携带 `thinking: {"type": "disabled"}`；即使兼容服务意外返回 Thinking 增量，界面也不显示 Thinking 区域。（验证：运行 Anthropic 单元/集成测试和关闭 Thinking 的 Windows ConPTY 场景）
- [x] **AC11——OpenAI 拒绝 Thinking：** 活动 OpenAI 配置的 `thinking: true` 在启动校验阶段失败，说明字段只适用于 Anthropic，且模拟服务没有收到请求；未激活条目不触发该校验。（验证：运行配置模型测试并检查模拟服务请求数为零）
- [x] **AC12——统一事件：** 两个 Provider 都只向上返回 `THINKING_DELTA`、`TEXT_DELTA`、`COMPLETED` 或统一 `ProviderError`；ChatSession/TUI 中没有供应商 SSE 类型判断。（验证：运行 Provider、Session、TUI 单元测试，并用代码搜索检查依赖边界）
- [x] **AC13——错误恢复：** 认证失败、限流、网络断开、服务端错误和流中断均显示整理后的错误，随后可以继续发送并成功完成下一轮。（验证：运行两个 Provider 集成错误测试及 Windows ConPTY“失败后成功”场景）
- [x] **AC14——密钥不泄漏：** 配置错误、Provider 错误、CLI 输出、TUI 输出和异常字符串均不包含测试 Key、认证头或完整响应对象。（验证：运行安全相关测试，并对捕获输出执行 Key 全文搜索）
- [x] **AC15——安全退出：** `/exit`、`/quit` 和 `Ctrl+C` 均能结束程序，关闭网络客户端和 Live 区域，恢复终端且不打印 traceback。（验证：运行 TerminalUI/App/CLI 单元测试和 Windows ConPTY 退出场景）
- [x] **AC16——可替换 Provider：** 注入 FakeProvider 后，无需修改 ChatSession 或 TUI 即可完成一轮 Thinking、文本和完成事件对话。（验证：运行 `tests/unit/session/test_chat.py` 与 `tests/unit/ui/test_terminal.py`）
- [x] **AC17——无真实 Key 集成：** 使用占位 Key 和本机模拟 SSE 服务即可覆盖两种协议、流式输出、多轮上下文、Thinking 和错误恢复。（验证：运行 `.venv\Scripts\python.exe -m pytest tests/integration tests/e2e -q`）
- [x] **AC18——Windows 完整流程：** 在 Windows ConPTY 中启动 YCode、输入两轮消息、观察流式回复、退出，进程零退出。（验证：运行 `.venv\Scripts\python.exe -m pytest tests/e2e/test_terminal_chat.py -q`）
- [x] **AC19——示例与手动测试文档：** 示例配置不含真实 Key，README 和 `docs/manual-api-test.md` 分别给出 Anthropic/OpenAI 的配置、切换和真实 API 冒烟步骤。（验证：解析示例 YAML并人工审阅两份文档；不要求自动测试使用真实 Key）
- [x] **AC20——响应计时：** 用户提交后、首个增量前即可看到从零增长的耗时；完成或失败后冻结总耗时；下一轮从零重新开始，计时刷新不阻塞延迟 SSE。（验证：运行计时器/渲染单元测试和带首包延迟的 ConPTY 场景）
- [x] **AC21——完成后 Markdown：** 跨增量拆分 Markdown 标记时，完成前按顺序显示原始纯文本；完成后整体替换为标题、粗体、斜体、行内代码、代码块、列表、引用和链接格式；Thinking 始终为纯文本。（验证：运行 `tests/unit/ui/test_renderer.py` 和跨增量 Markdown ConPTY 场景）
- [x] **AC22——未激活配置延迟校验：** 一个有效活动配置与一个字段缺失或无效的备用配置可以共存并正常启动；备用配置的环境变量不被解析，也不创建 Provider。切换 `active` 到备用配置后才显示对应错误并以非零状态退出。（验证：运行配置单元测试、应用边界测试和 Windows ConPTY 启动场景）
- [ ] **AC23——四行输入提示区：** 输入等待期间按顺序同时显示普通上横线、蓝色 `❯` 与 `Send a message...`、普通下横线和独立的 `? for help`；没有反色工具栏、背景色块或左右竖边，退出后无动态区域残留。（验证：运行输入组件单元测试和 Windows ConPTY 输入提示场景）
- [ ] **AC24——用户消息背景板：** 提交消息后，滚动区显示无边框低对比度背景板、蓝色 `❯` 和消息正文；背景板与输入区等宽且最长 100 列，多行正文对齐，不显示 `You`。（验证：用户在真实终端人工验收）

## 活动 Provider 两阶段校验

- [x] 第一阶段拒绝非映射顶层、缺少 `active`、非列表 `providers`、缺少或空白 `name`、重复名称和找不到活动名称。（验证：运行 `tests/unit/config/test_models.py` 与 `test_loader.py`）
- [x] 未激活条目允许缺少 `protocol`、`model`、`base_url`、`api_key` 和 `thinking`。（验证：使用最小备用条目加载配置）
- [x] 未激活条目的无效协议、错误字段类型及 `thinking` 协议组合不参与完整校验。（验证：参数化配置加载测试）
- [x] 未激活条目中的 `${MISSING_ENV}` 不读取环境变量、不报错、不回写文件。（验证：加载前后比较环境读取记录与配置文件内容）
- [x] 活动条目仍完整校验所有必填字段、合法协议、URL、Key 和 `thinking` 适用范围。（验证：将 `active` 逐项切换到无效备用配置）
- [x] ProviderFactory 只接收完整的活动 `ProviderConfig`，只创建一个官方 SDK 客户端。（验证：应用装配测试断言工厂调用参数和次数）
- [x] 有效活动项加不完整备用项时，Windows 启动进入 TUI，发送请求只使用活动项；切换到备用项后启动安全失败且无 API 请求。（验证：Windows ConPTY 场景）
- [x] README 和真实 API 手册说明活动配置校验规则，示例仍不含真实 Key。（验证：人工审阅并执行密钥模式搜索）
- [x] 未实现热更新、Provider 预创建或自动故障转移。（验证：审阅配置、应用与工厂调用路径）

## 架构与集成

- [x] `TerminalUI → ChatSession → ChatProvider` 依赖方向成立，Provider 不调用 ChatSession，Session/TUI 不导入官方 SDK 类型。（验证：代码搜索导入关系，并运行全部单元测试）
- [x] ChatSession 保存统一 `ChatMessage`，Provider 只在请求时转换成供应商格式，不把供应商专用结构写回历史。（验证：运行多轮 Session 测试并检查 FakeProvider 收到的消息）
- [x] 成功轮次在 `COMPLETED` 后提交；Provider 错误、缺少完成事件和取消路径不提交当前残缺轮次，但保留此前成功历史。（验证：运行 `tests/unit/session/test_chat.py` 的成功、回滚和取消场景）
- [x] Anthropic/OpenAI 官方异步 SDK 均使用配置中的 `base_url`、模型和 Key，且关闭 SDK 自动重试。（验证：运行 Provider 单元测试并断言构造参数和调用次数）
- [x] ProviderFactory 只根据协议注册表创建实现，增加 FakeProvider 不需要修改 ChatSession/TUI。（验证：运行 `tests/unit/providers/test_factory.py` 和 FakeProvider 流程）
- [x] App 的正常、错误和取消路径都通过 `finally` 关闭 Session/Provider，重复关闭不会报错。（验证：运行 `tests/unit/test_app.py` 和资源释放断言）
- [x] 本机 SSE 测试服务结束后释放端口、线程和连接，不在完整测试结束后留下后台进程。（验证：运行集成/E2E 测试后检查测试进程正常退出）

## TUI 可观察行为

- [x] 宽终端显示蓝色 ASCII 猫图标，右侧仅显示 Provider、Protocol、Model、Thinking；不显示 Config 路径。（验证：运行头部单元测试并在 Windows Terminal 实际观察）
- [x] 窄终端把信息移动到猫图标下方，内容不因宽度不足而丢失。（验证：用窄 ConPTY 尺寸运行头部场景并观察输出）
- [ ] 输入区在 `Send a message...` 等待期间同时显示上下普通横线、蓝色 `❯`、占位符和下方 `? for help`；不支持该字符时回退为 `>`。（验证：运行输入框单元测试和 Windows ConPTY 输入等待场景）
- [x] 用户消息只显示正文，不出现 `You` 标签；回答标题为 `● YCode`，Thinking 标题为 `◇ Thinking`。（验证：运行 TUI 单元测试并检查 ConPTY 输出）
- [x] 响应进行期间不同时激活新输入提示，完成或失败后才恢复输入。（验证：运行带延迟 SSE 的 ConPTY 场景）
- [x] 首期没有 TUI 内配置编辑、Provider 切换、代码语法高亮或自定义颜色主题。（验证：观察界面并搜索是否存在未批准的配置入口）

## 输入框与 Thinking 缺陷修复

- [ ] 输入组件使用非全屏 prompt_toolkit 四行布局，不再使用带默认反色语义的 `bottom_toolbar`。（验证：审阅 Application/HSplit 结构并运行输入框单元测试）
- [ ] 输入正常提交、`Ctrl+C`、EOF 或提示异常后均清理整个动态提示区，不遗留横线或提示文字。（验证：覆盖正常和异常输入测试）
- [x] `thinking: false` 的 Anthropic 请求体精确包含 `{"type": "disabled"}`，不包含 adaptive 或 display。（验证：Provider 单元测试和本机 SSE 请求记录）
- [x] 关闭 Thinking 时，服务端返回带唯一标记的 `thinking_delta` 不产生 `THINKING_DELTA`，文本增量与完成事件仍正常返回。（验证：Provider 单元/集成测试）
- [x] `thinking: true` 仍发送 adaptive summarized，并继续分别返回 Thinking、文本和完成事件。（验证：原有 Thinking 单元、集成和端到端测试）
- [x] Windows ConPTY 在输入等待阶段可同时观察上下横线；关闭 Thinking 场景不显示模拟思考标记、能显示最终回答并安全退出。（验证：运行对应 E2E 场景）

## 四行输入提示区扩展

- [ ] 四个区域固定按“上横线 → 输入行 → 下横线 → `? for help`”排列，并在等待输入期间同时可见。（验证：检查布局树和 ConPTY 输出位置）
- [ ] 上下横线使用相同字符与宽度，预留终端最后一列，不发生折行。（验证：宽、窄终端单元测试和 ConPTY 场景）
- [ ] 横线没有反色、背景色块或左右竖边，提示区不继承横线样式。（验证：检查样式定义和捕获的 ANSI 输出）
- [ ] 横线颜色通过内部参数或集中样式令牌注入；替换测试颜色后上下横线同时变化，`? for help` 保持原样。（验证：样式注入单元测试）
- [ ] 输入 `?` 会作为普通用户消息交给 ChatSession/Provider，不触发本地帮助、命令或 Skill。（验证：本机 SSE 请求记录）
- [ ] 项目没有新增帮助命令、Skill 实现、YAML 颜色字段或主题系统。（验证：审阅 CLI、配置模型、输入组件和依赖）

## 安全与范围

- [ ] `.ycode/config.yaml` 不被 Git 跟踪，环境变量解析后不回写文件。（验证：运行配置测试，并用 `git status --short`/`git check-ignore` 检查；阻塞：当前项目尚未初始化为 Git 仓库）
- [x] 项目没有会话历史持久化文件、数据库或缓存写入逻辑。（验证：端到端会话前后比较临时工作目录文件清单）
- [x] 项目没有 tool use、函数调用、Agent 框架、文件编辑、Shell 执行、MCP 或多代理实现。（验证：审阅依赖和模块，并搜索相关入口）
- [x] 运行依赖仅用于官方 API、配置、异步输入和终端渲染；pywinpty 仅为 Windows 测试依赖。（验证：审阅 `pyproject.toml` 的 dependencies 和 dev extra）

## 构建与测试

- [x] Ruff 格式检查通过。（验证：`.venv\Scripts\python.exe -m ruff format --check .`）
- [x] Ruff lint 通过。（验证：`.venv\Scripts\python.exe -m ruff check .`）
- [x] 全部单元测试通过。（验证：`.venv\Scripts\python.exe -m pytest tests/unit -q`）
- [x] 两种 Provider 的本机 SSE 集成测试通过。（验证：`.venv\Scripts\python.exe -m pytest tests/integration -q`）
- [x] Windows ConPTY 端到端测试通过。（验证：`.venv\Scripts\python.exe -m pytest tests/e2e -q`）
- [x] 完整测试集一次性通过，无跳过的必需验收场景。（验证：`.venv\Scripts\python.exe -m pytest -q`）
- [x] 所有源码与测试可编译。（验证：`.venv\Scripts\python.exe -m compileall -q ycode tests`）
- [x] 安装后的 `ycode` 和 `python -m ycode` 均可用，`--help` 正常显示且不要求 API Key。（验证：分别运行 `.venv\Scripts\ycode.exe --help` 和 `.venv\Scripts\python.exe -m ycode --help`）

## 端到端场景

- [x] **场景 1——OpenAI 双轮对话：** 从配置所在目录的子目录启动 → 看到头部和输入框 → 提交第一轮 → 首包前看到计时 → 流式看到原文 → 完成后看到 Markdown → 提交第二轮 → 模拟服务收到完整历史 → `/exit` 零退出。
- [x] **场景 2——Claude Thinking：** 使用 `thinking: true` 的 Anthropic 配置启动 → Thinking 在独立纯文本区域流式显示 → 最终回答单独显示并在结束后渲染 Markdown → 总耗时冻结 → 安全退出。
- [x] **场景 3——失败恢复：** 第一轮在部分文本后断流 → 部分文本和安全错误可见 → 计时冻结 → 第二轮计时归零并成功完成 → 第一轮残缺内容未进入第二轮请求。
- [x] **场景 4——重新启动：** 完成一轮后退出 → 工作目录没有历史文件 → 再次启动并发送消息 → 请求不包含上一个进程的内容。
- [x] **场景 5——配置失败：** 在无配置目录、无效配置和缺失环境变量三种情况下启动 → 每次都显示具体原因、无 traceback、无 API 请求并以非零状态退出。
