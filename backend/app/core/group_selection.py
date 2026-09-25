"""Keep Groups Together -- seleccion de Grupos COMPLETOS antes del packing
espacial (pedido "CUBOX 2.0 -- KEEP GROUPS TOGETHER -- COMPLETE-GROUP
SELECTION BEFORE PACKING").

Correccion semantica: Keep Groups Together NO debe optimizar primero por
cantidad total de piezas cargadas -debe preferir MAS Grupos COMPLETOS sobre
MAS piezas sueltas de Grupos parciales (seccion 1 del pedido: es mejor
embarcar menos Grupos completos que mas piezas de varios Grupos parciales).
Esto es una etapa NUEVA que corre ANTES del loop de 7 estrategias de
core/optimize.py -decide QUE Grupos intentar cargar completos, filtra la
lista de items a esos Grupos, y recien entonces el motor de packing normal
(Pocket Reuse para Panels, EMS para Boxes, packer legacy para Pallet/Custom
-nunca se reescribe ninguno de esos) corre sobre el subconjunto seleccionado
(seccion 6 del pedido: "run the normal appropriate packing engine").

Prioridad lexicografica (seccion 1/4 del pedido, NUNCA colapsada en un
promedio ponderado que pueda cambiar un Grupo completo por unas pocas
piezas sueltas):

    1. Restricciones fisicas duras (siempre, en cualquier motor)
    2. Maximizar cantidad de Grupos COMPLETOS
    3. Minimizar cantidad de Grupos PARCIALES
    4. Maximizar piezas cargadas DENTRO de los Grupos ya seleccionados
    5. Preservar cohesion espacial de Grupo (ya lo hace el motor via
       _cluster_key en panel_module_solver.py/packer_v2.py, sin tocar)
    6. Compacidad / longitud usada / reduccion de huecos (idem, sin tocar)

Algoritmo -- "greedy bin-covering con verificacion real" (seccion 2/16 del
pedido: nunca un item-por-item ciego, pero tampoco un 2^N -exploracion
acotada, bounded por cantidad de Grupos, cada decision VERIFICADA con el
motor de packing real, nunca solo una estimacion de volumen):

    1. Cada Grupo se ordena por (prioridad promedio ascendente, volumen
       total ascendente, Delivery Sequence minimo ascendente, unidades
       descendente, nombre) -- seccion 5: intentar primero los Grupos que
       "cuestan menos" (mas facil completarlos, maximiza CANTIDAD de
       Grupos completos, mismo principio que el clasico greedy de
       maximizar CANTIDAD de items en una mochila) con Load
       Priority/Delivery Sequence como desempate real, nunca inventado.
    2. Para determinar si un Grupo puede considerarse COMPLETO en algun
       contexto (aislado O compitiendo con otros ya seleccionados) SIN
       jamas confundir una restriccion FISICA real (una pieza que
       literalmente no entra, sea cual sea el contexto) con un
       desplazamiento por competencia de espacio con otro Grupo (seccion 1:
       "Hard physical constraints" es la prioridad MAS alta, por encima de
       completar Grupos), primero se mide la capacidad SOLA de cada Grupo
       (baseline, solo ese Grupo + lo ya preplaced) -- ninguna pieza que ya
       falle SOLA se le puede echar la culpa a la seleccion de Grupos.
    3. Recien entonces se procesan los Grupos en el orden del paso 1: cada
       candidato se agrega de a uno al pool ya aceptado y se vuelve a
       correr el motor real (mismo pack_fn que va a usar la corrida final,
       UNA sola estrategia de verificacion, no las 7 -acotado). Si el
       Grupo candidato alcanza exactamente su propio baseline (ninguna
       pieza adicional perdida por competir con los Grupos ya aceptados),
       se acepta (se compromete al pool); si no, se descarta ENTERO para
       esta pasada (nunca una inclusion parcial -seccion 3: "the Group is
       the primary business unit") y NO vuelve a intentarse.
    4. Items sin Group (`group == ""`) no tienen cohesion que proteger -se
       agregan al final, como relleno de lo que sobre (seccion 1: "unused
       space may be acceptable if the alternative is fragmenting Groups"
       aplica a Grupos reales; un item suelto sin Grupo no fragmenta nada).
"""

from dataclasses import dataclass

from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone
from app.models.schemas import ContainerSpec, OptimizationMode, PlacedPiece, UnloadedItem, WindowItem

_UNGROUPED_KEY = ""


