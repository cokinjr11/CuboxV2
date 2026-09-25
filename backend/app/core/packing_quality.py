"""Packing Engine Quality Pass -- metricas de calidad de layout (seccion 2
del pedido), separadas de core/scoring.py a proposito: scoring.py decide
CUAL de 7 estrategias gana (Optimization Mode, seccion 18 del pedido, sin
tocar), este modulo solo MIDE que tan compacto/ordenado quedo un layout ya
elegido -para benchmarks y regression tests, nunca para decidir el
resultado del cubicaje en si.

"Do not build an academically perfect void-decomposition engine" (seccion
2): estas son aproximaciones PRACTICAS, geometricamente honestas pero
deliberadamente locales -nunca una descomposicion 3D completa del espacio
libre. Todas trabajan sobre PlacedPiece/ContainerSpec ya resueltos, sin
depender de los internals de core/packer.py (Box/_Candidate), para poder
medir CUALQUIER layout (incluido uno editado a mano) sin acoplarse al
algoritmo que lo genero."""

from dataclasses import dataclass, field

from app.models.schemas import ContainerSpec, PlacedPiece

TOL = 1e-6

MIN_USEFUL_LATERAL_GAP_MM = 150.0
"""Mismo umbral practico que core/packer.py:_MIN_USEFUL_LATERAL_GAP_MM (ver
esa constante para la justificacion) -duplicado a proposito, no importado,
para que este modulo de medicion nunca dependa de los internals privados
del motor de colocacion."""


def _same_z_level(a: PlacedPiece, b: PlacedPiece, tol: float = TOL) -> bool:
    return a.z < b.z + b.dz - tol and b.z < a.z + a.dz - tol


def _same_x_range(a: PlacedPiece, b: PlacedPiece, tol: float = TOL) -> bool:
    return a.x < b.x + b.dx - tol and b.x < a.x + a.dx - tol


def _same_y_range(a: PlacedPiece, b: PlacedPiece, tol: float = TOL) -> bool:
    return a.y < b.y + b.dy - tol and b.y < a.y + a.dy - tol


# ==========================================================================
# Global
# ==========================================================================


def used_bounding_length(placed: list[PlacedPiece], container: ContainerSpec) -> float:
    """Longitud de contenedor realmente usada, medida desde el FONDO (x=
    length) hacia la puerta (x=0) -seccion 2: 'used bounding length from
    BACK toward door'. 0.0 si no hay piezas."""
    if not placed:
        return 0.0
    min_x = min(p.x for p in placed)
    return container.length - min_x


def wall_contact_ratio(placed: list[PlacedPiece], container: ContainerSpec, tol: float = TOL) -> float:
    """Fraccion de piezas que tocan AL MENOS una pared/el piso del Load
    Space (fondo, pared izquierda, pared derecha o piso) -proxy simple de
    "que tan pegado a los limites" quedo el layout (seccion 5-6 del
    pedido). 1.0 si no hay piezas (nada que penalizar)."""
    if not placed:
        return 1.0
    touching = 0
    for p in placed:
        if (
            (container.length - (p.x + p.dx)) <= tol
            or p.y <= tol
            or (container.width - (p.y + p.dy)) <= tol
            or p.z <= tol
        ):
            touching += 1
    return touching / len(placed)


def neighbor_face_contact_ratio(placed: list[PlacedPiece], tol: float = TOL) -> float:
    """Fraccion de piezas que tocan (cara a cara, sin solaparse) al menos
    OTRA pieza -distinto de wall_contact_ratio: mide "aislamiento" (Part 4:
    'avoid isolated islands'), no cercania a las paredes. 1.0 si no hay
    piezas."""
    if not placed:
        return 1.0
    touching = 0
    for a in placed:
        has_neighbor = False
        for b in placed:
            if a.id == b.id:
                continue
            # Contacto de cara: comparten rango en 2 ejes y son adyacentes
            # (gap=0) en el tercero.
            touches_x = abs((a.x + a.dx) - b.x) <= tol or abs((b.x + b.dx) - a.x) <= tol
            touches_y = abs((a.y + a.dy) - b.y) <= tol or abs((b.y + b.dy) - a.y) <= tol
            touches_z = abs((a.z + a.dz) - b.z) <= tol or abs((b.z + b.dz) - a.z) <= tol
            if touches_x and _same_y_range(a, b) and _same_z_level(a, b):
                has_neighbor = True
            elif touches_y and _same_x_range(a, b) and _same_z_level(a, b):
                has_neighbor = True
            elif touches_z and _same_x_range(a, b) and _same_y_range(a, b):
                has_neighbor = True
            if has_neighbor:
                break
        if has_neighbor:
            touching += 1
    return touching / len(placed)


