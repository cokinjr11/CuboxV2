"""Datasets de regresion para Cross-Module Residual Pocket Reuse (Stage 2 del
Panel Module Solver, seccion 20 del pedido): POCKET-1..9. Cada funcion
devuelve exactamente lo que su test necesita (lista de items, y a veces un
ReservedZone) -disenados y verificados empiricamente uno por uno contra
core/panel_pocket_reuse.py antes de fijarse aca (ver seccion 20: 'Create...
expected: ...').

Container de referencia para POCKET-1/2/3/4/5/6: un CUSTOM simple de
3000x1000x1000mm -mas facil de razonar a mano que el catalogo real. POCKET-7
y POCKET-9 usan 40ft_standard (el catalogo real): POCKET-9 con dimensiones
DERIVADAS del plan real de 67 lineas (el caso D12 que origino este trabajo),
POCKET-7 con un dataset variado estilo PANEL-REAL de fixtures_panel_module.py
mas un outlier D12-like."""

from app.core.reserved_zones import ReservedZone
from app.models.containers import build_custom_load_space, get_container
from app.models.schemas import ItemType, LoadSpaceType, WindowItem


def _panel(code: str, w: float, h: float, t: float = 40.0, **kw) -> WindowItem:
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, **kw,
    )


def pocket_container():
    """3000 x 1000 x 1000mm -- lo bastante chico para razonar cada pocket a
    mano, lo bastante grande para que un outlier (dx=1800) fuerce un modulo
    mucho mas profundo que sus companeros (dx=40), replicando el patron real
    del caso D12 (un panel con una unica orientacion valida, mucho mas
    profundo que el resto de su modulo)."""
    return build_custom_load_space("POCKET-TEST", LoadSpaceType.CUSTOM, length=3000.0, width=1000.0, height=1000.0, max_weight=100000.0)


# ==========================================================================
# POCKET-1: outlier profundo + modulo poco profundo + items futuros que
# CABEN en el pocket resultante -deben reusarlo (seccion 20: "future items
# reuse pocket").
# ==========================================================================
def pocket_1_fitting_items() -> list[WindowItem]:
    out = _panel("OUT", 1800, 200, 200)  # unica orientacion valida: dx=1800 (P1-a)
    n1 = _panel("N1", 400, 900, 40)  # dx=40 via P1-b (primaria)
    n2 = _panel("N2", 400, 900, 40)
    fit1 = _panel("FIT1", 300, 800, 40)
    fit2 = _panel("FIT2", 300, 800, 40)
    return [out, n1, n2, fit1, fit2]


# ==========================================================================
# POCKET-2: MISMA geometria de outlier/modulo que POCKET-1, pero el item
# pendiente no entra de pie en el contenedor bajo NINGUNA orientacion
# (altura > container.height) -el pocket debe quedar vacio, seccion 20:
# "no item can fit -> pocket remains".
# ==========================================================================
def pocket_2_no_fit() -> list[WindowItem]:
    out = _panel("OUT", 1800, 200, 200)
    n1 = _panel("N1", 400, 900, 40)
    n2 = _panel("N2", 400, 900, 40)
    too_tall = _panel("BIG", 1300, 1300, 1300)  # 1300 > container.height (1000) en TODAS las orientaciones
    return [out, n1, n2, too_tall]


# ==========================================================================
# POCKET-3: DOS outliers de profundidad distinta en el mismo modulo (ancho
# lateral exacto: 200+200+300+300=1000mm) -deben generar mas de un pocket
# residual independiente (seccion 20: "multiple residual pockets").
# ==========================================================================
def pocket_3_multiple_pockets() -> list[WindowItem]:
    out1 = _panel("OUT1", 1800, 200, 200)
    out2 = _panel("OUT2", 1200, 200, 200)
    n1 = _panel("N1", 300, 900, 40)
    n2 = _panel("N2", 300, 900, 40)
    fit1 = _panel("FIT1", 250, 800, 40)
    fit2 = _panel("FIT2", 250, 800, 40)
    return [out1, out2, n1, n2, fit1, fit2]


# ==========================================================================
# POCKET-4: misma geometria que POCKET-1, mas una Zona Reservada que
# atraviesa PARTE del pocket -seccion 15/20: el reuso debe honrar la zona
# (nunca colocar nada adentro), sin perder la parte del pocket que SI sigue
# libre.
# ==========================================================================
def pocket_4_reserved_zone_intersects() -> tuple[list[WindowItem], ReservedZone]:
    items = pocket_1_fitting_items()
    zone = ReservedZone(x=1200.0, y=400.0, z=0.0, length=1800.0, width=200.0, height=1000.0, label="POCKET-4-zone")
    return items, zone


# ==========================================================================
# POCKET-5: limite de Group bajo Keep Groups Together -un item FUTURO del
# MISMO grupo que el modulo dueno del pocket debe reusarlo; uno de un grupo
# DISTINTO nunca debe cruzar ese limite (seccion 12/20).
# ==========================================================================
def pocket_5_group_boundary() -> list[WindowItem]:
    out = _panel("OUT", 1800, 200, 200, group="A")
    n1 = _panel("N1", 400, 900, 40, group="A")
    n2 = _panel("N2", 400, 900, 40, group="A")
    fit_same = _panel("FITA", 300, 800, 40, group="A")
    fit_other = _panel("FITB", 300, 800, 40, group="B")
    return [out, n1, n2, fit_same, fit_other]


