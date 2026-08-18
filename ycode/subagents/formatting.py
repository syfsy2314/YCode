"""子 Agent 工具、命令与通知的统一格式化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ycode.prompt.models import SupplementKind, SystemSupplement
from ycode.subagents.models import SubagentTaskView
from ycode.worktrees.formatting import summary_payload


def task_payload(task: SubagentTaskView) -> dict[str, object]:
    usage = task.usage
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "creation_mode": task.creation_mode.value,
        "role": task.role,
        "result": task.result,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
        },
        "started_at": task.started_at.isoformat(),
        "finished_at": task.finished_at.isoformat() if task.finished_at is not None else None,
        "error": (
            {"code": task.error.code, "message": task.error.message}
            if task.error is not None
            else None
        ),
        "worktree": summary_payload(task.worktree) if task.worktree is not None else None,
    }


def format_tool_result(task: SubagentTaskView) -> str:
    return json.dumps(task_payload(task), ensure_ascii=False, separators=(",", ":"))


def format_task_detail(task: SubagentTaskView) -> str:
    payload = task_payload(task)
    payload["task"] = task.task
    payload["run_mode"] = task.run_mode.value
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_task_list(
    tasks: tuple[SubagentTaskView, ...],
    *,
    now: datetime | None = None,
) -> str:
    if not tasks:
        return "当前会话没有子 Agent 任务。"
    current = now or datetime.now(UTC)
    lines = ["ID           状态           创建方式  角色       时长      Token  Worktree"]
    for task in tasks:
        end = task.finished_at or current
        elapsed = max(0.0, (end - task.started_at).total_seconds())
        usage = task.usage
        total = (
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_creation_input_tokens
            + usage.cache_read_input_tokens
        )
        lines.append(
            f"{task.task_id[:12]:<12} {task.status.value:<14} "
            f"{task.creation_mode.value:<9} {(task.role or '-'):<10} "
            f"{elapsed:>7.1f}s {total:>8} "
            f"{task.worktree.disposition.value if task.worktree is not None else '-'}"
        )
    return "\n".join(lines)


def format_runtime_notification(task: SubagentTaskView) -> SystemSupplement:
    content = "异步子 Agent 任务已进入终态：\n" + format_tool_result(task)
    return SystemSupplement(SupplementKind.SYSTEM_REMINDER, content)
