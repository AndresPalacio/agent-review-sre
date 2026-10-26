from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_the_unmodified_apache_template() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Version 2.0, January 2004" in text
    assert "Copyright [yyyy] [name of copyright owner]" in text


def test_notice_credits_this_project_and_each_upstream_holder() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")

    assert "Copyright 2026 The fin-graph authors" in notice
    assert "Copyright 2026 Tracer Cloud" in notice
    assert "Copyright 2025 Abhishek Chauhan" in notice
    assert "Copyright (c) 2024 Virat Singh" in notice
    assert "Permission is hereby granted, free of charge" in notice
