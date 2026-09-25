"""CUBOX 2.0 -- Keep Groups Together, correccion "CORRECTNESS GATE --
COMPLETE-GROUP-BEFORE-PARTIAL + SEQUENCE FILTER SAFETY", seccion 7/8:
prueba geometrica de por que la ventana en X que Stage 3.1 agrego al
chequeo de ciclo de secuencia (panel_pocket_reuse.py y section_
coordinator.py:_try_backfill_into_module) NO es exacta, y por que fue
revertida en vez de conservada.

El predicado de bloqueo lateral real (core/sequence.py:
compute_blocking_pairs / _all_blocking_pairs) es `abox.max_x <= bbox.x`
(el bloqueador esta COMPLETAMENTE entre la puerta y la pieza bloqueada) +
overlap Y-Z -- sin ningun termino de distancia en X. Este test construye
dos piezas reales, geometricamente validas (no se solapan, mismo Y/Z,
nada colocado entre ambas), separadas 8500mm en un contenedor de 40ft, y
verifica que:

1. El grafo CANONICO (sin cota de distancia) SI encuentra la relacion de
   bloqueo/dependencia directa entre ambas -no es un caso raro, es el
   comportamiento documentado de compute_blocking_pairs.
2. Una ventana en X del tamano que Stage 3.1 uso (~3000mm minimo) NO
   incluiria a la pieza lejana como vecino local -si ese grafo AISLADO
   (bounded) se usara para el chequeo de ciclo, la arista real de
   dependencia se perderia en silencio.

Esto prueba la seccion 8 del pedido de correccion: el chequeo acotado y
el chequeo canonico NO producen resultados identicos en general -por lo
tanto (seccion 7-B) se usa el chequeo canonico completo, nunca una
heuristica de distancia, en ningun chequeo de seguridad/correctitud de
secuencia."""

from app.core.sequence import compute_blocking_pairs, compute_unload_dependencies
from app.models.schemas import ItemType, PlacedPiece


def _piece(id_, x, y, dx, dy, dz, z=0.0):
    return PlacedPiece(
        id=id_, code=id_, description=id_, weight=10.0, stackable=True,
        x=x, y=y, z=z, dx=dx, dy=dy, dz=dz, orientation_label="FIXED",
        source_width=1.0, source_height=1.0, source_thickness=1.0, item_type=ItemType.PANEL,
    )


def _bounded_x_neighbors(all_pieces, anchor, x_margin):
    """Reproduce EXACTAMENTE el filtro que Stage 3.1 agregaba (ya
    revertido en el codigo de produccion) -se reconstruye aca solo para
    demostrar, de forma aislada, que habria excluido a la pieza lejana."""
    return [
        p for p in all_pieces
        if p.x < anchor.x + anchor.dx + x_margin and p.x + p.dx > anchor.x - x_margin
    ]


def test_direct_block_survives_large_x_separation():
    """FAR_BLOCKER (cerca de la puerta) y TARGET (cerca del fondo, a
    8500mm de distancia en un contenedor de 40ft) comparten el mismo
    rango Y/Z y no tienen NINGUNA pieza colocada entre ambas -por
    construccion, FAR_BLOCKER es el bloqueador DIRECTO (y unico) de
    TARGET segun compute_blocking_pairs, sin importar la distancia."""
    far_blocker = _piece("FAR_BLOCKER", x=0.0, y=0.0, dx=500.0, dy=1000.0, dz=300.0)
    target = _piece("TARGET", x=8500.0, y=0.0, dx=500.0, dy=1000.0, dz=300.0)

    pairs = compute_blocking_pairs([far_blocker, target])
    assert ("FAR_BLOCKER", "TARGET") in pairs, "el bloqueo lateral no tiene cota de distancia en X -este par debe existir"

    deps = compute_unload_dependencies([far_blocker, target])
    assert "FAR_BLOCKER" in deps["TARGET"], "TARGET debe depender de FAR_BLOCKER (debe salir primero) pese a los 8500mm de separacion"


def test_x_window_would_have_dropped_the_real_edge():
    """Reproduce el filtro acotado que Stage 3.1 agregaba: con la misma
    geometria del test anterior, una ventana de ~3000mm alrededor de
    TARGET NO incluye a FAR_BLOCKER como vecino local -el grafo acotado
    quedaria CIEGO a una dependencia real. Prueba directa de la seccion 8
    del pedido de correccion: el chequeo acotado y el canonico NO son
    equivalentes en general."""
    far_blocker = _piece("FAR_BLOCKER", x=0.0, y=0.0, dx=500.0, dy=1000.0, dz=300.0)
    target = _piece("TARGET", x=8500.0, y=0.0, dx=500.0, dy=1000.0, dz=300.0)

    x_margin = max(target.dz * 4.0, 3000.0)  # misma formula que Stage 3.1 usaba
    bounded_neighbors = _bounded_x_neighbors([far_blocker], target, x_margin)
    assert far_blocker not in bounded_neighbors, "la ventana en X SI excluye a FAR_BLOCKER -confirma que el filtro acotado pierde la arista real"

    # El grafo CANONICO (sin ventana) si conserva la arista -- confirma
    # que "usar siempre el chequeo completo" (seccion 7-B/8 del pedido)
    # es la unica opcion correcta aca, nunca una heuristica de distancia.
    deps_canonical = compute_unload_dependencies([far_blocker, target])
    deps_bounded = compute_unload_dependencies(bounded_neighbors + [target])
    assert deps_canonical["TARGET"] == ["FAR_BLOCKER"]
    assert deps_bounded["TARGET"] == [], "el grafo acotado (bounded_neighbors excluye a FAR_BLOCKER) pierde la dependencia real -divergencia real, no solo teorica"
