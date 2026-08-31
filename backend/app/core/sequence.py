"""Orden de carga y descarga (secciones 17-21 de V2, 40-45 de V3, Anchored
Loading Sequence post-V4).

Convencion: la puerta del contenedor esta en x=0, el fondo en x=length. Z=0 es
el piso. Y (ancho) no tenia una convencion de izquierda/derecha establecida en
el resto del proyecto -se reusa el mismo split que ya usa compute_metrics para
weight balance: RIGHT = y grande (cerca de width), LEFT = y chico (cerca de
0).

Ademas del orden espacial, ninguna pieza puede cargarse antes que lo que la
soporta fisicamente, ni descargarse antes que lo que tiene apoyado encima
(dependencia). Para la CARGA, el orden ya no es un sort de una sola pasada:
es un algoritmo "ancla y crece" (Anchored Loading Sequence) que arranca en una
esquina fija (fondo + pared lateral elegida, BACK_RIGHT por defecto) y en cada
paso prefiere la pieza mas anclada a una referencia fisica real (piso, pared
del fondo, pared lateral, o una pieza ya cargada) -no solo la geometricamente
"siguiente". Para la DESCARGA se mantiene el orden espacial + dependencia
invertida de siempre (no hace falta un ancla: se descarga por accesibilidad,
no por punto de partida).

DeliverySequence (opcional) es una preferencia SOFT para la descarga: las
piezas con numero mas bajo (salen primero) tienden a listarse antes,
respetando siempre la dependencia de soporte.
"""

from app.core.geometry import TOL, Box, boxes_too_close
from app.models.schemas import ContainerSpec, LoadingAnchor, PlacedPiece

_NO_DELIVERY_SEQUENCE = 999_999

MAX_STEP_SIZE_AUTO = 6
"""Tamano maximo de un paso automatico. Un modulo fisico contiguo puede tener
muchas piezas; sin este tope un solo paso quedaria gigante e inutil para un
operador. No busca ser optimo, solo un limite razonable y determinista."""

ANCHOR_TOLERANCE_MM = 50
"""Distancia maxima para considerar que una pieza "toca" la pared del fondo,
la pared lateral elegida, o una pieza ya cargada -evita exigir contacto
matematico exacto (clearance, tolerancias, floating point)."""


def _boxes(placed: list[PlacedPiece]) -> dict[str, Box]:
    return {p.id: Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable) for p in placed}


def _xy_overlap_area(a: Box, b: Box) -> float:
    ox = max(0.0, min(a.x + a.dx, b.x + b.dx) - max(a.x, b.x))
    oy = max(0.0, min(a.y + a.dy, b.y + b.dy) - max(a.y, b.y))
    return ox * oy


def _direct_supporters(placed: list[PlacedPiece], boxes: dict[str, Box]) -> dict[str, list[str]]:
    """id -> ids de las piezas que la soportan directamente (tocan su z y
    solapan en XY), igual criterio que geometry.check_support. Este es el
    Load Dependency Graph: la unica fuente de verdad de "que debe cargarse
    antes que que" en todo el modulo."""
    supporters: dict[str, list[str]] = {p.id: [] for p in placed}
    for p in placed:
        if p.z <= TOL:
            continue
        pbox = boxes[p.id]
        for other in placed:
            if other.id == p.id:
                continue
            obox = boxes[other.id]
            if abs(obox.top_z - pbox.z) < TOL and _xy_overlap_area(pbox, obox) > TOL:
                supporters[p.id].append(other.id)
    return supporters


def _topological_order(piece_ids_in_base_order: list[str], deps: dict[str, list[str]]) -> list[str]:
    """Devuelve los ids en un orden que respeta `deps` (cada id sale despues
    de todo lo que tiene en deps[id]), usando el orden base como desempate
    entre piezas que ya estan listas para salir. Usado por compute_unload_
    sequence, que no necesita logica de ancla (se descarga por accesibilidad,
    no desde un punto de partida fijo)."""
    order_index = {pid: i for i, pid in enumerate(piece_ids_in_base_order)}
    done: set[str] = set()
    result: list[str] = []
    remaining = list(piece_ids_in_base_order)

    while remaining:
        remaining.sort(key=lambda pid: order_index[pid])
        next_id = next((pid for pid in remaining if all(d in done for d in deps.get(pid, []))), None)
        if next_id is None:
            # No deberia ocurrir (el soporte fisico no puede ser circular),
            # pero por seguridad se vuelca el resto en su orden base en vez
            # de trabarse.
            result.extend(remaining)
            break
        result.append(next_id)
        done.add(next_id)
        remaining.remove(next_id)

    return result


def _touches_back_wall(p: PlacedPiece, container: ContainerSpec, tol: float) -> bool:
    return (container.length - (p.x + p.dx)) <= tol


