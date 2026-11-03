from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fin_graph.acceptance import REQUIRED_CHECKS, run_acceptance, write_acceptance_report


async def test_sqlite_acceptance_covers_complete_public_financial_surface(tmp_path: Path) -> None:
    secret = "acceptance-secret-that-must-never-be-reported"
    report = await run_acceptance(
        backend="sqlite",
        data_dir=tmp_path / "runtime",
        acceptance_secret=secret,
    )

    assert report["passed"] is True
    assert report["backend"] == "sqlite"
    assert report["capabilities"] == {
        "backend": "sqlite",
        "distributed_locks": False,
        "multi_worker": False,
    }
    assert set(REQUIRED_CHECKS) <= {item["name"] for item in report["checks"] if item["passed"]}
    assert len(report["public_digest"]) == 64
    assert secret not in json.dumps(report, sort_keys=True)


@pytest.mark.postgres
async def test_postgres_acceptance_has_same_public_digest_and_distributed_capabilities(
    tmp_path: Path,
) -> None:
    database_url = os.getenv("FIN_GRAPH_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("FIN_GRAPH_TEST_DATABASE_URL is not set")
    sqlite = await run_acceptance(backend="sqlite", data_dir=tmp_path / "sqlite")
    postgres = await run_acceptance(backend="postgres", database_url=database_url)

    assert postgres["passed"] is True
    assert postgres["capabilities"] == {
        "backend": "postgres",
        "distributed_locks": True,
        "multi_worker": True,
    }
    assert postgres["public_digest"] == sqlite["public_digest"]


async def test_acceptance_writer_emits_machine_and_human_reports(tmp_path: Path) -> None:
    report = await run_acceptance(backend="sqlite", data_dir=tmp_path / "runtime")
    json_path, markdown_path = write_acceptance_report(report, tmp_path / "evidence")

    assert (
        json.loads(json_path.read_text(encoding="utf-8"))["public_digest"]
        == report["public_digest"]
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# AI Hedge Fund runtime acceptance" in markdown
    assert "SQLite" in markdown
    assert report["public_digest"] in markdown
