"""从公开 HTTPS 来源安装项目 Skill。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.skills.installer import SkillInstaller, SkillInstallError
from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.contracts import ToolAccess, ToolContext, ToolDefinition, ToolExecutionResult
from ycode.tools.errors import ToolError


class InstallSkillArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(
        min_length=1,
        description=(
            "Public HTTPS URL of a direct Skill ZIP, a skills.sh Skill page, a GitHub "
            "tree directory, or a raw SKILL.md file."
        ),
    )


class InstallSkillTool:
    definition = ToolDefinition(
        name="install_skill",
        description=(
            "Install one project Skill from a supported public HTTPS source URL: a direct "
            "ZIP, a skills.sh Skill page, a GitHub tree directory, or a raw SKILL.md. Call "
            "this tool when the user asks to install a Skill and provides one of these URLs. "
            "Do not ask for "
            "confirmation in text; calling this tool automatically triggers the required "
            "user approval. Do not use it for a GitHub repository root, local files, private "
            "URLs, updates, or overwriting an existing Skill."
        ),
        access=ToolAccess.WRITE,
        arguments=PydanticToolArguments(InstallSkillArguments),
    )
    timeout_seconds = 300.0

    def __init__(self, installer: SkillInstaller) -> None:
        self._installer = installer

    async def execute(
        self,
        arguments: InstallSkillArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        try:
            entry = await self._installer.install(arguments.source_url)
        except SkillInstallError as error:
            raise ToolError(error.code, str(error)) from error
        if entry.snapshot is not None:
            return ToolExecutionResult(
                f'Skill "{entry.snapshot.name}" installed.',
                metadata={"skill": entry.snapshot.name, "status": "installed"},
            )
        reasons = "; ".join(problem.message for problem in entry.problems)
        return ToolExecutionResult(
            f'Skill "{entry.directory_name}" installed but unavailable: {reasons}',
            metadata={"skill": entry.directory_name, "status": "unavailable"},
        )


__all__ = ["InstallSkillArguments", "InstallSkillTool"]