def _touches_side_wall(p: PlacedPiece, container: ContainerSpec, anchor: LoadingAnchor, tol: float) -> bool:
    if anchor == LoadingAnchor.BACK_RIGHT:
        return (container.width - (p.y + p.dy)) <= tol
    return p.y <= tol


def _has_reference(pid: str, loaded: set[str], deps: dict[str, list[str]], boxes: dict[str, Box], tol: float) -> bool:
    """True si `pid` descansa sobre algo ya cargado, o es lateralmente
    adyacente (dentro de `tol`) a algo ya cargado -esto es lo que hace que la
    secuencia "crezca" desde lo ya construido en vez de saltar a otra zona."""
    if any(s in loaded for s in deps.get(pid, [])):
        return True
    pbox = boxes[pid]
    return any(boxes_too_close(pbox, boxes[other], tol) for other in loaded)


def _anchored_load_order_with_warnings(
    placed: list[PlacedPiece],
    container: ContainerSpec,
    anchor: LoadingAnchor,
    deps: dict[str, list[str]],
    tol: float = ANCHOR_TOLERANCE_MM,
) -> tuple[list[str], list[str]]:
    """Anchored Loading Sequence: greedy piece-by-piece. En cada paso, entre
    las piezas cuyas dependencias de soporte ya estan satisfechas (el "ready
    set"), elige la que mejor cumple esta prioridad estricta (tupla de
    comparacion, igual estilo que los sort keys de este archivo):

    1. piso antes que apilado (ya lo garantiza el Load Dependency Graph, se
       refuerza como desempate)
    2. toca la pared del fondo
    3. toca la pared lateral elegida (BACK_RIGHT/BACK_LEFT)
    4. es adyacente a una pieza YA elegida en la secuencia (crece desde lo
       construido en vez de saltar de zona)
    5. mas al fondo primero (-x)
    6. z, y, id como desempate deterministico

    La primera pieza (con el "ya cargado" vacio) cae naturalmente en la
    esquina fondo+lateral elegida gracias a los criterios 2-3, sin necesitar
    un caso especial. Como solo se elige del "ready set", el resultado ya es
    topologicamente valido.

    Devuelve (orden, warnings): una advertencia por cada pieza que, en el
    momento de cargarse, no tenia NINGUNA referencia fisica (piso/fondo/
    lateral/pieza ya cargada) -ver seccion 19, OPERATIONAL_LOADABILITY_WARNING.
    """
    by_id = {p.id: p for p in placed}
    boxes = _boxes(placed)
    loaded: set[str] = set()
    order: list[str] = []
    warnings: list[str] = []
    remaining = set(by_id)

    def score(pid: str) -> tuple[int, int, int, int, float, float, float, str]:
        p = by_id[pid]
        floor = 0 if p.z <= TOL else 1
        back = 0 if _touches_back_wall(p, container, tol) else 1
        side = 0 if _touches_side_wall(p, container, anchor, tol) else 1
        adjacent = 0 if _has_reference(pid, loaded, deps, boxes, tol) else 1
        return (floor, back, side, adjacent, -p.x, p.z, p.y, pid)

    while remaining:
        ready = [pid for pid in remaining if all(d in loaded for d in deps.get(pid, []))]
        if not ready:
            # No deberia ocurrir (el soporte fisico no puede ser circular),
            # pero por seguridad se vuelca el resto en su orden base en vez
            # de trabarse.
            order.extend(sorted(remaining, key=lambda pid: (-by_id[pid].x, by_id[pid].z, by_id[pid].y, pid)))
            break

        best = min(ready, key=score)
        _floor, back, side, adjacent, *_rest = score(best)
        # El piso NO cuenta como referencia de posicionamiento por si solo
        # (seccion 10 del pedido): una pieza puede estar bien apoyada y
        # seguir siendo mala primera pieza si esta a 5000mm de la pared y
        # 1300mm del lateral sin ninguna otra referencia. Solo pared del
        # fondo/lateral/pieza ya cargada cuentan aca.
        if back == 1 and side == 1 and adjacent == 1:
            warnings.append(
                f"{best}: sin referencia fisica (pared del fondo/pared lateral/pieza ya cargada) "
                "en el punto en que la secuencia la carga."
            )
        order.append(best)
        loaded.add(best)
        remaining.discard(best)

    return order, warnings


def _anchored_load_order(
    placed: list[PlacedPiece], container: ContainerSpec, anchor: LoadingAnchor, deps: dict[str, list[str]], tol: float = ANCHOR_TOLERANCE_MM
) -> list[str]:
    order, _ = _anchored_load_order_with_warnings(placed, container, anchor, deps, tol)
    return order


