"""Regression tests para el Panel Module Solver (Stage A.6, Parte B del
pedido). Prototipo EXPERIMENTAL -no wireado a produccion (ver docstring de
panel_module_solver.py), estos tests solo garantizan que no rompe ninguna
regla de negocio mientras se evalua si su arquitectura reemplaza al motor
EMS generico para Panels & Fragile."""

from app.core.final_validation import validate_for_export
from app.core.packing_quality import panel_height_order_violations
from app.core.panel_module_solver import pack_panels_module
from app.core.reserved_zones import central_aisle_zone
from app.models.containers import build_custom_load_space, get_container
from app.models.schemas import ItemType, LoadSpaceType, OptimizationMode, WindowItem

CONTAINER = get_container("40ft_standard")


def _panel(code, w=600, h=2000, t=40.0, **kw):
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 15.0), quantity=1, item_type=ItemType.PANEL, stackable=False, **kw,
    )


def test_first_panel_anchors_back_right_floor():
    result, _ = pack_panels_module([_panel("A")], CONTAINER)
    assert len(result.placed) == 1
    p = result.placed[0]
    assert abs((p.x + p.dx) - CONTAINER.length) < 1e-6
    assert abs((p.y + p.dy) - CONTAINER.width) < 1e-6
    assert abs(p.z) < 1e-6


def test_module_fills_tighter_than_naive_leftover():
    # 650+620+580+400 = 2250mm de 2352mm -> >=95% de llenado en UN solo
    # modulo, no el ~10% que da procesar cada ancho como su propio modulo
    # aislado (el motor EMS generico hacia justamente eso, ver Stage A.5).
    items = [
        _panel("A", w=650, h=2200), _panel("B", w=620, h=2100), _panel("C", w=580, h=2000),
        _panel("E", w=400, h=1500),
    ]
    result, stats = pack_panels_module(items, CONTAINER)
    assert len(result.placed) == 4
    assert len(stats.modules) == 1, "4 anchos que caben juntos no deberian abrir mas de un modulo"
    assert stats.modules[0].fill_pct >= 95.0


def test_bilateral_height_order_no_violations_with_uneven_widths():
    items = [
        _panel(f"P{i}", w=w, h=h)
        for i, (w, h) in enumerate([(650, 2300), (620, 2100), (580, 1900), (530, 1700), (400, 1400), (300, 1000)])
    ]
    result, _ = pack_panels_module(items, CONTAINER)
    assert len(result.placed) == 6
    assert panel_height_order_violations(result.placed, CONTAINER) == 0


def test_wide_panel_falls_back_to_narrow_upright_orientation():
    # Stage B, bug real: width=2500mm excede container.width (2352mm).
    # _upright_orientations prueba P1-b primero (Width propio -> lateral),
    # que NO entra lateralmente aunque su altura (dz=height=1000) si
    # entre -_gather_module_pool antes solo validaba dz, asi que esta
    # pieza se perdia entera (0 placed) en vez de caer a P1-a
    # (dy=thickness=80, que si entra facilmente).
    wide = _panel("WIDE", w=2500, h=1000, t=80)
    result, _ = pack_panels_module([wide], CONTAINER)
    assert len(result.placed) == 1
    assert len(result.unloaded) == 0


def test_fractional_widths_do_not_collide_or_crash():
    # URGENTE, produccion real (plan real de 67 lineas importado por
    # Excel, nunca reproducido por ningun fixture sintetico porque todos
    # usaban mm enteros): dos bugs relacionados.
    #
    # 1) UnloadedReason.OUT_OF_BOUNDS no existe -crasheaba con
    #    AttributeError apenas una pieza colocada fallaba el chequeo de
    #    dentro-del-contenedor (nunca pasaba con mm enteros, casi
    #    garantizado con mm reales de Excel).
    # 2) El ancho usado para el cursor bilateral (mm entero, redondeado
    #    para el DP del knapsack) y el ancho de la caja fisica final (mm
    #    float real) podian desalinearse -como el knapsack busca
    #    DELIBERADAMENTE el ajuste mas ajustado posible, un ajuste
    #    "perfecto" en enteros es EXACTAMENTE el caso donde el redondeo
    #    hace que la suma de anchos reales supere la capacidad real,
    #    produciendo colisiones fisicas entre paneles.
    #
    # Caso minimo reducido: 2 paneles de 500.3mm en un contenedor de
    # 1000mm de ancho -sus enteros redondeados (500+500=1000) calzan
    # EXACTO con la capacidad, pero su suma real (1000.6mm) la excede.
    container = build_custom_load_space(
        name="test-fractional", load_space_type=LoadSpaceType.CONTAINER,
        length=12032, width=1000, height=2393, max_weight=26512,
    )
    items = [_panel("A", w=500.3, h=1800), _panel("B", w=500.3, h=1800)]
    result, _ = pack_panels_module(items, container)
    assert len(result.placed) == 2
    assert len(result.unloaded) == 0
    issues = validate_for_export(result, container)
    assert issues == []
    # Con el fix (ceil en vez de round para el ancho que ve el knapsack),
    # 500.3mm cuesta 501mm en la cuenta del DP -2x501=1002 > 1000mm de
    # capacidad, asi que NO deben entrar juntos en un solo modulo (eso
    # es correcto: evita la colision). Terminan en modulos de profundidad
    # DISTINTA -el chequeo de superposicion real debe considerar los 3
    # ejes, no solo Y (una superposicion en Y sin superposicion en X no
    # es una colision fisica real)."""
    a = next(p for p in result.placed if p.code == "A")
    b = next(p for p in result.placed if p.code == "B")
    overlap_x = a.x < b.x + b.dx - 1e-6 and b.x < a.x + a.dx - 1e-6
    overlap_y = a.y < b.y + b.dy - 1e-6 and b.y < a.y + a.dy - 1e-6
    assert not (overlap_x and overlap_y), "los paneles se superponen fisicamente"


