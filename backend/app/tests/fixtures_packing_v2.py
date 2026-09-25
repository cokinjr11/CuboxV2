"""Datasets de regresion persistentes para el prototipo Packing Engine V2
(seccion 14 del pedido de esta tarea): BOX-A/B/C/D, PANEL-A/B/C/D, mas un
dataset "real/manual-test" que mezcla ambos tipos como un pedido tipico. Se
definen una sola vez aca para que el benchmark OLD-vs-V2 (seccion 23) y
cualquier test de regresion (seccion 22) usen EXACTAMENTE los mismos datos.

Cada funcion devuelve una lista nueva de WindowItem (nunca instancias
compartidas: los solvers marcan estado on cada corrida)."""

from app.models.schemas import ItemType, WindowItem


def _box(code: str, w: float, h: float, t: float, qty: int = 1) -> WindowItem:
    return WindowItem(
        code=code,
        description=f"Box {code}",
        width=w,
        height=h,
        thickness=t,
        weight=20.0,
        quantity=qty,
        item_type=ItemType.BOX,
        stackable=True,
    )


def _panel(code: str, w: float, h: float, t: float = 40.0) -> WindowItem:
    return WindowItem(
        code=code,
        description=f"Panel {code}",
        width=w,
        height=h,
        thickness=t,
        weight=15.0,
        quantity=1,
        item_type=ItemType.PANEL,
        stackable=False,
    )


# ==========================================================================
# BOX-A: muchas piezas del MISMO tamano (stress de evaluaciones duplicadas,
# sin variedad -sirve de piso de referencia: cualquier motor razonable debe
# lograr ~100% de utilizacion volumetrica aca).
# ==========================================================================
def box_a(n: int = 200) -> list[WindowItem]:
    return [_box(f"BXA{i}", 500, 500, 500) for i in range(n)]


# ==========================================================================
# BOX-B: dimensiones fuertemente mixtas (5 formas muy distintas entre si,
# repetidas) -el dataset de mezcla ya usado en el benchmark de performance
# de esta tarea, conservado aca como fixture formal.
# ==========================================================================
def box_b(n: int = 150) -> list[WindowItem]:
    specs = [(600, 600, 600), (500, 500, 500), (700, 400, 400), (450, 450, 450), (800, 500, 500)]
    out = []
    for i in range(n):
        w, h, t = specs[i % len(specs)]
        out.append(_box(f"BXB{i}", w, h, t))
    return out


# ==========================================================================
# BOX-C: tamanos disenados para producir huecos residuales ANGOSTOS -pares
# de piezas cuya diferencia de ancho es pequena (p.ej. 600 vs 620) generan
# franjas "muertas" de ~20mm al colocarse una junto a otra, el caso que
# _sliver_penalty existe para castigar.
# ==========================================================================
def box_c(n: int = 150) -> list[WindowItem]:
    specs = [(600, 600, 600), (620, 600, 600), (580, 600, 600), (610, 600, 600), (590, 600, 600)]
    out = []
    for i in range(n):
        w, h, t = specs[i % len(specs)]
        out.append(_box(f"BXC{i}", w, h, t))
    return out


# ==========================================================================
# BOX-D: orden de entrada deliberadamente MALO para un first-fit ingenuo
# -grande, chico, grande, chico... de forma que procesar en orden de
# input puro deja huecos que solo un item pequeno "fuera de turno" puede
# llenar limpiamente (seccion 5: '650mm Box before 900mm Box si...').
# ==========================================================================
def box_d(n: int = 150) -> list[WindowItem]:
    big = (900, 600, 600)
    small = (250, 600, 600)
    out = []
    for i in range(n):
        w, h, t = big if i % 2 == 0 else small
        out.append(_box(f"BXD{i}", w, h, t))
    return out


