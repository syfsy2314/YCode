from ycode.hooks.template import escape_reminder_text, render_hook_template


def test_render_values_and_missing_once() -> None:
    context = {"name": "demo", "flag": True, "items": [1, 2], "nested": "{{ name }}"}
    rendered = render_hook_template(
        "{{ name }}|{{ flag }}|{{ items }}|{{ missing }}|{{ nested }}", context
    )
    assert rendered == "demo|true|[1,2]||{{ name }}"


def test_escape_reminder_xml() -> None:
    assert escape_reminder_text("a < b & c") == "a &lt; b &amp; c"
