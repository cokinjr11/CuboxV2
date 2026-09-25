"""Modelos Pydantic: piezas de carga, contenedores y resultados de cubicaje."""

from enum import Enum

from pydantic import BaseModel, Field, computed_field, model_validator


class ItemType(str, Enum):
    """Clasificacion fisica del item (CUBOX 2.0). No confundir con los
    "Planning Modes" (Loose Boxes, Palletized Load, Build Pallets, Panels &
    Fragile, Custom Load) que son flujos de preparacion, no tipos de item."""

    BOX = "box"
    PALLET = "pallet"
    PANEL = "panel"
    CUSTOM = "custom"


class OrientationPolicy(str, Enum):
    """Que orientaciones son fisicamente validas para un item.

    Separada de ItemType a proposito: un BOX puede ser FREE o UPRIGHT segun
    reglas de manejo (fragilidad, "this side up"), no segun su tipo fisico.

    FREE            = las 6 orientaciones axis-aligned posibles.
    UPRIGHT         = la dimension `height` siempre vertical; solo rota 90
                       grados en el piso entre `width`/`thickness`.
    PANEL_EDGE_ONLY = regla critica actual de ventanas: la cara
                       Width x Height jamas puede ser la base (4 orientaciones).
    FIXED           = una sola orientacion, tal como se especifico el item.
    """

    FREE = "free"
    UPRIGHT = "upright"
    PANEL_EDGE_ONLY = "panel_edge_only"
    FIXED = "fixed"


DEFAULT_ORIENTATION_POLICY_BY_ITEM_TYPE: dict[ItemType, OrientationPolicy] = {
    ItemType.PANEL: OrientationPolicy.PANEL_EDGE_ONLY,
    ItemType.BOX: OrientationPolicy.FREE,
    ItemType.PALLET: OrientationPolicy.UPRIGHT,
    ItemType.CUSTOM: OrientationPolicy.FREE,
}


TILT_MAX_ANGLE_DEG = 30.0
"""Fase 5C: dominio seguro para Tilt/Inclination, el mismo para cualquier
item que lo soporte (hoy solo PANEL -ver core/handling_rules.py). No es un
limite arbitrario: a 30 grados, cos(30)=0.87 (el panel sigue siendo
reconocible como "de pie", no una reclinacion), y sin(30)=0.5 ya agrega la
mitad de su altura como huella horizontal extra -mas alla de este punto el
Tilt empieza a comportarse como una reclinacion libre (fuera de alcance de
esta fase) en vez de una inclinacion controlada. Aplicado en 2 capas: el
Field(le=...) de abajo (rechaza el valor crudo) y, por defensa en
profundidad, el clamp en resolve_effective_item."""


def resolve_orientation_policy(
    item_type: ItemType,
    orientation_policy: OrientationPolicy | None,
    plan_default: OrientationPolicy | None = None,
) -> OrientationPolicy:
    """Precedencia (Fase 5B): item explicito > default del PLAN > default de
    item_type (system fallback). `plan_default` es opcional y por defecto
    None -llamar esta funcion con 2 argumentos (como ya hacia todo el codigo
    antes de Fase 5B, p.ej. LoadItem.resolved_orientation_policy) sigue
    dando exactamente el mismo resultado de siempre.

    Excepcion dura, verificada y reforzada tras un pedido de seguridad
    explicito: PANEL_EDGE_ONLY es una regla de seguridad FISICA (la cara de
    vidrio jamas puede quedar como base), no una preferencia -y por lo tanto
    NINGUNA fuente puede aflojarla, ni siquiera un `orientation_policy`
    explicito en el item. Este chequeo va PRIMERO, antes de mirar
    `orientation_policy`: si item_type es PANEL, el resultado es SIEMPRE
    PANEL_EDGE_ONLY sin excepcion. En la practica ni el import (PANEL usa
    orientation_mode="none", nunca expone la columna) ni el request HTTP
    (validado aca, no solo alli) deberian poder pasar otra cosa para un
    panel -este chequeo es la ultima linea de defensa, no la unica."""
    if item_type == ItemType.PANEL:
        return OrientationPolicy.PANEL_EDGE_ONLY
    if orientation_policy is not None:
        return orientation_policy
    if plan_default is not None:
        return plan_default
    return DEFAULT_ORIENTATION_POLICY_BY_ITEM_TYPE[item_type]


class Dimensions3D(BaseModel):
    """Representacion generica CANONICA de las 3 dimensiones fisicas de un
    item (CUBOX 2.0, Fase 3A). `length`/`width`/`height` son etiquetas
    neutras: su significado fisico (cual es "vertical", cual es "la cara")
    lo determina ItemType + OrientationPolicy (y, para PANEL,
    PanelDimensionMapping) en core/orientation.py -nunca una heuristica
    geometrica como min(length, width, height).

    Fase 3A.1: esta es la UNICA fuente de verdad fisica almacenada en
    LoadItem.dimensions/PlacedPiece.source_dimensions/UnloadedItem.dimensions.
    Los campos legacy width/height/thickness (y source_*) ya NO se
    almacenan de forma independiente: son `@computed_field` de solo lectura
    derivados de este campo (ver legacy_from_dimensions) -no tienen setter,
    asi que es estructuralmente imposible que diverjan."""

    length: float = Field(gt=0, description="mm")
    width: float = Field(gt=0, description="mm")
    height: float = Field(gt=0, description="mm")


def dimensions_from_legacy(width: float, height: float, thickness: float) -> Dimensions3D:
    """Mapeo LEGACY -> GENERICO (Fase 3A): fijo, posicional, y el MISMO para
    cualquier ItemType (no hay una version distinta para PANEL vs BOX).

        legacy.width     -> generic.length
        legacy.thickness -> generic.width
        legacy.height    -> generic.height

    No es una reinterpretacion fisica -es una correspondencia arbitraria
    pero deterministica entre 2 representaciones planas de 3 numeros. Los
    items genericos (BOX/PALLET/CUSTOM) usan estos 3 valores con su
    significado natural (UPRIGHT: `height` permanece vertical, igual
    criterio que ya se usaba desde la Fase 2B). Para PANEL,
    PANEL_DIMENSION_MAPPING reinterpreta estos mismos 3 valores para
    reproducir, sin ninguna heuristica, la regla original de vidrio (cara =
    legacy.width x legacy.height, thickness = legacy.thickness) -ver
    core/orientation.py."""
    return Dimensions3D(length=width, width=thickness, height=height)


