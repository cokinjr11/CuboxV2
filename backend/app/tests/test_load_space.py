"""
CUBOX 2.0 - Fase 2A: generalizacion de ContainerSpec en LoadSpaceSpec.

Cubre los Tests A-I especificados para esta fase. No reimplementa reglas de
geometria/orientacion/soporte -reutiliza pack_container, validate_placement
y validate_for_export tal cual, solo variando el LoadSpaceSpec de entrada
(Container/Truck/Trailer/Custom)."""

from fastapi.testclient import TestClient

from app.core.final_validation import validate_for_export
from app.core.geometry import Box
from app.core.manual_move import validate_placement
from app.core.packer import pack_container
from app.core.reserved_zones import central_aisle_zone, zone_conflict
from app.main import app
from app.models.containers import CONTAINER_CATALOG, build_custom_load_space, get_container, list_load_spaces
from app.models.schemas import LoadSpaceType, OptimizationMode, WindowItem

client = TestClient(app)


def _window(**overrides):
    defaults = dict(
        code="W1",
        description="Item test",
        width=800,
        height=1200,
        thickness=80,
        weight=20,
        quantity=1,
        system="SysA",
        group="G1",
        stackable=True,
        priority=1,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


# ---------------------------------------------------------------------------
# TEST A - los 3 presets de contenedor existentes no cambian.
# ---------------------------------------------------------------------------


def test_legacy_containers_unchanged():
    twenty = CONTAINER_CATALOG["20ft_standard"]
    assert (twenty.id, twenty.length, twenty.width, twenty.height, twenty.max_weight) == (
        "20ft_standard",
        5898,
        2352,
        2393,
        28180,
    )
    assert twenty.load_space_type == LoadSpaceType.CONTAINER

    forty = CONTAINER_CATALOG["40ft_standard"]
    assert (forty.id, forty.length, forty.width, forty.height, forty.max_weight) == (
        "40ft_standard",
        12032,
        2352,
        2393,
        26512,
    )

    forty_hc = CONTAINER_CATALOG["40ft_high_cube"]
    assert (forty_hc.id, forty_hc.length, forty_hc.width, forty_hc.height, forty_hc.max_weight) == (
        "40ft_high_cube",
        12032,
        2352,
        2698,
        26330,
    )


# ---------------------------------------------------------------------------
# TEST B - packing legacy con un contenedor existente sigue siendo valido.
# ---------------------------------------------------------------------------


def test_legacy_packing_into_existing_container_still_valid():
    container = get_container("40ft_standard")
    items = [_window(quantity=5)]
    result = pack_container(items, container, strategy="highest_priority")

    assert len(result.placed) == 5
    errors = validate_for_export(result, container)
    assert errors == []


# ---------------------------------------------------------------------------
# TEST C - CONTAINER con dimensiones custom: el packer respeta esos limites.
# ---------------------------------------------------------------------------


def test_custom_container_respects_its_own_boundaries():
    small_container = build_custom_load_space(
        name="Mini Container", load_space_type=LoadSpaceType.CONTAINER, length=1000, width=1000, height=1000, max_weight=5000
    )
    assert small_container.load_space_type == LoadSpaceType.CONTAINER

    # La ventana (800x1200x80) no entra parada en un espacio de 1000mm de alto
    # en ninguna orientacion valida -debe quedar unloaded, nunca colocada
    # fuera de los limites.
    result = pack_container([_window(quantity=1)], small_container, strategy="highest_priority")
    assert len(result.placed) == 0
    assert len(result.unloaded) == 1


# ---------------------------------------------------------------------------
# TEST D - TRUCK custom: el mismo motor coloca items validos adentro.
# ---------------------------------------------------------------------------


def test_custom_truck_places_valid_items():
    truck = build_custom_load_space(
        name="Warehouse Truck 01", load_space_type=LoadSpaceType.TRUCK, length=7200, width=2400, height=2500, max_weight=8000
    )
    assert truck.load_space_type == LoadSpaceType.TRUCK

    result = pack_container([_window(quantity=3)], truck, strategy="highest_priority")
    assert len(result.placed) == 3
    assert validate_for_export(result, truck) == []


# ---------------------------------------------------------------------------
# TEST E - TRAILER custom: idem.
# ---------------------------------------------------------------------------


def test_custom_trailer_places_valid_items():
    trailer = build_custom_load_space(
        name="Flatbed Trailer 01", load_space_type=LoadSpaceType.TRAILER, length=13600, width=2480, height=2700, max_weight=24000
    )
    assert trailer.load_space_type == LoadSpaceType.TRAILER

    result = pack_container([_window(quantity=3)], trailer, strategy="highest_priority")
    assert len(result.placed) == 3
    assert validate_for_export(result, trailer) == []


# ---------------------------------------------------------------------------
# TEST F - ningun item puede exceder length/width/height, sin importar el
# LoadSpaceType.
# ---------------------------------------------------------------------------


def test_boundary_validation_rejects_oversized_item_regardless_of_type():
    for load_space_type in (LoadSpaceType.CONTAINER, LoadSpaceType.TRUCK, LoadSpaceType.TRAILER, LoadSpaceType.CUSTOM):
        space = build_custom_load_space(
            name=f"space-{load_space_type.value}", load_space_type=load_space_type, length=1000, width=1000, height=1000, max_weight=50000
        )
        valid, reason = validate_placement(
            piece_id="oversized",
            width=800,
            height=1200,
            thickness=80,
            stackable=True,
            weight=20,
            max_stack_weight=None,
            x=0,
            y=0,
            z=0,
            dx=800,
            dy=80,
            dz=1200,  # dz=1200 > space.height=1000
            other_pieces=[],
            container=space,
        )
        assert valid is False, f"{load_space_type} deberia rechazar un item mas alto que el espacio"
        assert "limites" in reason.lower()


# ---------------------------------------------------------------------------
# TEST G - max_weight se respeta para los 4 tipos.
# ---------------------------------------------------------------------------


def test_max_payload_respected_for_every_load_space_type():
    for load_space_type in (LoadSpaceType.CONTAINER, LoadSpaceType.TRUCK, LoadSpaceType.TRAILER, LoadSpaceType.CUSTOM):
        space = build_custom_load_space(
            name=f"space-{load_space_type.value}", load_space_type=load_space_type, length=5000, width=2400, height=2500, max_weight=50
        )
        # 3 items de 20kg = 60kg > max_weight=50kg -al menos uno debe quedar unloaded.
        result = pack_container([_window(quantity=3, weight=20)], space, strategy="highest_priority")
        total_placed_weight = sum(p.weight for p in result.placed)
        assert total_placed_weight <= 50 + 1e-6
        assert len(result.unloaded) >= 1


# ---------------------------------------------------------------------------
# TEST H - Central Aisle funciona igual en un LoadSpace tipo Truck/Trailer:
# la zona queda centrada y no puede ocuparse.
# ---------------------------------------------------------------------------


def test_central_aisle_works_on_truck_and_trailer_load_spaces():
    for load_space_type in (LoadSpaceType.TRUCK, LoadSpaceType.TRAILER):
        space = build_custom_load_space(
            name=f"space-{load_space_type.value}", load_space_type=load_space_type, length=8000, width=2400, height=2500, max_weight=10000
        )
        aisle = central_aisle_zone(space, 500)

        # Centrado geometrico: igual formula que para CONTAINER, sin
        # depender de load_space_type.
        assert aisle.y == (space.width - 500) / 2
        assert aisle.width == 500

        result = pack_container([_window(quantity=40)], space, OptimizationMode.BEST_SPACE, [aisle], 0.0)
        assert len(result.placed) > 0
        for p in result.placed:
            box = Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable)
            assert zone_conflict(box, [aisle]) is None, f"{load_space_type}: pieza {p.id} invade el pasillo"


