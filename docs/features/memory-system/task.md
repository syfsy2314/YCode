# YCode 记忆系统 Tasks

> 状态：已批准

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `.gitignore` | 忽略本地会话和记忆目录 |
| 修改 | `pyproject.toml` | 打包记忆整理 Prompt 资源 |
| 修改 | `README.md` | 说明项目指令、会话恢复和项目记忆 |
| 修改 | `ycode/prompt/models.py` | 项目指令、项目记忆补充类型与快照模型 |
| 修改 | `ycode/prompt/runtime.py` | 会话补充顺序和模式状态重置 |
| 修改 | `ycode/prompt/__init__.py` | 导出项目上下文接口 |
| 新建 | `ycode/prompt/project.py` | `YCODE.md` 展开与项目上下文加载 |
| 修改 | `ycode/agent/contracts.py` | `TurnMessage` 与带时间的回合结果 |
| 修改 | `ycode/agent/loop.py` | 为完整 Agent 消息记录 UTC 时间 |
| 修改 | `ycode/agent/plain.py` | 保持纯聊天结果契约兼容 |
| 修改 | `ycode/agent/events.py` | 会话恢复成功事件 |
| 修改 | `ycode/agent/__init__.py` | 导出新增事件和模型 |
| 修改 | `ycode/context/models.py` | 恢复候选、检查点结果模型 |
| 修改 | `ycode/context/manager.py` | 无副作用恢复预检、激活和状态重置 |
| 修改 | `ycode/context/__init__.py` | 导出恢复接口 |
| 新建 | `ycode/session/models.py` | JSONL 记录、描述、快照和警告 |
| 新建 | `ycode/session/codec.py` | 结构化消息与 JSON 双向转换 |
| 新建 | `ycode/session/manager.py` | `SessionManager` CRUD、追加、重放和修复 |
| 修改 | `ycode/session/chat.py` | 写前提交、`/resume`、退出整理协调 |
| 修改 | `ycode/session/__init__.py` | 导出会话存储接口 |
| 新建 | `ycode/memory/__init__.py` | 记忆包公共接口 |
| 新建 | `ycode/memory/models.py` | 记忆类型、条目、快照和操作计划 |
| 新建 | `ycode/memory/store.py` | 记忆加载、校验和安全应用 |
| 新建 | `ycode/memory/updater.py` | 退出时隔离模型分析和响应解析 |
| 新建 | `ycode/memory/resources/__init__.py` | 记忆 Prompt 资源包 |
| 新建 | `ycode/memory/resources/update.md` | 记忆整理 System Prompt |
| 修改 | `ycode/cli.py` | `--continue` 参数 |
| 修改 | `ycode/app.py` | 使用项目根并装配新组件 |
| 修改 | `ycode/ui/terminal.py` | 恢复事件、警告和退出整理结果 |
| 新建 | `tests/unit/prompt/test_project.py` | 指令展开和项目上下文测试 |
| 修改 | `tests/unit/prompt/test_models.py` | 新补充类型测试 |
| 修改 | `tests/unit/prompt/test_runtime.py` | 补充顺序和模式重置测试 |
| 新建 | `tests/unit/session/test_models.py` | 会话记录模型测试 |
| 新建 | `tests/unit/session/test_codec.py` | JSON 消息编解码测试 |
| 新建 | `tests/unit/session/test_manager.py` | 会话 CRUD、提交、恢复和修复测试 |
| 修改 | `tests/unit/session/test_chat.py` | 写前提交、压缩、恢复和退出测试 |
| 新建 | `tests/unit/memory/test_models.py` | 记忆模型验证测试 |
| 新建 | `tests/unit/memory/test_store.py` | 索引和主题文件存储测试 |
| 新建 | `tests/unit/memory/test_updater.py` | 隔离模型请求和响应测试 |
| 修改 | `tests/unit/context/test_models.py` | 恢复候选模型测试 |
| 修改 | `tests/unit/context/test_manager.py` | 无副作用恢复和检查点测试 |
| 修改 | `tests/unit/agent/test_contracts.py` | 带时间回合结果测试 |
| 修改 | `tests/unit/agent/test_loop.py` | Agent 消息时间记录测试 |
| 修改 | `tests/unit/test_cli.py` | `--continue` 解析测试 |
| 修改 | `tests/unit/test_app.py` | 项目根和组件装配测试 |
| 修改 | `tests/unit/ui/test_terminal.py` | 恢复与退出提示测试 |
| 修改 | `tests/support/fake_provider.py` | 支持记忆整理和恢复集成响应 |
| 新建 | `tests/integration/test_memory_system.py` | 会话、上下文和记忆集成测试 |
| 修改 | `tests/e2e/test_terminal_chat.py` | 真实终端恢复与退出记忆场景 |

