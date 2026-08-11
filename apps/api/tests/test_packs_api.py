from __future__ import annotations


async def test_list_packs_includes_healthcare_diagnostics(client):
    response = await client.get("/packs")
    assert response.status_code == 200
    slugs = {p["slug"] for p in response.json()}
    assert "healthcare-diagnostics" in slugs