# ---------------------------------------------------------------------------
# TEST I - compatibilidad de API: /api/containers sigue funcionando y
# /api/load-spaces devuelve el catalogo generalizado.
# ---------------------------------------------------------------------------


def test_containers_endpoint_still_works():
    r = client.get("/api/containers")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert "20ft_standard" in ids
    assert "40ft_standard" in ids
    assert "40ft_high_cube" in ids


def test_load_spaces_endpoint_returns_generalized_catalog():
    r = client.get("/api/load-spaces")
    assert r.status_code == 200
    body = r.json()
    ids = [c["id"] for c in body]
    assert "20ft_standard" in ids
    assert all(c["load_space_type"] == "container" for c in body)
    assert [c["id"] for c in body] == [c.id for c in list_load_spaces()]


def test_pack_endpoint_still_accepts_container_id():
    r = client.post("/api/pack", json={"items": [_window(quantity=2).model_dump()], "container_id": "40ft_standard"})
    assert r.status_code == 200
    assert r.json()["best"]["metrics"]["total_pieces"] == 2


def test_pack_endpoint_accepts_custom_load_space():
    r = client.post(
        "/api/pack",
        json={
            "items": [_window(quantity=2).model_dump()],
            "custom_load_space": {
                "name": "Warehouse Truck 01",
                "load_space_type": "truck",
                "length": 7200,
                "width": 2400,
                "height": 2500,
                "max_weight": 8000,
            },
        },
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["metrics"]["total_pieces"] == 2
    assert best["container"]["load_space_type"] == "truck"


# ---------------------------------------------------------------------------
# TEST J (Fase 5A, seccion 9): precedencia explicita -si el request trae
# AMBOS container_id y custom_load_space (no deberia pasar desde el wizard
# real, que son mutuamente excluyentes en la UI, pero el contrato HTTP no lo
# impide), custom_load_space debe ganar siempre. Nunca un fallback silencioso
# al catalogo cuando el usuario eligio explicitamente un Load Space custom.
# ---------------------------------------------------------------------------


def test_pack_endpoint_prefers_custom_load_space_when_both_are_present():
    r = client.post(
        "/api/pack",
        json={
            "items": [_window(quantity=2).model_dump()],
            "container_id": "40ft_standard",
            "custom_load_space": {
                "name": "Warehouse Truck 01",
                "load_space_type": "truck",
                "length": 7200,
                "width": 2400,
                "height": 2500,
                "max_weight": 8000,
            },
        },
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["container"]["load_space_type"] == "truck"
    assert best["container"]["name"] == "Warehouse Truck 01"
    assert best["container"]["length"] == 7200
    assert best["container"]["id"] != "40ft_standard"


# ---------------------------------------------------------------------------
# TEST K (Fase 5A, seccion 11-12): manual movement y final validation deben
# resolver el MISMO Load Space custom que uso el packing original -no un
# catalogo default. Se prueba end-to-end via los endpoints reales (no solo
# validate_placement de bajo nivel, que ya cubre TEST F), para probar que
# _current_state["load_space"] efectivamente queda seteado al objeto custom
# despues de /api/pack y que /api/validate-move lo reutiliza.
# ---------------------------------------------------------------------------


def test_manual_move_and_final_validation_use_the_active_custom_load_space():
    # Espacio angosto a proposito: un movimiento que seria valido en un
    # contenedor estandar (40ft) es invalido aca -si el endpoint cayera de
    # nuevo en un catalogo por error, este movimiento pasaria igual.
    r = client.post(
        "/api/pack",
        json={
            "items": [_window(quantity=1, width=800, height=1200, thickness=80).model_dump()],
            "custom_load_space": {
                "name": "Tiny Custom Space",
                "load_space_type": "custom",
                "length": 1000,
                "width": 1000,
                "height": 1300,
                "max_weight": 5000,
            },
        },
    )
    assert r.status_code == 200
    placed = r.json()["best"]["placed"]
    assert len(placed) == 1
    piece = placed[0]

    # Mover la pieza exactamente a x=length (1000): sin importar que
    # orientacion (dx) haya elegido el packer, x+dx siempre supera el limite
    # del custom space -si el endpoint cayera de nuevo en un catalogo mas
    # grande, este movimiento pasaria igual.
    invalid_move = client.post(
        "/api/validate-move",
        json={
            "piece_id": piece["id"], "x": 1000, "y": 0, "z": 0,
            "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"],
        },
    )
    assert invalid_move.status_code == 200
    assert invalid_move.json()["valid"] is False

    # La misma pieza, movida a una posicion valida DENTRO del custom space,
    # debe aceptarse.
    valid_move = client.post(
        "/api/validate-move",
        json={
            "piece_id": piece["id"], "x": 0, "y": 0, "z": 0,
            "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"],
        },
    )
    assert valid_move.status_code == 200
    assert valid_move.json()["valid"] is True

    # Final validation (Report -> Validate) tambien debe resolver el mismo
    # custom Load Space, sin errores para el layout actual.
    validate_r = client.post("/api/report/validate")
    assert validate_r.status_code == 200
    validate_body = validate_r.json()
    assert validate_body["valid"] is True
    assert validate_body["errors"] == []
    assert validate_body["warnings"] == []