# ==========================================================================
# Boxes (Part 4-8, seccion 2 del pedido -- "Boxes" bullet list)
# ==========================================================================


def average_lateral_gap(placed: list[PlacedPiece], container: ContainerSpec, tol: float = TOL) -> float:
    """Promedio, sobre todas las piezas, del hueco lateral (eje Y) MINIMO
    entre esta pieza y la proxima obstruccion (pared o pieza vecina) en
    CUALQUIERA de sus dos lados abiertos -mismo concepto que
    core/packer.py:_box_compactness_cost, reimplementado aca sin depender
    de esos internals (ver docstring del modulo). 0.0 = en promedio, cada
    pieza esta perfectamente flush de al menos un lado Y sin nada mas
    encima; numeros mas altos = mas hueco lateral promedio sin usar."""
    if not placed:
        return 0.0
    gaps = []
    for p in placed:
        lo, hi = 0.0, container.width
        for o in placed:
            if o.id == p.id or not _same_z_level(p, o) or not _same_x_range(p, o):
                continue
            if o.y + o.dy <= p.y + tol and o.y + o.dy > lo:
                lo = o.y + o.dy
            if o.y >= p.y + p.dy - tol and o.y < hi:
                hi = o.y
        left_residual = max(0.0, p.y - lo)
        right_residual = max(0.0, hi - (p.y + p.dy))
        gaps.append(min(left_residual, right_residual))
    return sum(gaps) / len(gaps)


def narrow_sliver_count(placed: list[PlacedPiece], container: ContainerSpec, threshold: float = MIN_USEFUL_LATERAL_GAP_MM) -> int:
    """Cuenta lados abiertos (izquierda/derecha de cada pieza, eje Y) cuyo
    hueco residual es positivo pero MENOR al umbral practico -las "franjas
    muertas" que la seccion 8 del pedido pide evitar. No cuenta huecos
    exactamente 0 (flush, perfecto) ni huecos grandes (genuinamente
    reutilizables)."""
    if not placed:
        return 0
    count = 0
    for p in placed:
        lo, hi = 0.0, container.width
        for o in placed:
            if o.id == p.id or not _same_z_level(p, o) or not _same_x_range(p, o):
                continue
            if o.y + o.dy <= p.y + TOL and o.y + o.dy > lo:
                lo = o.y + o.dy
            if o.y >= p.y + p.dy - TOL and o.y < hi:
                hi = o.y
        for residual in (max(0.0, p.y - lo), max(0.0, hi - (p.y + p.dy))):
            if TOL < residual < threshold:
                count += 1
    return count


def distinct_floor_lanes(placed: list[PlacedPiece], tol: float = 1.0) -> int:
    """Cantidad de "carriles"/filas distintas en Y a nivel de piso -piezas
    cuyo `y` cae dentro de `tol` mm se consideran el mismo carril. Proxy
    simple de "cuantas filas laterales distintas" (seccion 2: 'number of
    distinct floor lanes / rows')."""
    floor_pieces = [p for p in placed if p.z <= TOL]
    if not floor_pieces:
        return 0
    ys = sorted(p.y for p in floor_pieces)
    lanes = 1
    for prev, cur in zip(ys, ys[1:]):
        if cur - prev > tol:
            lanes += 1
    return lanes


@dataclass
class BoxQualityReport:
    loaded_units: int
    unloaded_units: int
    volume_utilization_pct: float
    used_length_mm: float
    wall_contact_ratio: float
    neighbor_contact_ratio: float
    average_lateral_gap_mm: float
    narrow_sliver_count: int
    floor_lanes: int


def box_quality_report(placed: list[PlacedPiece], unloaded_count: int, container: ContainerSpec, volume_utilization_pct: float) -> BoxQualityReport:
    return BoxQualityReport(
        loaded_units=len(placed),
        unloaded_units=unloaded_count,
        volume_utilization_pct=volume_utilization_pct,
        used_length_mm=used_bounding_length(placed, container),
        wall_contact_ratio=wall_contact_ratio(placed, container),
        neighbor_contact_ratio=neighbor_face_contact_ratio(placed),
        average_lateral_gap_mm=average_lateral_gap(placed, container),
        narrow_sliver_count=narrow_sliver_count(placed, container),
        floor_lanes=distinct_floor_lanes(placed),
    )


# ==========================================================================
# Panels & Fragile (seccion 2 del pedido -- "Panels" bullet list)
# ==========================================================================


