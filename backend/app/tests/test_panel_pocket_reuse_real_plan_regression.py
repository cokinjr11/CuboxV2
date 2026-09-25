"""Regresion PERMANENTE de produccion (seccion 9 del pedido de integracion
de Pocket Reuse): el plan real de 67 lineas que expuso el caso D12 (panel
"Marcos", 4521.2 x 300 x 300mm, unica orientacion valida -ver
core/panel_pocket_reuse.py) reducido a sus dimensiones reales, IDs/Group
anonimizados (el Group original embebia el nombre de un cliente real).

Antes de la integracion de Pocket Reuse (PANEL_MODULE_V1, Stage 1 solo):
32/67 cargadas, 35 sin cargar, usaba 10867.9mm. Con Pocket Reuse en
produccion: 67/67, 0 sin cargar. Una regresion futura que vuelva a ~32
cargadas (o rompa validacion/secuencia) sobre este dataset exacto es un
regression HARD -bloquear el merge, no solo advertir."""

from app.core.final_validation import validate_for_export
from app.core.optimize import ENGINE_PANEL_MODULE_POCKET_V2, _select_pack_fn
from app.core.packing_quality import used_bounding_length
from app.core.sequence import compute_load_steps, compute_operational_warnings, compute_unload_steps
from app.models.containers import get_container
from app.models.schemas import ItemType, OptimizationMode, WindowItem
from app.core.optimize import run_optimization

CONTAINER = get_container("40ft_high_cube")

