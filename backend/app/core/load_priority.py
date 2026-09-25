"""Load Priority: capa de presentacion sobre `LoadItem.priority` (CUBOX 2.0,
Load Organization Model Cleanup).

Cubox internamente sigue usando exactamente el mismo `priority: int` de
siempre (1=Highest ... 5=Lowest, 0/fuera de rango = medio -ver
core/scoring.py:_priority_weight, sin cambios). "Load Priority" es solo el
nombre y la representacion USER-FACING: Excel/UI ahora hablan de High/
Normal/Low en vez de un numero crudo, pero el campo que llega al packer/
scoring/persistencia nunca cambio de tipo ni de escala -es la migracion mas
chica y compatible posible (ningun archivo Excel viejo con numeros deja de
importar; ningun test que dependa de la escala 1-5 se rompe).

No reemplaza a Delivery Sequence: Load Priority nunca decide orden de
descarga, solo que items priorizar al ADMITIR carga cuando no entra todo
-ver core/strategies.py/scoring.py, sin cambios de logica aca."""

HIGH = 1
NORMAL = 3
LOW = 5

LOAD_PRIORITY_LABELS = ["High", "Normal", "Low"]

_TEXT_TO_INT = {"high": HIGH, "h": HIGH, "normal": NORMAL, "n": NORMAL, "medium": NORMAL, "med": NORMAL, "low": LOW, "l": LOW}


def parse_load_priority_cell(value) -> int | None:
    """Acepta, en este orden: vacio (None, el caller decide el default -ver
    import_items.py/excel_import.py, que siguen default=0 sin cambios),
    texto High/Normal/Low (case-insensitive, ver `_TEXT_TO_INT`), o -
    compatibilidad con archivos viejos- un entero crudo tal cual. Devuelve
    None si el valor no es reconocible como ninguna de las 2 formas (el
    caller decide si eso es un error soft o hard, igual que hoy)."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in _TEXT_TO_INT:
        return _TEXT_TO_INT[text]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_priority_label(value: int) -> str:
    """Inverso de `parse_load_priority_cell` para mostrar (templates/export):
    1-2 -> High, 4-5 -> Low, cualquier otra cosa (3, 0, fuera de rango) ->
    Normal -mismo criterio de "medio" que ya usa scoring.py para 0/invalido,
    nunca se inventa una regla de bucketing nueva."""
    if 1 <= value <= 2:
        return "High"
    if 4 <= value <= 5:
        return "Low"
    return "Normal"
