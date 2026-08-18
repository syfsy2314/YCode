"""Worktree 状态的稳定用户输出。"""

from __future__ import annotations

import json

from ycode.worktrees.git import deletion_decision
from ycode.worktrees.manager import WorktreeCleanupReport, WorktreeDeletePreview
from ycode.worktrees.models import WorktreeRecord, WorktreeStatusSnapshot, WorktreeSummary


def status_payload(status: WorktreeStatusSnapshot | None) -> dict[str, object] | None:
    if status is None:
        return None
    return {
        "head": status.head,
        "staged": list(status.staged),
        "modified": list(status.modified),
        "untracked": list(status.untracked),
        "commits": [{"oid": item.oid, "subject": item.subject} for item in status.commits],
        "diff_stat": status.diff_stat,
        "upstream": status.upstream,
        "unpushed_commits": [
            {"oid": item.oid, "subject": item.subject} for item in status.unpushed_commits
        ],
        "error": status.error,
    }


def summary_payload(summary: WorktreeSummary) -> dict[str, object]:
    return {
        "name": summary.name,
        "path": summary.path,
        "branch": summary.branch,
        "base_head": summary.base_head,
        "current_head": summary.current_head,
        "disposition": summary.disposition.value,
        "status": status_payload(summary.status),
        "initialization_warnings": list(summary.initialization_warnings),
        "blocking_reasons": list(summary.blocking_reasons),
    }


def format_record(record: WorktreeRecord) -> str:
    blocking = (
        deletion_decision(record.lifecycle, record.last_status).reasons
        if record.last_status is not None
        else ("status_not_checked",)
    )
    payload = {
        "name": record.name,
        "path": record.path,
        "branch": record.branch,
        "base_head": record.base_head,
        "current_head": record.current_head,
        "lifecycle": record.lifecycle.value,
        "owner_session": record.owner.session_id,
        "last_activity_at": record.last_activity_at.isoformat(),
        "initialization_warnings": list(record.initialization_warnings),
        "status": status_payload(record.last_status),
        "deletion_blocking_reasons": list(blocking),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_records(records: tuple[WorktreeRecord, ...]) -> str:
    if not records:
        return "当前没有受管 Worktree。"
    lines = ["名称  状态  分支  Owner  最后活动  阻止原因  路径"]
    for item in records:
        blocking = (
            deletion_decision(item.lifecycle, item.last_status).reasons
            if item.last_status is not None
            else ("status_not_checked",)
        )
        owner = f"{item.owner.session_id}/{item.owner.task_id}"
        lines.append(
            f"{item.name}  {item.lifecycle.value}  {item.branch}  {owner}  "
            f"{item.last_activity_at.isoformat()}  {','.join(blocking) or '-'}  {item.path}"
        )
    return "\n".join(lines)


def format_delete_preview(preview: WorktreeDeletePreview) -> str:
    risks = preview.decision.reasons or ("none",)
    status = preview.status
    return (
        f"将强制删除 {preview.record.name}\n"
        f"路径：{preview.record.path}\n分支：{preview.record.branch}\n"
        f"风险：{', '.join(risks)}\n"
        f"staged：{', '.join(status.staged) or '-'}\n"
        f"modified：{', '.join(status.modified) or '-'}\n"
        f"untracked：{', '.join(status.untracked) or '-'}\n"
        f"新 commit：{len(status.commits)}；未推送：{len(status.unpushed_commits)}\n"
        f"diff stat：{status.diff_stat or '-'}"
    )


def format_cleanup(report: WorktreeCleanupReport) -> str:
    return (
        f"清理完成：删除 {len(report.deleted)} 个，标记中断 {len(report.interrupted)} 个，"
        f"告警 {len(report.warnings)} 个。"
    )
