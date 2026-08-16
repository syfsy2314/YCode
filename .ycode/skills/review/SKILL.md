---
name: review
description: 审查当前实现的正确性和潜在回归。
metadata:
  ycode-execution-mode: isolated
  ycode-context: recent
  ycode-recent-turns: "5"
  ycode-visible-tools: Read Grep Bash
  ycode-argument-hint: "[focus]"
---
Review the requested implementation and relevant surrounding code. Prioritize concrete correctness
issues and regressions. Return findings ordered by severity with file references; say explicitly when
no actionable issue is found.
