"""供应商无关的 Agent 循环契约。"""

from ycode.agent.contracts import (
    AgentMode,
    AgentTermination,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
    ConversationRunner,
)
from ycode.agent.events import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentLimitReachedEvent,
    AgentTextDelta,
    AgentThinkingDelta,
    FinalResponseEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    ToolApprovalRequested,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserMessageEvent,
)
from ycode.agent.loop import AgentLoop
from ycode.agent.plain import PlainChatRunner

__all__ = [
    "AgentCancelledEvent",
    "AgentErrorEvent",
    "AgentEvent",
    "AgentLimitReachedEvent",
    "AgentLoop",
    "AgentMode",
    "AgentTermination",
    "AgentTextDelta",
    "AgentThinkingDelta",
    "AgentTurn",
    "AgentTurnResult",
    "AgentTurnStream",
    "ConversationRunner",
    "FinalResponseEvent",
    "ModeChangedEvent",
    "PermissionGrantsClearedEvent",
    "PermissionModeChangedEvent",
    "PlainChatRunner",
    "ToolExecutionCancelled",
    "ToolApprovalRequested",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
    "UserMessageEvent",
]
