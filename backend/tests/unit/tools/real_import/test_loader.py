import httpx
import pytest

from energia.tools.real_import.loader import import_payloads
from energia.tools.real_import.mapper import ImportPayloads


def _summary(created: int, *, rejected: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "created": created,
        "updated": 0,
        "unchanged": 0,
        "restored": 0,
        "rejected": rejected or [],
    }


@pytest.mark.asyncio
async def test_import_payloads_posts_every_entity_in_fk_order() -> None:
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request.url.path)
        return httpx.Response(200, json=_summary(len(request.read().split(b"{")) - 1))

    payloads = ImportPayloads(
        clientes=[{"numero_cliente": "c1"}],
        suministros=[{"numero_suministro": "s1"}],
        lotes=[{"codigo_lote": "REAL-2024-B1"}],
        consumos=[{"numero_suministro": "s1"}, {"numero_suministro": "s1"}],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        report = await import_payloads(client, payloads)

    assert posted == [
        "/api/v1/clientes/import",
        "/api/v1/suministros/import",
        "/api/v1/lotes/import",
        "/api/v1/consumos/import",
    ]
    assert report.por_entidad["consumos"].created == 2


@pytest.mark.asyncio
async def test_import_payloads_reports_rejections_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/suministros/import"):
            return httpx.Response(
                200, json=_summary(0, rejected=[{"reasons": ["cliente inexistente"]}])
            )
        return httpx.Response(200, json=_summary(1))

    payloads = ImportPayloads(
        clientes=[{"numero_cliente": "c1"}],
        suministros=[{"numero_suministro": "s1"}],
        lotes=[],
        consumos=[],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        report = await import_payloads(client, payloads)

    assert len(report.por_entidad["suministros"].rejected) == 1
    assert report.por_entidad["clientes"].created == 1