## T1：扩展系统补充模型

**文件：** `ycode/prompt/models.py`、`ycode/prompt/runtime.py`、
`ycode/prompt/__init__.py`、`tests/unit/prompt/test_models.py`、
`tests/unit/prompt/test_runtime.py`  
**依赖：** 无  
**步骤：**

1. 增加项目指令和项目记忆补充类型，并保持确定性会话补充顺序。
2. 为运行时上下文增加模式提醒状态重置入口。
3. 增加类型、顺序、生命周期和重置测试。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/prompt/test_models.py tests/unit/prompt/test_runtime.py`，期望全部通过。

## T2：定义项目记忆模型

**文件：** `ycode/memory/__init__.py`、`ycode/memory/models.py`、
`tests/unit/memory/test_models.py`  
**依赖：** 无  
**步骤：**

1. 定义四类记忆、文件名前缀映射、条目、快照、警告和变更操作。
2. 校验路径、非空字段、类型与操作负载组合。
3. 测试合法模型及类别、路径和操作错误。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/memory/test_models.py`，期望全部通过。

## T3：实现记忆索引与主题读取

**文件：** `ycode/memory/store.py`、`tests/unit/memory/test_store.py`  
**依赖：** T2  
**步骤：**

1. 解析固定 Markdown 索引格式和 YAML front matter。
2. 校验名称、说明、类别、文件前缀和单层相对路径。
3. 使用真实路径边界拦截绝对路径、`..` 和符号链接逃逸。
4. 返回只包含有效条目的规范化索引及非致命警告。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/memory/test_store.py -k load`，期望缺失索引、坏条目和路径逃逸场景全部通过。

## T4：实现记忆变更安全应用

**文件：** `ycode/memory/store.py`、`tests/unit/memory/test_store.py`  
**依赖：** T3  
**步骤：**

1. 在内存中计算并完整校验操作后的最终记忆集合。
2. 实现创建、正文更新、替代项创建和受限删除。
3. 先完成主题文件，再原子替换索引，最后删除旧文件。
4. 模拟无效操作和中途写入失败，验证旧索引仍可读取。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/memory/test_store.py -k apply`，期望全部通过。

## T5：实现项目指令与上下文加载

**文件：** `ycode/prompt/project.py`、`ycode/prompt/__init__.py`、
`tests/unit/prompt/test_project.py`  
**依赖：** T1、T3  
**步骤：**

1. 读取可选 `YCODE.md`，在原位置展开独占行 `@include`。
2. 实现第 0 至第 5 层深度、循环检测和相对当前文件解析。
3. 把指令错误转换为启动错误，把记忆问题保留为警告。
4. 生成项目指令和规范化记忆索引两个会话级补充。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/prompt/test_project.py`，期望正常展开、缺失文件、循环、超深和路径逃逸测试通过。

## T6：为 Agent 回合记录消息时间

**文件：** `ycode/agent/contracts.py`、`ycode/agent/loop.py`、
`ycode/agent/plain.py`、`tests/unit/agent/test_contracts.py`、
`tests/unit/agent/test_loop.py`  
**依赖：** 无  
**步骤：**

1. 定义 UTC `TurnMessage`，让回合结果保存带时间消息并提供兼容消息视图。
2. 在用户提交、Assistant 完整消息和工具结果形成时记录时间。
3. 更新纯聊天运行器和现有构造点。
4. 测试时间有效性、消息顺序和既有终态约束。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_contracts.py tests/unit/agent/test_loop.py tests/unit/agent/test_plain.py`，期望全部通过。