@dataclass
class GroupCompletionReport:
    """Metrica reportable (seccion 10 del pedido) -- se puede calcular
    DESPUES de cualquier packing (no solo el de esta etapa) a partir de
    placed/unloaded finales, para que sirva como medida de calidad
    independiente de como se llego a ese resultado."""

    group: str
    total_units: int
    loaded_units: int
    status: str  # "COMPLETE" | "PARTIAL" | "NOT_LOADED"

    @property
    def completion_pct(self) -> float:
        return 100.0 * self.loaded_units / self.total_units if self.total_units else 0.0


def group_completion_report(items: list[WindowItem], placed: list[PlacedPiece]) -> list[GroupCompletionReport]:
    """Deriva el estado COMPLETE/PARTIAL/NOT_LOADED de cada Grupo (seccion
    10 del pedido) a partir de la lista de items ORIGINAL (fuente de
    verdad de cuantas unidades tiene cada Grupo en total, sin depender de
    que ninguna haya sido excluida) y las piezas finales colocadas. Items
    sin Group (`group == ""`) se excluyen del reporte -sin cohesion que
    medir, seccion 1."""
    totals: dict[str, int] = {}
    for w in items:
        key = w.group or _UNGROUPED_KEY
        if key == _UNGROUPED_KEY:
            continue
        totals[key] = totals.get(key, 0) + w.quantity

    loaded: dict[str, int] = {}
    for p in placed:
        key = p.group or _UNGROUPED_KEY
        if key == _UNGROUPED_KEY:
            continue
        loaded[key] = loaded.get(key, 0) + 1

    reports = []
    for key, total in totals.items():
        n_loaded = loaded.get(key, 0)
        if n_loaded >= total:
            status = "COMPLETE"
        elif n_loaded <= 0:
            status = "NOT_LOADED"
        else:
            status = "PARTIAL"
        reports.append(GroupCompletionReport(group=key, total_units=total, loaded_units=n_loaded, status=status))
    reports.sort(key=lambda r: (r.status != "COMPLETE", r.status != "PARTIAL", -r.total_units, r.group))
    return reports


def _group_volume(ws: list[WindowItem]) -> float:
    return sum(w.width * w.height * w.thickness * w.quantity for w in ws)


def _group_units(ws: list[WindowItem]) -> int:
    return sum(w.quantity for w in ws)


def _group_avg_priority(ws: list[WindowItem]) -> float:
    n = _group_units(ws)
    if n == 0:
        return 0.0
    return sum(w.priority * w.quantity for w in ws) / n


def _group_min_delivery(ws: list[WindowItem]) -> float:
    seqs = [w.delivery_sequence for w in ws if w.delivery_sequence is not None]
    return min(seqs) if seqs else float("inf")


def _group_cluster_key(item) -> str:
    """Cluster key por defecto -Grupo (seccion 2 del pedido "KEEP SYSTEMS
    TOGETHER -- REUSE THE APPROVED SECTION-FIRST 3D ENGINE"): identico al
    comportamiento historico de este modulo, extraido a funcion para poder
    pasarlo como default explicito de `select_complete_clusters` sin
    cambiar NADA del camino KEEP_GROUPS existente. Funciona sobre
    WindowItem O PlacedPiece por igual -ambos exponen `.group` como texto
    plano (seccion 33 del pedido: reuso conservador, sin duplicar la
    logica de agrupamiento)."""
    return (item.group or "").strip()


def _system_cluster_key(item) -> str:
    """Cluster key para Keep Systems Together (seccion 2 del pedido): la
    UNICA diferencia real entre los dos modos -mismo motor, mismo
    algoritmo, distinto campo de agrupamiento. Funciona sobre WindowItem O
    PlacedPiece por igual, igual que _group_cluster_key."""
    return (item.system or "").strip()


def _placed_count_for_group(placed: list[PlacedPiece], group: str, cluster_key=_group_cluster_key) -> int:
    return sum(1 for p in placed if cluster_key(p) == group)


