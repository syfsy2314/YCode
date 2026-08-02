"""单个 Agent 任务内的延迟工具发现状态。"""

from dataclasses import dataclass, field


@dataclass(slots=True)
class ToolExposureSession:
    searchable_names: frozenset[str]
    _discovered_tools: set[str] = field(default_factory=set)

    def activate(self, names: list[str]) -> dict[str, str]:
        results: dict[str, str] = {}
        for name in sorted(set(names)):
            if name not in self.searchable_names:
                results[name] = "not_found"
            elif name in self._discovered_tools:
                results[name] = "already_loaded"
            else:
                self._discovered_tools.add(name)
                results[name] = "loaded"
        return results

    @property
    def discovered_tools(self) -> frozenset[str]:
        return frozenset(self._discovered_tools)

    @property
    def exposed_names(self) -> frozenset[str]:
        return self.discovered_tools

    def clear(self) -> None:
        self._discovered_tools.clear()
