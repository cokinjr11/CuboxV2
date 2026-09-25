"""CUBOX 2.0 -- Keep Groups Together, pedido "REAL STACKABLE / HANDLING
RULES AUDIT -- THEN REAL LEVEL-2 ACCEPTANCE IF STACKING IS ACTUALLY
ALLOWED", seccion 20: STACK-RULE-1..9.

Contexto del hallazgo (ver reporte final de esta tarea): el plan real de
67 lineas (Excel-importado, wizard "Panels & Fragile") resuelve Stackable
efectivo = TRUE en el 100% de sus items, con override explicito por item
-confirmado consultando /api/plans/{plan_id} en la app corriendo, tanto en
el plan original (2026-09-22) como en el plan activo actual (2026-09-24).
La creencia previa de "stackable=false" en el plan real era un artefacto
de un script de reconstruccion de datos de PRUEBA de esta misma sesion
(nunca capturo el campo `stackable`, y una Section de comparacion propia
creada para la tarea de Pocket Reuse tampoco preservo los overrides
reales) -NUNCA un bug en Cubox. Estos tests documentan/cierran esa
auditoria a nivel de regresion permanente; varios overlapean
deliberadamente con test_handling_rules.py/test_plan_persistence.py/
test_import_items.py (cobertura ya existente, confirmada durante la
auditoria) pero bajo los nombres STACK-RULE-N que este pedido especifica
como entregable propio."""

import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.geometry import TOL
from app.core.handling_rules import resolve_effective_item
from app.core.level_coordinator import try_place_level_2
from app.core.section_coordinator import (
    compute_group_contiguity_metrics,
    section_keep_groups_pocket_reuse,
    section_keep_groups_pocket_reuse_stage3,
)
from app.main import app
from app.models.containers import get_container
from app.models.schemas import ItemType, PlanHandlingRules, PlacedPiece, WindowItem

client = TestClient(app)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CONTAINER = get_container("40ft_standard")


def _window(code, stackable=True, stackable_override=None, group="G", **kw):
    return WindowItem(
        code=code, description=code, width=1000.0, height=2000.0, thickness=150.0,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=stackable,
        stackable_override=stackable_override, group=group, priority=1, **kw,
    )


# ==========================================================================
# STACK-RULE-1: plan default Stackable=true, item sin override -> efectivo
# true (pipeline completo via resolve_effective_item, no solo el helper
# resolve_stackable).
# ==========================================================================
def test_stack_rule_1_plan_default_true_blank_item_override_resolves_true():
    item = _window("A", stackable=False, stackable_override=None)  # `.stackable` crudo aca es irrelevante -override=None es lo que importa
    plan_rules = PlanHandlingRules(default_stackable=True)
    effective = resolve_effective_item(item, plan_rules)
    assert effective.stackable is True


# ==========================================================================
# STACK-RULE-2: plan default Stackable=true, item override=false -> el
# override GANA -efectivo false.
# ==========================================================================
def test_stack_rule_2_item_override_false_wins_over_true_plan_default():
    item = _window("B", stackable=True, stackable_override=False)
    plan_rules = PlanHandlingRules(default_stackable=True)
    effective = resolve_effective_item(item, plan_rules)
    assert effective.stackable is False


# ==========================================================================
# STACK-RULE-3: guardar y reabrir un plan preserva el Stackable EFECTIVO
# (no solo el override crudo -eso ya lo cubre test_plan_persistence.py;
# aca se verifica explicitamente el valor RESUELTO que ve Stage 3).
# ==========================================================================
def test_stack_rule_3_save_reopen_preserves_effective_stackable():
    from app.core.plan_store import PlanRepository
    from app.api import routes

    def _run(tmp_path, plan_default: bool):
        repo = PlanRepository(tmp_path / f"stack_rule_3_{plan_default}.db")
        app.dependency_overrides[routes.get_plan_repository] = lambda: repo
        try:
            item = {
                "code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20,
                "quantity": 1, "item_type": "panel",
            }
            body = {
                "items": [item], "container_id": "40ft_standard", "name": "STACK-RULE-3",
                "plan_handling_rules": {"default_stackable": plan_default},
            }
            created = client.post("/api/plans", json=body).json()
            reopened = client.get(f"/api/plans/{created['plan_id']}").json()
            assert reopened["plan_handling_rules"]["default_stackable"] is plan_default
            piece = reopened["result"]["placed"][0]
            assert piece["stackable_override"] is None  # sigue heredado, nunca materializado como override falso
            assert piece["stackable"] is plan_default  # el EFECTIVO reabierto coincide con el default del plan
        finally:
            app.dependency_overrides.pop(routes.get_plan_repository, None)

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        _run(tmp_path, True)
        _run(tmp_path, False)


# ==========================================================================
# STACK-RULE-4: celda de Excel Stackable en blanco, default del plan=true
# -> efectivo TRUE, nunca False silencioso (seccion 6 del pedido: esto es
# "especialmente importante").
# ==========================================================================
def test_stack_rule_4_blank_excel_cell_inherits_plan_default_true_not_false():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Code", "Quantity", "Width", "Height", "Thickness", "Weight", "Stackable"])
    sheet.append(["W1", 1, 1200, 2000, 100, 45, ""])  # Stackable en blanco a proposito
    buffer = io.BytesIO()
    workbook.save(buffer)

    r = client.post(
        "/api/import-items-excel",
        files={"file": ("panel.xlsx", buffer.getvalue(), XLSX_MIME)},
        data={"profile": "panel", "default_stackable": "true"},
    )
    preview = r.json()
    assert preview["is_valid"] is True
    assert preview["items"][0]["stackable"] is True
    assert any(w["code"] == "STACKABLE_DEFAULTED" for w in preview["warnings"])