def legacy_from_dimensions(dims: Dimensions3D) -> tuple[float, float, float]:
    """Inversa EXACTA de dimensions_from_legacy(): reconstruye (width,
    height, thickness) legacy a partir de Dimensions3D. Se usa SOLO para
    generar el output de compatibilidad (`@computed_field` width/height/
    thickness en LoadItem/PlacedPiece/UnloadedItem) -la logica interna del
    dominio (packer, orientation, manual_move, final_validation) debe seguir
    consumiendo Dimensions3D directamente, nunca este resultado.

    dimensions_from_legacy(*legacy_from_dimensions(d)) == d para cualquier
    Dimensions3D `d` -round trip sin perdida (ver test_dimension_
    canonicalization.py:TEST K)."""
    return dims.length, dims.height, dims.width


def _merge_legacy_and_generic_dimensions(data: dict, dims_key: str, legacy_keys: tuple[str, str, str]) -> dict:
    """Logica compartida (Fase 3A.1) para el limite de compatibilidad
    LEGACY INPUT -> Adaptador -> Dimensions3D descrito en el plan de esta
    fase. Acepta el input legacy (width/height/thickness, o source_*), el
    input generico nativo (`dims_key`: Dimensions3D) o ambos a la vez -en
    cuyo caso deben ser fisicamente equivalentes o se rechaza el request con
    un error claro (nunca se elige uno silenciosamente).

    Usada por los `model_validator(mode="before")` de LoadItem, PlacedPiece
    (con legacy_keys=source_*) y UnloadedItem."""
    if dims_key in data and data[dims_key] is not None:
        has_generic = True
    else:
        has_generic = False

    present_legacy = [k for k in legacy_keys if data.get(k) is not None]
    if present_legacy and len(present_legacy) != 3:
        raise ValueError(
            f"Deben proveerse los 3 campos legacy juntos ({', '.join(legacy_keys)}); "
            f"solo se recibieron: {', '.join(present_legacy)}"
        )

    if not present_legacy:
        return data  # solo input generico (o ninguno -> error natural de Pydantic: "field required")

    width_key, height_key, thickness_key = legacy_keys
    legacy_dims = dimensions_from_legacy(data[width_key], data[height_key], data[thickness_key])

    if has_generic:
        generic_dims = Dimensions3D.model_validate(data[dims_key])
        if legacy_dims != generic_dims:
            raise ValueError(
                f"'{dims_key}' ({generic_dims}) y los campos legacy {legacy_keys} "
                f"(equivalen a {legacy_dims}) son fisicamente inconsistentes -provee solo uno de los dos"
            )

    data = dict(data)
    for k in legacy_keys:
        data.pop(k, None)
    data[dims_key] = legacy_dims
    return data


class PanelDimensionMapping(BaseModel):
    """Que 2 ejes de Dimensions3D forman la cara grande (panel/vidrio) y
    cual es el eje de thickness -unico mecanismo para interpretar
    OrientationPolicy.PANEL_EDGE_ONLY sobre dimensiones genericas. Explicito
    a proposito (Fase 3A): jamas se infiere por heuristica (p.ej. "la
    dimension mas chica es el thickness")."""

    face_axes: tuple[str, str]
    thickness_axis: str


PANEL_DIMENSION_MAPPING = PanelDimensionMapping(face_axes=("length", "height"), thickness_axis="width")
"""Unico perfil de panel que existe hoy (ventanas legacy). Combinado con
dimensions_from_legacy(), reproduce EXACTAMENTE la regla original: cara =
legacy.width x legacy.height, thickness = legacy.thickness -ver
core/orientation.py:_panel_edge_only_orientations."""