def _panel_modules(placed: list[PlacedPiece], tol: float = 1.0) -> list[list[PlacedPiece]]:
    """Agrupa piezas PANEL por profundidad (x) -mismo concepto informal de
    "modulo/fila transversal" que ya usa core/packer.py (ver
    _panel_lateral_cost) y la Loading Guide (compute_load_steps agrupa por
    adyacencia fisica real, esto es solo para medicion, mas simple a
    proposito): piezas cuyo `x` cae dentro de `tol` mm se consideran el
    mismo modulo."""
    if not placed:
        return []
    by_x = sorted(placed, key=lambda p: -p.x)  # el fondo primero (x mas grande)
    modules: list[list[PlacedPiece]] = [[by_x[0]]]
    for p in by_x[1:]:
        if abs(p.x - modules[-1][0].x) <= tol:
            modules[-1].append(p)
        else:
            modules.append([p])
    return modules


def panel_module_count(placed: list[PlacedPiece]) -> int:
    return len(_panel_modules(placed))


@dataclass
class PhysicalModuleReport:
    """Un modulo derivado PURAMENTE de las coordenadas fisicas finales
    -nunca de metadata interna de ningun solver (Stage A.6.1, seccion 5
    del pedido: 'A module must be derived from physical X/depth bands...
    Do not compare solver-internal module metadata against inferred OLD
    modules')."""

    back_x: float
    front_x: float
    depth: float
    panel_count: int
    occupied_width: float
    residual_width: float
    fill_pct: float
    panel_ids: list[str] = field(default_factory=list)


def physical_depth_modules(placed: list[PlacedPiece], container: ContainerSpec, tol: float = TOL) -> list[PhysicalModuleReport]:
    """Deriva modulos de profundidad a partir SOLO de las piezas ya
    colocadas -agrupa por SOLAPAMIENTO de intervalo en X (no por
    coincidencia de `p.x` dentro de una tolerancia fija, que es lo que
    hacia `_panel_modules`): dos piezas quedan en el MISMO modulo fisico
    si sus rangos [x, x+dx] se tocan o se solapan, transitivamente (una
    cadena de piezas que se tocan en X es un solo modulo). Esto es
    deliberadamente mas robusto que `_panel_modules` cuando el grosor
    (dx) varia DENTRO de un mismo modulo logico -piezas alineadas por su
    borde de FONDO comun pero con distinto grosor tienen `p.x` (borde de
    puerta) DISTINTO, y `_panel_modules` las separaria incorrectamente en
    modulos falsos (Stage A.6.1, seccion 3/5/6 del pedido: auditar esto
    exactamente, nunca asumir que coinciden).

    `occupied_width` es la UNION de los rangos Y ocupados (no la suma
    ingenua) -seccion 6 del pedido: 'Detect overlaps, double-counted
    width'. Si la union es menor que la suma de anchos individuales, hay
    solapamiento lateral real (un bug), y esta funcion lo reflejaria como
    occupied_width < suma(dy) -se puede detectar comparando ambos
    externamente si hace falta depurar."""
    if not placed:
        return []
    by_front_x = sorted(placed, key=lambda p: p.x)
    groups: list[list[PlacedPiece]] = []
    current: list[PlacedPiece] = [by_front_x[0]]
    current_max_back = by_front_x[0].x + by_front_x[0].dx
    for p in by_front_x[1:]:
        if p.x <= current_max_back + tol:
            current.append(p)
            current_max_back = max(current_max_back, p.x + p.dx)
        else:
            groups.append(current)
            current = [p]
            current_max_back = p.x + p.dx
    groups.append(current)

    reports = []
    for group in groups:
        back_x = max(p.x + p.dx for p in group)
        front_x = min(p.x for p in group)
        y_intervals = sorted((p.y, p.y + p.dy) for p in group)
        union = 0.0
        cur_lo, cur_hi = y_intervals[0]
        for lo, hi in y_intervals[1:]:
            if lo <= cur_hi + tol:
                cur_hi = max(cur_hi, hi)
            else:
                union += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
        union += cur_hi - cur_lo
        residual = container.width - union
        reports.append(
            PhysicalModuleReport(
                back_x=back_x, front_x=front_x, depth=back_x - front_x, panel_count=len(group),
                occupied_width=union, residual_width=residual, fill_pct=100.0 * union / container.width,
                panel_ids=[p.code for p in group],
            )
        )
    reports.sort(key=lambda r: -r.back_x)
    return reports


def panel_lateral_gaps(placed: list[PlacedPiece], container: ContainerSpec) -> list[float]:
    """Por modulo, la lista de huecos INTERNOS entre paneles adyacentes
    (ordenados por Y) -no incluye el hueco hacia las paredes (eso es
    wall_occupancy, ver mas abajo). Seccion 14 del pedido: preferir un
    unico hueco residual grande cerca del centro sobre muchos huecos chicos
    dispersos -esta lista es la materia prima para verificar eso."""
    gaps: list[float] = []
    for module in _panel_modules(placed):
        row = sorted(module, key=lambda p: p.y)
        for a, b in zip(row, row[1:]):
            gap = b.y - (a.y + a.dy)
            if gap > TOL:
                gaps.append(gap)
    return gaps