def test_no_pairwise_overlap():
    items = [_panel(f"P{i}", w=300 + (i % 5) * 60, h=1200 + (i % 6) * 150) for i in range(40)]
    result, _ = pack_panels_module(items, CONTAINER)
    placed = result.placed
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            overlap_x = a.x < b.x + b.dx - 1e-6 and b.x < a.x + a.dx - 1e-6
            overlap_y = a.y < b.y + b.dy - 1e-6 and b.y < a.y + a.dy - 1e-6
            overlap_z = a.z < b.z + b.dz - 1e-6 and b.z < a.z + a.dz - 1e-6
            assert not (overlap_x and overlap_y and overlap_z), f"{a.code} se superpone con {b.code}"


def test_all_placed_within_container_bounds():
    items = [_panel(f"P{i}", w=300 + (i % 5) * 60, h=1200 + (i % 6) * 150) for i in range(40)]
    result, _ = pack_panels_module(items, CONTAINER)
    for p in result.placed:
        assert p.x >= -1e-6 and p.x + p.dx <= CONTAINER.length + 1e-6
        assert p.y >= -1e-6 and p.y + p.dy <= CONTAINER.width + 1e-6
        assert p.z >= -1e-6 and p.z + p.dz <= CONTAINER.height + 1e-6


def test_oversized_panel_is_unloaded_not_forced():
    # Ancho > container.width por si solo NO basta para ser irresoluble
    # (Stage B: P1-a usa ese ancho como PROFUNDIDAD en vez de lateral, y
    # el contenedor tiene mucha mas profundidad que ancho disponible -ver
    # test_wide_panel_falls_back_to_narrow_upright_orientation). Para un
    # caso genuinamente sin ninguna orientacion de pie valida, la ALTURA
    # debe exceder container.height (2393mm en 40ft_standard) -eso
    # bloquea P1-a Y P1-b por igual (ambas comparten dz=height).
    huge = _panel("HUGE", w=CONTAINER.width + 500, h=CONTAINER.height + 500)
    result, _ = pack_panels_module([huge], CONTAINER)
    assert len(result.placed) == 0
    assert len(result.unloaded) == 1


def test_too_tall_panel_falls_back_to_physically_valid_orientation_or_unloads():
    # 2400mm > 2393mm de altura interior -no puede pararse (Stage A.5,
    # confirmado en aislamiento) -el solver no debe reventar, debe caer a
    # una orientacion fisica valida o marcarlo sin cargar, nunca crashear.
    too_tall = _panel("TALL", w=400, h=2400)
    result, _ = pack_panels_module([too_tall], CONTAINER)
    assert len(result.placed) + len(result.unloaded) == 1


def test_reserved_central_aisle_avoided():
    aisle = central_aisle_zone(CONTAINER, aisle_width_mm=800.0)
    items = [_panel(f"P{i}", w=300 + (i % 4) * 50, h=1200 + (i % 5) * 100) for i in range(30)]
    result, _ = pack_panels_module(items, CONTAINER, reserved_zones=[aisle])
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length - 1e-6 and aisle.x < p.x + p.dx - 1e-6
        overlaps_y = p.y < aisle.y + aisle.width - 1e-6 and aisle.y < p.y + p.dy - 1e-6
        assert not (overlaps_x and overlaps_y), f"{p.code} invade el pasillo reservado"


def test_reserved_zone_carving_preserves_capacity():
    # Stage B, incidente real: ANTES de tallar en intervalos usables, una
    # zona reservada activa perdia capacidad severa (verificado en Stage
    # A.6.1: 42 -> 10 piezas con este mismo dataset/pasillo). Con
    # _usable_lateral_intervals + orientacion angosta de respaldo
    # (_effective_orientation_for_capacity), el Module Solver debe cargar
    # TODAS las piezas, igual que el motor legacy en el mismo escenario.
    from app.tests.fixtures_panel_module import panel_real

    aisle = central_aisle_zone(CONTAINER, aisle_width_mm=800.0)
    items = panel_real()
    result, _ = pack_panels_module(items, CONTAINER, reserved_zones=[aisle])
    assert len(result.placed) == len(items)
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length - 1e-6 and aisle.x < p.x + p.dx - 1e-6
        overlaps_y = p.y < aisle.y + aisle.width - 1e-6 and aisle.y < p.y + p.dy - 1e-6
        assert not (overlaps_x and overlaps_y), f"{p.code} invade el pasillo reservado"