# ==========================================================================
# STACK-RULE-5: stackable=false (real, resuelto) produce CERO placements
# de Level 2 -nunca se bypassea, nunca se fuerza el apilado.
# ==========================================================================
def test_stack_rule_5_false_supporter_yields_zero_level_2_placements():
    floor_piece = PlacedPiece(
        id="F1", code="F1", description="F1", weight=10.0, stackable=False,
        x=0.0, y=0.0, z=0.0, dx=1000.0, dy=1000.0, dz=300.0, orientation_label="FIXED",
        source_width=1.0, source_height=1.0, source_thickness=1.0, item_type=ItemType.PANEL,
    )
    pool = [_window("U1", stackable=True)]
    newly, remaining, report = try_place_level_2(
        [floor_piece], pool, [floor_piece], CONTAINER, [], 0.0, group_key_for=lambda w: "G",
    )
    assert newly == []
    assert len(remaining) == 1
    assert report.rejection_counts.get("NON_STACKABLE_SUPPORT", 0) >= 1


# ==========================================================================
# STACK-RULE-6: stackable=true con soporte valido SI produce un placement
# de Level 2.
# ==========================================================================
def test_stack_rule_6_true_supporter_with_valid_support_places_level_2():
    floor_piece = PlacedPiece(
        id="F1", code="F1", description="F1", weight=10.0, stackable=True,
        x=0.0, y=0.0, z=0.0, dx=1000.0, dy=1000.0, dz=300.0, orientation_label="FIXED",
        source_width=1.0, source_height=1.0, source_thickness=1.0, item_type=ItemType.PANEL,
    )
    pool = [_window("U1", stackable=True)]
    newly, remaining, report = try_place_level_2(
        [floor_piece], pool, [floor_piece], CONTAINER, [], 0.0, group_key_for=lambda w: "G",
    )
    assert len(newly) == 1
    assert remaining == []
    assert report.candidates_accepted == 1


# ==========================================================================
# STACK-RULE-7: usar el Level 2 de la Section ACTIVA le gana a abrir una
# Section nueva -Stage 3 (Level 2 habilitado) necesita MENOS longitud
# usada que Stage 2.2 (piso/pocket solamente) para cargar el MISMO Grupo
# completo con Stackable=true real.
# ==========================================================================
def test_stack_rule_7_current_section_level_2_beats_opening_new_section():
    items = [
        WindowItem(
            code=f"A{i}", description=f"A{i}", width=2352.0, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, stackable_override=True,
            group="A", priority=1,
        )
        for i in range(60)
    ]
    result_22, sections_22, reports_22, _f22 = section_keep_groups_pocket_reuse(items, CONTAINER)
    result_3, sections_3, reports_3, _f3 = section_keep_groups_pocket_reuse_stage3(items, CONTAINER)

    used_22 = CONTAINER.length - min((p.x for p in result_22.placed), default=CONTAINER.length)
    used_3 = CONTAINER.length - min((p.x for p in result_3.placed), default=CONTAINER.length)
    assert reports_22[0].loaded_units == reports_3[0].loaded_units, "ambas corridas deben cargar la MISMA cantidad para que la comparacion de longitud sea valida"
    assert any(len(s.levels) > 0 for s in sections_3), "Stage 3 debe haber usado Level 2 en al menos una Section"
    assert used_3 < used_22, f"Stage 3 (con Level 2) deberia usar menos longitud que Stage 2.2 (solo piso): {used_3} vs {used_22}"


# ==========================================================================
# STACK-RULE-8: multiples items superiores llenan la plataforma de soporte
# disponible -no se detiene despues del primero.
# ==========================================================================
def test_stack_rule_8_multiple_upper_items_fill_available_platform():
    floor_piece = PlacedPiece(
        id="F1", code="F1", description="F1", weight=50.0, stackable=True,
        x=0.0, y=0.0, z=0.0, dx=2352.0, dy=2352.0, dz=150.0, orientation_label="FIXED",
        source_width=1.0, source_height=1.0, source_thickness=1.0, item_type=ItemType.PANEL,
    )
    pool = [
        WindowItem(
            code=f"U{i}", description=f"U{i}", width=500.0, height=300.0, thickness=100.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="G",
        )
        for i in range(4)
    ]
    newly, remaining, report = try_place_level_2(
        [floor_piece], pool, [floor_piece], CONTAINER, [], 0.0, group_key_for=lambda w: "G",
    )
    assert len(newly) > 1, "debe llenar mas de un item en la plataforma amplia, no detenerse en el primero"
    assert report.candidates_accepted == len(newly)


# ==========================================================================
# STACK-RULE-9: la cronologia de Grupo permanece monotonica (reentry=0)
# despues de que existen placements reales de Level 2.
# ==========================================================================
def test_stack_rule_9_group_chronology_monotonic_after_upper_placement():
    group_a = [
        WindowItem(
            code=f"A{i}", description=f"A{i}", width=2352.0, height=300.0, thickness=294.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, stackable_override=True,
            group="A", priority=1,
        )
        for i in range(28)
    ]
    group_b = [
        WindowItem(
            code=f"B{i}", description=f"B{i}", width=2352.0, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, stackable_override=True,
            group="B", priority=2,
        )
        for i in range(2)
    ]
    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b, CONTAINER)
    assert any(len(s.levels) > 0 for s in sections), "esta fixture debe ejercitar Level 2 de verdad"
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())
