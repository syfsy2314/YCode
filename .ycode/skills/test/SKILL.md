---
name: test
description: 针对请求的变更运行聚焦的功能检查。
metadata:
  ycode-execution-mode: isolated
  ycode-context: none
  ycode-visible-tools: Read Grep Bash
  ycode-argument-hint: "[target]"
---
Identify the smallest relevant functional test set, run it, and diagnose failures that belong to the
requested change. Return the commands executed, their outcomes, and any remaining failure.
