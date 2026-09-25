"""CUBOX 2.0 -- Keep Groups Together, pedido "FINAL INTEGRATION + LIVE
VISUAL ACCEPTANCE", seccion 20: LIVE-GROUP-1..10.

Verifica el ruteo REAL de produccion (core/optimize.py:run_optimization,
el mismo que llama api/routes.py) hacia section_keep_groups_pocket_reuse_
stage3 para KEEP_GROUPS+PANEL sin Tilt/Clearance, y que los fallbacks
existentes (Tilt, Clearance, BEST_SPACE, KEEP_SYSTEMS, otros item_type)
permanecen exactamente iguales. Reusa la geometria real anonimizada de
test_panel_pocket_reuse_real_plan_regression.py (_REAL_67_LINE_PLAN)
-mismas dimensiones/Group/System reales, nunca un dataset inventado- pero
con Stackable=True (el valor real confirmado por la auditoria de
Handling Rules, ver esa tarea) en vez del False historico de ese archivo
-ese otro test protege Stage 1/2 (Pocket Reuse) especificamente, que
sigue siendo un escenario floor-only valido; este archivo prueba Stage 3
(Section + Level 2), que necesita el Stackable real para ejercitar algo."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.final_validation import validate_for_export
from app.core.optimize import (
    ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK,
    ENGINE_PANEL_LEGACY_TILT_FALLBACK,
    ENGINE_PANEL_SECTION_GROUP_3D_V3,
    _select_pack_fn,
    run_optimization,
)
from app.core.section_coordinator import compute_group_contiguity_metrics, section_keep_groups_pocket_reuse_stage3
from app.core.sequence import compute_unload_dependencies, detect_sequence_cycle
from app.main import app
from app.models.containers import get_container
from app.models.schemas import ItemType, OptimizationMode, WindowItem

from .test_panel_pocket_reuse_real_plan_regression import _REAL_67_LINE_PLAN

CONTAINER_HC = get_container("40ft_high_cube")
CONTAINER_STD = get_container("40ft_standard")
client = TestClient(app)


def _panel(code, w=500, h=1800, t=40, **kw):
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 15.0), quantity=1, item_type=ItemType.PANEL, stackable=False, **kw,
    )


def _real_67_items_stackable_true() -> list[WindowItem]:
    return [
        WindowItem(
            code=code, description=code, width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, stackable_override=True,
            group=group, system=system,
        )
        for code, w, h, t, group, system in _REAL_67_LINE_PLAN
    ]


# ==========================================================================
# LIVE-GROUP-1: PANEL + KEEP_GROUPS en una configuracion soportada (sin
# Tilt/Clearance) rutea DE VERDAD a Stage 3 -verificado interceptando la
# funcion real (nunca solo inferido por el resultado).
# ==========================================================================
def test_live_group_1_supported_settings_routes_stage3():
    items = [_panel("A1", group="A"), _panel("A2", group="A")]
    with patch(
        "app.core.section_coordinator.section_keep_groups_pocket_reuse_stage3",
        wraps=section_keep_groups_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER_STD, OptimizationMode.KEEP_GROUPS)
        assert spy.called, "KEEP_GROUPS+PANEL sin Tilt/Clearance debe rutear a section_keep_groups_pocket_reuse_stage3"


# ==========================================================================
# LIVE-GROUP-2: el plan real de 67 lineas (Stackable=true real) carga
# 67/67 via el camino de produccion real (run_optimization).
# ==========================================================================
def test_live_group_2_real_67_plan_loads_all():
    items = _real_67_items_stackable_true()
    best, _alternatives = run_optimization(items, CONTAINER_HC, OptimizationMode.KEEP_GROUPS)
    assert len(best.placed) == 67
    assert len(best.unloaded) == 0
    assert validate_for_export(best, CONTAINER_HC) == []


# ==========================================================================
# LIVE-GROUP-3: el resultado real incluye los 8 placements de Level 2
# (z > 0) esperados -6 de GROUP-1 (Raquel Zaki), 2 de GROUP-2 (INFINITE
# WINDOWS). Los codigos EXACTOS elegidos entre hermanos de dimension
# identica (varios D3/D12 con las mismas medidas) dependen del orden de
# entrada del pool -no es el invariante real a proteger aca (confirmado:
# el mismo dataset via /api/optimize-remaining con el orden real de
# PlacedPiece elige F-001/D12-002/D3-001..004, mientras que el orden de
# _REAL_67_LINE_PLAN elige F-001/D12-005/D3-005..008 -mismas piezas
# fisicas por simetria, distinto desempate); lo que SI es invariante es
# la cantidad total y el desglose por Grupo.
# ==========================================================================
def test_live_group_3_real_67_plan_includes_expected_z_positive_placements():
    items = _real_67_items_stackable_true()
    best, _alternatives = run_optimization(items, CONTAINER_HC, OptimizationMode.KEEP_GROUPS)
    upper = [p for p in best.placed if p.z > 1e-6]
    assert len(upper) == 8
    assert sum(1 for p in upper if p.group == "GROUP-1") == 6
    assert sum(1 for p in upper if p.group == "GROUP-2") == 2
    assert all(p.code in {"F", "D12", "D3", "Sidelite A", "Sidelite B"} for p in upper)


# ==========================================================================
# LIVE-GROUP-4: group_reentry_count = 0 en el resultado real (verificado
# con el arbitro canonico, compute_group_contiguity_metrics sobre
# `sections` -nunca un chequeo plano por coordenada X, que puede dar
# falsos positivos dentro de una Section con handoff legitimo).
# ==========================================================================
def test_live_group_4_real_67_plan_zero_group_reentry():
    items = _real_67_items_stackable_true()
    _result, sections, _reports, _frontiers = section_keep_groups_pocket_reuse_stage3(items, CONTAINER_HC)
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())


# ==========================================================================
# LIVE-GROUP-5: BEST_SPACE_UTILIZATION nunca rutea a Stage 3, ni siquiera
# con el mismo item_type/dataset que si lo hace bajo KEEP_GROUPS.
# ==========================================================================
def test_live_group_5_best_space_does_not_route_stage3():
    items = [_panel("A1", group="A"), _panel("A2", group="A")]
    with patch(
        "app.core.section_coordinator.section_keep_groups_pocket_reuse_stage3",
        wraps=section_keep_groups_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER_STD, OptimizationMode.BEST_SPACE)
        assert not spy.called


# ==========================================================================
# LIVE-GROUP-6: KEEP_SYSTEMS nunca rutea a Stage 3 (Stage 3 es
# exclusivamente para KEEP_GROUPS -Keep Systems Together sigue sin
# tocarse en esta tarea).
# ==========================================================================
def test_live_group_6_keep_systems_does_not_route_stage3():
    items = [_panel("A1", group="A"), _panel("A2", group="A")]
    with patch(
        "app.core.section_coordinator.section_keep_groups_pocket_reuse_stage3",
        wraps=section_keep_groups_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER_STD, OptimizationMode.KEEP_SYSTEMS)
        assert not spy.called


# ==========================================================================
# LIVE-GROUP-7: Tilt activo sigue cayendo al fallback legacy de siempre
# -Stage 3 nunca se fuerza a un caso que no fue construido para soportar.
# Tilt es PLAN-LEVEL ONLY (Fase 5C-FINAL, core/handling_rules.py:
# resolve_plan_tilt) -run_optimization resuelve allow_tilt/max_tilt_angle
# efectivos a partir de `plan_handling_rules` ANTES de rutear (sobreescribe
# cualquier valor crudo de item), asi que activarlo de verdad requiere
# pasar `plan_handling_rules`, no solo construir el WindowItem con
# allow_tilt=True (ese valor crudo se descarta si no hay Plan Tilt
# Settings real -mismo criterio que el resto del motor de Handling
# Rules).
# ==========================================================================
def test_live_group_7_tilt_fallback_stays_intact():
    from app.models.schemas import PlanHandlingRules

    items = [_panel("A1", group="A"), _panel("A2", group="A")]
    plan_rules = PlanHandlingRules(default_allow_tilt=True, default_max_tilt_angle=5.0)
    resolved = [
        w.model_copy(update={"allow_tilt": True, "max_tilt_angle": 5.0}) for w in items
    ]
    fn, engine = _select_pack_fn(resolved, None, clearance=0.0)
    assert engine == ENGINE_PANEL_LEGACY_TILT_FALLBACK
    with patch(
        "app.core.section_coordinator.section_keep_groups_pocket_reuse_stage3",
        wraps=section_keep_groups_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER_STD, OptimizationMode.KEEP_GROUPS, plan_handling_rules=plan_rules)
        assert not spy.called


# ==========================================================================
# LIVE-GROUP-8: Clearance no soportado (>0) sigue cayendo al fallback
# legacy de siempre.
# ==========================================================================
def test_live_group_8_unsupported_clearance_fallback_stays_intact():
    items = [_panel("A1", group="A"), _panel("A2", group="A")]
    fn, engine = _select_pack_fn(items, None, clearance=20.0)
    assert engine == ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK
    with patch(
        "app.core.section_coordinator.section_keep_groups_pocket_reuse_stage3",
        wraps=section_keep_groups_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER_STD, OptimizationMode.KEEP_GROUPS, clearance=20.0)
        assert not spy.called


# ==========================================================================
# LIVE-GROUP-9: guardar y reabrir el plan real (via /api/plans, el mismo
# camino que usa el Workspace) preserva la geometria apilada EXACTA -
# x/y/z/dx/dy/dz/orientation/Group para las 67 piezas, especialmente las
# 8 con z > 0.
# ==========================================================================
def test_live_group_9_save_reopen_preserves_stacked_geometry(tmp_path):
    from app.api import routes
    from app.core.plan_store import PlanRepository

    repo = PlanRepository(tmp_path / "live_group_9.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    try:
        items_payload = [
            {
                "code": code, "description": code, "width": w, "height": h, "thickness": t,
                "weight": 10.0, "quantity": 1, "item_type": "panel", "stackable": True,
                "stackable_override": True, "group": group, "system": system,
            }
            for code, w, h, t, group, system in _REAL_67_LINE_PLAN
        ]
        body = {
            "items": items_payload, "container_id": "40ft_high_cube", "name": "LIVE-GROUP-9",
            "optimization_mode": "keep_groups",
        }
        created = client.post("/api/plans", json=body).json()
        before = {p["id"]: p for p in created["best"]["placed"]}
        assert len(before) == 67

        reopened = client.get(f"/api/plans/{created['plan_id']}").json()
        after = {p["id"]: p for p in reopened["result"]["placed"]}

        assert set(before) == set(after)
        fields = ["x", "y", "z", "dx", "dy", "dz", "orientation_label", "group"]
        for pid, b in before.items():
            for f in fields:
                assert b[f] == after[pid][f], f"{pid}.{f} changed on reopen: {b[f]} -> {after[pid][f]}"

        before_upper = [p for p in before.values() if p["z"] > 1e-6]
        assert len(before_upper) == 8
        assert all(after[p["id"]]["z"] > 1e-6 for p in before_upper)
    finally:
        app.dependency_overrides.pop(routes.get_plan_repository, None)


# ==========================================================================
# LIVE-GROUP-10: las dependencias de carga/descarga de las piezas
# apiladas son correctas -cada item de Level 2 depende (carga despues,
# descarga antes) de su(s) soporte(s) real(es), sin ciclo de secuencia.
# compute_unload_dependencies mezcla soporte + bloqueo lateral (ver
# core/sequence.py) -un upper puede depender de piezas que NO lo tocan
# (bloqueo), asi que este test aisla especificamente la relacion de
# SOPORTE real (geometry.check_support, canonico) en vez de asumir que
# CUALQUIER dependencia reportada es un contacto fisico.
# ==========================================================================
def test_live_group_10_stacked_load_unload_dependencies_are_correct():
    from app.core.geometry import TOL, Box, check_support

    items = _real_67_items_stackable_true()
    best, _alternatives = run_optimization(items, CONTAINER_HC, OptimizationMode.KEEP_GROUPS)
    unload_deps = compute_unload_dependencies(best.placed)
    assert detect_sequence_cycle(unload_deps) is None

    boxes = [
        Box(id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz, stackable=p.stackable, max_stack_weight=p.max_stack_weight)
        for p in best.placed
    ]
    by_id_box = {b.id: b for b in boxes}
    upper = [p for p in best.placed if p.z > 1e-6]
    assert upper, "esta fixture debe ejercitar Level 2 de verdad"
    for p in upper:
        pbox = by_id_box[p.id]
        support_ok, _reason = check_support(pbox, [b for b in boxes if b.id != p.id])
        assert support_ok, f"{p.id} (Level 2, z={p.z}) debe tener soporte fisico real valido"
        real_supporters = [
            b.id for b in boxes
            if b.id != p.id and abs(b.top_z - pbox.z) < TOL
            and max(0.0, min(pbox.x + pbox.dx, b.x + b.dx) - max(pbox.x, b.x)) * max(0.0, min(pbox.y + pbox.dy, b.y + b.dy) - max(pbox.y, b.y)) > TOL
        ]
        assert real_supporters, f"{p.id} debe tener al menos un soporte con contacto XY real"
        # deps[pid] = "que debe salir ANTES que pid" (docstring de compute_
        # unload_dependencies) -- el soporte (base) depende de lo que tiene
        # ENCIMA, nunca al reves: para sacar la base hay que sacar primero
        # lo que esta arriba, asi que deps[supporter] debe incluir a `p`
        # (el item de Level 2), no deps[p] al soporte.
        for supporter_id in real_supporters:
            assert p.id in unload_deps.get(supporter_id, []), (
                f"{supporter_id} (base) debe depender de {p.id} (Level 2, encima) para la descarga -{p.id} debe salir primero"
            )