def _run_greedy_pass(
    order: list[str],
    grouped: dict[str, list[WindowItem]],
    baseline_loaded: dict[str, int],
    container: ContainerSpec,
    pack_fn,
    reserved_zones: list[ReservedZone],
    clearance: float,
    preplaced: list[PlacedPiece] | None,
    verification_strategy: str,
    optimization_mode: OptimizationMode = OptimizationMode.KEEP_GROUPS,
    cluster_key=_group_cluster_key,
) -> tuple[list[WindowItem], list[str], list[str]]:
    """UNA pasada greedy (seccion 2/3 del docstring del modulo) sobre un
    orden de Grupos/Sistemas ya dado -nunca decide el orden por si misma,
    ver select_complete_clusters. Devuelve (selected_items, accepted_keys,
    excluded_keys) para ESE orden en particular. `optimization_mode`
    (seccion 18 del pedido de Keep Systems: pasar el modo REAL que se esta
    verificando, no siempre KEEP_GROUPS hardcodeado -el `_cluster_key`
    interno del solver de Panels tambien depende de el, afecta que tan
    realista es la verificacion de bilateral pairing dentro de un
    modulo)."""
    selected_items: list[WindowItem] = []
    accepted_keys: list[str] = []
    excluded_keys: list[str] = []

    for key in order:
        trial_items = selected_items + grouped[key]
        trial_result = pack_fn(
            trial_items, container, optimization_mode, reserved_zones, clearance,
            verification_strategy, preplaced,
        )
        candidate_loaded = _placed_count_for_group(trial_result.placed, key, cluster_key)
        if candidate_loaded < baseline_loaded[key]:
            # Desplazado por competencia de espacio con clusters ya
            # aceptados -se descarta ENTERO, nunca parcial (seccion 3/4).
            excluded_keys.append(key)
            continue
        # Alcanzo (al menos) lo que podia lograr solo -verificar que los
        # clusters YA aceptados sigan intactos en este mismo trial antes de
        # comprometer (defensa adicional, seccion 4: nunca sacrificar un
        # cluster ya completo por uno nuevo).
        regression = any(
            _placed_count_for_group(trial_result.placed, accepted_key, cluster_key) < baseline_loaded[accepted_key]
            for accepted_key in accepted_keys
        )
        if regression:
            excluded_keys.append(key)
            continue
        selected_items = trial_items
        accepted_keys.append(key)

    return selected_items, accepted_keys, excluded_keys


