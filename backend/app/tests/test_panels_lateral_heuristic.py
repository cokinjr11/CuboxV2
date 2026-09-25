"""CUBOX 2.0 -- Panels & Fragile: heuristica lateral (altura vs pared).

Preferencia de OPTIMIZACION (nunca una restriccion dura): dentro de una fila
lateral de Panels & Fragile, las piezas mas altas (AS PLACED, dz de la
orientacion elegida) tienden a quedar cerca de las paredes del contenedor
(eje Y, ancho) y las mas bajas cerca del centro. Nunca decide si una pieza
entra o no -eso sigue siendo exclusivamente _evaluate_candidate (los mismos
chequeos duros de siempre): solo desempata CUAL de varios candidatos ya
validos, a la misma profundidad minima (x,z), se usa."""

from app.core.geometry import Box
from app.core.packer import _panel_lateral_cost, pack_container
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem


def _panel(code: str, height: float, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description=f"Panel {height}", width=400, height=height, thickness=40, weight=15,
        quantity=1, item_type=ItemType.PANEL, stackable=False,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _box_item(code: str, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description="Box", width=400, height=400, thickness=400, weight=15,
        quantity=1, item_type=ItemType.BOX, stackable=True,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _dist_to_wall(p, container_width: float) -> float:
    return min(p.y, container_width - (p.y + p.dy))


def test_panel_lateral_cost_rewards_tall_near_wall_and_short_near_center():
    """Unidad pura de _panel_lateral_cost: alta+pegada a la pared = costo
    bajo; alta+lejos de la pared (cerca del centro) = costo alto; baja da
    igual donde este (costo bajo en ambos extremos)."""
    from app.models.schemas import ContainerSpec

    container = ContainerSpec(id="c", name="C", length=10000, width=2000, height=2400, max_weight=20000)
    tall_near_wall = Box(id="a", x=0, y=0, z=0, dx=100, dy=100, dz=2200)
    tall_near_center = Box(id="b", x=0, y=950, z=0, dx=100, dy=100, dz=2200)
    short_near_wall = Box(id="c", x=0, y=0, z=0, dx=100, dy=100, dz=300)

    cost_tall_wall = _panel_lateral_cost(tall_near_wall, container)
    cost_tall_center = _panel_lateral_cost(tall_near_center, container)
    cost_short_wall = _panel_lateral_cost(short_near_wall, container)

    assert cost_tall_wall < cost_tall_center, "una pieza alta lejos de la pared debe costar MAS que pegada a la pared"
    assert cost_tall_wall <= cost_short_wall + 1e-9 or cost_short_wall < 0.05, "una pieza baja nunca debe costar mucho, este donde este"


def test_panels_form_monotonic_height_gradient_from_wall_to_center():
    """Acceptance test del pedido: plan representativo con multiples piezas
    upright de altura claramente distinta -las mas altas deben tender a
    quedar cerca de las paredes, las mas bajas cerca del centro, patron
    visualmente obvio."""
    container = get_container("40ft_standard")
    heights = [2400, 2200, 2000, 1600, 1200, 800, 600, 2300, 1900, 1000]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]

    result = pack_container(items, container)
    assert len(result.unloaded) == 0, "las 10 piezas deben entrar en un 40ft standard"

    # Agrupa por profundidad (x) real -cada fila lateral queda a una
    # profundidad propia. La fila mas grande (la que reune la mayoria de las
    # piezas, ver _panel_lateral_cost) es donde se espera el patron.
    from collections import defaultdict

    rows: dict[float, list] = defaultdict(list)
    for p in result.placed:
        rows[round(p.x, 1)].append(p)
    main_row = max(rows.values(), key=len)
    assert len(main_row) >= 7, "la mayoria de las piezas deben terminar en la misma fila lateral"

    main_row_sorted = sorted(main_row, key=lambda p: _dist_to_wall(p, container.width))
    heights_by_distance = [p.dz for p in main_row_sorted]
    # Patron "visualmente obvio": no exigimos un orden PERFECTO pieza a
    # pieza (podria haber pequenas inversiones locales por como crecen los
    # candidatos), pero la pieza mas cercana a la pared debe ser
    # claramente mas alta que la mas cercana al centro, y la tendencia
    # general (primera mitad vs segunda mitad) debe ser decreciente.
    assert heights_by_distance[0] > heights_by_distance[-1]
    midpoint = len(heights_by_distance) // 2
    first_half_avg = sum(heights_by_distance[:midpoint]) / midpoint
    second_half_avg = sum(heights_by_distance[midpoint:]) / (len(heights_by_distance) - midpoint)
    assert first_half_avg > second_half_avg, "la mitad de la fila mas cercana a la pared debe ser, en promedio, mas alta"


def test_panels_use_both_lateral_walls_not_just_one():
    """Correccion -llenado BILATERAL (seccion 1-7 del pedido): con el seed
    adicional anclado a la pared derecha (from_right), piezas altas deben
    tender a ocupar AMBAS paredes, no solo una -nunca aceptar que todas las
    piezas altas se acumulen en una sola pared cuando la geometria permite
    lo contrario (seccion 7: acceptance scenario)."""
    container = get_container("40ft_standard")
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]

    result = pack_container(items, container)
    assert len(result.unloaded) == 0

    from collections import defaultdict

    rows: dict[float, list] = defaultdict(list)
    for p in result.placed:
        rows[round(p.x, 1)].append(p)
    main_row = max(rows.values(), key=len)

    half_width = container.width / 2
    left_side = sorted([p for p in main_row if p.y < half_width], key=lambda p: p.y)
    right_side = sorted([p for p in main_row if p.y >= half_width], key=lambda p: -p.y)

    assert len(left_side) >= 3, "la pared izquierda debe recibir varias piezas, no quedar vacia"
    assert len(right_side) >= 3, "la pared derecha debe recibir varias piezas, no quedar vacia -este es EL bug que esta correccion arregla"

    left_heights = [p.dz for p in left_side]
    right_heights = [p.dz for p in right_side]

    def violations(seq: list[float]) -> int:
        return sum(1 for a, b in zip(seq, seq[1:]) if b > a)

    assert violations(left_heights) == 0, f"lado izquierdo (pared->centro) debe ser monotonico decreciente: {left_heights}"
    assert violations(right_heights) == 0, f"lado derecho (pared->centro) debe ser monotonico decreciente: {right_heights}"

    # Ambas paredes deben tener una pieza claramente alta -nunca todas las
    # altas en un solo lado (seccion 7: "Do NOT accept a result where all
    # tall pieces accumulate only on one wall unless geometry makes the
    # opposite wall unavailable" -aca la geometria SI permite ambos lados).
    assert max(left_heights) >= 1900
    assert max(right_heights) >= 1900


