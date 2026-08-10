---
name: commit
description: Inspect current changes and create a focused Git commit.
allowed-tools: Read Grep Bash(git:*)
---
Inspect the current repository changes and understand their purpose. Run the relevant focused checks,
stage only files belonging to the requested change, and create one concise commit. Report the commit
identifier and any files intentionally left uncommitted.
