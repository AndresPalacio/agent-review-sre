from __future__ import annotations

from pathlib import Path

import pytest

from fin_graph.skills.loader import load_skill_catalog, read_skill_resource


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _skill(root: Path, skill_id: str = "safe-skill") -> Path:
    directory = root / skill_id
    _write(
        directory / "SKILL.md",
        "---\n"
        f"name: {skill_id}\n"
        "description: A safe test skill.\n"
        "category: test\n"
        "---\n"
        "# Safe skill\n",
    )
    return directory


@pytest.mark.parametrize(
    "document",
    [
        "# no frontmatter\n",
        "---\nname: broken\ncategory: test\n---\n# Missing description\n",
        "---\nname: wrong-name\ndescription: mismatch\ncategory: test\n---\n# Bad\n",
        "---\nname: bad id\ndescription: invalid\ncategory: test\n---\n# Bad\n",
    ],
)
def test_invalid_frontmatter_is_rejected(tmp_path: Path, document: str) -> None:
    directory = tmp_path / "broken"
    _write(directory / "SKILL.md", document)

    with pytest.raises(ValueError):
        load_skill_catalog(tmp_path)


def test_unicode_resource_and_mime_are_preserved(tmp_path: Path) -> None:
    directory = _skill(tmp_path)
    _write(directory / "references" / "核心概念" / "中枢.md", "中文内容\n")
    _write(directory / "scripts" / "example.py", "print('inert')\n")

    catalog = load_skill_catalog(tmp_path)
    markdown = read_skill_resource(
        "safe-skill", "references/核心概念/中枢.md", 100, root=tmp_path
    )
    python = read_skill_resource("safe-skill", "scripts/example.py", 100, root=tmp_path)

    assert catalog[0].resources == (
        "references/核心概念/中枢.md",
        "scripts/example.py",
    )
    assert markdown.content.splitlines() == ["中文内容"]
    assert markdown.mime_type == "text/markdown"
    assert python.mime_type == "text/x-python"


@pytest.mark.parametrize(
    "resource_path",
    [
        "../SKILL.md",
        "references/../../SKILL.md",
        "/etc/passwd",
        r"C:\Windows\win.ini",
        r"..\SKILL.md",
        "./SKILL.md",
        "",
    ],
)
def test_traversal_and_absolute_paths_are_rejected(
    tmp_path: Path, resource_path: str
) -> None:
    _skill(tmp_path)

    with pytest.raises(ValueError):
        read_skill_resource("safe-skill", resource_path, 1_000, root=tmp_path)


def test_unknown_skill_resource_and_size_limit_are_distinct(tmp_path: Path) -> None:
    directory = _skill(tmp_path)
    _write(directory / "large.md", "123456")

    with pytest.raises(KeyError, match="Unknown skill"):
        read_skill_resource("missing", "large.md", 10, root=tmp_path)
    with pytest.raises(FileNotFoundError):
        read_skill_resource("safe-skill", "missing.md", 10, root=tmp_path)
    with pytest.raises(ValueError, match="exceeds"):
        read_skill_resource("safe-skill", "large.md", 5, root=tmp_path)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    directory = _skill(tmp_path)
    outside = tmp_path / "outside.md"
    _write(outside, "secret")
    link = directory / "escape.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this Windows installation")

    with pytest.raises(ValueError, match=r"symlink|outside"):
        load_skill_catalog(tmp_path)


def test_reading_python_resource_never_executes_it(tmp_path: Path) -> None:
    directory = _skill(tmp_path)
    sentinel = tmp_path / "executed.txt"
    _write(
        directory / "payload.py",
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n",
    )

    resource = read_skill_resource("safe-skill", "payload.py", 10_000, root=tmp_path)

    assert "write_text" in resource.content
    assert not sentinel.exists()