## T7：定义会话记录与恢复模型

**文件：** `ycode/session/models.py`、`ycode/session/__init__.py`、
`tests/unit/session/test_models.py`  
**依赖：** T6  
**步骤：**

1. 定义消息、提交和检查点记录及统一版本。
2. 定义会话描述、快照、提交结果、恢复警告和错误。
3. 校验 UTC 时间、会话 ID、回合 ID、消息数量和检查点字段。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_models.py`，期望全部通过。

## T8：实现结构化消息 JSON 编解码

**文件：** `ycode/session/codec.py`、`tests/unit/session/test_codec.py`  
**依赖：** T7  
**步骤：**

1. 显式编码和解码文本、Thinking、Redacted Thinking、工具调用与工具结果内容块。
2. 实现三类版本化 JSONL 记录的单行 JSON 编解码和 UTC 时间转换。
3. 拒绝未知版本、未知记录类型和非法消息结构。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_codec.py`，期望所有内容块往返一致且错误输入被拒绝。

## T9：实现会话 ID、列表和删除

**文件：** `ycode/session/manager.py`、`tests/unit/session/test_manager.py`  
**依赖：** T7、T8  
**步骤：**

1. 初始化 `.ycode/sessions/` 并维护待创建和活动会话状态。
2. 从第一条用户消息生成时间戳加 32 字符短标题的安全 ID。
3. 通过文件名列出会话和选择最新会话，不扫描正文。
4. 实现精确 ID 删除并拒绝删除活动会话。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py -k "id or list or delete"`，期望中文标题、非法字符、冲突后缀和删除限制通过。

## T10：实现会话写前提交

**文件：** `ycode/session/manager.py`、`tests/unit/session/test_manager.py`  
**依赖：** T9  
**步骤：**

1. 为活动会话分配递增固定宽度回合 ID。
2. 按消息、可选检查点、提交边界顺序追加 UTF-8 JSONL。
3. 每轮完成后刷新文件，写入失败时不推进活动会话状态。
4. 验证文件只包含完整消息记录和提交边界。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py -k commit`，期望正常追加和模拟写入失败测试通过。

## T11：实现会话重放与文件修复

**文件：** `ycode/session/manager.py`、`tests/unit/session/test_manager.py`  
**依赖：** T10  
**步骤：**

1. 按字节偏移读取并跳过无法解析的行。
2. 用状态机校验回合顺序、消息数量和工具调用结果配对。
3. 只有合法提交边界才推进安全偏移。
4. 对结构损坏或不完整尾部截断原文件并返回警告。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py -k "load or repair or tool"`，期望坏行、半回合、错配工具和二次恢复测试通过。

## T12：实现上下文无副作用恢复

**文件：** `ycode/context/models.py`、`ycode/context/manager.py`、
`ycode/context/__init__.py`、`tests/unit/context/test_models.py`、
`tests/unit/context/test_manager.py`  
**依赖：** 无  
**步骤：**

1. 定义恢复候选和压缩结果模型。
2. 实现不修改当前状态的恢复预估和最多一次压缩。
3. 实现候选激活、失败计数重置和会话摘要标签分离。
4. 将手动压缩改为先返回候选，再由调用方显式激活。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/context/test_models.py tests/unit/context/test_manager.py`，期望恢复成功、压缩失败回滚和状态重置测试通过。

## T13：接入上下文检查点