class LoadItem(BaseModel):
    """Una fila de carga a planificar (una linea de producto, con cantidad).

    Generalizacion de lo que antes era WindowItem: mismo comportamiento por
    defecto (item_type=PANEL sin orientation_policy se comporta exactamente
    como una ventana), mas item_type/orientation_policy para representar
    otros tipos de carga. `WindowItem` es un alias de este modelo por
    compatibilidad -no se elimina ni se duplica logica.

    Fase 3A.1: `dimensions` (Dimensions3D) es el UNICO campo fisico
    almacenado -width/height/thickness son `@computed_field` de solo
    lectura derivados de el (ver legacy_from_dimensions), y el
    `model_validator` de abajo acepta indistintamente:
      - input legacy: width/height/thickness (comportamiento CUBOX 1.0)
      - input nativo: dimensions={length,width,height} (CUBOX 2.0)
      - ambos a la vez, si son fisicamente equivalentes (si no, se rechaza)
    """

    code: str
    description: str = ""
    dimensions: Dimensions3D
    weight: float = Field(gt=0, description="kg, peso unitario")
    quantity: int = Field(gt=0)
    system: str = ""
    group: str = Field(
        default="",
        description="Etiqueta de agrupamiento libre (proyecto/obra/cliente/grupo de entrega). NO implica orden de "
        "descarga -eso es exclusivamente Delivery Sequence, ver mas abajo. No confundir con System.",
    )
    stackable: bool = True
    priority: int = Field(
        default=0,
        description="Load Priority (nombre user-facing; el campo interno se mantiene sin cambios por "
        "compatibilidad). 1=Highest...5=Lowest, 0/fuera de rango=Normal/medio (ver core/load_priority.py y "
        "core/scoring.py:_priority_weight). Afecta SOLO que items se prefieren admitir/cargar cuando no entra "
        "todo -nunca decide posicion fisica ni orden de descarga (eso es Delivery Sequence).",
    )
    max_stack_weight: float | None = Field(default=None, description="kg, None = sin limite")
    delivery_sequence: int | None = Field(
        default=None,
        description="Orden/parada de entrega deseado; menor numero = entrega mas temprana. None = sin preferencia "
        "definida (NUNCA se interpreta como 0). Varias piezas pueden compartir el mismo valor (misma parada) -no "
        "es un orden unico por item. Independiente de Group y de Load Priority.",
    )
    boxes_inside: int | None = Field(
        default=None,
        description="Informativo (trazabilidad/inventario): cuantas cajas/unidades individuales contiene este Load "
        "Unit (tipicamente un pallet). NO participa en packing/collision/orientation -ver core/packer.py, que la "
        "copia tal cual a PlacedPiece/UnloadedItem sin leerla para ninguna decision fisica.",
    )
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = Field(
        default=None, description="None = usar la politica por defecto de item_type"
    )
    stackable_override: bool | None = Field(
        default=None,
        description="Fase 5B: valor CRUDO de Stackable tal como vino del Excel (None = celda vacia, el import no "
        "trajo un valor explicito para esta fila). Distinto de `stackable` -que YA viene resuelto/materializado "
        "con el default del plan al momento del import, por compatibilidad con versiones previas- este campo "
        "preserva si esa resolucion fue una herencia o una eleccion explicita, para que el motor de packing pueda "
        "re-resolver correctamente si el default del plan cambia despues del import (ver core/handling_rules.py).",
    )
    orientation_override: OrientationPolicy | None = Field(
        default=None,
        description="Fase 5B: igual idea que stackable_override, para Orientation. None = el Excel no trajo un "
        "valor explicito para esta fila (o el perfil de import no expone esa columna, p.ej. PANEL).",
    )
    allow_tilt: bool = Field(
        default=False,
        description="Fase 5C-FINAL: si este item puede inclinarse (Tilt/Inclination). Ya resuelto/materializado a "
        "partir de PlanHandlingRules.default_allow_tilt -Tilt es PLAN-LEVEL ONLY (seccion 2/3 del pedido final): "
        "no existe override de item, ninguna columna de Excel lo alimenta. Default False (deshabilitado salvo que "
        "el plan lo habilite explicitamente).",
    )
    max_tilt_angle: float | None = Field(
        default=None,
        ge=0,
        le=TILT_MAX_ANGLE_DEG,
        description="Fase 5C-FINAL: angulo MAXIMO de Tilt en grados (magnitud, no signo) ya resuelto del plan -el "
        "rango real y firmado que puede tomar tilt_angle es [-max_tilt_angle, +max_tilt_angle]. None = sin Tilt.",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_dimensions(cls, data):
        if not isinstance(data, dict):
            return data
        return _merge_legacy_and_generic_dimensions(data, "dimensions", ("width", "height", "thickness"))

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions -ver legacy_from_dimensions")  # type: ignore[misc]
    @property
    def width(self) -> float:
        return legacy_from_dimensions(self.dimensions)[0]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions")  # type: ignore[misc]
    @property
    def height(self) -> float:
        return legacy_from_dimensions(self.dimensions)[1]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions")  # type: ignore[misc]
    @property
    def thickness(self) -> float:
        return legacy_from_dimensions(self.dimensions)[2]


WindowItem = LoadItem


class LoadSpaceType(str, Enum):
    """Clasificacion del espacio de carga (CUBOX 2.0)."""

    CONTAINER = "container"
    TRUCK = "truck"
    TRAILER = "trailer"
    CUSTOM = "custom"


class LoadingOpeningType(str, Enum):
    """Por donde se accede al espacio de carga. Fase 6A Final Product
    Decision: TODOS los Load Space de Cubox se consideran rear-loading -REAR
    es el UNICO comportamiento activo del producto (Container/Truck/
    Trailer/Custom siempre lo resuelven asi, ver models/containers.py). SIDE/
    TOP/MULTIPLE se conservan en el enum solo por compatibilidad futura -no
    hay forma de que el producto construya hoy un LoadSpaceSpec con esos
    valores; el motor de secuencia (core/sequence.py) los sigue soportando
    si alguna vez reaparecen (p.ej. un plan persistido muy viejo), pero
    ningun flujo actual los genera."""

    REAR = "rear"
    SIDE = "side"
    TOP = "top"
    MULTIPLE = "multiple"


class RoadSupport(BaseModel):
    """Un punto/grupo de apoyo longitudinal de un vehiculo de carretera
    (p.ej. Front Axle Group, Rear Axle Group, o Kingpin/Trailer Axle Group en
    un semirremolque). Generico a proposito: el mismo modelo sirve para
    Truck y Trailer, sin dos motores de fisica distintos (ver
    core/road_weight.py)."""

    id: str
    name: str
    position_x_mm: float = Field(
        description="Posicion longitudinal del support en el mismo eje X que PlacedPiece.x "
        "(x=0 es la puerta/abertura de carga, x=LoadSpaceSpec.length es la pared del fondo)"
    )
    max_load_kg: float = Field(gt=0, description="Limite CONFIGURADO -nunca un valor legal/de fabricante asumido")
    baseline_load_kg: float = Field(default=0.0, ge=0, description="Carga del vehiculo ya presente en este support antes de la carga de items")


class RoadWeightConfig(BaseModel):
    """Configuracion opcional de distribucion de peso longitudinal (Fase 2B).
    Si enabled=False (o el campo es None en LoadSpaceSpec), no afecta en nada
    el empaque -comportamiento identico a Fase 2A. Cuando enabled=True, se
    exige exactamente 2 supports (modelo estatico de 2 apoyos, ver
    core/road_weight.py); mas de 2 requeriria un modelo de distribucion
    multi-eje que este modulo NO intenta adivinar."""

    enabled: bool = False
    supports: list[RoadSupport] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_supports(self) -> "RoadWeightConfig":
        if not self.enabled:
            return self
        if len(self.supports) != 2:
            raise ValueError(
                f"RoadWeightConfig.enabled requiere exactamente 2 supports (modelo estatico de 2 apoyos); "
                f"se recibieron {len(self.supports)}"
            )
        a, b = self.supports
        if abs(a.position_x_mm - b.position_x_mm) < 1e-6:
            raise ValueError("RoadWeightConfig: los 2 supports no pueden tener la misma position_x_mm")
        return self


class LoadSpaceSpec(BaseModel):
    """Espacio de carga generico: contenedor, camion, trailer o
    personalizado. Generalizacion de lo que antes era ContainerSpec -mismos
    campos y mismo comportamiento (ContainerSpec es un alias de este modelo,
    igual patron que WindowItem/LoadItem). Todo ContainerSpec existente
    sigue siendo valido: load_space_type default = CONTAINER,
    road_weight_config default = None (sin efecto en el empaque)."""

    id: str
    name: str
    load_space_type: LoadSpaceType = LoadSpaceType.CONTAINER
    length: float = Field(description="mm, eje X interno")
    width: float = Field(description="mm, eje Y interno")
    height: float = Field(description="mm, eje Z interno")
    max_weight: float = Field(description="kg, peso maximo de carga")
    loading_opening_type: LoadingOpeningType | None = Field(
        default=LoadingOpeningType.REAR,
        description="Fase 6A Final Product Decision: siempre REAR -unico comportamiento activo del "
        "producto, no configurable por el usuario. Sigue aceptando None por compatibilidad con planes "
        "persistidos de antes de esta decision (se trata igual que REAR, sin warning de limitacion).",
    )
    rear_opening_width: float | None = Field(default=None, description="mm; None = sin dato conocido")
    rear_opening_height: float | None = Field(default=None, description="mm; None = sin dato conocido")
    road_weight_config: RoadWeightConfig | None = Field(
        default=None, description="None = sin distribucion de peso longitudinal (comportamiento Fase 2A)"
    )


ContainerSpec = LoadSpaceSpec


class SupportLoadOut(BaseModel):
    """Metricas de un RoadSupport para un cubicaje dado (ver core/road_weight.py:evaluate_road_weight)."""

    id: str
    name: str
    position_x_mm: float
    cargo_reaction_kg: float
    baseline_load_kg: float
    total_load_kg: float
    max_load_kg: float
    utilization_pct: float
    overloaded: bool
    unstable: bool = Field(description="True si la reaccion calculada es negativa mas alla de la tolerancia numerica")


class RoadWeightMetrics(BaseModel):
    """Resultado de evaluar RoadWeightConfig contra un conjunto de piezas
    cargadas. None en PackingResult cuando el LoadSpace no tiene
    road_weight_config habilitado."""

    load_center_x_mm: float | None = Field(default=None, description="Centro de carga longitudinal; None si no hay piezas cargadas")
    total_item_weight_kg: float
    supports: list[SupportLoadOut]
    valid: bool
    errors: list[str] = []


class CustomLoadSpaceRequest(BaseModel):
    """Definicion de un espacio de carga hecha por el usuario (Truck,
    Trailer, Container o Custom con dimensiones propias). No se persiste en
    el catalogo -se resuelve a un LoadSpaceSpec ad-hoc por request (ver
    models/containers.py:build_custom_load_space); la persistencia real de
    espacios de carga se manejara en una fase separada."""

    name: str
    load_space_type: LoadSpaceType
    length: float = Field(gt=0, description="mm")
    width: float = Field(gt=0, description="mm")
    height: float = Field(gt=0, description="mm")
    max_weight: float = Field(gt=0, description="kg")
    road_weight_config: RoadWeightConfig | None = Field(
        default=None, description="Fase 2B: distribucion de peso longitudinal, opcional"
    )


class OptimizationMode(str, Enum):
    """PRIORITIZE_DELIVERY (Fase 6A.1): ordena la colocacion para que
    Delivery Sequence mas bajo (entrega mas temprana) quede cerca de la
    apertura de carga y Delivery Sequence mas alto (entrega mas tardia)
    quede en el fondo -evita que el Sequence Engine (Fase 6A) reporte
    decenas de DELIVERY_SEQUENCE_CONFLICT por un modo que nunca intento
    respetar Delivery Sequence en primer lugar (ver
    core/strategies.py:_delivery_key y core/scoring.py)."""

    BEST_SPACE = "best_space"
    KEEP_GROUPS = "keep_groups"
    KEEP_SYSTEMS = "keep_systems"
    PRIORITIZE_DELIVERY = "prioritize_delivery"


class LoadingAnchor(str, Enum):
    """Esquina de inicio de la secuencia de carga (Anchored Loading
    Sequence): que pared lateral (ademas de la pared del fondo, siempre fija)
    se prioriza como punto de partida. BACK_RIGHT es el default."""

    BACK_RIGHT = "back_right"
    BACK_LEFT = "back_left"


class WeightBalanceMode(str, Enum):
    IGNORE = "ignore"
    NORMAL = "normal"
    IMPORTANT = "important"


class OperationalWarningType(str, Enum):
    """Fase 6A, seccion 28: tipos estructurados de warning operacional -evita
    que cada consumidor (UI, reportes) tenga que parsear texto libre para
    saber que clase de conflicto es."""

    OPERATIONAL_LOADABILITY_WARNING = "operational_loadability_warning"
    DELIVERY_SEQUENCE_CONFLICT = "delivery_sequence_conflict"
    STACKING_SEQUENCE_CONFLICT = "stacking_sequence_conflict"
    SEQUENCE_CYCLE = "sequence_cycle"
    OPENING_CONFIGURATION_LIMITATION = "opening_configuration_limitation"


class OperationalWarning(BaseModel):
    """Fase 6A, seccion 29: un warning util identifica pieza afectada, pieza
    bloqueante (si aplica) y las Delivery Sequence en conflicto -no solo un
    mensaje de texto. `message` sigue siendo human-readable (se usa tal cual
    en load_sequence_warnings/reportes) para no duplicar el texto en cada
    consumidor."""

    type: OperationalWarningType
    message: str
    item_id: str | None = None
    blocking_item_id: str | None = None
    requested_delivery_sequence: int | None = None
    blocking_delivery_sequence: int | None = None


class PlacedPiece(BaseModel):
    id: str
    code: str
    description: str = ""
    system: str = ""
    group: str = Field(
        default="",
        description="Load Organization Model Cleanup: agrupamiento de negocio (proyecto/obra/cliente/grupo de "
        "entrega), copiado tal cual de LoadItem.group. NO implica orden de descarga -ver LoadItem.group.",
    )
    weight: float
    stackable: bool
    priority: int = Field(
        default=0,
        description="Load Priority (nombre user-facing), copiado tal cual de LoadItem.priority -ver "
        "core/load_priority.py. Afecta admision/packing bajo restriccion de capacidad, nunca posicion fisica final.",
    )
    max_stack_weight: float | None = None
    delivery_sequence: int | None = Field(
        default=None,
        description="Orden/parada de entrega deseado, copiado tal cual de LoadItem.delivery_sequence -ver "
        "core/sequence.py. None = sin preferencia (nunca 0). No determina por si solo el orden fisico final: "
        "eso lo decide la geometria + accesibilidad (compute_unload_dependencies), Delivery Sequence es solo "
        "el desempate SOFT entre piezas ya listas.",
    )
    boxes_inside: int | None = None
    locked: bool = False
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    orientation_label: str
    source_dimensions: Dimensions3D
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = None
    stackable_override: bool | None = Field(
        default=None, description="Fase 5B: copiado tal cual del LoadItem de origen -ver LoadItem.stackable_override."
    )
    orientation_override: OrientationPolicy | None = Field(
        default=None, description="Fase 5B: copiado tal cual del LoadItem de origen -ver LoadItem.orientation_override."
    )
    allow_tilt: bool = Field(
        default=False,
        description="Fase 5C-FINAL: copiado del plan ya resuelto (PLAN-LEVEL ONLY -no hay override de item, ver "
        "LoadItem.allow_tilt).",
    )
    max_tilt_angle: float | None = Field(
        default=None,
        description="Fase 5C-FINAL: magnitud maxima de Tilt del plan (grados, sin signo) -el rango real firmado "
        "de tilt_angle es [-max_tilt_angle, +max_tilt_angle].",
    )
    tilt_angle: float = Field(
        default=0.0,
        description="Fase 5C-FINAL: angulo de Tilt REALMENTE usado para esta pieza, SIGNED (grados; positivo/"
        "negativo = direccion de inclinacion, 0 = sin inclinar). Es el mismo valor que debe renderizar Scene3D y "
        "que valida final_validation -nunca 'solo visual'. abs(tilt_angle) <= max_tilt_angle siempre.",
    )
    tilt_axis: str | None = Field(
        default=None,
        description="Fase 5C: 'x' o 'y' -eje horizontal de Thickness sobre el que se inclina esta pieza en su "
        "orientacion actual (None = orientacion sin eje de Tilt aplicable, p.ej. item_type != PANEL). Define la "
        "direccion de inclinacion; ver core/orientation.py:apply_tilt.",
    )
    base_dx: float | None = Field(
        default=None,
        description="Fase 5C: dx SIN Tilt (0 grados) para la orientacion actual de esta pieza -junto con base_dy/"
        "base_dz y tilt_axis, permite recalcular la geometria al cambiar manualmente el angulo sin ambiguedad "
        "(ver core/orientation.py:apply_tilt). None cuando tilt_axis es None (equivale a dx tal cual).",
    )
    base_dy: float | None = Field(default=None, description="Fase 5C: dy SIN Tilt -ver base_dx.")
    base_dz: float | None = Field(default=None, description="Fase 5C: dz SIN Tilt -ver base_dx.")

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_source_dimensions(cls, data):
        if not isinstance(data, dict):
            return data
        return _merge_legacy_and_generic_dimensions(
            data, "source_dimensions", ("source_width", "source_height", "source_thickness")
        )

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de source_dimensions")  # type: ignore[misc]
    @property
    def source_width(self) -> float:
        return legacy_from_dimensions(self.source_dimensions)[0]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de source_dimensions")  # type: ignore[misc]
    @property
    def source_height(self) -> float:
        return legacy_from_dimensions(self.source_dimensions)[1]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de source_dimensions")  # type: ignore[misc]
    @property
    def source_thickness(self) -> float:
        return legacy_from_dimensions(self.source_dimensions)[2]


class UnloadedItem(BaseModel):
    id: str
    code: str
    description: str = ""
    dimensions: Dimensions3D
    weight: float
    system: str = ""
    group: str = Field(default="", description="Ver LoadItem.group -copiado tal cual, no implica orden de descarga.")
    stackable: bool = True
    priority: int = Field(default=0, description="Load Priority (nombre user-facing) -ver core/load_priority.py.")
    max_stack_weight: float | None = None
    delivery_sequence: int | None = Field(
        default=None, description="Ver LoadItem.delivery_sequence -None = sin preferencia, nunca 0."
    )
    boxes_inside: int | None = None
    reason: str
    reason_code: str
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = None
    stackable_override: bool | None = Field(
        default=None, description="Fase 5B: copiado tal cual del LoadItem de origen -ver LoadItem.stackable_override."
    )
    orientation_override: OrientationPolicy | None = Field(
        default=None, description="Fase 5B: copiado tal cual del LoadItem de origen -ver LoadItem.orientation_override."
    )
    allow_tilt: bool = Field(default=False, description="Fase 5C-FINAL: copiado del plan ya resuelto (PLAN-LEVEL ONLY).")
    max_tilt_angle: float | None = Field(default=None, description="Fase 5C-FINAL: magnitud maxima de Tilt del plan.")

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_dimensions(cls, data):
        if not isinstance(data, dict):
            return data
        return _merge_legacy_and_generic_dimensions(data, "dimensions", ("width", "height", "thickness"))

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions")  # type: ignore[misc]
    @property
    def width(self) -> float:
        return legacy_from_dimensions(self.dimensions)[0]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions")  # type: ignore[misc]
    @property
    def height(self) -> float:
        return legacy_from_dimensions(self.dimensions)[1]

    @computed_field(description="mm; compatibilidad CUBOX 1.0, derivado de dimensions")  # type: ignore[misc]
    @property
    def thickness(self) -> float:
        return legacy_from_dimensions(self.dimensions)[2]


class PackingMetrics(BaseModel):
    total_pieces: int
    loaded_pieces: int
    unloaded_pieces: int
    used_volume_pct: float
    total_weight: float
    weight_utilization_pct: float
    floor_utilization_pct: float
    container_floor_area: float
    used_floor_area: float
    max_payload: float
    number_of_groups: int
    number_of_systems: int
    weight_balance_pct: float
    left_weight_kg: float
    right_weight_kg: float
    left_weight_pct: float
    right_weight_pct: float
    front_weight_kg: float
    back_weight_kg: float
    front_weight_pct: float
    back_weight_pct: float
    center_of_mass_x: float
    center_of_mass_y: float
    center_of_mass_z: float


class ReservedZoneOut(BaseModel):
    """Version Pydantic (serializable) de core.reserved_zones.ReservedZone,
    solo para exponer la geometria de zonas reservadas (p.ej. el pasillo
    central) al frontend -single source of verdad, en vez de que el
    frontend recalcule el centrado por su cuenta."""

    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    label: str = "reserved"


class PackingResult(BaseModel):
    container: ContainerSpec
    placed: list[PlacedPiece]
    unloaded: list[UnloadedItem]
    metrics: PackingMetrics
    load_sequence: list[str] = []
    unload_sequence: list[str] = []
    load_sequence_warnings: list[str] = Field(
        default=[],
        description="Fase 6A: proyeccion en texto plano de operational_warnings (mismo orden), mantenida por compatibilidad con Excel y consumidores existentes. Ver operational_warnings para el detalle estructurado.",
    )
    operational_warnings: list[OperationalWarning] = Field(
        default=[],
        description="Fase 6A: warnings operacionales estructurados -loadability, Delivery Sequence conflicts, Stacking Sequence conflicts, ciclos de dependencia y limitaciones de Loading Opening Type.",
    )
    blocked_by: dict[str, list[str]] = Field(
        default={},
        description="Fase 6A: id de pieza -> ids que deben descargarse antes que ella (soporte fisico + bloqueo lateral hacia la apertura de carga), segun la geometria final del plan.",
    )
    reserved_zones: list[ReservedZoneOut] = []
    road_weight: RoadWeightMetrics | None = Field(
        default=None, description="None si el LoadSpace no tiene RoadWeightConfig habilitado (Fase 2A/legacy)"
    )


class AlternativeSolution(BaseModel):
    strategy: str
    score: float
    breakdown: dict[str, float] = {}
    result: PackingResult


class OptimizeResponse(BaseModel):
    best: PackingResult
    alternatives: list[AlternativeSolution]


class PlanHandlingRules(BaseModel):
    """Fase 5B: defaults de HANDLING RULES a nivel de PLAN (elegidos en el
    wizard, o editados despues en el Workspace) -distintos de:
      - el default de SISTEMA por item_type (DEFAULT_ORIENTATION_POLICY_BY_ITEM_TYPE,
        stackable=True), que es el ultimo fallback si ni el item ni el plan
        dicen nada;
      - el override EXPLICITO de un item (LoadItem.stackable_override/
        orientation_override/max_stack_weight), que siempre gana sobre esto.

    Todos los campos son opcionales: None significa "este plan no define un
    default para esta regla" -en ese caso se cae directo al default de
    sistema de siempre, asi un request que no manda plan_handling_rules en
    absoluto (o lo manda vacio) se comporta identico a antes de que este
    concepto existiera. Ver core/handling_rules.py:resolve_effective_item
    para el punto unico donde se aplica esta precedencia."""

    default_stackable: bool | None = None
    default_orientation_policy: OrientationPolicy | None = None
    default_max_stack_weight: float | None = Field(default=None, description="kg, None = sin limite de plan")
    default_allow_tilt: bool | None = Field(
        default=None, description="Fase 5C: None = este plan no define un default de Tilt (cae al system default: False)."
    )
    default_max_tilt_angle: float | None = Field(
        default=None,
        ge=0,
        le=TILT_MAX_ANGLE_DEG,
        description="Fase 5C: grados, None = este plan no define un maximo de Tilt.",
    )


class PackRequest(BaseModel):
    """container_id (catalogo existente) y custom_load_space (Truck/Trailer/
    Container/Custom con dimensiones propias, sin catalogo) son mutuamente
    excluyentes -custom_load_space tiene prioridad si ambos llegan. Un
    request legacy que solo manda container_id sigue funcionando igual."""

    items: list[WindowItem]
    container_id: str | None = None
    plan_handling_rules: PlanHandlingRules | None = None
    custom_load_space: CustomLoadSpaceRequest | None = None
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL
    loading_anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT
    enable_central_aisle: bool = False
    aisle_width_mm: float = 500
    clearance_mm: float = 0


class MoveRequest(BaseModel):
    """Solicitud de mover manualmente una pieza ya colocada."""

    piece_id: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float


class MoveValidationResult(BaseModel):
    valid: bool
    reason: str = ""


class RemovePieceRequest(BaseModel):
    """Quitar manualmente una pieza colocada; pasa a Unloaded Items."""

    piece_id: str


class InsertPieceRequest(BaseModel):
    """Intentar colocar manualmente una pieza que esta en Unloaded Items."""

    unloaded_id: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float


class RotatePieceRequest(BaseModel):
    """Alternar una pieza colocada entre sus 2 orientaciones validas."""

    piece_id: str


class SetTiltRequest(BaseModel):
    """Fase 5C-FINAL: cambiar manualmente el angulo de Tilt (SIGNED) de una
    pieza colocada. Validado en core/manual_move.py:validate_tilt_change
    (misma fuente de verdad que el packer automatico) -ver
    core/api/routes.py:set_tilt. El rango real depende del max_tilt_angle
    efectivo de la pieza (abs(tilt_angle) <= max_tilt_angle); este
    Field(ge/le) solo acota el dominio seguro absoluto del producto."""

    piece_id: str
    tilt_angle: float = Field(ge=-TILT_MAX_ANGLE_DEG, le=TILT_MAX_ANGLE_DEG)


class LockPieceRequest(BaseModel):
    """Bloquear una pieza para que Optimize Remaining no la mueva."""

    piece_id: str


class UnlockPieceRequest(BaseModel):
    piece_id: str


class OptimizeRemainingRequest(BaseModel):
    """Ambos campos opcionales: si se omiten, se reusa el ultimo optimization
    mode / weight balance mode guardado en el estado activo (comportamiento
    identico al de antes de que este request body existiera). Si se pasan,
    reflejan la seleccion actual de la UI para que Optimize Remaining (y
    Re-optimize cuando hay piezas Locked) realmente respete Keep Groups/Keep
    Systems/Weight Balance en vez de quedarse con lo que habia en el ultimo
    /api/pack."""

    optimization_mode: OptimizationMode | None = None
    weight_balance_mode: WeightBalanceMode | None = None
    loading_anchor: LoadingAnchor | None = None
    plan_handling_rules: PlanHandlingRules | None = None


class ReportMetadata(BaseModel):
    """Datos que no existen en ningun otro lado del modelo -solo tienen
    sentido al momento de exportar un reporte, asi que viven aca y no en
    PackingResult/PackingMetrics."""

    project_name: str = ""
    customer: str = ""


class SortReportBy(str, Enum):
    GROUP = "group"
    SYSTEM = "system"


class StepMode(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ContainerReportRequest(BaseModel):
    meta: ReportMetadata = ReportMetadata()
    sort_by: SortReportBy = SortReportBy.GROUP
    include_overview_image: bool = True
    overview_image_png_base64: str | None = Field(
        default=None, description="PNG del snapshot 3D en base64; requerido si include_overview_image=True"
    )
    allow_export_with_errors: bool = Field(
        default=False,
        description="Configurable Export Validation Override: si True y el plan esta NOT_READY (errores "
        "bloqueantes), el export procede igual en vez de devolver 422 -el documento generado se marca "
        "explicitamente como 'EXPORTED WITH VALIDATION ERRORS' (nunca se oculta ni se reinterpreta el estado). "
        "Reflejo backend-autoritativo del toggle 'Block exports when validation errors exist' de Settings -ver "
        "routes.py:_resolve_export_validation.",
    )


class GuideReportRequest(BaseModel):
    meta: ReportMetadata = ReportMetadata()
    step_mode: StepMode = StepMode.AUTOMATIC
    pieces_per_step: int | None = Field(default=None, description="requerido si step_mode == MANUAL")
    allow_export_with_errors: bool = Field(
        default=False, description="Ver ContainerReportRequest.allow_export_with_errors -mismo contrato."
    )
    step_images_png_base64: list[str] = Field(default_factory=list, description="un PNG por paso, en el mismo orden")
    groups: list[str] | None = Field(
        default=None,
        description="Load Organization Model Cleanup: si se define, filtra la Unloading Guide a solo estos "
        "Groups (Group Unloading Guide). None = Full Unloading Guide (comportamiento de siempre, todo el plan). "
        "Nunca cambia el orden fisico ni recalcula dependencias -solo filtra los pasos ya resueltos.",
    )


class ReportDirection(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"


class ReportStepsRequest(BaseModel):
    direction: ReportDirection
    step_mode: StepMode = StepMode.AUTOMATIC
    pieces_per_step: int | None = Field(default=None, description="requerido si step_mode == MANUAL")


class UnloadStepDeliveryInfo(BaseModel):
    """Fase 6B: anotacion de Delivery Sequence de un paso de DESCARGA ya
    resuelto -NUNCA cambia el orden de `steps`, solo lo describe. Ver
    core/sequence.py:annotate_unload_steps_with_delivery."""

    delivery_sequences: list[int] = Field(
        default=[], description="Valores DISTINTOS de Delivery Sequence presentes en este paso, ordenados; [] si ninguna pieza del paso tiene el campo definido."
    )
    is_mixed: bool = Field(
        default=False, description="True si el paso tiene mas de un valor DISTINTO de Delivery Sequence -seccion 15 del pedido: nunca se inventa un numero unico para este caso."
    )
    conflict_messages: list[str] = Field(
        default=[],
        description="Mensajes de PackingResult.operational_warnings (DELIVERY_SEQUENCE_CONFLICT/STACKING_SEQUENCE_CONFLICT) que involucran a alguna pieza de este paso -texto reusado tal cual, nunca generado de nuevo.",
    )
    conflict_types: list[OperationalWarningType] = Field(
        default=[],
        description=(
            "Fase 6B.3: mismo orden/longitud que conflict_messages -el tipo de "
            "cada mensaje, para poder resumirlos por categoria (ej. 'Loading "
            "Guide PDF' -> 'Delivery blocking conflicts' x5) sin tener que "
            "parsear el texto. No agrega/quita ningun warning, solo conserva "
            "el `.type` que ya traia el OperationalWarning original."
        ),
    )


class UnloadDeliverySection(BaseModel):
    """Fase 6B, seccion 14: agrupacion de pasos de descarga CONTIGUOS que
    comparten la misma anotacion de Delivery Sequence -solo para mostrar
    encabezados "DELIVERY N" legibles. Nunca reordena `steps`; `step_indices`
    siempre es un rango contiguo en el orden fisico original."""

    label: str | None = Field(
        default=None,
        description="None = sin encabezado (ningun item del rango tiene Delivery Sequence). 'DELIVERY {n}' para una seccion limpia, o una etiqueta 'mixed' si el rango mezcla valores distintos.",
    )
    step_indices: list[int] = Field(description="Indices (0-based) dentro de `steps`, siempre un rango contiguo.")


class ReportStepsResponse(BaseModel):
    steps: list[list[str]]
    unload_step_info: list[UnloadStepDeliveryInfo] | None = Field(
        default=None, description="Fase 6B: solo presente cuando direction=UNLOAD; misma longitud que `steps`."
    )
    delivery_sections: list[UnloadDeliverySection] | None = Field(
        default=None, description="Fase 6B: solo presente cuando direction=UNLOAD."
    )


class ValidationCategory(str, Enum):
    """Fase 6C: categorias ESTABLES y CHICAS a proposito (seccion 5 del
    pedido: "do not create dozens of tiny categories") -cada issue de
    final_validation.py/operational_warnings/unloaded items se etiqueta con
    una de estas, nunca se infiere parseando el texto del mensaje. Reserved
    Zone vive bajo LOAD_SPACE (seccion 5); Tilt legacy vive bajo ORIENTATION
    (seccion 37, nunca reaparece como categoria propia -eso reintroduciria
    Tilt en la UI, que sigue deshabilitado)."""

    DATA_INTEGRITY = "data_integrity"
    LOAD_SPACE = "load_space"
    COLLISION_CLEARANCE = "collision_clearance"
    SUPPORT_STABILITY = "support_stability"
    ORIENTATION = "orientation"
    STACK_WEIGHT = "stack_weight"
    PAYLOAD = "payload"
    ROAD_WEIGHT = "road_weight"
    OPERATIONAL_SEQUENCE = "operational_sequence"
    DELIVERY_SEQUENCE = "delivery_sequence"
    UNLOADED_ITEMS = "unloaded_items"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """Fase 6C, seccion 4 del pedido: metadata MINIMA sobre un mensaje que
    final_validation.py/operational_warnings ya generaba -nunca una regla
    nueva, solo la categoria/severidad/item_id que ya se conocian en el
    momento en que el mensaje se armo. `message` es exactamente el mismo
    texto que ya devolvia validate_for_export()/state.operational_warnings,
    nunca reescrito."""

    category: ValidationCategory
    severity: ValidationSeverity
    message: str
    item_id: str | None = None


class CategoryStatusValue(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class CategoryStatus(BaseModel):
    category: ValidationCategory
    status: CategoryStatusValue
    count: int = 0


class PlanValidationStatus(str, Enum):
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    NOT_READY = "not_ready"


class ReportValidationResponse(BaseModel):
    """Fase 6A: `valid`/`errors`/`warnings` -SIN CAMBIOS de significado,
    cualquier caller viejo (el preflight de handleGenerateReport, por
    ejemplo) sigue funcionando exactamente igual. Fase 6C: agrega
    `status`/`error_count`/`warning_count`/`unloaded_count`/`categories`/
    `error_issues`/`warning_issues` de forma ADITIVA -mismo aggregador
    (core/final_validation.py:build_plan_validation_result) alimenta tanto
    el preflight de reportes como el nuevo panel Plan Validation del
    Workspace, nunca dos calculos de "listo para exportar" independientes."""

    valid: bool
    errors: list[str] = []
    warnings: list[str] = Field(
        default=[],
        description="Fase 6A, seccion 35: warnings operacionales NO bloqueantes (Delivery Sequence/Stacking Sequence conflicts, limitaciones de Loading Opening Type) -no impiden exportar, pero no deben quedar ocultos antes de generar un reporte.",
    )
    status: PlanValidationStatus = PlanValidationStatus.READY
    error_count: int = 0
    warning_count: int = 0
    unloaded_count: int = 0
    categories: list[CategoryStatus] = []
    error_issues: list[ValidationIssue] = []
    warning_issues: list[ValidationIssue] = []


# ---------------------------------------------------------------------------
# Fase 5D: Recent Plans & Persistence. Ver core/plan_store.py (repositorio
# SQLite) y core/plan_service.py (traduccion hacia/desde estos modelos).
# ---------------------------------------------------------------------------


class CreatePlanRequest(PackRequest):
    """Igual que PackRequest (seccion 12 del pedido: crear un Load Plan
    corre exactamente la misma optimizacion que /api/pack) mas un nombre
    opcional -None usa un nombre por defecto (core/plan_service.py:
    default_plan_name)."""

    name: str | None = Field(default=None, min_length=1)


class CreatePlanResponse(BaseModel):
    plan_id: str
    name: str
    best: PackingResult
    alternatives: list[AlternativeSolution]


class PlanSummary(BaseModel):
    """Fase 5D, seccion 38 del pedido: forma LIVIANA para listar Recent
    Plans -nunca placed/unloaded completos, solo lo que una tarjeta
    necesita mostrar."""

    plan_id: str
    name: str
    load_type: str
    load_space_name: str
    total_items: int
    loaded_items: int
    unloaded_items: int
    created_at: str
    updated_at: str


class PlanDetailResponse(BaseModel):
    """Fase 5D, seccion 13 del pedido: todo lo que el frontend necesita
    para reconstruir el Workspace exactamente como se guardo, SIN volver a
    correr el optimizador -`result` ya trae placed/unloaded/metrics/
    secuencias/road_weight recalculados frescos (nunca desde una cache
    persistida, ver plan_service.py), y el resto de los campos son la
    configuracion activa que el frontend necesita para poblar sus propios
    estados (Optimize Remaining, Handling Rules, etc.)."""

    plan_id: str
    name: str
    created_at: str
    updated_at: str
    load_type: str = Field(
        description="Fase 5D (correccion final), seccion 5/7 del pedido: el Load Type PERSISTIDO del plan -metadata "
        "propia del plan, nunca re-inferida de placed[0]/unloaded[0].item_type al reabrir."
    )
    result: PackingResult
    plan_handling_rules: PlanHandlingRules | None
    optimization_mode: OptimizationMode
    weight_balance_mode: WeightBalanceMode
    loading_anchor: LoadingAnchor
    clearance_mm: float
    enable_central_aisle: bool
    aisle_width_mm: float
    container_id: str | None = Field(default=None, description="Solo si el Load Space es del catalogo (seccion 18 del pedido)")
    custom_load_space: CustomLoadSpaceRequest | None = Field(
        default=None, description="Solo si el Load Space es custom (seccion 19 del pedido); nunca un fallback a catalogo"
    )


class RenamePlanRequest(BaseModel):
    """Seccion 28 del pedido: renombrar un plan guardado."""

    name: str = Field(min_length=1)


class SavePlanRequest(BaseModel):
    """Fase 5D (correccion final), seccion 1/2 del pedido: PUT /api/plans/{id}
    sigue sin body para el autosave normal (dispara con cualquier cambio de
    `result` -ver api/routes.py). Este body OPCIONAL es exclusivamente para
    el autosave de Plan Handling Rules: cuando esta presente,
    `plan_handling_rules` se aplica a _current_state ANTES de persistir -sin
    tocar placed/unloaded ni volver a correr el optimizador (seccion 2:
    guardar la configuracion no es lo mismo que recalcular la colocacion)."""

    plan_handling_rules: PlanHandlingRules | None = None


class DeletePlanResponse(BaseModel):
    deleted: bool
    plan_id: str
