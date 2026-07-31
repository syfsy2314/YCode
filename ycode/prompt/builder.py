"""内置提示词资源加载与稳定拼装。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources

from ycode.prompt.models import PromptBundle, PromptSection


@dataclass(frozen=True, slots=True)
class PromptResource:
    id: str
    priority: int
    filename: str

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("提示词资源文件名不能为空")


BUILTIN_RESOURCES = (
    PromptResource("identity", 100, "identity.md"),
    PromptResource("behavior", 200, "behavior.md"),
    PromptResource("tool-use", 300, "tool-use.md"),
    PromptResource("coding", 400, "coding.md"),
    PromptResource("safety", 500, "safety.md"),
    PromptResource("output", 600, "output.md"),
)

ResourceLoader = Callable[[str], str]


def _load_builtin_resource(filename: str) -> str:
    try:
        return (
            resources.files("ycode.prompt.resources").joinpath(filename).read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError) as error:
        raise RuntimeError(f"无法加载内置提示词资源：{filename}") from error


class PromptBuilder:
    def __init__(
        self,
        prompt_resources: Sequence[PromptResource] = BUILTIN_RESOURCES,
        *,
        loader: ResourceLoader | None = None,
    ) -> None:
        self._resources = tuple(prompt_resources)
        if not self._resources:
            raise ValueError("提示词资源列表不能为空")
        if any(not isinstance(item, PromptResource) for item in self._resources):
            raise TypeError("提示词资源列表只能包含 PromptResource")
        self._loader = loader or _load_builtin_resource

    def build(self) -> PromptBundle:
        sections = []
        for item in self._resources:
            try:
                content = self._loader(item.filename)
            except RuntimeError:
                raise
            except Exception as error:
                raise RuntimeError(f"无法加载内置提示词资源：{item.filename}") from error
            sections.append(
                PromptSection(
                    id=item.id,
                    priority=item.priority,
                    content=content.strip(),
                )
            )
        return PromptBundle(tuple(sections))


def build_builtin_prompt() -> PromptBundle:
    return PromptBuilder().build()
