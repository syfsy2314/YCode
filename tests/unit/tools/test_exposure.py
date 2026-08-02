from ycode.tools.exposure import ToolExposureSession


def test_exposure_activation_is_sorted_idempotent_and_clearable() -> None:
    exposure = ToolExposureSession(frozenset({"mcp_demo_alpha", "mcp_demo_beta"}))

    first = exposure.activate(["mcp_demo_beta", "missing", "mcp_demo_alpha", "mcp_demo_beta"])
    second = exposure.activate(["mcp_demo_alpha"])

    assert list(first) == ["mcp_demo_alpha", "mcp_demo_beta", "missing"]
    assert first == {
        "mcp_demo_alpha": "loaded",
        "mcp_demo_beta": "loaded",
        "missing": "not_found",
    }
    assert second == {"mcp_demo_alpha": "already_loaded"}
    assert exposure.exposed_names == frozenset({"mcp_demo_alpha", "mcp_demo_beta"})

    exposure.clear()

    assert exposure.discovered_tools == frozenset()


def test_exposure_instances_are_isolated() -> None:
    first = ToolExposureSession(frozenset({"mcp_demo_echo"}))
    second = ToolExposureSession(frozenset({"mcp_demo_echo"}))

    first.activate(["mcp_demo_echo"])

    assert first.discovered_tools == frozenset({"mcp_demo_echo"})
    assert second.discovered_tools == frozenset()
