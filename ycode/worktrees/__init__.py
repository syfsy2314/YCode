"""子 Agent Git Worktree 隔离。"""

from ycode.worktrees.access import WorktreeAccessGuard
from ycode.worktrees.git import (
    GitCommandResult,
    GitWorktreeClient,
    GitWorktreeEntry,
    LinkedWorktreeHead,
    LinkedWorktreeHeadReader,
    ParsedGitStatus,
    WorktreeGitError,
    deletion_decision,
    parse_commit_records,
    parse_status_porcelain_v2,
    parse_worktree_porcelain,
)
from ycode.worktrees.initialize import (
    InitializationWarning,
    InitializedDirectoryLink,
    WorktreeInitializationError,
    WorktreeInitializationResult,
    WorktreeInitializer,
    git_config_environment,
)
from ycode.worktrees.manager import (
    WorktreeCleanupReport,
    WorktreeDeletePreview,
    WorktreeManager,
    WorktreeManagerError,
)
from ycode.worktrees.models import (
    WorktreeCommit,
    WorktreeDeleteDecision,
    WorktreeDisposition,
    WorktreeLease,
    WorktreeLifecycle,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStatusSnapshot,
    WorktreeSummary,
)
from ycode.worktrees.naming import (
    WorktreeName,
    managed_worktree_name,
    worktree_name_from_branch,
)
from ycode.worktrees.store import RECORD_VERSION, WorktreeStore, WorktreeStoreError

__all__ = [
    "WorktreeCommit",
    "WorktreeAccessGuard",
    "InitializationWarning",
    "InitializedDirectoryLink",
    "WorktreeDeleteDecision",
    "WorktreeDisposition",
    "WorktreeLease",
    "WorktreeLifecycle",
    "WorktreeInitializationError",
    "WorktreeInitializationResult",
    "WorktreeInitializer",
    "WorktreeCleanupReport",
    "WorktreeDeletePreview",
    "WorktreeManager",
    "WorktreeManagerError",
    "GitCommandResult",
    "GitWorktreeClient",
    "GitWorktreeEntry",
    "LinkedWorktreeHead",
    "LinkedWorktreeHeadReader",
    "RECORD_VERSION",
    "ParsedGitStatus",
    "WorktreeName",
    "WorktreeOwner",
    "WorktreeRecord",
    "WorktreeStatusSnapshot",
    "WorktreeSummary",
    "WorktreeGitError",
    "WorktreeStore",
    "WorktreeStoreError",
    "deletion_decision",
    "git_config_environment",
    "managed_worktree_name",
    "parse_commit_records",
    "parse_status_porcelain_v2",
    "parse_worktree_porcelain",
    "worktree_name_from_branch",
]