**文件：** `ycode/session/manager.py`、`ycode/context/manager.py`、
`tests/unit/session/test_manager.py`、`tests/unit/context/test_manager.py`  
**依赖：** T11、T12  
**步骤：**

1. 在正常回合压缩和手动压缩时生成检查点记录。
2. 恢复时使用最新有效检查点的摘要与保留历史，再重放后续回合。
3. 实现独立检查点追加，失败时不激活上下文候选。
4. 验证同一长会话第二次恢复不重复压缩已覆盖历史。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_manager.py tests/unit/context/test_manager.py -k checkpoint`，期望全部通过。

## T14：增加会话恢复事件

**文件：** `ycode/agent/events.py`、`ycode/agent/__init__.py`、
`tests/unit/agent/test_contracts.py`  
**依赖：** T7  
**步骤：**

1. 定义恢复成功事件，包含会话 ID、消息数和安全警告摘要。
2. 把新事件加入统一事件联合并导出。
3. 验证事件字段不允许携带完整会话正文。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/agent/test_contracts.py`，期望全部通过。

## T15：接入正常回合写前提交与手动压缩

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T6、T10、T12、T13  
**步骤：**

1. 向 `ChatSession` 注入 `SessionManager`，正常回合先落盘再提交历史。
2. 存档失败时转换为安全错误事件并保持内存状态。
3. 调整 `/compact` 为检查点落盘成功后才激活摘要和历史。
4. 记录本次运行新增的成功回合及其会话 ID。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k "commit or compact or storage"`，期望写前顺序和失败回滚测试通过。

## T16：实现 `--continue` 与 `/resume`

**文件：** `ycode/session/chat.py`、`ycode/prompt/runtime.py`、
`tests/unit/session/test_chat.py`  
**依赖：** T1、T11、T12、T13、T14、T15  
**步骤：**

1. 实现恢复候选加载、上下文预检、可选检查点保存和原子激活顺序。
2. 解析严格的 `/resume <session-id>`，错误时保持当前会话。
3. 切换成功后重置 Agent 模式、临时权限、上下文失败状态和模式提醒。
4. 计算 24 小时时间跨度并只向下一普通请求注入一次提醒。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k "resume or restore or timespan"`，期望成功切换、失败不变和提醒一次性测试通过。

## T17：实现隔离记忆分析

**文件：** `ycode/memory/updater.py`、`ycode/memory/resources/__init__.py`、
`ycode/memory/resources/update.md`、`ycode/memory/__init__.py`、
`tests/unit/memory/test_updater.py`  
**依赖：** T2  
**步骤：**

1. 构建包含当前记忆与多会话新增对话边界的只读 transcript。
2. 发起无工具、关闭 Thinking 的隔离 Anthropic 请求。
3. 只接受单个 JSON 变更对象，空操作表示无需更新。
4. 拒绝工具事件、异常停止、额外文本和非法操作。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/memory/test_updater.py`，期望无需更新、合法操作和非法模型响应测试通过。

## T18：接入退出记忆整理

**文件：** `ycode/session/chat.py`、`tests/unit/session/test_chat.py`  
**依赖：** T4、T15、T17  
**步骤：**

1. 为正常退出提供幂等的记忆整理入口和结果报告。
2. 没有新增成功回合时直接跳过模型调用。
3. 退出时重新加载当前记忆，分析本次运行所有会话的新增回合并自动应用。
4. 使用 30 秒超时，超时、模型失败或写入失败转换为非致命报告。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/session/test_chat.py -k memory`，期望跨会话收集、无变化、成功应用和超时测试通过。

## T19：装配项目根、上下文、会话和记忆组件

**文件：** `ycode/cli.py`、`ycode/app.py`、`pyproject.toml`、
`tests/unit/test_cli.py`、`tests/unit/test_app.py`  
**依赖：** T5、T9、T16、T17、T18  
**步骤：**

