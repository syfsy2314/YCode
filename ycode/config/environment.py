"""项目环境变量读取、解析与敏感值处理。"""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import SecretStr

from ycode.core.messages import FrozenJson, freeze_json
from ycode.errors import ConfigError

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_project_dotenv(project_root: str | Path) -> Mapping[str, str]:
    """读取项目根目录的 `.env`，不会写入进程环境。"""

    dotenv_path = Path(project_root).expanduser().resolve() / ".env"
    if not dotenv_path.is_file():
        return MappingProxyType({})

    try:
        with dotenv_path.open(encoding="utf-8") as stream:
            if any(binding.error for binding in parse_stream(stream)):
                raise ConfigError(f"无法解析环境文件：{dotenv_path}")
        values = dotenv_values(dotenv_path, interpolate=False, encoding="utf-8")
    except ConfigError:
        raise
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"无法读取环境文件：{dotenv_path}") from error

    return MappingProxyType(
        {name: value for name, value in values.items() if name is not None and value is not None}
    )


class EnvironmentResolver:
    """以系统环境优先级解析项目所需变量。"""

    def __init__(
        self,
        dotenv_values: Mapping[str, str] | None = None,
        system_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._dotenv_values = MappingProxyType(dict(dotenv_values or {}))
        self._system_environment = MappingProxyType(
            dict(os.environ) if system_environment is None else dict(system_environment)
        )

    def resolve(self, variable_name: str) -> str | None:
        """返回系统环境或项目 `.env` 中的变量值。"""

        return self._system_environment.get(variable_name, self._dotenv_values.get(variable_name))

    def interpolate(self, value: str) -> SecretStr:
        """展开字符串中所有 `${VARIABLE}` 引用。"""

        missing_variables: list[str] = []

        def replace(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            resolved = self.resolve(variable_name)
            if resolved is None:
                missing_variables.append(variable_name)
                return ""
            return resolved

        resolved_value = _ENV_REFERENCE.sub(replace, value)
        if missing_variables:
            names = ", ".join(dict.fromkeys(missing_variables))
            raise ConfigError(f"引用的环境变量不存在：{names}")
        return SecretStr(resolved_value)


class SecretRedactor:
    """集中保存已知秘密，并在边界输出前统一替换。"""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, value: SecretStr | str) -> None:
        """登记非空敏感值。"""

        secret = value.get_secret_value() if isinstance(value, SecretStr) else value
        if secret:
            self._secrets.add(secret)

    def redact_text(self, value: str) -> str:
        """以确定性顺序替换文本中的已登记敏感值。"""

        redacted = value
        for secret in sorted(self._secrets, key=lambda item: (-len(item), item)):
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    def redact_json(self, value: FrozenJson) -> FrozenJson:
        """递归替换冻结 JSON 中的字符串标量。"""

        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return freeze_json({key: self.redact_json(item) for key, item in value.items()})
        if isinstance(value, tuple):
            return tuple(self.redact_json(item) for item in value)
        return value