def detect_operational_warnings(
    placed: list[PlacedPiece],
    container: ContainerSpec,
    anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT,
    tol: float = ANCHOR_TOLERANCE_MM,
) -> list[str]:
    """OPERATIONAL_LOADABILITY_WARNING (seccion 19): detecta piezas que la
    Anchored Loading Sequence tuvo que cargar sin ninguna referencia fisica
    real -el algoritmo ya evita esto siempre que exista una alternativa; esto
    solo marca el caso residual donde no la habia."""
    if not placed:
        return []
    boxes = _boxes(placed)
    deps = _direct_supporters(placed, boxes)
    _, warnings = _anchored_load_order_with_warnings(placed, container, anchor, deps, tol)
    return warnings


def _group_into_modules(
    order: list[str], deps: dict[str, list[str]], boxes: dict[str, Box], tol: float, max_size: int = MAX_STEP_SIZE_AUTO
) -> list[list[str]]:
    """Agrupa un orden ya resuelto (topologicamente valido) en modulos/steps:
    una pieza se suma al step actual solo si es adyacente (dentro de `tol`) a
    algo que ya esta en ese step, o es su dependencia directa -si no, cierra
    el step y abre uno nuevo. `max_size` sigue actuando como techo duro para
    que un modulo fisico gigante no se vuelva un solo step inmanejable. Este
    reemplaza el mecanismo anterior de "wave topologica completa + division
    por contador fijo": ahora el limite de un step es conectividad fisica, no
    un numero arbitrario."""
    if not order:
        return []
    steps: list[list[str]] = []
    current: list[str] = [order[0]]
    current_set = {order[0]}

    for pid in order[1:]:
        connects = any(other in deps.get(pid, []) for other in current_set) or any(
            boxes_too_close(boxes[pid], boxes[other], tol) for other in current_set
        )
        if connects and len(current) < max_size:
            current.append(pid)
            current_set.add(pid)
        else:
            steps.append(current)
            current = [pid]
            current_set = {pid}

    steps.append(current)
    return steps


def compute_load_steps(
    placed: list[PlacedPiece], container: ContainerSpec, anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT
) -> list[list[str]]:
    """Agrupacion automatica en pasos: Final Layout -> Operational Load
    Sequence (Anchored) -> modulos fisicos -> Step Groups. Los steps se
    generan DESPUES de resolver la secuencia operativa, no dividiendo el
    layout final en lotes arbitrarios."""
    if not placed:
        return []
    boxes = _boxes(placed)
    deps = _direct_supporters(placed, boxes)
    order = _anchored_load_order(placed, container, anchor, deps)
    return _group_into_modules(order, deps, boxes, ANCHOR_TOLERANCE_MM)


def compute_unload_steps(placed: list[PlacedPiece]) -> list[list[str]]:
    """Igual idea que compute_load_steps pero para descarga: se agrupa en
    modulos el orden de compute_unload_sequence (que ya respeta accesibilidad
    + dependencia invertida) -la descarga no necesita esquina de inicio."""
    if not placed:
        return []
    order = compute_unload_sequence(placed)
    boxes = _boxes(placed)
    supporters = _direct_supporters(placed, boxes)
    supported_by: dict[str, list[str]] = {p.id: [] for p in placed}
    for supported_id, its_supporters in supporters.items():
        for supporter_id in its_supporters:
            supported_by[supporter_id].append(supported_id)
    return _group_into_modules(order, supported_by, boxes, ANCHOR_TOLERANCE_MM)


def chunk_sequence(sequence: list[str], size: int) -> list[list[str]]:
    """Modo Manual de "Pieces per Step": division fija en lotes de `size`,
    sin razonar sobre dependencias -a proposito, es el chunking simple que se
    contrasta contra el automatico."""
    if size <= 0:
        raise ValueError("size debe ser mayor a 0")
    return [sequence[i : i + size] for i in range(0, len(sequence), size)]


def compute_load_sequence(
    placed: list[PlacedPiece], container: ContainerSpec, anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT
) -> list[str]:
    if not placed:
        return []
    boxes = _boxes(placed)
    deps = _direct_supporters(placed, boxes)
    return _anchored_load_order(placed, container, anchor, deps)


def compute_unload_sequence(placed: list[PlacedPiece]) -> list[str]:
    if not placed:
        return []

    def base_key(p: PlacedPiece):
        delivery = p.delivery_sequence if p.delivery_sequence is not None else _NO_DELIVERY_SEQUENCE
        return (delivery, p.x, -p.z, p.y, p.id)

    base_order = [p.id for p in sorted(placed, key=base_key)]
    boxes = _boxes(placed)
    supporters = _direct_supporters(placed, boxes)
    # Invertido: una pieza solo puede descargarse despues de todo lo que
    # tiene apoyado encima (lo que ella misma soporta).
    supported_by: dict[str, list[str]] = {p.id: [] for p in placed}
    for supported_id, its_supporters in supporters.items():
        for supporter_id in its_supporters:
            supported_by[supporter_id].append(supported_id)
    return _topological_order(base_order, supported_by)
