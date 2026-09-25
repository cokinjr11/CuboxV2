"""CUBOX 2.0 -- Keep Groups Together, Stage 3: Support-Aware Level 2 inside
Dynamic Sections (pedido "STAGE 3 -- SUPPORT-AWARE 3D LEVELS INSIDE DYNAMIC
SECTIONS, SECTION-FIRST, NOT GLOBAL-LAYER-FIRST").

SAFETY GATE (seccion 17 del pedido, auditado antes de escribir una sola
linea de candidato): geometry.py:check_stack_weight es DIRECTO, no
transitivo (confirmado leyendo el codigo -"Chequeo directo, no transitivo
(no seguimos apilamientos de varios niveles)", docstring textual de esa
funcion) -mismo hallazgo que la auditoria de soporte de la tarea de
integracion de Pocket Reuse. Con EXACTAMENTE 2 niveles (Level 1 = piso,
Level 2 = una capa encima) esta limitacion es INOFENSIVA por construccion:
no existe ningun Level 3 desde el cual una carga necesite propagarse A
TRAVES de un Level 2 hacia el Level 1 -el chequeo directo YA ES el chequeo
completo y fisicamente correcto para 2 niveles exactos. Por eso:

    MAX_LEVEL = 2

es una constante de seguridad EXPLICITA, nunca un numero mas alto sin
resolver antes la semantica de carga transitiva (seccion 17: 'If Level 3+
load propagation is unresolved... do NOT enable Level 3+').

Generacion de candidatos ACOTADA (seccion 7 del pedido, nunca busqueda XYZ
arbitraria): un candidato = (pieza de piso ya colocada como ancla, item
elegible del pool del Grupo activo, orientacion valida) -el candidato se
ancla en el propio (x, y) de la pieza ancla, a z = ancla.top_z (su
superficie real, nunca 'Nivel * altura arbitraria', seccion 4). El soporte
MULTI-PIEZA (seccion 6) sale GRATIS de este diseno: si el candidato (con
SU PROPIO dx/dy real, no necesariamente el mismo tamano que el ancla)
termina solapando a MAS de una pieza con el mismo top_z, geometry.py:
check_support ya suma el area de solape real de TODAS -nunca se
reimplementa esa suma aca, seccion 5: 'Reuse the existing canonical...
Do NOT create another support calculation.'"""

from dataclasses import dataclass, field

from app.core.geometry import TOL, Box, check_stack_weight, check_support, within_container
from app.core.packer import _expand_instances
from app.core.panel_module_solver import _build_placed_piece, _upright_orientations
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.models.schemas import ContainerSpec, PlacedPiece, WindowItem

MAX_LEVEL = 2
"""Compuerta de seguridad explicita -ver docstring del modulo. NUNCA subir
sin antes resolver semantica de carga transitiva en geometry.py."""

_REJECTION_ORIENTATION = "ORIENTATION"
_REJECTION_HEIGHT = "HEIGHT"
_REJECTION_OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
_REJECTION_RESERVED_ZONE = "RESERVED_ZONE"
_REJECTION_CLEARANCE = "CLEARANCE"
_REJECTION_COLLISION = "COLLISION"
_REJECTION_NON_STACKABLE_SUPPORT = "NON_STACKABLE_SUPPORT"
_REJECTION_INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
_REJECTION_STACK_WEIGHT = "STACK_WEIGHT"


@dataclass
class LevelReport:
    """Metrica reportable por Level (seccion 25 del pedido)."""

    level_id: int
    support_z: float
    items_by_group: dict[str, list[str]] = field(default_factory=dict)
    item_ids: list[str] = field(default_factory=list)
    supporter_ids: list[str] = field(default_factory=list)
    footprint_utilization_pct: float = 0.0
    max_top_z: float = 0.0
    candidates_evaluated: int = 0
    candidates_accepted: int = 0
    rejection_counts: dict[str, int] = field(default_factory=dict)


def _xy_overlap_area_boxes(a: Box, b: Box) -> float:
    ox = max(0.0, min(a.x + a.dx, b.x + b.dx) - max(a.x, b.x))
    oy = max(0.0, min(a.y + a.dy, b.y + b.dy) - max(a.y, b.y))
    return ox * oy


def _boxes_overlap(a: Box, b: Box, tol: float = TOL) -> bool:
    return (
        a.x < b.x + b.dx - tol and b.x < a.x + a.dx - tol
        and a.y < b.y + b.dy - tol and b.y < a.y + a.dy - tol
        and a.z < b.z + b.dz - tol and b.z < a.z + a.dz - tol
    )


def _piece_box(p: PlacedPiece) -> Box:
    return Box(
        id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz,
        stackable=p.stackable, max_stack_weight=p.max_stack_weight, item_type=p.item_type,
        tilt_angle=p.tilt_angle,
    )


