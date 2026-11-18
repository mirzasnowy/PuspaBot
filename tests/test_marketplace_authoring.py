"""Marketplace authoring standards (G3, hermes pattern).

In-memory: run with ``pytest tests/test_marketplace_authoring.py
--noconftest -q``.
"""

import pytest

from puspa.marketplace import check_authoring, inject_config, resolve_config


def _clean_skill(**frontmatter_overrides):
    frontmatter = {
        "name": "Price Compare",
        "description": "Compare prices across marketplaces.",
        "author": "Puspa Team",
    }
    frontmatter.update(frontmatter_overrides)
    text = (
        "Author: Puspa Team\n\n"
        "## Description\n\nCompares prices.\n\n"
        "## When to use\n\nWhen the user asks for a price.\n\n"
        "## Tools\n\nUses search_files and read_file.\n\n"
        "## Configuration\n\nNeeds an API key.\n\n"
        "## Examples\n\nAsk for a price.\n"
    )
    return text, frontmatter


def _codes(violations):
    return [v.code for v in violations]


def test_clean_skill_passes():
    text, frontmatter = _clean_skill()
    assert check_authoring(text, frontmatter) == []


def test_description_over_60_chars_fails():
    text, frontmatter = _clean_skill(
        description="x" * 61,
    )
    violations = check_authoring(text, frontmatter)
    assert "description_too_long" in _codes(violations)


def test_description_exactly_60_chars_passes():
    text, frontmatter = _clean_skill(description="x" * 60)
    assert "description_too_long" not in _codes(check_authoring(text, frontmatter))


@pytest.mark.parametrize("util", ["grep", "cat", "ls", "sed", "awk", "curl"])
def test_shell_util_in_prose_fails(util):
    text, frontmatter = _clean_skill()
    text = text.replace("Uses search_files and read_file.",
                        f"Run {util} to find it.")
    assert "shell_util_reference" in _codes(check_authoring(text, frontmatter))


def test_shell_util_inside_a_code_fence_is_exempt():
    text, frontmatter = _clean_skill()
    text += "\n```sh\ngrep -r price .\n```\n"
    assert "shell_util_reference" not in _codes(check_authoring(text, frontmatter))


def test_native_tool_reference_passes():
    text, frontmatter = _clean_skill()
    assert "shell_util_reference" not in _codes(check_authoring(text, frontmatter))


def test_platform_gating_without_audit_flag_fails():
    text, frontmatter = _clean_skill()
    text += "\nOnly run this when sys.platform == 'linux'.\n"
    assert "platform_gating_unaudited" in _codes(
        check_authoring(text, frontmatter)
    )


def test_platform_gating_with_audit_flag_passes():
    text, frontmatter = _clean_skill(platform_gating_audited=True)
    text += "\nOnly run this when sys.platform == 'linux'.\n"
    assert "platform_gating_unaudited" not in _codes(
        check_authoring(text, frontmatter)
    )


def test_missing_author_fails():
    text, frontmatter = _clean_skill()
    del frontmatter["author"]
    assert "human_credit_missing" in _codes(check_authoring(text, frontmatter))


def test_author_without_credit_line_fails():
    text, frontmatter = _clean_skill()
    text = text.replace("Author: Puspa Team\n\n", "")
    assert "human_credit_missing" in _codes(check_authoring(text, frontmatter))


def test_sections_out_of_order_fail():
    text = (
        "Author: Puspa Team\n\n"
        "## Examples\n\nAsk for a price.\n\n"
        "## Description\n\nCompares prices.\n"
    )
    _, frontmatter = _clean_skill()
    assert "section_order" in _codes(check_authoring(text, frontmatter))


def test_missing_sections_are_fine():
    text = "Author: Puspa Team\n\n## Description\n\nCompares prices.\n"
    _, frontmatter = _clean_skill()
    assert "section_order" not in _codes(check_authoring(text, frontmatter))


def test_long_inline_code_block_fails():
    text, frontmatter = _clean_skill()
    lines = "\n".join(f"line {i}" for i in range(11))
    text += f"\n```python\n{lines}\n```\n"
    assert "inline_logic_too_long" in _codes(check_authoring(text, frontmatter))


def test_short_inline_code_block_passes():
    text, frontmatter = _clean_skill()
    text += "\n```python\nprint('hi')\n```\n"
    assert "inline_logic_too_long" not in _codes(
        check_authoring(text, frontmatter)
    )


def test_undeclared_config_placeholder_fails():
    text, frontmatter = _clean_skill()
    text += "\nCall the API with {{config.api_key}}.\n"
    assert "config_undeclared" in _codes(check_authoring(text, frontmatter))


def test_declared_config_placeholder_passes():
    text, frontmatter = _clean_skill(
        config=[{"name": "api_key", "prompt": "API key?", "required": True}]
    )
    text += "\nCall the API with {{config.api_key}}.\n"
    assert "config_undeclared" not in _codes(check_authoring(text, frontmatter))


def test_resolve_config_from_answers_and_defaults():
    declared = [
        {"name": "api_key", "prompt": "API key?", "required": True},
        {"name": "region", "prompt": "Region?", "default": "id"},
    ]
    resolved = resolve_config(declared, answers={"api_key": "secret"})
    assert resolved == {"api_key": "secret", "region": "id"}


def test_resolve_config_asks_via_asker():
    declared = [{"name": "api_key", "prompt": "API key?", "required": True}]
    resolved = resolve_config(
        declared, asker=lambda name, prompt, default: "asked-key"
    )
    assert resolved == {"api_key": "asked-key"}


def test_resolve_config_missing_required_raises():
    declared = [{"name": "api_key", "prompt": "API key?", "required": True}]
    with pytest.raises(ValueError, match="api_key"):
        resolve_config(declared)


def test_inject_config_substitutes_placeholders():
    text = "Call with {{config.api_key}} in {{config.region}}."
    assert inject_config(text, {"api_key": "k", "region": "id"}) == (
        "Call with k in id."
    )


def test_inject_config_leaves_unknown_placeholders():
    text = "Call with {{config.api_key}}."
    assert inject_config(text, {}) == text
