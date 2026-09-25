"""Fase 5D: Recent Plans & Persistence.

Cada test usa un archivo SQLite TEMPORAL (`tmp_path`, ver fixture `client`
mas abajo) -nunca la base de datos real del usuario (seccion 50 del
pedido). `app.dependency_overrides[routes.get_plan_repository]` es el punto
de inyeccion: los 5 endpoints de /api/plans reciben el repositorio de test
en vez del singleton de proceso."""

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.plan_store import PlanRepository
from app.main import app

_ITEM = {"code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 1, "item_type": "panel"}


@pytest.fixture
def client(tmp_path):
    repo = PlanRepository(tmp_path / "test_cubox.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_plan_repository, None)


def _create_plan(client, **overrides):
    body = {"items": [_ITEM], "container_id": "40ft_standard", "name": "Test Plan"}
    body.update(overrides)
    r = client.post("/api/plans", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD basico
# ---------------------------------------------------------------------------


def test_create_plan_persists_and_appears_in_list(client):
    created = _create_plan(client)
    plans = client.get("/api/plans").json()
    assert len(plans) == 1
    assert plans[0]["plan_id"] == created["plan_id"]
    assert plans[0]["name"] == "Test Plan"


def test_create_plan_id_is_a_real_uuid_hex(client):
    created = _create_plan(client)
    import uuid

    uuid.UUID(created["plan_id"])  # no lanza si es un UUID valido


def test_two_creates_never_produce_the_same_plan_id(client):
    a = _create_plan(client, name="A")
    b = _create_plan(client, name="B")
    assert a["plan_id"] != b["plan_id"]


def test_fetch_plan_returns_full_state(client):
    created = _create_plan(client)
    r = client.get(f"/api/plans/{created['plan_id']}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["plan_id"] == created["plan_id"]
    assert detail["name"] == "Test Plan"
    assert len(detail["result"]["placed"]) == 1
    assert detail["container_id"] == "40ft_standard"
    assert detail["custom_load_space"] is None


def test_fetch_unknown_plan_returns_404(client):
    r = client.get("/api/plans/does-not-exist")
    assert r.status_code == 404


def test_default_plan_name_used_when_not_provided(client):
    r = client.post("/api/plans", json={"items": [_ITEM], "container_id": "40ft_standard"})
    assert r.status_code == 200
    assert "Load Plan" in r.json()["name"]


def test_delete_plan_removes_it_from_list_and_404s_after(client):
    created = _create_plan(client)
    r = client.delete(f"/api/plans/{created['plan_id']}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "plan_id": created["plan_id"]}

    assert client.get("/api/plans").json() == []
    assert client.get(f"/api/plans/{created['plan_id']}").status_code == 404


def test_delete_unknown_plan_returns_404(client):
    assert client.delete("/api/plans/does-not-exist").status_code == 404


def test_delete_does_not_affect_other_plans(client):
    """Seccion 49 del pedido: borrar un plan no afecta a los demas."""
    a = _create_plan(client, name="Keep me")
    b = _create_plan(client, name="Delete me")
    client.delete(f"/api/plans/{b['plan_id']}")

    plans = client.get("/api/plans").json()
    assert len(plans) == 1
    assert plans[0]["plan_id"] == a["plan_id"]
    assert client.get(f"/api/plans/{a['plan_id']}").status_code == 200


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


def test_rename_updates_name_and_updated_at(client):
    created = _create_plan(client)
    before = client.get(f"/api/plans/{created['plan_id']}").json()["updated_at"]

    r = client.patch(f"/api/plans/{created['plan_id']}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    after = client.get(f"/api/plans/{created['plan_id']}").json()
    assert after["name"] == "Renamed"
    assert after["updated_at"] >= before  # ISO8601 UTC: comparacion lexicografica == cronologica


def test_rename_unknown_plan_returns_404(client):
    r = client.patch("/api/plans/does-not-exist", json={"name": "X"})
    assert r.status_code == 404


def test_rename_appears_in_recent_plans_card(client):
    created = _create_plan(client)
    client.patch(f"/api/plans/{created['plan_id']}", json={"name": "Miami Shipment"})
    plans = client.get("/api/plans").json()
    assert plans[0]["name"] == "Miami Shipment"


# ---------------------------------------------------------------------------
# Recent Plans ordering (seccion 10 del pedido: updated_at DESC)
# ---------------------------------------------------------------------------


def test_recent_plans_ordered_by_updated_at_desc(client):
    a = _create_plan(client, name="Oldest")
    b = _create_plan(client, name="Middle")
    c = _create_plan(client, name="Newest touch")

    # tocar "a" de nuevo (autosave) lo trae al frente sin importar el orden de creacion
    piece_id = client.get(f"/api/plans/{a['plan_id']}").json()["result"]["placed"][0]["id"]
    client.post("/api/apply-move", json={"piece_id": piece_id, "x": 50, "y": 50, "z": 0, "dx": 800, "dy": 80, "dz": 1200})
    client.put(f"/api/plans/{a['plan_id']}")

    names = [p["name"] for p in client.get("/api/plans").json()]
    assert names[0] == "Oldest"  # el ultimo tocado queda primero


def test_recent_plans_respects_limit(client):
    for i in range(5):
        _create_plan(client, name=f"Plan {i}")
    plans = client.get("/api/plans?limit=2").json()
    assert len(plans) == 2


# ---------------------------------------------------------------------------
# Autosave (PUT) y "no repack on open" (secciones 13/14/24/25 del pedido)
# ---------------------------------------------------------------------------


def test_manual_edits_are_saved_and_restored_without_rerunning_the_optimizer(client):
    """Escenario central del pedido (secciones 13/14/43): mover una pieza a
    mano, guardar, reabrir -debe verse EXACTAMENTE en la posicion movida a
    mano, nunca la que el optimizador hubiera elegido de nuevo."""
    created = _create_plan(client)
    plan_id = created["plan_id"]
    piece_id = created["best"]["placed"][0]["id"]

    move = client.post("/api/apply-move", json={"piece_id": piece_id, "x": 123.0, "y": 45.0, "z": 0, "dx": 800, "dy": 80, "dz": 1200})
    assert move.status_code == 200

    save = client.put(f"/api/plans/{plan_id}")
    assert save.status_code == 200

    reopened = client.get(f"/api/plans/{plan_id}").json()
    piece = next(p for p in reopened["result"]["placed"] if p["id"] == piece_id)
    assert piece["x"] == 123.0
    assert piece["y"] == 45.0


def test_locked_piece_survives_save_and_reopen(client):
    created = _create_plan(client)
    plan_id = created["plan_id"]
    piece_id = created["best"]["placed"][0]["id"]

    client.post("/api/lock-piece", json={"piece_id": piece_id})
    client.put(f"/api/plans/{plan_id}")

    reopened = client.get(f"/api/plans/{plan_id}").json()
    piece = next(p for p in reopened["result"]["placed"] if p["id"] == piece_id)
    assert piece["locked"] is True


def test_removed_piece_stays_unloaded_after_save_and_reopen(client):
    """Seccion 16 del pedido: 1 loaded + 1 unloaded se restaura tal cual,
    NO 2 loaded porque se reoptimizo."""
    created = _create_plan(client, items=[_ITEM, {**_ITEM, "code": "W2"}])
    plan_id = created["plan_id"]
    piece_id = created["best"]["placed"][0]["id"]

    client.post("/api/remove-piece", json={"piece_id": piece_id})
    client.put(f"/api/plans/{plan_id}")

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert len(reopened["result"]["unloaded"]) == 1
    assert reopened["result"]["unloaded"][0]["reason_code"] == "MANUAL_REMOVE"
    assert len(reopened["result"]["placed"]) == 1  # el que quedo, no 2


def test_opening_a_plan_does_not_change_piece_positions_across_multiple_opens(client):
    """Abrir el mismo plan 2 veces seguidas debe dar EXACTAMENTE la misma
    geometria -si algo estuviera reoptimizando, las posiciones podrian
    variar entre llamadas."""
    created = _create_plan(client)
    plan_id = created["plan_id"]
    first = client.get(f"/api/plans/{plan_id}").json()["result"]["placed"]
    second = client.get(f"/api/plans/{plan_id}").json()["result"]["placed"]
    assert first == second


def test_save_rejects_when_active_session_is_a_different_plan(client):
    """Seccion 35 del pedido: PUT no debe poder pisar el plan equivocado si
    la sesion activa no es realmente ese plan."""
    a = _create_plan(client, name="A")
    _create_plan(client, name="B")  # esto vuelve a B el plan activo de _current_state

    r = client.put(f"/api/plans/{a['plan_id']}")
    assert r.status_code == 409


def test_report_steps_works_immediately_after_reopening_a_plan_no_optimize_needed(client):
    """Fase 6B, seccion 23/36: reabrir un Recent Plan debe dejar la Guia
    lista para usar de inmediato -sin necesidad de tocar Optimize- porque
    /report/steps se deriva de la geometria ya restaurada, no de un cache
    de la sesion que creo el plan."""
    created = _create_plan(client)
    plan_id = created["plan_id"]

    client.get(f"/api/plans/{plan_id}")  # simula "Home -> reabrir", reemplaza _current_state por completo

    r = client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"})
    assert r.status_code == 200
    flat = [pid for step in r.json()["steps"] for pid in step]
    assert sorted(flat) == sorted(p["id"] for p in created["best"]["placed"])

    unload_r = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    assert unload_r.status_code == 200
    assert unload_r.json()["unload_step_info"] is not None


@pytest.mark.parametrize("mode", ["best_space", "keep_groups", "keep_systems", "prioritize_delivery"])
def test_all_four_optimization_modes_save_and_reopen_exactly(client, mode):
    """Fase 6A.1, seccion 8 del pedido: select mode -> Optimize -> Saved ->
    Home -> reopen -> el modo exacto sigue seleccionado y la geometria
    restaurada es identica -sin volver a correr el optimizador (mismas
    posiciones x/y/z, no solo el mismo conteo de piezas)."""
    created = client.post(
        "/api/plans",
        json={"items": [_ITEM], "container_id": "40ft_standard", "name": "Mode Test", "optimization_mode": mode},
    ).json()
    plan_id = created["plan_id"]

    save = client.put(f"/api/plans/{plan_id}")
    assert save.status_code == 200, save.text

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert reopened["optimization_mode"] == mode
    assert [p["id"] for p in reopened["result"]["placed"]] == [p["id"] for p in created["best"]["placed"]]
    for created_piece, reopened_piece in zip(created["best"]["placed"], reopened["result"]["placed"]):
        assert (created_piece["x"], created_piece["y"], created_piece["z"]) == (
            reopened_piece["x"], reopened_piece["y"], reopened_piece["z"],
        )


def test_reoptimizing_an_existing_plan_via_pack_endpoint_can_still_be_saved(client):
    """Fase 6A.1, seccion 7 del pedido -regresion real reproducida via
    navegador: crear un plan, cambiar Optimization Mode y volver a
    /api/pack (asi es como App.tsx:runOptimize reoptimiza un plan YA
    persistido -planId nunca se limpia en el frontend, asi que esa rama
    SIEMPRE opera sobre el plan activo) NO debe romper la asociacion con el
    plan -el siguiente PUT /api/plans/{id} (autosave) tiene que poder
    guardar, no devolver 409 'El plan activo no coincide...'."""
    created = _create_plan(client)
    plan_id = created["plan_id"]

    repack = client.post(
        "/api/pack",
        json={"items": [_ITEM], "container_id": "40ft_standard", "optimization_mode": "keep_groups"},
    )
    assert repack.status_code == 200

    save = client.put(f"/api/plans/{plan_id}")
    assert save.status_code == 200, save.text

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert reopened["optimization_mode"] == "keep_groups"


# ---------------------------------------------------------------------------
# Custom Load Space (seccion 19/44 del pedido)
# ---------------------------------------------------------------------------


def test_custom_load_space_is_restored_exactly_not_a_catalog_fallback(client):
    custom = {
        "name": "Custom Truck",
        "load_space_type": "truck",
        "length": 7200,
        "width": 2450,
        "height": 2650,
        "max_weight": 12000,
    }
    created = _create_plan(client, container_id=None, custom_load_space=custom, name="Custom Plan")
    plan_id = created["plan_id"]

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert reopened["container_id"] is None
    assert reopened["custom_load_space"]["length"] == 7200
    assert reopened["custom_load_space"]["width"] == 2450
    assert reopened["custom_load_space"]["height"] == 2650
    assert reopened["custom_load_space"]["max_weight"] == 12000
    assert reopened["result"]["container"]["length"] == 7200


# ---------------------------------------------------------------------------
# Plan Handling Rules + item overrides (secciones 20/21/22/45 del pedido)
# ---------------------------------------------------------------------------


def test_plan_handling_rules_are_restored(client):
    rules = {"default_stackable": False, "default_max_stack_weight": 500}
    created = _create_plan(client, plan_handling_rules=rules)
    reopened = client.get(f"/api/plans/{created['plan_id']}").json()
    assert reopened["plan_handling_rules"]["default_stackable"] is False
    assert reopened["plan_handling_rules"]["default_max_stack_weight"] == 500


def test_item_override_survives_reopen_and_still_overrides_after_default_flips(client):
    """Escenario exacto de la seccion 22/45 del pedido: default Stackable=
    Yes, P002 explicito No -reabrir, cambiar el default a No, P002 sigue
    siendo su propio No explicito (no 'No heredado')."""
    items = [
        {**_ITEM, "code": "P001"},
        {**_ITEM, "code": "P002", "stackable": False, "stackable_override": False},
    ]
    created = _create_plan(client, items=items, plan_handling_rules={"default_stackable": True})
    plan_id = created["plan_id"]

    reopened = client.get(f"/api/plans/{plan_id}").json()
    p002 = next(p for p in reopened["result"]["placed"] if p["code"] == "P002")
    assert p002["stackable_override"] is False  # el crudo, no solo el valor resuelto
    p001 = next(p for p in reopened["result"]["placed"] if p["code"] == "P001")
    assert p001["stackable_override"] is None  # sigue heredado


# ---------------------------------------------------------------------------
# Two-plan isolation (secciones 35/46 del pedido: A -> B -> A)
# ---------------------------------------------------------------------------


def test_switching_between_two_plans_never_mixes_state(client):
    a = _create_plan(client, items=[{**_ITEM, "code": "FROM-A"}], name="Plan A")
    b = _create_plan(client, items=[{**_ITEM, "code": "FROM-B"}], name="Plan B")

    reopened_a = client.get(f"/api/plans/{a['plan_id']}").json()
    assert reopened_a["name"] == "Plan A"
    assert all(p["code"] == "FROM-A" for p in reopened_a["result"]["placed"])

    reopened_b = client.get(f"/api/plans/{b['plan_id']}").json()
    assert reopened_b["name"] == "Plan B"
    assert all(p["code"] == "FROM-B" for p in reopened_b["result"]["placed"])

    reopened_a_again = client.get(f"/api/plans/{a['plan_id']}").json()
    assert all(p["code"] == "FROM-A" for p in reopened_a_again["result"]["placed"])


def test_current_state_is_not_used_as_persistence(client):
    """Seccion 34/50 del pedido: /api/pack por si solo (sin pasar por
    /api/plans) NUNCA debe aparecer en Recent Plans -_current_state es
    transitorio, no la fuente de verdad."""
    r = client.post("/api/pack", json={"items": [_ITEM], "container_id": "40ft_standard"})
    assert r.status_code == 200
    assert client.get("/api/plans").json() == []


# ---------------------------------------------------------------------------
# Schema version / corrupcion (secciones 30/41/42 del pedido)
# ---------------------------------------------------------------------------


def test_unsupported_schema_version_is_rejected_cleanly(client, tmp_path):
    from app.core.plan_store import PlanRow

    repo = app.dependency_overrides[routes.get_plan_repository]()
    repo.create(
        PlanRow(
            plan_id="future-plan", name="From the future", created_at="2099-01-01T00:00:00+00:00",
            updated_at="2099-01-01T00:00:00+00:00", schema_version=999, load_type="Panels & Fragile",
            load_space_name="X", total_items=0, loaded_items=0, unloaded_items=0, state_json="{}",
        )
    )
    r = client.get("/api/plans/future-plan")
    assert r.status_code == 409
    assert "unsupported" in r.json()["detail"].lower()


def test_corrupt_state_json_is_rejected_cleanly_not_a_crash(client):
    from app.core.plan_store import SCHEMA_VERSION, PlanRow

    repo = app.dependency_overrides[routes.get_plan_repository]()
    repo.create(
        PlanRow(
            plan_id="corrupt-plan", name="Corrupt", created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00", schema_version=SCHEMA_VERSION, load_type="Panels & Fragile",
            load_space_name="X", total_items=0, loaded_items=0, unloaded_items=0,
            state_json="{not valid json",
        )
    )
    r = client.get("/api/plans/corrupt-plan")
    assert r.status_code == 422


def test_missing_required_field_in_state_json_is_rejected_cleanly(client):
    """El JSON parsea pero le falta un campo requerido por los schemas
    Pydantic actuales -tambien debe rechazarse limpio, no crashear."""
    import json as jsonlib

    from app.core.plan_store import SCHEMA_VERSION, PlanRow

    repo = app.dependency_overrides[routes.get_plan_repository]()
    repo.create(
        PlanRow(
            plan_id="incomplete-plan", name="Incomplete", created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00", schema_version=SCHEMA_VERSION, load_type="Panels & Fragile",
            load_space_name="X", total_items=0, loaded_items=0, unloaded_items=0,
            state_json=jsonlib.dumps({"schema_version": SCHEMA_VERSION}),  # falta load_space/placed/unloaded
        )
    )
    r = client.get("/api/plans/incomplete-plan")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Persistencia real de SQLite (independiente del proceso -seccion 47 del
# pedido: no basta con probar mientras el mismo proceso sigue vivo).
# ---------------------------------------------------------------------------


def test_a_second_independent_repository_instance_sees_the_same_data(tmp_path):
    """Simula un reinicio del backend: una SEGUNDA instancia de
    PlanRepository, construida desde cero apuntando al MISMO archivo, debe
    ver exactamente lo que la primera guardo -sin compartir ningun estado
    en memoria entre ambas."""
    db_path = tmp_path / "restart_test.db"

    repo1 = PlanRepository(db_path)
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo1
    client1 = TestClient(app)
    created = _create_plan(client1, name="Survives Restart")
    app.dependency_overrides.pop(routes.get_plan_repository, None)

    # "reinicio": instancia nueva, mismo archivo en disco
    repo2 = PlanRepository(db_path)
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo2
    client2 = TestClient(app)
    try:
        plans = client2.get("/api/plans").json()
        assert len(plans) == 1
        assert plans[0]["plan_id"] == created["plan_id"]
        assert plans[0]["name"] == "Survives Restart"

        detail = client2.get(f"/api/plans/{created['plan_id']}").json()
        assert len(detail["result"]["placed"]) == 1
    finally:
        app.dependency_overrides.pop(routes.get_plan_repository, None)


# ---------------------------------------------------------------------------
# Fase 5D, correccion final: Plan Handling Rules deben autoguardarse
# independientemente de `result` -PUT /api/plans/{id} con un body opcional
# `{plan_handling_rules}` aplica la regla a _current_state y persiste SIN
# tocar placed/unloaded ni volver a correr el optimizador (secciones 1/2/3
# del pedido).
# ---------------------------------------------------------------------------


def test_handling_rule_autosave_persists_without_running_optimize(client):
    """Escenario exacto de la seccion 3 del pedido: cambiar Default
    Stackable de Yes a No y guardar -SIN correr Optimize- debe sobrevivir a
    un reabrir del plan."""
    created = _create_plan(client, plan_handling_rules={"default_stackable": True})
    plan_id = created["plan_id"]

    r = client.put(f"/api/plans/{plan_id}", json={"plan_handling_rules": {"default_stackable": False}})
    assert r.status_code == 200

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert reopened["plan_handling_rules"]["default_stackable"] is False


def test_handling_rule_autosave_does_not_repack_or_alter_placements(tmp_path):
    """Seccion 2 del pedido: guardar una regla no es lo mismo que
    reoptimizar -la colocacion existente debe quedar exactamente igual
    (misma posicion, mismo id) despues del autosave de la regla."""
    repo = PlanRepository(tmp_path / "no_repack_test.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    client_local = TestClient(app)
    try:
        created = _create_plan(client_local, plan_handling_rules={"default_stackable": True})
        plan_id = created["plan_id"]
        before = created["best"]["placed"]

        r = client_local.put(f"/api/plans/{plan_id}", json={"plan_handling_rules": {"default_stackable": False}})
        assert r.status_code == 200

        reopened = client_local.get(f"/api/plans/{plan_id}").json()
        after = reopened["result"]["placed"]
        assert before == after
    finally:
        app.dependency_overrides.pop(routes.get_plan_repository, None)


def test_handling_rule_autosave_preserves_item_overrides(client):
    """Seccion 4 del pedido: P002 con override explicito debe seguir
    explicito, P001 (inherit) debe seguir inherit, despues de cambiar y
    guardar el default del plan -sin materializar el default sobre los
    items."""
    items = [
        {**_ITEM, "code": "P001"},
        {**_ITEM, "code": "P002", "stackable": False, "stackable_override": False},
    ]
    created = _create_plan(client, items=items, plan_handling_rules={"default_stackable": True})
    plan_id = created["plan_id"]

    r = client.put(f"/api/plans/{plan_id}", json={"plan_handling_rules": {"default_stackable": False}})
    assert r.status_code == 200

    reopened = client.get(f"/api/plans/{plan_id}").json()
    p001 = next(p for p in reopened["result"]["placed"] if p["code"] == "P001")
    p002 = next(p for p in reopened["result"]["placed"] if p["code"] == "P002")
    assert p001["stackable_override"] is None  # sigue heredado
    assert p002["stackable_override"] is False  # sigue explicito, no se materializo el nuevo default


def test_handling_rule_autosave_rejects_when_active_session_is_a_different_plan(client):
    """Mismo criterio de aislamiento que el autosave normal (seccion 35)."""
    a = _create_plan(client, name="A")
    _create_plan(client, name="B")  # B queda como sesion activa

    r = client.put(f"/api/plans/{a['plan_id']}", json={"plan_handling_rules": {"default_stackable": False}})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Fase 5D, correccion final, seccion 5/6/7: Load Type es metadata del PLAN,
# fijada al crearlo -nunca re-inferida de placed[0]/unloaded[0].item_type en
# cada autosave/reopen.
# ---------------------------------------------------------------------------


def test_load_type_is_authoritative_plan_metadata_in_recent_plans(client):
    created = _create_plan(client)
    plans = client.get("/api/plans").json()
    assert plans[0]["load_type"] == "Panels & Fragile"


def test_load_type_is_returned_when_opening_a_plan(client):
    created = _create_plan(client)
    detail = client.get(f"/api/plans/{created['plan_id']}").json()
    assert detail["load_type"] == "Panels & Fragile"


def test_load_type_survives_autosave_even_when_all_items_become_unloaded(client):
    """Seccion 5/6 del pedido: un plan que termina con TODAS sus piezas en
    Unloaded (placed vacio) no debe perder ni cambiar su Load Type -si se
    siguiera infiriendo de placed[0]/unloaded[0] en cada guardado, este caso
    seguiria funcionando por casualidad (unloaded[0] todavia existe), pero
    la regla pedida es que el valor NUNCA se re-derive despues de crear el
    plan, sin importar el estado de las piezas."""
    created = _create_plan(client)
    plan_id = created["plan_id"]
    piece_id = created["best"]["placed"][0]["id"]

    client.post("/api/remove-piece", json={"piece_id": piece_id})
    save = client.put(f"/api/plans/{plan_id}")
    assert save.status_code == 200
    assert save.json()["load_type"] == "Panels & Fragile"

    reopened = client.get(f"/api/plans/{plan_id}").json()
    assert reopened["load_type"] == "Panels & Fragile"
    assert len(reopened["result"]["placed"]) == 0
    assert len(reopened["result"]["unloaded"]) == 1


def test_load_type_survives_handling_rule_autosave(client):
    """Combina ambas correcciones: guardar una Handling Rule (sin repack)
    tampoco debe alterar el Load Type persistido."""
    created = _create_plan(client)
    plan_id = created["plan_id"]

    client.put(f"/api/plans/{plan_id}", json={"plan_handling_rules": {"default_stackable": False}})
    plans = client.get("/api/plans").json()
    assert plans[0]["load_type"] == "Panels & Fragile"
