from pathlib import Path

import httpx

from sre_graph.server.app import create_app


async def test_spa_fallback_protects_api_and_missing_assets(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>console</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("export {}")
    app = create_app(ui_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for route in ("/", "/investigations/abc", "/chat", "/remediations", "/schedules"):
            assert "console" in (await client.get(route)).text
        assert (await client.get("/assets/app.js")).status_code == 200
        for route in (
            "/assets/missing.js",
            "/v1/unknown",
            "/unknown",
            "/health/nope",
            "/assets/%2e%2e/%2e%2e/private.txt",
            "/ready/nope",
        ):
            assert (await client.get(route)).status_code == 404
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200


async def test_missing_build_and_documentation_are_preserved(tmp_path: Path) -> None:
    app = create_app(ui_dir=tmp_path / "missing")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 404
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200