1. 新增 `--continue` 并传入应用装配。
2. 使用 `config.project_root` 装配路径解析、环境、工具、安全、会话和记忆组件。
3. 把项目上下文快照写入 Prompt 运行时并传递启动警告。
4. Anthropic 启动时新建或恢复会话；OpenAI 保持旧路径并拒绝 `--continue`。
5. 把记忆 Prompt 纳入包资源。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/test_cli.py tests/unit/test_app.py`，期望项目根、组件边界、启动恢复和 OpenAI 兼容测试通过。

## T20：接入终端恢复和退出反馈

**文件：** `ycode/ui/terminal.py`、`tests/unit/ui/test_terminal.py`  
**依赖：** T14、T16、T18、T19  
**步骤：**

1. 展示启动警告和会话恢复结果，不输出完整正文。
2. 处理 `/resume` 成功事件和失败事件。
3. 在 `/exit`、`/quit`、EOF 和空闲 `Ctrl+C` 的正常退出路径执行记忆整理。
4. 展示更新、无需更新、超时和失败的简洁结果，再返回应用关闭流程。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/unit/ui/test_terminal.py`，期望所有退出路径和恢复反馈测试通过。

## T21：更新本地存储边界和用户文档

**文件：** `.gitignore`、`README.md`  
**依赖：** T19、T20  
**步骤：**

1. 忽略 `.ycode/sessions/` 和 `.ycode/memory/`。
2. 说明 `YCODE.md`、`@include` 深度和安全边界。
3. 说明会话文件、`--continue`、`/resume`、记忆索引和退出整理。
4. 明确当前只对 Anthropic 接入及进程崩溃持久性边界。

**验证：** `rg -n "YCODE.md|--continue|/resume|MEMORY.md|sessions/|memory/" README.md .gitignore`，期望所有公开行为均有说明。

## T22：补充跨模块集成测试

**文件：** `tests/integration/test_memory_system.py`、`tests/support/fake_provider.py`  
**依赖：** T19、T20、T21  
**步骤：**

1. 覆盖项目指令和记忆索引进入真实 Agent 请求。
2. 覆盖工具回合落盘、关闭、恢复和继续提交。
3. 覆盖恢复超限压缩检查点及第二次恢复复用。
4. 覆盖跨会话新增回合在退出时共同生成记忆变更。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/integration/test_memory_system.py`，期望全部通过且不访问真实 API。

## T23：补充真实终端端到端场景

**文件：** `tests/e2e/test_terminal_chat.py`  
**依赖：** T22  
**步骤：**

1. 在临时项目中启动 YCode，完成包含工具调用的新会话并正常退出。
2. 使用 `--continue` 恢复并验证历史、项目指令和记忆索引。
3. 创建第二会话，再用 `/resume <session-id>` 切回并继续对话。
4. 退出时模拟记忆更新，验证目标文件和终端提示。

**验证：** `.venv\Scripts\python.exe -m pytest -q tests/e2e/test_terminal_chat.py -k memory_system`，期望真实交互流程通过。

## T24：执行完整质量验证

**文件：** 全部改动文件  
**依赖：** T23  
**步骤：**

1. 执行格式、静态、编译和完整测试。
2. 修复所有与本功能相关的失败并重新运行。
3. 对照已批准 Checklist 逐项记录实际证据。

**验证：** 依次运行：

```powershell
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q ycode tests
.venv\Scripts\python.exe -m pytest -q
```

期望四条命令全部成功。

## 执行顺序

```text
T1 ───────────────┐
T2 → T3 → T4 ─────┼→ T5
T6 → T7 → T8 → T9 → T10 → T11 ─┐
T12 ───────────────────────→ T13 ├→ T15 → T16 ─┐
T7 → T14 ────────────────────────┘              │
T2 → T17 ───────────────────────→ T18 ──────────┤
T5 + T9 + T16 + T17 + T18 → T19 → T20 → T21 → T22 → T23 → T24
```
