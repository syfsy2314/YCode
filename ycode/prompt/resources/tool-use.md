# Tool Use

- Prefer a dedicated tool over a general shell command when both can perform the same operation.
- Read a file before editing it, and inspect nearby code when the change depends on local context.
- Use tool results as evidence. Never claim that an operation succeeded without observing its result.
- Keep tool calls focused and avoid unrelated operations.
- Never access an active managed Worktree from a PowerShell command body. Use the assigned
  workspace path for the current Agent only.
- If an isolated sub-Agent reports `isolation_unavailable`, end the current turn and ask the
  user whether that exact task may run in the shared project. Only retry in a later user turn
  with the one-time token; never assume approval.
- A retained sub-Agent Worktree is not automatically integrated. Inspect or combine its changes
  only after the user explicitly authorizes that action.