# (code, width, height, thickness, group, system) -- geometria/Group/System
# REALES del plan de 67 lineas que expuso D12 (Group anonimizado, ver
# docstring del modulo). El item "D12" con width=4521.2002 es el outlier
# real (descripcion original "Marcos" vs. "Hojas" de sus hermanos -- panel
# legitimo, no un error de importacion, ver el reporte de la investigacion
# de root cause).
_REAL_67_LINE_PLAN: list[tuple[str, float, float, float, str, str]] = [
    ("D1", 1574.8, 2743.2, 160.0, "GROUP-1", "VINTAGE"),
    ("Arched Window Wall", 2433.0, 1220.2, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Arched Window Wall", 2433.0, 1220.2, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Arched Window Wall", 2433.0, 1209.7, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Arched Window Wall", 2433.0, 1209.7, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("D12", 1138.2, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D12", 1138.2, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D12", 1130.9001, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("A", 1045.7625, 2743.2, 155.4, "GROUP-1", "Window Wall"),
    ("A", 1045.7625, 2743.2, 155.4, "GROUP-1", "Window Wall"),
    ("A", 1039.0626, 2743.2, 155.4, "GROUP-1", "Window Wall"),
    ("A", 1028.5626, 2743.2, 155.4, "GROUP-1", "Window Wall"),
    ("D4", 1016.0, 2743.2, 160.0, "GROUP-1", "VINTAGE"),
    ("D12", 4521.2002, 300.0, 300.0, "GROUP-1", "Sliding glass"),  # el outlier real
    ("D1", 773.65, 2697.4, 160.0, "GROUP-1", "VINTAGE"),
    ("M1", 609.6, 1828.8, 109.1, "GROUP-1", "Casement out KA"),
    ("D12", 1130.9001, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("D3", 960.4, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D3", 960.4, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D3", 960.4, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D3", 960.4, 2702.1, 155.4, "GROUP-1", "Sliding glass"),
    ("D3", 953.1, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("Sidelite A", 2349.49, 749.3, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Sidelite A", 2349.49, 749.3, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Sidelite B", 2349.49, 749.3, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Sidelite B", 2349.49, 749.3, 155.4, "GROUP-2", "KA WINDOW WALL"),
    ("Tapas", 2349.49, 300.0, 300.0, "GROUP-2", "KA WINDOW WALL"),
    ("D2", 908.05, 2438.3999, 160.0, "GROUP-1", "VINTAGE"),
    ("C", 1016.0, 1676.4, 102.6, "GROUP-1", "Tilt & Turn"),
    ("V19", 1598.0, 2723.0, 160.0, "GROUP-3", "VINTAGE"),
    ("D3", 953.1, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("D-1", 2463.0, 914.4, 90.9, "GROUP-2", "KA FRENCH DOOR"),
    ("D-1.1", 2463.0, 914.4, 90.9, "GROUP-2", "KA FRENCH DOOR"),
    ("D3", 953.1, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("D3", 953.1, 2702.1, 171.6, "GROUP-1", "Sliding glass"),
    ("G", 1676.4, 2108.2, 80.0, "GROUP-1", "Picture Window"),
    ("P", 1676.4, 1828.8, 80.0, "GROUP-1", "Picture Window"),
    ("P", 1676.4, 1828.8, 80.0, "GROUP-1", "Picture Window"),
    ("F", 1968.5, 1346.2, 80.0, "GROUP-1", "Picture Window"),
    ("H", 1016.0, 2108.2, 80.0, "GROUP-1", "Picture Window"),
    ("H", 1016.0, 2108.2, 80.0, "GROUP-1", "Picture Window"),
    ("J", 1016.0, 1320.8, 109.1, "GROUP-1", "Casement out KA"),
    ("B", 1016.0, 1676.4, 80.0, "GROUP-1", "Picture Window"),
    ("B", 1016.0, 1676.4, 80.0, "GROUP-1", "Picture Window"),
    ("B", 1016.0, 1676.4, 80.0, "GROUP-1", "Picture Window"),
    ("B", 1016.0, 1676.4, 80.0, "GROUP-1", "Picture Window"),
    ("B", 1016.0, 1676.4, 80.0, "GROUP-1", "Picture Window"),
    ("E", 908.05, 1346.2, 109.1, "GROUP-1", "Casement out KA"),
    ("M2", 609.6, 1828.8, 109.1, "GROUP-1", "Casement out KA"),
    ("O", 609.6, 2286.0, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("K", 1016.0, 1320.8, 80.0, "GROUP-1", "Picture Window"),
    ("D", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("N1", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("N2", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("N3", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("N4", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("N5", 609.6, 1524.0, 109.1, "GROUP-1", "Casement out KA"),
    ("Q", 609.6, 1828.8, 80.0, "GROUP-1", "Picture Window"),
    ("Q", 609.6, 1828.8, 80.0, "GROUP-1", "Picture Window"),
    ("V19", 760.75, 2677.2, 160.0, "GROUP-3", "VINTAGE"),
    ("Totem", 2000.0, 2400.0, 1000.0, "GROUP-4", "Totem"),
]


def _build_items() -> list[WindowItem]:
    return [
        WindowItem(
            code=code, description=code, width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False,
            group=group, system=system,
        )
        for code, w, h, t, group, system in _REAL_67_LINE_PLAN
    ]


def test_real_67_line_plan_routes_to_pocket_reuse_in_production():
    fn, engine = _select_pack_fn(_build_items(), None, clearance=0.0)
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_real_67_line_plan_loads_all_67_via_production_path():
    items = _build_items()
    assert len(items) == 67

    best, _alternatives = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)

    assert len(best.placed) == 67, (
        f"HARD REGRESSION: expected 67/67 loaded via production routing, got {len(best.placed)}/67 "
        "-- a drop back toward 32 means Pocket Reuse silently stopped being used or stopped working."
    )
    assert len(best.unloaded) == 0

    used_length = used_bounding_length(best.placed, CONTAINER)
    # ~10888mm observed at integration time; small deterministic/tolerance
    # differences are acceptable (seccion 9 del pedido), a large regression
    # back toward not using pockets at all (~13600mm, two module-depths) is not.
    assert used_length < 11500.0, f"used length grew unexpectedly to {used_length:.1f}mm -- pockets may no longer be reused"

    issues = validate_for_export(best, CONTAINER)
    assert issues == [], f"validation errors on the real 67-line plan: {issues}"

    warnings = compute_operational_warnings(best.placed, CONTAINER)
    assert warnings == [], f"unexpected operational warnings on the real 67-line plan: {warnings}"

    # secuencia recomputa limpio, sin ciclos, sin IDs faltantes/duplicados
    load_steps = compute_load_steps(best.placed, CONTAINER)
    unload_steps = compute_unload_steps(best.placed)
    load_ids = [pid for step in load_steps for pid in step]
    unload_ids = [pid for step in unload_steps for pid in step]
    all_ids = {p.id for p in best.placed}
    assert set(load_ids) == all_ids
    assert set(unload_ids) == all_ids
    assert len(load_ids) == len(set(load_ids)), "duplicate IDs in load sequence"
    assert len(unload_ids) == len(set(unload_ids)), "duplicate IDs in unload sequence"
