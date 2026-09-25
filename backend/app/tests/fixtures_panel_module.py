"""Datasets de regresion para el Panel Module Solver (Stage A.6, seccion
B10 del pedido): PANEL-M1..M5, disenados especificamente para estresar la
seleccion de subconjunto por ANCHO (el problema que el motor generico EMS
nunca resolvia de forma optima, ver Stage A.5). Container de referencia:
40ft_standard, container.width=2352mm.

Todas las alturas quedan <=2350mm (Stage A.5, Parte 5: 2400mm no entra
parado en este contenedor, leccion ya aplicada a los fixtures PANEL-A/B/C/D
-se repite aca para no reintroducir el mismo problema)."""

from app.models.schemas import ItemType, WindowItem


def _panel(code: str, w: float, h: float, t: float = 40.0) -> WindowItem:
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=15.0, quantity=1, item_type=ItemType.PANEL, stackable=False,
    )


# ==========================================================================
# PANEL-M1: combinaciones de ancho EXACTAS -4 anchos que suman EXACTAMENTE
# 2352mm (700+650+600+402), repetidos n veces -el caso "de libro" donde un
# solver de subconjunto optimo debe llenar cada modulo al 100%.
# ==========================================================================
def panel_m1(repeats: int = 6) -> list[WindowItem]:
    group = [(700, 2300), (650, 2100), (600, 1900), (402, 1500)]
    out = []
    for r in range(repeats):
        for i, (w, h) in enumerate(group):
            out.append(_panel(f"M1-{r}-{i}", w, h))
    return out


# ==========================================================================
# PANEL-M2: orden de entrada ADVERSARIAL -anchos grandes primero que NO
# combinan limpio entre si, con anchos pequenos (que SI cerrarian el hueco)
# listados al final. Un first-fit/greedy por orden de entrada desperdicia
# ancho; un solver de subconjunto (sin importar el orden de entrada) debe
# encontrar la combinacion tensa igual.
# ==========================================================================
def panel_m2(repeats: int = 5) -> list[WindowItem]:
    # 900+900 = 1800 (residual 552 si un greedy se detiene ahi); la
    # combinacion tensa real es 900+900+300+250 = 2350 (residual 2), pero
    # 300/250 aparecen ULTIMO en el orden de entrada.
    group = [(900, 2200), (900, 2000), (300, 1200), (250, 1000)]
    out = []
    for r in range(repeats):
        for i, (w, h) in enumerate(group):
            out.append(_panel(f"M2-{r}-{i}", w, h))
    return out


# ==========================================================================
# PANEL-M3: MULTIPLES subconjuntos posibles, uno claramente mejor -pool
# {1200,1150,700,700,700}: {1200,1150}=2350 (residual 2mm) vs
# {700,700,700}=2100 (residual 252mm) -ambas son subconjuntos VALIDOS
# (cada uno cabe), pero uno es 125x mas ajustado que el otro. Repetido n
# veces para poblar varios modulos con la misma decision.
# ==========================================================================
def panel_m3(repeats: int = 5) -> list[WindowItem]:
    group = [(1200, 2300), (1150, 2100), (700, 1400), (700, 1300), (700, 1200)]
    out = []
    for r in range(repeats):
        for i, (w, h) in enumerate(group):
            out.append(_panel(f"M3-{r}-{i}", w, h))
    return out


# ==========================================================================
# PANEL-M4: alto Y ancho mixtos simultaneamente -estresa seleccion de
# subconjunto (ancho) Y gradiente bilateral (alto) al mismo tiempo, seis
# anchos/altos bien distintos entre si, sin duplicados exactos.
# ==========================================================================
def panel_m4(repeats: int = 5) -> list[WindowItem]:
    group = [
        (650, 2300), (620, 2100), (580, 1900), (530, 1700), (400, 1400), (300, 1000),
    ]
    out = []
    for r in range(repeats):
        for i, (w, h) in enumerate(group):
            out.append(_panel(f"M4-{r}-{i}", w, h))
    return out


# ==========================================================================
# PANEL-M5: dataset "estilo real" -distribucion representativa de un
# pedido de ventanas/paneles real (modelos con cantidades variadas, no un
# stress sintetico puro).
# ==========================================================================
def panel_m5() -> list[WindowItem]:
    models = [
        ("WIN-A", 900, 1200, 8),
        ("WIN-B", 1200, 1500, 6),
        ("WIN-C", 600, 900, 10),
        ("DOOR-A", 800, 2100, 4),
        ("FRAME-A", 450, 2200, 5),
        ("WIN-D", 1500, 1800, 3),
    ]
    out = []
    for code, w, h, qty in models:
        for q in range(qty):
            out.append(_panel(f"{code}-{q}", w, h))
    return out


# ==========================================================================
# PANEL-REAL: dataset "estilo real" con GROSOR variable (Stage A.6.1,
# seccion 8 del pedido: "Use varied: width, height, thickness, quantity")
# -M1..M5 usan grosor uniforme (40mm) sin querer; esto ejercita el caso que
# _panel_modules (agrupa por p.x, el borde de PUERTA) manejaria mal si
# hubiera un solo modulo logico con piezas de distinto grosor alineadas
# por su borde de FONDO -physical_depth_modules (solapamiento de
# intervalo) debe seguir dando el resultado fisico correcto.
# ==========================================================================
def panel_real() -> list[WindowItem]:
    models = [
        ("WIN-A", 900, 1200, 30, 8),
        ("WIN-B", 1200, 1500, 40, 6),
        ("WIN-C", 600, 900, 25, 10),
        ("DOOR-A", 800, 2100, 45, 4),
        ("FRAME-A", 450, 2200, 35, 5),
        ("WIN-D", 1500, 1800, 50, 3),
        ("WIN-E", 700, 1400, 30, 6),
    ]
    out = []
    for code, w, h, t, qty in models:
        for q in range(qty):
            out.append(_panel(f"{code}-{q}", w, h, t))
    return out


PANEL_MODULE_FIXTURES = {
    "PANEL-M1": panel_m1, "PANEL-M2": panel_m2, "PANEL-M3": panel_m3,
    "PANEL-M4": panel_m4, "PANEL-M5": panel_m5,
}
