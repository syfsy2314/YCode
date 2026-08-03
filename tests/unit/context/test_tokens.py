from pydantic import BaseModel, ConfigDict, Field

from ycode.context import TokenEstimator
from ycode.core import AgentModelRequest, ChatMessage
from ycode.tools import PydanticToolArguments, ToolAccess, ToolDefinition


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="文件路径")


READ_TOOL = ToolDefinition(
    name="read_file",
    description="读取工作区文件",
    access=ToolAccess.READ,
    arguments=PydanticToolArguments(ReadArguments),
)


def request(**overrides: object) -> AgentModelRequest:
    values: dict[str, object] = {"messages": (ChatMessage.user_text("hello"),)}
    values.update(overrides)
    return AgentModelRequest(**values)  # type: ignore[arg-type]


def test_estimate_covers_all_request_channels() -> None:
    estimator = TokenEstimator()
    base = estimator.estimate(request()).total_tokens

    assert estimator.estimate(request(system_prompt=("identity" * 20,))).total_tokens > base
    assert estimator.estimate(request(supplements=("environment" * 20,))).total_tokens > base
    assert estimator.estimate(request(tools=(READ_TOOL,))).total_tokens > base
    assert (
        estimator.estimate(request(messages=(ChatMessage.user_text("hello" * 30),))).total_tokens
        > base
    )


def test_estimate_is_deterministic() -> None:
    estimator = TokenEstimator()
    model_request = request(
        system_prompt=("identity",),
        supplements=("environment",),
        tools=(READ_TOOL,),
    )

    assert estimator.estimate(model_request) == estimator.estimate(model_request)


def test_actual_usage_only_calibrates_upward() -> None:
    estimator = TokenEstimator()
    model_request = request()
    initial = estimator.estimate(model_request)

    estimator.observe(initial.local_tokens, initial.local_tokens // 2 or 1)
    assert estimator.estimate(model_request).total_tokens == initial.total_tokens

    estimator.observe(initial.local_tokens, initial.local_tokens * 2)
    calibrated = estimator.estimate(model_request)
    assert calibrated.calibrated_tokens == initial.local_tokens * 2
    assert calibrated.total_tokens >= calibrated.local_tokens
