"""斜杠命令输入解析。"""

import re

from ycode.commands.contracts import CommandInvocation

_WHITESPACE = re.compile(r"\s")


class CommandParser:
    def parse(self, text: str) -> CommandInvocation | None:
        raw_text = text.strip()
        if not raw_text.startswith("/"):
            return None
        match = _WHITESPACE.search(raw_text)
        if match is None:
            command_text = raw_text
            arguments = ""
        else:
            command_text = raw_text[: match.start()]
            arguments = raw_text[match.end() :].lstrip()
        return CommandInvocation(raw_text, command_text[1:].lower(), arguments)