def try_place_level_2(
    section_anchors: list[PlacedPiece],
    pool_items: list[WindowItem],
    all_placed: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone],
    clearance: float,
    group_key_for: dict,
) -> tuple[list[PlacedPiece], list[WindowItem], LevelReport]:
    """Intenta Level 2 (z>0, UNA capa) sobre `section_anchors` (las piezas
    de Level 1 de ESTA Section) usando `pool_items` (pool acotado, ya
    restringido por el llamador al Grupo activo -seccion 8/9 del pedido:
    'active Group has first right to upper space'). Devuelve (newly_
    placed, remaining_pool, LevelReport). Reusa check_support/check_
    stack_weight/within_container/zone_conflict_with_clearance CANONICOS
    sin cambios (seccion 5/18/19/20/21)."""
    all_placed_boxes = [_piece_box(p) for p in all_placed]
    weights_by_id = {p.id: p.weight for p in all_placed}
    reserved_ids = {p.id for p in all_placed}
    instances = _expand_instances(pool_items, reserved_ids)
    placed_flags = [False] * len(instances)

    report = LevelReport(level_id=2, support_z=0.0)
    newly_placed: list[PlacedPiece] = []

    anchors_sorted = sorted(section_anchors, key=lambda p: (p.x, p.y, p.id))
    max_top_z = 0.0

    # Seccion 10/11 del pedido de correccion Stage 3.2: "Upper packing is
    # not one item only... recompute remaining usable upper geometry...
    # Continue until no more valid Level-2 placements exist" -- bug real
    # encontrado auditando el test de aceptacion 23 (plataforma grande,
    # 4 items chicos: el diseno anterior probaba SOLO 1 candidato por
    # ancla y pasaba a la siguiente, aunque la MISMA ancla tuviera lugar
    # de sobra para varios items mas). Cada ancla ahora se ofrece en un
    # loop acotado: coloca un item, avanza un cursor Y DENTRO del propio
    # ancla (tiling determinista a lo largo de su propio ancho), y sigue
    # intentando mientras queden candidatos del pool Y espacio en esa
    # ancla -recien entonces pasa a la siguiente ancla. Acotado por
    # construccion: como mucho len(pool) intentos por ancla.
    for anchor in anchors_sorted:
        anchor_top_z = anchor.z + anchor.dz
        y_cursor = anchor.y
        y_limit = anchor.y + anchor.dy

        while y_cursor < y_limit - TOL:
            next_head = next((k for k in range(len(instances)) if not placed_flags[k]), None)
            if next_head is None:
                break
            inst = instances[next_head]
            w = inst.source
            orientations = _upright_orientations(w)
            placed_here = False
            for o in orientations:
                report.candidates_evaluated += 1
                if o.dz + anchor_top_z > container.height + TOL:
                    report.rejection_counts[_REJECTION_HEIGHT] = report.rejection_counts.get(_REJECTION_HEIGHT, 0) + 1
                    continue
                box = Box(
                    id=inst.instance_id, x=anchor.x, y=y_cursor, z=anchor_top_z, dx=o.dx, dy=o.dy, dz=o.dz,
                    stackable=w.stackable, max_stack_weight=w.max_stack_weight, item_type=w.item_type,
                    tilt_angle=o.tilt_angle, tilt_axis=o.tilt_axis,
                )
                if not within_container(box, container.length, container.width, container.height, TOL):
                    report.rejection_counts[_REJECTION_OUT_OF_BOUNDS] = report.rejection_counts.get(_REJECTION_OUT_OF_BOUNDS, 0) + 1
                    continue
                if zone_conflict_with_clearance(box, reserved_zones, clearance) is not None:
                    report.rejection_counts[_REJECTION_RESERVED_ZONE] = report.rejection_counts.get(_REJECTION_RESERVED_ZONE, 0) + 1
                    continue
                if any(_boxes_overlap(box, ob) for ob in all_placed_boxes):
                    report.rejection_counts[_REJECTION_COLLISION] = report.rejection_counts.get(_REJECTION_COLLISION, 0) + 1
                    continue
                support_ok, support_reason = check_support(box, all_placed_boxes)
                if not support_ok:
                    key = _REJECTION_NON_STACKABLE_SUPPORT if "no apilable" in support_reason or "inclinada" in support_reason else _REJECTION_INSUFFICIENT_SUPPORT
                    report.rejection_counts[key] = report.rejection_counts.get(key, 0) + 1
                    continue
                weight_ok, _weight_reason = check_stack_weight(box, w.weight, all_placed_boxes, weights_by_id)
                if not weight_ok:
                    report.rejection_counts[_REJECTION_STACK_WEIGHT] = report.rejection_counts.get(_REJECTION_STACK_WEIGHT, 0) + 1
                    continue

                piece = _build_placed_piece(inst, w, box, o.label)
                newly_placed.append(piece)
                all_placed_boxes.append(box)
                weights_by_id[piece.id] = piece.weight
                placed_flags[next_head] = True
                placed_here = True
                max_top_z = max(max_top_z, box.z + box.dz)
                report.candidates_accepted += 1
                group_key = group_key_for(w)
                report.items_by_group.setdefault(group_key, []).append(piece.id)
                report.item_ids.append(piece.id)
                # Soporte real (seccion 6 del pedido: "Do not double-count
                # overlapping support" -- pero SI hay que registrar cada
                # pieza que realmente lo toca): todas las piezas tocando
                # el mismo top_z con solape XY real, no solo el ancla que
                # disparo este intento -si el candidato termino abarcando
                # VARIAS piezas coplanares (soporte multi-pieza, gratis
                # por diseno -ver docstring del modulo), todas deben
                # figurar como supporter_ids. Nunca se acota el ancho del
                # candidato al propio ancla (seccion 6): un candidato
                # puede legitimamente abarcar mas de una pieza de piso
                # coplanar; el cursor Y solo decide DONDE arranca el
                # PROXIMO intento dentro de esta ancla, nunca el tamano
                # maximo permitido del candidato actual.
                for other in all_placed_boxes[:-1]:
                    if abs(other.top_z - box.z) < TOL and _xy_overlap_area_boxes(box, other) > TOL:
                        if other.id not in report.supporter_ids:
                            report.supporter_ids.append(other.id)
                y_cursor += o.dy
                break

            if not placed_here:
                break

    remaining_pool = [instances[k].source for k in range(len(instances)) if not placed_flags[k]]
    report.support_z = anchors_sorted[0].z + anchors_sorted[0].dz if anchors_sorted else 0.0
    report.max_top_z = max_top_z
    return newly_placed, remaining_pool, report