def test_wide_panel_uses_narrow_orientation_to_avoid_reserved_zone():
    # Un panel mas ancho que CUALQUIER intervalo usable individual (pero
    # que cabria en el ancho total del contenedor) debe reconsiderarse con
    # su orientacion mas angosta (grosor -> lateral) en vez de perderse
    # -mismo mecanismo que evita la zona reservada sin necesitar el
    # fallback de ruteo a motor legacy.
    aisle = central_aisle_zone(CONTAINER, aisle_width_mm=800.0)
    # intervalos resultantes: [0, 776] y [1576, 2352] (~776mm cada uno)
    wide = _panel("WIDE", w=1200, h=1800, t=40)
    result, _ = pack_panels_module([wide], CONTAINER, reserved_zones=[aisle])
    assert len(result.placed) == 1


def test_two_disjoint_intervals_both_get_filled():
    # Ejemplo literal del usuario: usable intervals [0..850] y
    # [1350..2388] -verifica con una zona que deja esos dos intervalos
    # (en un contenedor mas ancho ad-hoc) que AMBOS lados reciben piezas,
    # no solo el primero/mas grande.
    from app.models.containers import build_custom_load_space
    from app.models.schemas import LoadSpaceType
    from app.core.reserved_zones import ReservedZone

    wide_container = build_custom_load_space(
        name="test-wide", load_space_type=LoadSpaceType.CONTAINER,
        length=12032, width=2388, height=2393, max_weight=26512,
    )
    zone = ReservedZone(x=0, y=850, z=0, length=12032, width=500, height=2393, label="test_zone")
    items = [_panel(f"P{i}", w=400, h=1800) for i in range(8)]
    result, _ = pack_panels_module(items, wide_container, reserved_zones=[zone])
    left_side = [p for p in result.placed if p.y + p.dy <= 850 + 1e-6]
    right_side = [p for p in result.placed if p.y >= 1350 - 1e-6]
    assert left_side, "el intervalo [0,850] no recibio ninguna pieza"
    assert right_side, "el intervalo [1350,2388] no recibio ninguna pieza"


def test_keep_groups_together_does_not_mix_clusters_in_one_module():
    items = [
        _panel("A1", w=500, h=1800, group="A"), _panel("A2", w=500, h=1700, group="A"),
        _panel("B1", w=500, h=1600, group="B"), _panel("B2", w=500, h=1500, group="B"),
    ]
    result, _ = pack_panels_module(items, CONTAINER, optimization_mode=OptimizationMode.KEEP_GROUPS)
    by_x = {}
    for p in result.placed:
        by_x.setdefault(round(p.x, 1), set()).add(p.group)
    for x, groups in by_x.items():
        assert len(groups) == 1, f"modulo en x={x} mezclo grupos: {groups}"


def test_determinism():
    items = [_panel(f"P{i}", w=300 + (i % 6) * 55, h=1200 + (i % 7) * 130) for i in range(60)]
    items2 = [_panel(f"P{i}", w=300 + (i % 6) * 55, h=1200 + (i % 7) * 130) for i in range(60)]
    r1, _ = pack_panels_module(items, CONTAINER)
    r2, _ = pack_panels_module(items2, CONTAINER)
    coords1 = sorted((p.code, p.x, p.y, p.z) for p in r1.placed)
    coords2 = sorted((p.code, p.x, p.y, p.z) for p in r2.placed)
    assert coords1 == coords2


def test_subset_search_stays_bounded_at_scale():
    # Seccion B2/B13 del pedido: "Do not brute-force 2^N" -con un pool
    # acotado a _MODULE_POOL_SIZE candidatos y una capacidad ~2352mm, cada
    # modulo cuesta como mucho _MODULE_POOL_SIZE*capacity "evaluaciones"
    # de DP (celdas tocadas) -verifica que el total observado con 400
    # piezas se mantiene en ese orden de magnitud, nunca exponencial.
    from app.core.panel_module_solver import _MODULE_POOL_SIZE

    items = [_panel(f"P{i}", w=300 + (i % 8) * 40, h=1200 + (i % 9) * 100) for i in range(400)]
    result, stats = pack_panels_module(items, CONTAINER)
    assert len(result.placed) == 400
    per_module_cap = _MODULE_POOL_SIZE * int(CONTAINER.width)
    assert stats.subset_evaluations <= per_module_cap * len(stats.modules)
