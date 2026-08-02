from ycode.mcp.naming import map_tool_names, normalize_tool_name


def test_normalizes_camel_case_separators_and_ascii_only() -> None:
    assert normalize_tool_name("HTTPServer.run-tool.v2") == "http_server_run_tool_v2"
    assert normalize_tool_name("Café_工具") == "caf"


def test_maps_remote_names_to_prefixed_stable_public_names() -> None:
    mappings, issues = map_tool_names("filesystem", ["ReadFile", "list-files"])

    assert [(item.public_name, item.remote_name) for item in mappings] == [
        ("mcp_filesystem_list_files", "list-files"),
        ("mcp_filesystem_read_file", "ReadFile"),
    ]
    assert issues == ()


def test_rejects_empty_normalization_and_excludes_both_collision_sides() -> None:
    mappings, issues = map_tool_names("tools", ["工具", "read-file", "read_file"])

    assert mappings == ()
    assert [(issue.remote_name, issue.message) for issue in issues] == [
        ("read-file", "远端工具名称规范化冲突：read_file"),
        ("read_file", "远端工具名称规范化冲突：read_file"),
        ("工具", "远端工具名称无法规范化"),
    ]