def select_complete_clusters(
    items: list[WindowItem],
    container: ContainerSpec,
    pack_fn,
    reserved_zones: list[ReservedZone] | None,
    clearance: float,
    preplaced: list[PlacedPiece] | None,
    verification_strategy: str = "group_and_size",
    cluster_key=_group_cluster_key,
    optimization_mode: OptimizationMode = OptimizationMode.KEEP_GROUPS,
) -> tuple[list[WindowItem], list[GroupCompletionReport], list[WindowItem], list[str]]:
    """Etapa de seleccion de clusters completos (seccion 2/3/4 del pedido
    original de Keep Groups Together; generalizada en el pedido "KEEP
    SYSTEMS TOGETHER -- REUSE THE APPROVED SECTION-FIRST 3D ENGINE",
    seccion 2/33: MISMO algoritmo, `cluster_key` decide si el cluster es
    el Grupo o el Sistema de cada item -nunca dos copias que puedan
    divergir). Devuelve (selected_items, reports, excluded_items,
    accepted_order) -- accepted_order (Section Coordinator, core/
    section_coordinator.py) es el orden EXACTO en que los clusters
    aceptados fueron comprometidos (el cluster parcial de cola, si existe,
    siempre al final) -el coordinador de Secciones lo usa para procesar
    un cluster activo a la vez, secuencialmente, en vez de mezclar todos
    los clusters seleccionados en una sola pasada. selected_items es el
    subconjunto (clusters completos aceptados + items sin cluster) que el
    loop normal de 7 estrategias de run_optimization debe usar en
    lugar de `items`; excluded_items son los items de clusters NO
    seleccionados (nunca llegan al motor de packing en absoluto, seccion
    12 del pedido original: "It must not pull individual items from
    Groups that were not selected" -queda garantizado por construccion,
    no por un chequeo aparte, porque Pocket Reuse/el motor que sea jamas
    los ve).

    Multi-arranque acotado (bug real encontrado validando el ejemplo de la
    seccion 8 del pedido original, A=30/B=25/C=20/capacidad~50): un greedy
    de UNA sola pasada, siempre en el mismo orden ascendente por volumen,
    puede "quedar atado" a su primera aceptacion -en el ejemplo real,
    aceptar C (el mas chico) primero deja un remanente de espacio que, por
    como el motor de packing existente arma clusters bajo Keep Groups/
    Systems Together (_cluster_key del solver, nunca tocado aca), no le
    alcanza a NINGUN otro cluster para igualar su propio baseline -aunque
    'A solo' (30 unidades, tambien 1 cluster completo) hubiera sido un
    resultado mejor por la seccion 5 ('same complete cluster count ->
    maximize loaded units'). Un greedy de una sola pasada nunca puede
    descubrir eso porque nunca reconsidera su primera eleccion. Se corren
    tantas pasadas greedy como clusters haya (cada una empezando por un
    cluster distinto, el resto en el mismo orden de respaldo) -acotado a
    O(numero de clusters) pasadas, cada una O(numero de clusters) llamadas
    de packing real (nunca 2^N, mismo principio de 'bounded, not
    brute-force' que ya usa Pocket Reuse) -y se conserva la de MEJOR
    resultado por la prioridad lexicografica exacta de la seccion 4: mas
    clusters completos primero, despues mas unidades cargadas totales."""
    reserved_zones = reserved_zones or []
    ungrouped = [w for w in items if not cluster_key(w)]
    grouped: dict[str, list[WindowItem]] = {}
    for w in items:
        key = cluster_key(w)
        if key:
            grouped.setdefault(key, []).append(w)

    if not grouped:
        # Nada que seleccionar -comportamiento identico a antes (item-por-
        # item, cohesion espacial via _cluster_key del motor real).
        return list(items), [], [], []

    default_order = sorted(
        grouped.keys(),
        key=lambda k: (
            _group_avg_priority(grouped[k]),
            _group_volume(grouped[k]),
            _group_min_delivery(grouped[k]),
            -_group_units(grouped[k]),
            k,
        ),
    )

    # Paso 2 del docstring del modulo: capacidad SOLA de cada cluster
    # (nunca confundir una restriccion fisica real con un desplazamiento
    # por competencia de espacio con otro cluster -seccion 1, "Hard
    # physical constraints" es la prioridad mas alta). Calculado UNA vez,
    # reusado por todas las pasadas greedy de abajo.
    baseline_loaded: dict[str, int] = {}
    for key in default_order:
        solo_result = pack_fn(
            grouped[key], container, optimization_mode, reserved_zones, clearance,
            verification_strategy, preplaced,
        )
        baseline_loaded[key] = _placed_count_for_group(solo_result.placed, key, cluster_key)

    candidate_orders = [default_order]
    for first_key in default_order:
        if first_key == default_order[0]:
            continue  # ya es el default_order de arriba
        alt_order = [first_key] + [k for k in default_order if k != first_key]
        candidate_orders.append(alt_order)

    best_selected_items: list[WindowItem] = []
    best_accepted_keys: list[str] = []
    best_excluded_keys: list[str] = list(grouped.keys())
    best_score: tuple[int, int, int] | None = None

    for order in candidate_orders:
        selected_items, accepted_keys, excluded_keys = _run_greedy_pass(
            order, grouped, baseline_loaded, container, pack_fn, reserved_zones, clearance, preplaced, verification_strategy,
            optimization_mode, cluster_key,
        )
        # Prioridad lexicografica EXACTA de la seccion 15 del pedido de
        # correccion (COMPLETED CLUSTER PAYLOAD, no complete_cluster_count):
        # 1) unidades que pertenecen a clusters 100% COMPLETOS (nunca
        # cuenta de nombres de cluster -un cluster grande completo debe
        # ganarle a varios chicos completos, seccion 14/21: "Do not
        # choose 3 tiny complete clusters... over 1 major complete
        # cluster... only because 3 > 1"); 2) cantidad de clusters
        # completos SOLO como desempate entre payloads iguales; 3) total
        # de unidades aceptadas (incluye baseline-parciales genuinos,
        # ver mas abajo) como ultimo desempate.
        payload_complete = sum(baseline_loaded[k] for k in accepted_keys if baseline_loaded[k] >= _group_units(grouped[k]))
        num_complete = sum(1 for k in accepted_keys if baseline_loaded[k] >= _group_units(grouped[k]))
        total_units_accepted = sum(baseline_loaded[k] for k in accepted_keys)
        score = (payload_complete, num_complete, total_units_accepted)
        if best_score is None or score > best_score:
            best_score = score
            best_selected_items = selected_items
            best_accepted_keys = accepted_keys
            best_excluded_keys = excluded_keys

    actual_loaded: dict[str, int] = {k: baseline_loaded[k] for k in best_accepted_keys}

    # Seccion 16 del pedido original: permitir COMO MAXIMO un cluster
    # parcial "de cola" -una vez que ya no entra ningun cluster COMPLETO
    # mas, sumar UN candidato adicional con lo que SI entre (parcial) en
    # vez de dejar ese espacio sin usar, eligiendo el que mas unidades
    # aporte. Nunca mas de uno (seccion 16: "Do not scatter partial
    # shipment across multiple new Groups simply to fill every last cubic
    # millimeter"), y nunca a costa de un cluster ya aceptado (misma
    # verificacion de regresion que el greedy principal).
    trailing_partial_key = None
    trailing_partial_units = 0
    trailing_partial_items: list[WindowItem] = []
    for key in default_order:
        if key not in best_excluded_keys:
            continue
        trial_items = best_selected_items + grouped[key]
        trial_result = pack_fn(
            trial_items, container, optimization_mode, reserved_zones, clearance,
            verification_strategy, preplaced,
        )
        candidate_loaded = _placed_count_for_group(trial_result.placed, key, cluster_key)
        if candidate_loaded <= 0:
            continue
        regression = any(
            _placed_count_for_group(trial_result.placed, accepted_key, cluster_key) < baseline_loaded[accepted_key]
            for accepted_key in best_accepted_keys
        )
        if regression:
            continue
        if candidate_loaded > trailing_partial_units:
            trailing_partial_units = candidate_loaded
            trailing_partial_key = key
            trailing_partial_items = trial_items

    if trailing_partial_key is not None:
        best_selected_items = trailing_partial_items
        best_accepted_keys = best_accepted_keys + [trailing_partial_key]
        best_excluded_keys = [k for k in best_excluded_keys if k != trailing_partial_key]
        actual_loaded[trailing_partial_key] = trailing_partial_units

    selected_items = best_selected_items + ungrouped
    excluded_items = [w for key in best_excluded_keys for w in grouped[key]]

    reports = []
    for key in default_order:
        total = _group_units(grouped[key])
        if key in best_accepted_keys:
            loaded = actual_loaded[key]
            status = "COMPLETE" if loaded >= total else "PARTIAL"
        else:
            loaded = 0
            status = "NOT_LOADED"
        reports.append(GroupCompletionReport(group=key, total_units=total, loaded_units=loaded, status=status))
    reports.sort(key=lambda r: (r.status != "COMPLETE", r.status != "PARTIAL", -r.total_units, r.group))

    return selected_items, reports, excluded_items, best_accepted_keys