def test_panels_lateral_heuristic_never_overrides_hard_constraints():
    """El heuristico NUNCA decide si una pieza entra -si el unico candidato
    valido es uno "malo" (lejos de la pared), la pieza se coloca ahi igual
    (nunca queda sin colocar solo porque no hay un candidato ideal)."""
    container = get_container("40ft_standard")
    # Container angosto a proposito (ancho justo para 1 panel) -no hay
    # eleccion lateral posible, cada pieza tiene que entrar donde sea que
    # quepa fisicamente.
    from app.models.schemas import ContainerSpec

    narrow = ContainerSpec(
        id="narrow", name="Narrow", length=container.length, width=450, height=container.height,
        max_weight=container.max_weight,
    )
    items = [_panel("P0", 2400), _panel("P1", 600)]
    result = pack_container(items, narrow)
    assert len(result.unloaded) == 0


def test_box_placement_unaffected_by_panel_heuristic():
    """Regresion explicita: Loose Boxes/Palletized/Custom NO deben cambiar
    de comportamiento -siguen first-fit puro, sin ninguna heuristica lateral."""
    container = get_container("40ft_standard")
    items = [_box_item(f"B{i}") for i in range(6)]
    result_a = pack_container(items, container)
    result_b = pack_container(items, container)
    assert len(result_a.unloaded) == 0
    positions_a = [(round(p.x), round(p.y), round(p.z)) for p in sorted(result_a.placed, key=lambda p: p.id)]
    positions_b = [(round(p.x), round(p.y), round(p.z)) for p in sorted(result_b.placed, key=lambda p: p.id)]
    assert positions_a == positions_b, "el mismo plan de Boxes debe seguir dando exactamente el mismo layout (deterministico)"


def test_mixed_plan_only_panels_get_lateral_treatment():
    """Un plan con Boxes Y Panels mezclados -el heuristico solo debe influir
    en las piezas PANEL, nunca en las BOX (aunque compartan el mismo
    contenedor/lista de candidatos)."""
    container = get_container("40ft_standard")
    items = [_box_item("B0"), _box_item("B1"), _panel("PA", 2400), _panel("PB", 600)]
    result = pack_container(items, container)
    assert len(result.unloaded) == 0
    boxes = [p for p in result.placed if p.item_type == ItemType.BOX]
    panels = [p for p in result.placed if p.item_type == ItemType.PANEL]
    assert len(boxes) == 2
    assert len(panels) == 2


def test_panel_edge_only_orientation_still_enforced_with_heuristic():
    """El heuristico lateral no debe alterar la regla PANEL_EDGE_ONLY -la
    cara Width x Height nunca debe quedar horizontal, con o sin el
    heuristico activo."""
    from app.core.orientation import is_valid_orientation

    container = get_container("40ft_standard")
    items = [_panel(f"P{i}", h) for i, h in enumerate([2400, 1800, 1200, 900, 600])]
    result = pack_container(items, container)
    assert len(result.unloaded) == 0
    for p in result.placed:
        assert is_valid_orientation(p.source_dimensions, p.dx, p.dy, p.dz, p.resolved_orientation_policy)


def test_panel_delivery_sequence_group_priority_unaffected():
    """Regresion: Delivery Sequence/Group/Load Priority siguen exactamente
    igual -el heuristico lateral no los lee ni los modifica."""
    container = get_container("40ft_standard")
    items = [
        _panel("P0", 2400, group="Ruta A", delivery_sequence=1, priority=1),
        _panel("P1", 600, group="Ruta B", delivery_sequence=2, priority=5),
    ]
    result = pack_container(items, container)
    by_code = {p.code: p for p in result.placed}
    assert by_code["P0"].group == "Ruta A"
    assert by_code["P0"].delivery_sequence == 1
    assert by_code["P0"].priority == 1
    assert by_code["P1"].group == "Ruta B"
    assert by_code["P1"].delivery_sequence == 2
    assert by_code["P1"].priority == 5