# ==========================================================================
# PANEL-A: alturas mixtas (el dataset bilateral de 10 alturas ya usado para
# depurar el gradiente alto-cerca-de-pared, repetido para llenar un modulo
# completo).
#
# Stage A.5, Parte 5 del pedido: la altura util interior del contenedor
# 40ft_standard es 2393mm -un panel de 2400mm de alto NO PUEDE pararse
# (verificado en aislamiento contra core/packer.py: ambos motores lo
# acuestan correctamente, "physical feasibility always wins", nunca fue
# un bug de orientacion). El maximo baja a 2350mm (holgura real) para que
# el fixture ejercite el gradiente bilateral con piezas que SI paran,
# reflejando el perfil operativo real de Panels & Fragile -no se toco
# orientation.py, la regla fisica es correcta y se deja como esta.
# ==========================================================================
def panel_a(n: int = 40) -> list[WindowItem]:
    heights = [2350, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    return [_panel(f"PNA{i}", 400, heights[i % len(heights)]) for i in range(n)]


# ==========================================================================
# PANEL-B: anchos mixtos (mismo alto, ancho variable) -stress del llenado
# lateral cuando lo que varia es cuanto ancho lateral consume cada pieza,
# no su altura.
# ==========================================================================
def panel_b(n: int = 40) -> list[WindowItem]:
    widths = [600, 500, 450, 400, 350, 300, 250, 200]
    return [_panel(f"PNB{i}", widths[i % len(widths)], 2200) for i in range(n)]


# ==========================================================================
# PANEL-C: orden de entrada deliberadamente MALO (alturas alternando
# bajo/alto en vez de descendente) -si el motor procesara estrictamente en
# orden de input, el primer panel (bajo) ocuparia la pared en vez del mas
# alto disponible. Alturas <=2350mm por la misma razon que PANEL-A (Stage
# A.5, Parte 5: 2400mm no entra parado en este contenedor).
# ==========================================================================
def panel_c(n: int = 40) -> list[WindowItem]:
    heights = [600, 2350, 800, 2200, 1000, 2000, 1200, 1900, 1600, 2300]
    return [_panel(f"PNC{i}", 400, heights[i % len(heights)]) for i in range(n)]


# ==========================================================================
# PANEL-D: tamanos que un greedy ingenuo tiende a dejar con huecos internos
# dispersos (varias combinaciones de ancho que no suman limpio al ancho
# lateral del contenedor) -el caso disenado para medir "few concentrated
# gaps vs many scattered gaps" (seccion 10).
#
# Stage A.5, Parte 5 del pedido: la version original de este fixture tenia
# w=340/h=2400 -esa combinacion NO ENTRA PARADA en este contenedor (altura
# interior 2393mm < 2400mm), asi que 5/40 piezas se acostaban en AMBOS
# motores (verificado en aislamiento, no es un bug de V2 ni de
# orientation.py) y dominaban las metricas de hueco con una geometria que
# nunca refleja el perfil operativo real de Panels & Fragile (paneles
# siempre de pie). Alturas bajadas a <=2350mm, anchos verificados
# individualmente contra core/packer.py para confirmar que las 8
# combinaciones quedan de pie antes de fijarlas aca.
# ==========================================================================
def panel_d(n: int = 40) -> list[WindowItem]:
    widths = [370, 410, 340, 460, 390, 430, 350, 480]
    heights = [2200, 1800, 2350, 1500, 2000, 1300, 2100, 1100]
    return [_panel(f"PND{i}", widths[i % len(widths)], heights[i % len(heights)]) for i in range(n)]


# ==========================================================================
# REAL-MIX: dataset "estilo pedido real" -Boxes y Panels mezclados en
# proporciones y tamanos representativos de un caso manual de prueba, no un
# stress sintetico (seccion 14: 'at least one real/manual-test style
# dataset').
# ==========================================================================
def real_mix() -> list[WindowItem]:
    boxes = [
        _box(f"RMB{i}", w, h, t)
        for i, (w, h, t) in enumerate(
            [(600, 400, 300)] * 12
            + [(500, 500, 400)] * 10
            + [(800, 600, 500)] * 6
            + [(350, 350, 350)] * 15
            + [(700, 450, 450)] * 8
        )
    ]
    panels = [_panel(f"RMP{i}", 400, h) for i, h in enumerate([2300, 2100, 1900, 1700, 1500, 1300, 1100] * 3)]
    return boxes + panels


BOX_FIXTURES = {"BOX-A": box_a, "BOX-B": box_b, "BOX-C": box_c, "BOX-D": box_d}
PANEL_FIXTURES = {"PANEL-A": panel_a, "PANEL-B": panel_b, "PANEL-C": panel_c, "PANEL-D": panel_d}
