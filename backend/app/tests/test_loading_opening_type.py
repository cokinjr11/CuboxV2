"""Fase 6A Final Product Decision: Rear Loading Only.

Todos los Load Space de Cubox se consideran rear-loading -LoadingOpeningType
= REAR es el unico comportamiento activo del producto para Container/Truck/
Trailer/Custom Space, no configurable por el usuario. Este archivo confirma
que TODO camino de construccion de un LoadSpaceSpec (catalogo o custom) y
TODO camino de persistencia (crear/reabrir un plan) resuelven siempre REAR,
y que el motor de secuencia de Fase 6A sigue funcionando igual (ver
test_sequence.py para la logica de bloqueo/warnings en si -este archivo NO
duplica esos tests, solo confirma el valor de loading_opening_type)."""

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.plan_store import PlanRepository
from app.models.containers import build_custom_load_space, get_container, list_containers, list_load_spaces
from app.models.schemas import LoadingOpeningType, LoadSpaceType
from app.main import app

_ITEM = {"code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 1, "item_type": "panel"}


@pytest.fixture
def client(tmp_path):
    repo = PlanRepository(tmp_path / "test_cubox.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_plan_repository, None)


# ---------------------------------------------------------------------------
# 1-3: Container / Truck / Trailer / Custom Space -> REAR
# ---------------------------------------------------------------------------


def test_catalog_containers_are_all_rear():
    for container in list_containers():
        assert container.loading_opening_type == LoadingOpeningType.REAR


def test_get_container_by_id_is_rear():
    assert get_container("40ft_standard").loading_opening_type == LoadingOpeningType.REAR


def test_list_load_spaces_are_all_rear():
    for space in list_load_spaces():
        assert space.loading_opening_type == LoadingOpeningType.REAR


def test_custom_truck_resolves_as_rear():
    truck = build_custom_load_space("My Truck", LoadSpaceType.TRUCK, 7200, 2450, 2650, 12000)
    assert truck.loading_opening_type == LoadingOpeningType.REAR


def test_custom_trailer_resolves_as_rear():
    trailer = build_custom_load_space("My Trailer", LoadSpaceType.TRAILER, 13000, 2480, 2700, 24000)
    assert trailer.loading_opening_type == LoadingOpeningType.REAR


def test_custom_generic_space_resolves_as_rear():
    custom = build_custom_load_space("My Custom Space", LoadSpaceType.CUSTOM, 6000, 2300, 2400, 15000)
    assert custom.loading_opening_type == LoadingOpeningType.REAR


# ---------------------------------------------------------------------------
# 4: /api/pack -- every path (catalog container_id, custom Truck/Trailer/
# Custom) resolves as REAR on the actual packed result.
# ---------------------------------------------------------------------------


def test_pack_with_catalog_container_id_result_is_rear(client):
    r = client.post("/api/pack", json={"items": [_ITEM], "container_id": "40ft_standard"})
    assert r.status_code == 200
    assert r.json()["best"]["container"]["loading_opening_type"] == "rear"


@pytest.mark.parametrize("load_space_type", ["truck", "trailer", "custom"])
def test_pack_with_custom_load_space_result_is_rear(client, load_space_type):
    custom = {
        "name": "Custom Space",
        "load_space_type": load_space_type,
        "length": 8000,
        "width": 2400,
        "height": 2600,
        "max_weight": 18000,
    }
    r = client.post("/api/pack", json={"items": [_ITEM], "custom_load_space": custom})
    assert r.status_code == 200
    assert r.json()["best"]["container"]["loading_opening_type"] == "rear"


# ---------------------------------------------------------------------------
# 5: persistence -- created plan and reopened plan (incl. after a fresh
# PlanRepository instance, simulating a backend restart) both stay REAR.
# ---------------------------------------------------------------------------


def test_created_plan_is_rear(client):
    r = client.post("/api/plans", json={"items": [_ITEM], "container_id": "40ft_standard", "name": "Plan A"})
    assert r.status_code == 200
    assert r.json()["best"]["container"]["loading_opening_type"] == "rear"


def test_reopened_plan_remains_rear(client):
    created = client.post(
        "/api/plans", json={"items": [_ITEM], "container_id": "40ft_standard", "name": "Plan A"}
    ).json()
    reopened = client.get(f"/api/plans/{created['plan_id']}")
    assert reopened.status_code == 200
    assert reopened.json()["result"]["container"]["loading_opening_type"] == "rear"


def test_reopened_custom_load_space_plan_remains_rear(client):
    custom = {
        "name": "Custom Truck",
        "load_space_type": "truck",
        "length": 7200,
        "width": 2450,
        "height": 2650,
        "max_weight": 12000,
    }
    created = client.post(
        "/api/plans", json={"items": [_ITEM], "custom_load_space": custom, "name": "Custom Plan"}
    ).json()
    reopened = client.get(f"/api/plans/{created['plan_id']}")
    assert reopened.status_code == 200
    assert reopened.json()["result"]["container"]["loading_opening_type"] == "rear"


def test_reopened_plan_remains_rear_after_a_fresh_repository_instance(client, tmp_path):
    """Simula un restart real del backend (nueva instancia de PlanRepository
    sobre el mismo archivo SQLite, igual criterio que el resto de los tests
    de restart de Fase 5D) -confirma que REAR sigue siendo REAR, no se
    reinfiere ni se pierde."""
    created = client.post(
        "/api/plans", json={"items": [_ITEM], "container_id": "40ft_standard", "name": "Plan A"}
    ).json()
    plan_id = created["plan_id"]

    fresh_repo = PlanRepository(tmp_path / "test_cubox.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: fresh_repo
    try:
        reopened = TestClient(app).get(f"/api/plans/{plan_id}")
    finally:
        app.dependency_overrides[routes.get_plan_repository] = lambda: PlanRepository(tmp_path / "test_cubox.db")

    assert reopened.status_code == 200
    assert reopened.json()["result"]["container"]["loading_opening_type"] == "rear"


# ---------------------------------------------------------------------------
# 6: legacy/unsupported values never leak into a NEW UI-generated plan --
# even if a caller tries to sneak a non-REAR value into custom_load_space,
# build_custom_load_space always overrides it to REAR (schema has no
# loading_opening_type input field on CustomLoadSpaceRequest to begin with).
# ---------------------------------------------------------------------------


def test_custom_load_space_request_has_no_opening_type_field():
    """CustomLoadSpaceRequest (lo que la Wizard/API realmente aceptan como
    input) no expone loading_opening_type -no hay forma de que un caller
    pida Side/Top/Multiple ni siquiera a nivel de API, sin necesidad de un
    selector en la UI que remover."""
    from app.models.schemas import CustomLoadSpaceRequest

    assert "loading_opening_type" not in CustomLoadSpaceRequest.model_fields