# ==========================================================================
# POCKET-6: identico a POCKET-5 pero con System bajo Keep Systems Together.
# ==========================================================================
def pocket_6_system_boundary() -> list[WindowItem]:
    out = _panel("OUT", 1800, 200, 200, system="SYS-A")
    n1 = _panel("N1", 400, 900, 40, system="SYS-A")
    n2 = _panel("N2", 400, 900, 40, system="SYS-A")
    fit_same = _panel("FITA", 300, 800, 40, system="SYS-A")
    fit_other = _panel("FITB", 300, 800, 40, system="SYS-B")
    return [out, n1, n2, fit_same, fit_other]


# ==========================================================================
# POCKET-7: propiedad de seguridad bajo Prioritize Delivery Sequence
# (seccion 13/20 del pedido), verificada sobre un dataset adversarial
# variado en vez de una geometria bloqueadora armada mano a mano -construir
# a mano un par exacto aceptado/rechazado resulto fragil (el orden de
# procesamiento cambia COMPLETO entre modos, ya que Delivery Sequence pasa
# a ser la clave DOMINANTE de ordenamiento, no solo un desempate) y ademas
# `preplaced` en este solver (Stage 1, nunca tocado) no talla espacio
# alrededor de piezas preexistentes -cualquier intento con una pieza
# preplaced corria riesgo de una colision espuria ajena al mecanismo que
# se quiere probar. En cambio: un dataset con 7 modelos de panel variados
# (estilo PANEL-REAL de fixtures_panel_module.py) mas un outlier D12-like
# (unica orientacion valida, fuerza un modulo profundo) y Delivery Sequence
# ASIGNADA (semilla fija, reproducible) -verificado empiricamente: bajo
# esta semilla, Prioritize Delivery Sequence reusa MUCHOS MENOS pockets
# que Best Space Utilization (1 vs 15, sobre 23 items) y el resultado
# final nunca contiene un DELIVERY_SEQUENCE_CONFLICT -exactamente la
# propiedad que la seccion 13 exige (mas conservador, nunca crea un
# bloqueo nuevo evitable).
# ==========================================================================
def pocket_7_delivery_dataset() -> list[WindowItem]:
    import random

    models = [
        ("WIN-A", 900, 1200, 30, 4), ("WIN-B", 1200, 1500, 40, 3), ("WIN-C", 600, 900, 25, 5),
        ("DOOR-A", 800, 2100, 45, 2), ("FRAME-A", 450, 2200, 35, 3), ("WIN-D", 1500, 1800, 50, 2),
        ("WIN-E", 700, 1400, 30, 3), ("OUTLIER", 4300.0, 300.0, 300.0, 1),
    ]
    rnd = random.Random(7)
    items: list[WindowItem] = []
    for code, w, h, t, qty in models:
        for q in range(qty):
            items.append(_panel(f"{code}-{q}", w, h, t, delivery_sequence=rnd.randint(1, 5)))
    return items


def pocket_7_container():
    return get_container("40ft_standard")


# ==========================================================================
# POCKET-8: misma arquitectura que POCKET-1 con dimensiones FRACCIONARIAS
# (seccion 21 del pedido -proteger el fix de produccion real: el
# redondeo del knapsack nunca debe generar colision fisica real).
# ==========================================================================
def pocket_8_fractional_geometry() -> list[WindowItem]:
    out = _panel("OUT", 1800.37, 200.11, 200.29)
    n1 = _panel("N1", 400.41, 900.13, 40.07)
    n2 = _panel("N2", 400.22, 900.37, 40.19)
    fit = _panel("FIT", 300.53, 800.29, 40.31)
    return [out, n1, n2, fit]


# ==========================================================================
# POCKET-9: derivado del plan real de 67 lineas que origino este trabajo
# (codigos anonimizados) -el panel D12 "Marcos" real (4521.2 x 300 x 300mm,
# unica orientacion valida) junto a sus companeros reales de modulo (dx
# ~2743.2mm via P2-a) y dos items reales mas chicos (M1/M2, ~609.6mm) que
# en el plan real terminan reusando el pocket que el outlier deja. Container
# 40ft_standard real. Verificado empiricamente: V1 usa 7264.4mm de longitud
# (M1/M2 fuerzan un segundo modulo); con reuso de pocket, los mismos 9
# items caben en 4521.2mm -exactamente la profundidad del modulo del
# outlier, cero longitud adicional.
# ==========================================================================
def pocket_9_real_d12_derived() -> list[WindowItem]:
    outlier = _panel("D12-OUTLIER", 4521.2002, 300.0, 300.0)
    companions = [
        _panel("A1", 1045.8, 2743.2, 155.4),
        _panel("A2", 1045.8, 2743.2, 155.4),
        _panel("A3", 1039.1, 2743.2, 155.4),
        _panel("A4", 1028.6, 2743.2, 155.4),
        _panel("D4", 1016.0, 2743.2, 160.0),
        _panel("D1", 1574.8, 2743.2, 160.0),
    ]
    fits = [_panel("M1", 609.6, 1828.8, 109.1), _panel("M2", 609.6, 1828.8, 109.1)]
    return [outlier, *companions, *fits]


def pocket_9_container():
    return get_container("40ft_standard")


POCKET_REUSE_FIXTURES = {
    "POCKET-1": pocket_1_fitting_items,
    "POCKET-2": pocket_2_no_fit,
    "POCKET-3": pocket_3_multiple_pockets,
    "POCKET-5": pocket_5_group_boundary,
    "POCKET-6": pocket_6_system_boundary,
    "POCKET-8": pocket_8_fractional_geometry,
    "POCKET-9": pocket_9_real_d12_derived,
}
