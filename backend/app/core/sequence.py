"""Orden de carga y descarga (secciones 17-21 de V2, 40-45 de V3, Anchored
Loading Sequence post-V4, refinada en CUBOX 2.0 Fase 5.1).

Fase 5.1: el ranking greedy de la carga ya NO trata "piso antes que apilado"
como regla global -eso causaba cargar todo el piso del container antes de
volver a completar columnas apiladas (secuencia geometricamente valida pero
operativamente fragmentada, ver seccion 1-4 del pedido). Ahora el desempate
prioriza seguir construyendo el modulo/columna activa (incluida la
continuacion vertical inmediata) por sobre saltar a otro piso lejano. Ver el
docstring de `_anchored_load_order_with_warnings` para el mapeo campo por
campo contra la seccion 6 del pedido.

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
from app.models.schemas import (
    ContainerSpec,
    LoadingAnchor,
    LoadingOpeningType,
    OperationalWarning,
    OperationalWarningType,
    PlacedPiece,
    UnloadDeliverySection,
    UnloadStepDeliveryInfo,
)

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


def _yz_overlap_area(a: Box, b: Box) -> float:
    """Analogo a `_xy_overlap_area` pero en el plano perpendicular al eje de
    extraccion (X): usado por `compute_blocking_pairs` para decidir si una
    pieza realmente le tapa el camino a otra, no solo si esta "mas cerca" de
    la puerta."""
    oy = max(0.0, min(a.y + a.dy, b.y + b.dy) - max(a.y, b.y))
    oz = max(0.0, min(a.top_z, b.top_z) - max(a.z, b.z))
    return oy * oz


def _all_blocking_pairs(placed: list[PlacedPiece], boxes: dict[str, Box]) -> list[tuple[str, str]]:
    """Todo par (blocker_id, blocked_id) donde `blocker` esta COMPLETAMENTE
    entre la puerta y `blocked` a lo largo de X (con tolerancia de punto
    flotante, no la tolerancia de ANCHOR_TOLERANCE_MM -eso permitiria falsos
    "empate" en ambas direcciones entre piezas casi pegadas) y ambas se
    solapan en el plano Y-Z mas alla de esa misma tolerancia. Incluye pares
    TRANSITIVOS (si A bloquea a B y B bloquea a C, tambien aparece (A, C)) a
    proposito -`compute_blocking_pairs` es quien filtra esto a solo el
    bloqueador inmediato; este helper es el paso previo, no la API publica."""
    pairs: list[tuple[str, str]] = []
    for a in placed:
        abox = boxes[a.id]
        for b in placed:
            if a.id == b.id:
                continue
            bbox = boxes[b.id]
            if abox.max_x <= bbox.x + TOL and _yz_overlap_area(abox, bbox) > TOL:
                pairs.append((a.id, b.id))
    return pairs


def compute_blocking_pairs(placed: list[PlacedPiece], boxes: dict[str, Box] | None = None) -> list[tuple[str, str]]:
    """Fase 6A, seccion 9-11: aproximacion geometrica mas chica y robusta de
    "bloqueo" para una apertura de carga trasera (puerta en x=0, ver
    convencion de coordenadas del docstring del modulo). Devuelve SOLO pares
    DIRECTOS (blocker_id, blocked_id) -el obstaculo inmediato entre `blocked`
    y la puerta, no cualquier pieza que este "en el camino" en algun punto de
    la cadena.

    Correccion post-Fase 6A: la primera version devolvia TODOS los pares que
    cumplian el test geometrico, incluidos los transitivos (si A bloquea a B
    y B bloquea a C, tambien devolvia A->C). En un Load Space con columnas de
    profundidad de N piezas eso genera O(N^2) pares redundantes por columna
    en vez de las N-1 dependencias reales -exactamente el mismo error que
    _direct_supporters evita para el apoyo vertical (solo registra quien
    toca directamente, no toda la pila de abajo). El resultado observado en
    un plan real de ~300 cajas fueron miles de DELIVERY_SEQUENCE_CONFLICT
    (uno por cada par transitivo con Delivery Sequence distinta) en vez de
    uno por relacion de bloqueo real.

    Un blocker A de un blocked B se considera DIRECTO si ningun otro blocker
    de B (dentro del mismo conjunto de candidatos) esta a su vez bloqueado
    por A -es decir, no hay otra pieza C entre A y B que ya bloquee a B por
    si sola. Esto es el mismo patron "quedarse solo con el mas cercano" que
    ya usa _direct_supporters, aplicado al eje X en vez de Z.

    Deliberadamente NO modela maniobra lateral usando el clearance disponible
    (no hay path planning continuo, seccion 10 del pedido): cualquier
    solapamiento Y-Z mayor a la tolerancia de punto flotante se trata como
    bloqueo total. El packer ya separa piezas no apiladas por al menos el
    clearance configurado, asi que un bloqueo detectado aca siempre
    corresponde a una obstruccion fisica real del corredor de extraccion en
    linea recta hacia x=0 -ver limitaciones documentadas en el reporte de
    Fase 6A."""
    boxes = boxes if boxes is not None else _boxes(placed)
    all_pairs = _all_blocking_pairs(placed, boxes)

    blockers_of: dict[str, set[str]] = {}
    for blocker_id, blocked_id in all_pairs:
        blockers_of.setdefault(blocked_id, set()).add(blocker_id)
    blocks_set = set(all_pairs)

    direct: list[tuple[str, str]] = []
    for blocked_id, blockers in blockers_of.items():
        for a in blockers:
            # A es directo si ningun otro candidato C (tambien bloqueador de
            # blocked_id) esta, a su vez, siendo bloqueado por A -si lo
            # estuviera, C es el obstaculo inmediato y A queda "detras" de C.
            if not any((a, c) in blocks_set for c in blockers if c != a):
                direct.append((a, blocked_id))
    return direct


def compute_load_dependencies(placed: list[PlacedPiece], boxes: dict[str, Box] | None = None) -> dict[str, list[str]]:
    """Load Dependency Graph completo (seccion 14): soporte fisico directo
    (`_direct_supporters`, sin cambios) UNIDO al bloqueo lateral -una pieza
    que le tapa el acceso a otra (esta mas cerca de la puerta y se solapa en
    Y-Z) debe cargarse DESPUES de la que bloquea: si se coloca primero, corta
    el acceso fisico a donde tiene que ir la pieza bloqueada (se carga de
    atras hacia adelante, no al reves)."""
    boxes = boxes if boxes is not None else _boxes(placed)
    deps: dict[str, list[str]] = {pid: list(v) for pid, v in _direct_supporters(placed, boxes).items()}
    for blocker_id, blocked_id in compute_blocking_pairs(placed, boxes):
        bucket = deps.setdefault(blocker_id, [])
        if blocked_id not in bucket:
            bucket.append(blocked_id)
    return deps


def compute_unload_dependencies(placed: list[PlacedPiece], boxes: dict[str, Box] | None = None) -> dict[str, list[str]]:
    """Unload Dependency Graph completo (seccion 15): inverso del de soporte
    (lo apoyado encima sale antes que su base, igual que siempre) UNIDO al
    inverso del bloqueo lateral -lo que bloquea el acceso sale antes que lo
    bloqueado. Esta es tambien la fuente de `PackingResult.blocked_by`
    (Piece Inspector, seccion 32): deps[pid] ya es exactamente "que debe
    salir antes que pid"."""
    boxes = boxes if boxes is not None else _boxes(placed)
    supporters = _direct_supporters(placed, boxes)
    deps: dict[str, list[str]] = {p.id: [] for p in placed}
    for supported_id, its_supporters in supporters.items():
        for supporter_id in its_supporters:
            deps[supporter_id].append(supported_id)
    for blocker_id, blocked_id in compute_blocking_pairs(placed, boxes):
        if blocker_id not in deps[blocked_id]:
            deps[blocked_id].append(blocker_id)
    return deps