def panel_center_residual_gap(placed: list[PlacedPiece], container: ContainerSpec) -> float:
    """El hueco INTERNO mas grande de cada modulo (seccion 14: el residuo
    que deberia quedar concentrado cerca del centro, no disperso) -promedio
    sobre todos los modulos con al menos un hueco interno. 0.0 si ningun
    modulo tiene hueco interno (filas perfectamente flush)."""
    largest_per_module = []
    for module in _panel_modules(placed):
        row = sorted(module, key=lambda p: p.y)
        internal_gaps = [b.y - (a.y + a.dy) for a, b in zip(row, row[1:]) if b.y - (a.y + a.dy) > TOL]
        if internal_gaps:
            largest_per_module.append(max(internal_gaps))
    return sum(largest_per_module) / len(largest_per_module) if largest_per_module else 0.0


def panel_wall_occupancy(placed: list[PlacedPiece], container: ContainerSpec, tol: float = TOL) -> float:
    """Fraccion de MODULOS que tienen al menos una pieza tocando CADA
    pared lateral (izquierda Y derecha) -seccion 12: 'use both lateral
    walls'. 1.0 = todos los modulos ocupan ambas paredes."""
    modules = _panel_modules(placed)
    if not modules:
        return 1.0
    both = 0
    for module in modules:
        touches_left = any(p.y <= tol for p in module)
        touches_right = any((container.width - (p.y + p.dy)) <= tol for p in module)
        if touches_left and touches_right:
            both += 1
    return both / len(modules)


def panel_height_order_violations(placed: list[PlacedPiece], container: ContainerSpec) -> int:
    """Cuenta, por modulo y por lado (pared->centro), cuantos pares
    CONSECUTIVOS de piezas violan el orden "mas alta cerca de la pared, mas
    baja cerca del centro" (seccion 12) -mismo criterio de "violacion local"
    ya usado en test_panels_lateral_heuristic.py, generalizado a
    multi-modulo para poder compararlo antes/despues.

    Clasificacion por CENTRO de la pieza (p.y + p.dy/2), no por su borde
    izquierdo (Stage A.6, bug real encontrado benchmarking el Panel Module
    Solver: en un modulo con anchos MUY dispares entre lados, una pieza
    puede fisicamente EXTENDERSE mas alla del punto medio geometrico del
    contenedor aunque pertenezca, por construccion, a la secuencia de la
    pared opuesta -clasificar por borde izquierdo la contaba del lado
    equivocado y generaba 'violaciones' que eran un artefacto de la
    metrica, no un desorden fisico real -verificado item por item contra
    el Module Solver, 0 violaciones reales en los 5 fixtures PANEL-M1..M5
    una vez reclasificado por centro). Para el motor EMS (bilateral,
    anclajes simetricos de ambas paredes) esto no cambia ningun resultado
    ya medido -sus piezas casi nunca cruzan el punto medio geometrico."""
    violations = 0
    half_width = container.width / 2
    for module in _panel_modules(placed):
        left_side = sorted([p for p in module if (p.y + p.dy / 2) < half_width], key=lambda p: p.y)
        right_side = sorted([p for p in module if (p.y + p.dy / 2) >= half_width], key=lambda p: -p.y)
        for side in (left_side, right_side):
            heights = [p.dz for p in side]
            violations += sum(1 for a, b in zip(heights, heights[1:]) if b > a)
    return violations


@dataclass
class PanelQualityReport:
    loaded_units: int
    unloaded_units: int
    volume_utilization_pct: float
    module_count: int
    lateral_gaps: list[float] = field(default_factory=list)
    total_lateral_gap_mm: float = 0.0
    largest_lateral_gap_mm: float = 0.0
    center_residual_gap_mm: float = 0.0
    wall_occupancy: float = 0.0
    height_order_violations: int = 0


def panel_quality_report(placed: list[PlacedPiece], unloaded_count: int, container: ContainerSpec, volume_utilization_pct: float) -> PanelQualityReport:
    gaps = panel_lateral_gaps(placed, container)
    return PanelQualityReport(
        loaded_units=len(placed),
        unloaded_units=unloaded_count,
        volume_utilization_pct=volume_utilization_pct,
        module_count=panel_module_count(placed),
        lateral_gaps=gaps,
        total_lateral_gap_mm=sum(gaps),
        largest_lateral_gap_mm=max(gaps) if gaps else 0.0,
        center_residual_gap_mm=panel_center_residual_gap(placed, container),
        wall_occupancy=panel_wall_occupancy(placed, container),
        height_order_violations=panel_height_order_violations(placed, container),
    )