def select_complete_groups(
    items: list[WindowItem],
    container: ContainerSpec,
    pack_fn,
    reserved_zones: list[ReservedZone] | None,
    clearance: float,
    preplaced: list[PlacedPiece] | None,
    verification_strategy: str = "group_and_size",
) -> tuple[list[WindowItem], list[GroupCompletionReport], list[WindowItem], list[str]]:
    """Keep Groups Together -- envoltorio delgado de select_complete_
    clusters con cluster_key=Grupo (seccion 2 del pedido "KEEP SYSTEMS
    TOGETHER": MISMO codigo exacto que corria antes de esa generalizacion,
    cero cambio de comportamiento -ver ese modulo para la logica real).
    Firma y contrato identicos a como existian antes de Keep Systems."""
    return select_complete_clusters(
        items, container, pack_fn, reserved_zones, clearance, preplaced, verification_strategy,
        cluster_key=_group_cluster_key, optimization_mode=OptimizationMode.KEEP_GROUPS,
    )


def select_complete_systems(
    items: list[WindowItem],
    container: ContainerSpec,
    pack_fn,
    reserved_zones: list[ReservedZone] | None,
    clearance: float,
    preplaced: list[PlacedPiece] | None,
    verification_strategy: str = "group_and_size",
) -> tuple[list[WindowItem], list[GroupCompletionReport], list[WindowItem], list[str]]:
    """Keep Systems Together -- envoltorio delgado de select_complete_
    clusters con cluster_key=Sistema (seccion 2 del pedido "KEEP SYSTEMS
    TOGETHER"). `GroupCompletionReport.group` contiene el nombre del
    SISTEMA aca (el nombre del campo se preserva por compatibilidad --
    mismo patron que SectionReport.group_phases, ver core/section_
    coordinator.py)."""
    return select_complete_clusters(
        items, container, pack_fn, reserved_zones, clearance, preplaced, verification_strategy,
        cluster_key=_system_cluster_key, optimization_mode=OptimizationMode.KEEP_SYSTEMS,
    )


def _unloaded_from_excluded(excluded_items: list[WindowItem], reserved_ids: set[str]) -> list[UnloadedItem]:
    """Construye entradas UnloadedItem para items de Grupos NO
    seleccionados -PRIORITY_DISPLACED es el motivo mas cercano ya
    existente (desplazado por una decision de prioridad, aca la prioridad
    es 'completar otros Grupos primero', seccion 4/12 del pedido: nunca se
    agrega un UnloadedReason nuevo para esto)."""
    from app.core.packer import _expand_instances, _unloaded_item

    instances = _expand_instances(excluded_items, reserved_ids)
    return [
        _unloaded_item(
            inst, UnloadedReason.PRIORITY_DISPLACED,
            "Group not selected for this shipment (Keep Groups Together: another Group's completeness was prioritized)",
        )
        for inst in instances
    ]