def detect_sequence_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    """Fase 6A, seccion 16: DFS con coloreo (blanco/gris/negro) sobre un grafo
    de dependencias ya armado (carga o descarga) -devuelve los ids que forman
    el ciclo (en orden) o None si es un DAG. Con geometria valida (packer/
    manual_move ya evitan colisiones) esto no deberia disparar nunca, pero se
    detecta explicitamente en vez de confiar en el fallback silencioso de
    `_topological_order`/`_anchored_load_order_with_warnings`, que igual
    sigue funcionando como red de seguridad para no trabarse."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {pid: WHITE for pid in deps}
    stack: list[str] = []

    def visit(pid: str) -> list[str] | None:
        color[pid] = GRAY
        stack.append(pid)
        for dep in deps.get(pid, []):
            if color.get(dep, WHITE) == GRAY:
                return stack[stack.index(dep):] + [dep]
            if color.get(dep, WHITE) == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[pid] = BLACK
        return None

    for pid in deps:
        if color[pid] == WHITE:
            found = visit(pid)
            if found:
                return found
    return None


def _delivery_conflict(
    first_id: str,
    trapped_id: str,
    by_id: dict[str, PlacedPiece],
    warning_type: OperationalWarningType,
    verb: str,
) -> OperationalWarning | None:
    """`first_id` debe descargarse antes que `trapped_id` por una dependencia
    fisica dura (bloqueo lateral o soporte). Si ambas tienen Delivery
    Sequence definida y la de `first_id` es MAS TARDIA (numero mayor) que la
    de `trapped_id`, `trapped_id` queria salir antes pero fisicamente no
    puede -seccion 17 del pedido."""
    first, trapped = by_id[first_id], by_id[trapped_id]
    if first.delivery_sequence is None or trapped.delivery_sequence is None:
        return None
    if first.delivery_sequence <= trapped.delivery_sequence:
        return None
    return OperationalWarning(
        type=warning_type,
        message=(
            f"{trapped_id} is scheduled for Delivery Sequence {trapped.delivery_sequence} but is {verb} "
            f"{first_id} (Delivery Sequence {first.delivery_sequence})."
        ),
        item_id=trapped_id,
        blocking_item_id=first_id,
        requested_delivery_sequence=trapped.delivery_sequence,
        blocking_delivery_sequence=first.delivery_sequence,
    )


def compute_operational_warnings(
    placed: list[PlacedPiece],
    container: ContainerSpec,
    anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT,
    tol: float = ANCHOR_TOLERANCE_MM,
) -> list[OperationalWarning]:
    """Fase 6A: junta TODAS las warnings operacionales estructuradas sobre el
    layout final -reutiliza `detect_operational_warnings` para la parte de
    loadability (sin duplicar esa logica) y agrega Delivery/Stacking Sequence
    conflicts, deteccion de ciclos y la limitacion de Loading Opening Type."""
    if not placed:
        return []

    warnings: list[OperationalWarning] = [
        OperationalWarning(
            type=OperationalWarningType.OPERATIONAL_LOADABILITY_WARNING,
            message=msg,
            item_id=msg.split(":", 1)[0],
        )
        for msg in detect_operational_warnings(placed, container, anchor, tol)
    ]

    boxes = _boxes(placed)
    by_id = {p.id: p for p in placed}
    supporters = _direct_supporters(placed, boxes)
    blocking_pairs = compute_blocking_pairs(placed, boxes)

    for blocker_id, blocked_id in blocking_pairs:
        conflict = _delivery_conflict(
            blocker_id, blocked_id, by_id, OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT, "blocked by"
        )
        if conflict:
            warnings.append(conflict)

    for top_id, support_ids in supporters.items():
        for support_id in support_ids:
            conflict = _delivery_conflict(
                top_id, support_id, by_id, OperationalWarningType.STACKING_SEQUENCE_CONFLICT, "trapped under"
            )
            if conflict:
                warnings.append(conflict)

    load_deps = compute_load_dependencies(placed, boxes)
    unload_deps = compute_unload_dependencies(placed, boxes)
    cycle = detect_sequence_cycle(unload_deps) or detect_sequence_cycle(load_deps)
    if cycle:
        warnings.append(
            OperationalWarning(
                type=OperationalWarningType.SEQUENCE_CYCLE,
                message=f"Operational sequence conflict: {', '.join(cycle)} create incompatible dependencies.",
            )
        )

    opening_type = container.loading_opening_type
    if opening_type is not None and opening_type != LoadingOpeningType.REAR:
        warnings.append(
            OperationalWarning(
                type=OperationalWarningType.OPENING_CONFIGURATION_LIMITATION,
                message=(
                    f"Loading Opening Type '{opening_type.value}' is not fully modeled yet -sequence and "
                    "accessibility are computed assuming rear access (door at x=0)."
                ),
            )
        )

    return warnings


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


# BACK_RIGHT_FLOOR anchor audit (seccion 2 del pedido): invariante fisico
# explicito y reutilizable -"as seen from the loading door looking into the
# load space: BACK/DEEPEST available position, RIGHT SIDE WALL, FLOOR" en
# coordenadas backend exactas (ver convencion documentada al inicio de este
# modulo): x=0 puerta, x=length fondo; y grande=derecha, y chico=izquierda;
# z=0 piso. Reusa _touches_back_wall/_touches_side_wall(anchor=BACK_RIGHT)
# -nunca una segunda definicion de "toca la pared"- para que este invariante
# jamas se desincronice del que ya usa _anchored_load_order_with_warnings
# para anclar la Loading Sequence. `tol` default deliberadamente MAS
# ESTRICTO que ANCHOR_TOLERANCE_MM (50mm, pensado para el desempate de
# secuencia, "suficientemente cerca") -esta funcion se usa para afirmar un
# hecho geometrico exacto (tests de aceptacion), no para desempatar.
def is_at_back_right_floor(p: PlacedPiece, container: ContainerSpec, tol: float = TOL) -> bool:
    return (
        _touches_back_wall(p, container, tol)
        and _touches_side_wall(p, container, LoadingAnchor.BACK_RIGHT, tol)
        and p.z <= tol
    )


def _has_reference(pid: str, loaded: set[str], deps: dict[str, list[str]], boxes: dict[str, Box], tol: float) -> bool:
    """True si `pid` descansa sobre algo ya cargado, o es lateralmente
    adyacente (dentro de `tol`) a algo ya cargado -esto es lo que hace que la
    secuencia "crezca" desde lo ya construido en vez de saltar a otra zona."""
    if any(s in loaded for s in deps.get(pid, [])):
        return True
    pbox = boxes[pid]
    return any(boxes_too_close(pbox, boxes[other], tol) for other in loaded)


def _lateral_progress_key(p: PlacedPiece, anchor: LoadingAnchor) -> float:
    """Progresion lateral continua (seccion 9-10 de Fase 5.1): RIGHT = y
    grande, LEFT = y chico (verificado contra packer.py:left_weight/
    right_weight). BACK_RIGHT barre de derecha a izquierda -> se prefiere y
    mas grande primero, de ahi el signo invertido. BACK_LEFT es simetrico."""
    return -p.y if anchor == LoadingAnchor.BACK_RIGHT else p.y


def _anchored_load_order_with_warnings(
    placed: list[PlacedPiece],
    container: ContainerSpec,
    anchor: LoadingAnchor,
    deps: dict[str, list[str]],
    tol: float = ANCHOR_TOLERANCE_MM,
) -> tuple[list[str], list[str]]:
    """Anchored Loading Sequence (Fase 5.1): greedy piece-by-piece. En cada
    paso, entre las piezas cuyas dependencias de soporte ya estan satisfechas
    (el "ready set"), elige la que mejor cumple esta prioridad estricta
    (tupla de comparacion, cada campo mapea a un item de la seccion 6 del
    pedido de Fase 5.1 -documentado explicitamente para no dejar "constantes
    magicas" sin explicar):

    1. `continues_last_column` -sigue verticalmente a la ULTIMA pieza
       cargada (se apoya directo en ella). Esto es una especializacion mas
       fuerte que el item 1 generico del pedido ("mismo modulo activo"):
       tiene que ganarle incluso al desempate piso-antes-que-apilado (item 4)
       para cumplir el ejemplo de la seccion 10 (R1->R2->R3 completo antes
       que un piso lejano L1) -una lectura literal del orden numerado del
       pedido (floor en el item 4, antes del item 5 "seguir hacia arriba")
       rompe ese ejemplo, asi que este campo se resuelve primero a proposito.
    2. `adjacent` -item 1 del pedido: toca (por soporte o cercania lateral)
       cualquier pieza ya cargada, no solo la ultima. Hace que la secuencia
       "crezca" desde lo construido en vez de saltar de zona.
    3. `back_depth` -item 2: fondo antes que frente -distancia real a la
       pared del fondo (container.length - (x+dx), 0 = tocandola), no el
       `x` crudo de la pieza (dos piezas de distinto grosor pueden tocar la
       misma pared del fondo con un `x` muy distinto). Tambien cubre el
       item 7 ("avanzar hacia la puerta solo al final") porque es el mismo
       eje: una vez que este campo desempata, un segundo campo identico en
       el mismo eje nunca podria desempatar nada mas.
    4. `side` -item 3: toca la pared lateral elegida (BACK_RIGHT/BACK_LEFT).
       Principalmente decide la primera pieza de la secuencia.
    5. `floor` -item 4: piso antes que apilado, como desempate GENERICO
       (entre piezas sin relacion de columna activa) -no como regla global.
    6. `z` ascendente -item 5: entre candidatos ya "adjacent", seguir
       construyendo hacia arriba antes que preferir otra cosa a la misma
       altura.
    7. progresion lateral continua (`_lateral_progress_key`) -item 6:
       derecha->izquierda para BACK_RIGHT, simetrico para BACK_LEFT.
    8. `group` -item 8: desempate suave por Group/System.
    9. `pid` -item 9: desempate deterministico final.

    La primera pieza (con el "ya cargado" vacio) cae naturalmente en la
    esquina fondo+lateral elegida gracias a los campos 3-4, sin necesitar un
    caso especial. Como solo se elige del "ready set", el resultado ya es
    topologicamente valido -la geometria de soporte nunca se viola aunque las
    preferencias operativas cambien.

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

    def score(pid: str) -> tuple[int, int, float, int, int, float, float, str, str]:
        p = by_id[pid]
        last = order[-1] if order else None
        continues_last_column = 0 if last is not None and last in deps.get(pid, []) else 1
        adjacent = 0 if _has_reference(pid, loaded, deps, boxes, tol) else 1
        # BACK_RIGHT_FLOOR anchor audit (seccion 8 del pedido): distancia
        # real al fondo (0 = tocando la pared del fondo, igual formula que
        # _touches_back_wall) -NUNCA `-p.x` crudo. Root cause confirmado
        # (coordenadas reales, no visual): para piezas con distinto grosor
        # en el eje X (dx) que de todos modos tocan la MISMA pared del
        # fondo -ej. un Panel acostado con su HEIGHT a lo largo del largo
        # del contenedor (dx grande) junto a otro con su THICKNESS a lo
        # largo del largo (dx chico)- `-p.x` crudo penalizaba a la pieza
        # mas gruesa (su borde CERCANO, p.x, queda mas lejos del fondo
        # aunque su borde LEJANO -p.x+p.dx- toque la pared exactamente
        # igual que la mas delgada), asi que Step 1 podia anclarse en una
        # pieza que ni siquiera tocaba el fondo, salteando la pieza que SI
        # estaba en la esquina BACK_RIGHT_FLOOR real. Para piezas del mismo
        # grosor (el caso comun, BOX/PALLET/CUSTOM y la mayoria de Panels)
        # esto es un desplazamiento constante sobre `-p.x` -mismo orden
        # relativo, cero cambio de comportamiento.
        back_depth = container.length - (p.x + p.dx)
        side = 0 if _touches_side_wall(p, container, anchor, tol) else 1
        floor = 0 if p.z <= TOL else 1
        lateral = _lateral_progress_key(p, anchor)
        return (continues_last_column, adjacent, back_depth, side, floor, p.z, lateral, p.group, pid)

    while remaining:
        ready = [pid for pid in remaining if all(d in loaded for d in deps.get(pid, []))]
        if not ready:
            # No deberia ocurrir (el soporte fisico no puede ser circular),
            # pero por seguridad se vuelca el resto en su orden base en vez
            # de trabarse.
            order.extend(sorted(remaining, key=lambda pid: (-by_id[pid].x, by_id[pid].z, by_id[pid].y, pid)))
            break

        best = min(ready, key=score)
        _continues, adjacent, _back_depth, side, _floor, *_rest = score(best)
        back = 0 if _touches_back_wall(by_id[best], container, tol) else 1
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
    solo marca el caso residual donde no la habia.

    Fase 6A: usa el Load Dependency Graph COMPLETO (soporte + bloqueo
    lateral, `compute_load_dependencies`) -no solo soporte- para que el
    "ready set" simulado aca sea exactamente el mismo que usa
    `compute_load_sequence`, y las warnings nunca queden desincronizadas del
    orden real que ve el usuario."""
    if not placed:
        return []
    deps = compute_load_dependencies(placed)
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
    deps = compute_load_dependencies(placed, boxes)
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
    deps = compute_unload_dependencies(placed, boxes)
    return _group_into_modules(order, deps, boxes, ANCHOR_TOLERANCE_MM)


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
    deps = compute_load_dependencies(placed)
    return _anchored_load_order(placed, container, anchor, deps)


def compute_unload_sequence(placed: list[PlacedPiece]) -> list[str]:
    """Fase 6A: el sort de `base_key` (delivery_sequence, x, -z, y, id) sigue
    siendo el desempate SOFT entre piezas ya listas -pero ahora
    `compute_unload_dependencies` (soporte invertido + bloqueo lateral
    invertido) es una dependencia DURA que `_topological_order` respeta antes
    que nada: si una pieza con Delivery Sequence mas bajo esta fisicamente
    bloqueada por otra, la dependencia gana (ver seccion 17 del pedido;
    `compute_operational_warnings` es quien reporta ese caso como
    DELIVERY_SEQUENCE_CONFLICT en vez de dejarlo pasar en silencio)."""
    if not placed:
        return []

    def base_key(p: PlacedPiece):
        delivery = p.delivery_sequence if p.delivery_sequence is not None else _NO_DELIVERY_SEQUENCE
        return (delivery, p.x, -p.z, p.y, p.id)

    base_order = [p.id for p in sorted(placed, key=base_key)]
    deps = compute_unload_dependencies(placed)
    return _topological_order(base_order, deps)


# ==========================================================================
# Fase 6B -Interactive Step-by-Step Guide: anotacion de Delivery Sequence
# sobre pasos de descarga YA resueltos por compute_unload_steps/chunk_sequence
# (seccion 12-16 del pedido). Estas funciones NUNCA reordenan `steps` -el
# orden fisico/de dependencias sigue siendo la unica fuente de verdad; solo
# describen que Delivery Sequence(s) corresponden a cada paso, y agrupan
# pasos CONTIGUOS que ya comparten un valor limpio en secciones "DELIVERY N"
# para que la guia sea mas legible sin inventar un orden nuevo.
# ==========================================================================


def annotate_unload_steps_with_delivery(
    steps: list[list[str]],
    placed: list[PlacedPiece],
    operational_warnings: list[OperationalWarning] | None = None,
) -> list[UnloadStepDeliveryInfo]:
    """Por cada paso ya resuelto (en el MISMO orden que `steps`, sin
    tocarlo): que Delivery Sequence(s) distintos aparecen en el (vacio si
    ninguna pieza del paso la tiene definida, seccion 34), si el paso mezcla
    mas de un valor (seccion 15 -nunca se inventa un numero unico), y que
    mensajes de `operational_warnings` YA CALCULADOS (por
    compute_operational_warnings, nunca recalculados aca) involucran a
    alguna pieza del paso -reusa el texto tal cual (seccion 16: "no crear
    warnings heuristicos nuevos'')."""

    by_id = {p.id: p for p in placed}
    warnings = operational_warnings or []
    relevant_types = {OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT, OperationalWarningType.STACKING_SEQUENCE_CONFLICT}

    result: list[UnloadStepDeliveryInfo] = []
    for step_ids in steps:
        step_id_set = set(step_ids)
        distinct = sorted(
            {by_id[pid].delivery_sequence for pid in step_ids if pid in by_id and by_id[pid].delivery_sequence is not None}
        )
        step_warnings = [
            w
            for w in warnings
            if w.type in relevant_types and (w.item_id in step_id_set or w.blocking_item_id in step_id_set)
        ]
        # Fase 6B.3, seccion 4/21 del pedido: `conflict_types` va en el MISMO
        # orden/indice que `conflict_messages` -no es un warning nuevo, es el
        # `.type` que el OperationalWarning original ya traia, conservado
        # para que pdf_export.py pueda resumir por categoria sin tener que
        # parsear el texto de `message`.
        result.append(
            UnloadStepDeliveryInfo(
                delivery_sequences=distinct,
                is_mixed=len(distinct) > 1,
                conflict_messages=[w.message for w in step_warnings],
                conflict_types=[w.type for w in step_warnings],
            )
        )
    return result


def group_unload_steps_into_delivery_sections(
    step_delivery_info: list[UnloadStepDeliveryInfo],
) -> list[UnloadDeliverySection]:
    """Fase 6B, seccion 14: agrupa pasos CONTIGUOS que comparten exactamente
    la misma anotacion (mismo valor unico, o ambos sin Delivery Sequence, o
    ambos mezclando exactamente el mismo conjunto de valores) en una sola
    seccion con encabezado -nunca ordena ni mueve un paso, solo decide donde
    "cortar" un encabezado nuevo. Un paso mixto NUNCA se fusiona con un paso
    limpio (de un solo valor), aunque ese valor este entre los mezclados
    -mostrar 'Delivery Sequences: 1, 3' junto a un 'DELIVERY 1' limpio
    seria confuso, no una mejora de legibilidad."""

    # (key, label, step_indices) en una lista local -mas simple y sin
    # trucos de identidad que rastrear "la key de la ultima seccion".
    runs: list[tuple[tuple[int, ...], str | None, list[int]]] = []
    for i, info in enumerate(step_delivery_info):
        key = tuple(info.delivery_sequences)
        if runs and runs[-1][0] == key:
            runs[-1][2].append(i)
        else:
            runs.append((key, _delivery_section_label(info), [i]))
    return [UnloadDeliverySection(label=label, step_indices=indices) for _key, label, indices in runs]


def _delivery_section_label(info: UnloadStepDeliveryInfo) -> str | None:
    if not info.delivery_sequences:
        return None
    if info.is_mixed:
        return f"Mixed Delivery Step (Delivery Sequences: {', '.join(str(v) for v in info.delivery_sequences)})"
    return f"DELIVERY {info.delivery_sequences[0]}"
