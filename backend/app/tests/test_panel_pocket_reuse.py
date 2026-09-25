"""Regression tests para Cross-Module Residual Pocket Reuse (Stage 2 del
Panel Module Solver, seccion 19-24 del pedido). Prototipo EXPERIMENTAL
(PANEL_MODULE_POCKET_REUSE) -no wireado a produccion (core/optimize.py sigue
enrutando a PANEL_MODULE_V1 sin cambios, ver seccion 25 del pedido). Estos
tests garantizan la premisa central (seccion 6: un candidato que entra en un
pocket existente le gana a uno que solo puede abrir modulo nuevo) y que
ninguna regla de negocio existente se rompe mientras se evalua esta
arquitectura."""

from app.core.final_validation import validate_for_export
from app.core.packing_quality import panel_height_order_violations, used_bounding_length
from app.core.panel_module_solver import pack_panels_module
from app.core.panel_pocket_reuse import pack_panels_module_pocket_reuse
from app.core.sequence import compute_operational_warnings, is_at_back_right_floor
from app.models.schemas import OperationalWarningType, OptimizationMode

from .fixtures_pocket_reuse import (
    pocket_1_fitting_items,
    pocket_2_no_fit,
    pocket_3_multiple_pockets,
    pocket_4_reserved_zone_intersects,
    pocket_5_group_boundary,
    pocket_6_system_boundary,
    pocket_7_container,
    pocket_7_delivery_dataset,
    pocket_8_fractional_geometry,
    pocket_9_real_d12_derived,
    pocket_9_container,
    pocket_container,
)


def _no_real_overlap(placed) -> bool:
    """Chequeo de colision INDEPENDIENTE del propio solver (no reusa
    geometry.py a proposito -seccion 21 del pedido: verificar con geometria
    real, nunca confiar solo en la red de seguridad interna del mismo
    codigo que se esta probando)."""
    for i in range(len(placed)):
        a = placed[i]
        for b in placed[i + 1 :]:
            ox = a.x < b.x + b.dx - 1e-6 and b.x < a.x + a.dx - 1e-6
            oy = a.y < b.y + b.dy - 1e-6 and b.y < a.y + a.dy - 1e-6
            oz = a.z < b.z + b.dz - 1e-6 and b.z < a.z + a.dz - 1e-6
            if ox and oy and oz:
                return False
    return True


# ==========================================================================
# POCKET-1: reuso exitoso, cero (o negativo) impacto en used_length.
# ==========================================================================
def test_pocket_1_fitting_items_get_reused():
    container = pocket_container()
    items = pocket_1_fitting_items()
    v1, _ = pack_panels_module(items, container)
    pocket, stats = pack_panels_module_pocket_reuse(items, container)

    assert len(pocket.placed) == len(items), "todos los items deberian caber (outlier + 2 angostos + 2 que caben en el pocket)"
    assert stats.pockets_filled >= 1
    assert used_bounding_length(pocket.placed, container) <= used_bounding_length(v1.placed, container), (
        "reusar el pocket nunca deberia consumir MAS longitud que dejar que FIT abra su propio modulo"
    )
    assert validate_for_export(pocket, container) == []
    assert _no_real_overlap(pocket.placed)


# ==========================================================================
# POCKET-2: nada entra -el pocket queda vacio, sin forzar nada invalido.
# ==========================================================================
def test_pocket_2_nothing_fits_pocket_remains_empty():
    container = pocket_container()
    items = pocket_2_no_fit()
    pocket, stats = pack_panels_module_pocket_reuse(items, container)

    assert stats.pockets_filled == 0
    assert any("BIG" == u.code for u in pocket.unloaded), "el item demasiado alto nunca deberia forzarse"
    assert validate_for_export(pocket, container) == []


# ==========================================================================
# POCKET-3: mas de un pocket residual independiente en el mismo modulo.
# ==========================================================================
def test_pocket_3_multiple_pockets_generated_and_reused():
    container = pocket_container()
    items = pocket_3_multiple_pockets()
    pocket, stats = pack_panels_module_pocket_reuse(items, container)

    assert stats.pockets_generated >= 2
    assert len(pocket.placed) == len(items)
    assert validate_for_export(pocket, container) == []
    assert _no_real_overlap(pocket.placed)


# ==========================================================================
# POCKET-4: la Zona Reservada recorta el pocket -nunca se cruza.
# ==========================================================================
def test_pocket_4_reserved_zone_is_never_invaded():
    container = pocket_container()
    items, zone = pocket_4_reserved_zone_intersects()
    pocket, stats = pack_panels_module_pocket_reuse(items, container, reserved_zones=[zone])

    for p in pocket.placed:
        overlaps_x = p.x < zone.x + zone.length and zone.x < p.x + p.dx
        overlaps_y = p.y < zone.y + zone.width and zone.y < p.y + p.dy
        assert not (overlaps_x and overlaps_y), f"{p.id} invade la zona reservada"
    assert validate_for_export(pocket, container, reserved_zones=[zone]) == []


# ==========================================================================
# POCKET-5 / POCKET-6: limite de cluster (Group/System) nunca se cruza,
# aunque geometricamente el item de otro cluster cabria perfecto.
# ==========================================================================
def test_pocket_5_group_boundary_never_crossed():
    container = pocket_container()
    items = pocket_5_group_boundary()
    pocket, stats = pack_panels_module_pocket_reuse(items, container, optimization_mode=OptimizationMode.KEEP_GROUPS)

    by_id = {p.id: p for p in pocket.placed}
    fita = next(p for pid, p in by_id.items() if pid.startswith("FITA"))
    fitb = next(p for pid, p in by_id.items() if pid.startswith("FITB"))
    out = next(p for pid, p in by_id.items() if pid.startswith("OUT"))
    # FITA (mismo grupo que OUT) debe terminar DENTRO del rango de
    # profundidad del modulo de OUT (reusado); FITB (otro grupo) nunca.
    assert out.x <= fita.x <= out.x + out.dx
    assert not (out.x <= fitb.x <= out.x + out.dx)
    assert validate_for_export(pocket, container) == []


