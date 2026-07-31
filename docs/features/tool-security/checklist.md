# YCode 工具权限安全系统 Checklist

> 每项通过运行代码或观察行为验证。测试使用临时项目和本机模拟服务，不读取用户真实
> 安全配置，不连接真实模型。

## 功能验收

- [x] 项目 `.ycode/security.yaml` 的发现、默认值和校验正确；非法模式、动作、工具、
  参数、匹配条件或重复 ID 会阻止启动并显示明确错误。（验证：配置参数化测试；AC4）

- [x] exact、Glob、真实路径和有序首次命中正确；决策优先级保持为硬规则、
  plan-only、会话规则、项目规则、权限模式。（验证：规则与冲突矩阵测试；AC2、AC5）

- [x] 工作区内的普通路径、符号链接和 Junction 正常使用；越界、损坏或不可解析链接
  及写入目标父目录越界均被硬拒绝。（验证：路径规范化和链接测试；AC2）

- [x] PowerShell 检查器不会执行待检查命令；解析失败和全部危险类别及其别名、大小写、
  空白或管道变体被拒绝，安全反例不误判。（验证：命令检查参数化测试；AC3、AC10）

- [x] strict、default、allow 对读取、写入、命令和 UNKNOWN 的行为符合 Spec；
  UNKNOWN 默认询问并串行调度。（验证：权限模式和 Scheduler 测试；AC6）

- [x] 六个内置工具生成正确的规则参数、审批摘要和会话键；本会话允许按安全关键参数
  复用，关键参数变化后重新询问，UNKNOWN 使用完整参数匹配。（验证：工具规范化与
  会话授权参数化测试；AC1、AC8）

- [x] 每个工具在执行前经过统一权限判断；ASK 严格暂停 Agent，用户选择前不检查下一
  调用、不启动工具、不请求模型。拒绝无副作用并以结构化结果回填，获准批次保持读取
  并发、写入屏障和原始结果顺序。（验证：AgentLoop 与 Scheduler 测试；AC1、AC7）

- [x] `/permission`、模式切换和 clear 正确工作且不调用模型、不进入历史、不修改
  配置；启动和输入区域显示权限模式，审批界面只有拒绝、本次允许和本会话允许。
  （验证：Session 与 UI 测试；AC7、AC8、AC9）

- [x] 权限模式只通过动态 tool_state 补充发送；稳定 System Prompt 不变，具体规则、
  黑名单和会话授权不进入模型请求或历史。（验证：FakeProvider 和 Anthropic 捕获
  请求对比；AC9）

- [x] 配置、规范化、规则、命令解析或审批异常均安全拒绝；审批时 Ctrl+C 不执行当前
  或后续工具，提示和错误不泄露密钥、规则或超限正文。（验证：失败注入、取消和敏感
  标记测试；AC10）

- [x] 现有工具、Agent、Session、提示词、Anthropic 工具循环和 OpenAI 纯聊天行为
  不回归。（验证：完整自动化回归；AC11）

## 质量检查

- [x] 格式检查通过。（验证：`.venv\Scripts\python.exe -m ruff format --check .`）
- [x] 静态检查通过。（验证：`.venv\Scripts\python.exe -m ruff check .`）
- [x] 编译检查通过。（验证：`.venv\Scripts\python.exe -m compileall -q ycode tests`）
- [x] 完整测试通过且没有遗留临时配置、命令文件或后台解析进程。
  （验证：`.venv\Scripts\python.exe -m pytest -q`，随后检查工作区和进程）

## Windows ConPTY 端到端

- [x] default 模式下依次验证拒绝、本次允许和本会话允许；会话允许命中时免询问，
  参数变化或 clear 后重新询问，模型最终回复正常。（验证：ConPTY + 模拟 Anthropic；
  AC7、AC8）

- [x] strict、default、allow 切换后显示和工具行为正确，模式命令不产生 API 请求；
  危险命令在 allow 模式下仍直接拒绝且不显示允许选项。（验证：ConPTY 模式与黑名单
  场景；AC2、AC3、AC6、AC9）

- [x] 多工具审批期间后续工具尚未执行，Ctrl+C 取消当前回合并恢复输入。
  （验证：ConPTY 批次副作用与终端恢复；AC10）