def test_pocket_6_system_boundary_never_crossed():
    container = pocket_container()
    items = pocket_6_system_boundary()
    pocket, stats = pack_panels_module_pocket_reuse(items, container, optimization_mode=OptimizationMode.KEEP_SYSTEMS)

    by_id = {p.id: p for p in pocket.placed}
    fita = next(p for pid, p in by_id.items() if pid.startswith("FITA"))
    fitb = next(p for pid, p in by_id.items() if pid.startswith("FITB"))
    out = next(p for pid, p in by_id.items() if pid.startswith("OUT"))
    assert out.x <= fita.x <= out.x + out.dx
    assert not (out.x <= fitb.x <= out.x + out.dx)
    assert validate_for_export(pocket, container) == []


# ==========================================================================
# POCKET-7: Prioritize Delivery Sequence es MAS conservador que Best Space
# Utilization al reusar pockets (nunca introduce un bloqueo de Delivery
# Sequence evitable), verificado como propiedad sobre un dataset variado
# con Delivery Sequence mixta -seccion 13/20 del pedido.
# ==========================================================================
def test_pocket_7_prioritize_delivery_never_introduces_conflicts():
    container = pocket_7_container()
    items = pocket_7_delivery_dataset()

    best_space, best_stats = pack_panels_module_pocket_reuse(items, container, optimization_mode=OptimizationMode.BEST_SPACE)
    delivery, delivery_stats = pack_panels_module_pocket_reuse(items, container, optimization_mode=OptimizationMode.PRIORITIZE_DELIVERY)

    assert len(best_space.placed) == len(items)
    assert len(delivery.placed) == len(items)
    assert delivery_stats.pockets_filled < best_stats.pockets_filled, (
        "Prioritize Delivery Sequence deberia reusar pockets con mas cautela que Best Space Utilization en este dataset"
    )

    warnings = compute_operational_warnings(delivery.placed, container)
    assert not any(w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings), (
        "el resultado final bajo Prioritize Delivery Sequence nunca deberia tener un conflicto de entrega"
    )
    assert validate_for_export(delivery, container) == []
    assert validate_for_export(best_space, container) == []
    assert _no_real_overlap(delivery.placed)
    assert _no_real_overlap(best_space.placed)


# ==========================================================================
# POCKET-8: geometria fraccionaria -protege el fix de produccion real
# (redondeo del knapsack nunca genera colision fisica).
# ==========================================================================
def test_pocket_8_fractional_geometry_never_collides():
    container = pocket_container()
    items = pocket_8_fractional_geometry()
    pocket, stats = pack_panels_module_pocket_reuse(items, container)

    assert stats.pockets_filled >= 1
    assert _no_real_overlap(pocket.placed)
    assert validate_for_export(pocket, container) == []


# ==========================================================================
# POCKET-9: caso de aceptacion real (derivado del plan de 67 lineas que
# origino este trabajo) -el outlier D12 + companeros reales + 2 items
# reales que en produccion terminaban forzando un SEGUNDO modulo bajo
# PANEL_MODULE_V1; con reuso de pocket deben caber en la MISMA longitud que
# el modulo del outlier solo, cero longitud adicional.
# ==========================================================================
def test_pocket_9_real_d12_derived_reduces_used_length():
    container = pocket_9_container()
    items = pocket_9_real_d12_derived()

    v1, _ = pack_panels_module(items, container)
    pocket, stats = pack_panels_module_pocket_reuse(items, container)

    assert len(pocket.placed) == len(items) == len(v1.placed) == 9
    assert stats.pockets_filled >= 1
    used_v1 = used_bounding_length(v1.placed, container)
    used_pocket = used_bounding_length(pocket.placed, container)
    assert used_pocket < used_v1, "el reuso de pocket deberia necesitar MENOS longitud que dejar que M1/M2 abran su propio modulo"
    # La profundidad del propio outlier (4521.2mm) es el piso fisico -no
    # puede bajar mas que eso sin tocar el outlier, que Stage 2 nunca mueve.
    assert abs(used_pocket - 4521.2002) < 1.0
    assert validate_for_export(pocket, container) == []
    assert _no_real_overlap(pocket.placed)
    assert panel_height_order_violations(pocket.placed, container) == 0
    assert any(is_at_back_right_floor(p, container) for p in pocket.placed), "BACK_RIGHT_FLOOR debe seguir intacto"


# ==========================================================================
# Determinismo (seccion 23 del pedido): misma entrada -> mismas coordenadas.
# ==========================================================================
def test_pocket_reuse_is_deterministic():
    container = pocket_9_container()
    items = pocket_9_real_d12_derived()

    def snapshot(result):
        return [(p.id, round(p.x, 6), round(p.y, 6), round(p.z, 6), round(p.dx, 6), round(p.dy, 6), round(p.dz, 6)) for p in result.placed]

    r1, _ = pack_panels_module_pocket_reuse(items, container)
    r2, _ = pack_panels_module_pocket_reuse(items, container)
    assert snapshot(r1) == snapshot(r2)
