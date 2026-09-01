"""Catalogo de espacios de carga (CUBOX 2.0): contenedores maritimos
estandar (CONTAINER_CATALOG, sin cambios respecto de CUBOX 1.0) mas la
generalizacion a LOAD_SPACE_CATALOG y espacios Truck/Trailer/Custom
definidos por el usuario.

Truck y Trailer no tienen presets: sus dimensiones internas varian por
carrocera/fabricante/mercado, asi que inventar un "camion estandar" seria
incorrecto. Por ahora se resuelven como espacios custom (ver
build_custom_load_space); presets especificos se podran agregar despues sin
romper nada de lo de aca."""

from uuid import uuid4

from app.models.schemas import LoadingOpeningType, LoadSpaceSpec, LoadSpaceType, RoadWeightConfig

CONTAINER_CATALOG: dict[str, LoadSpaceSpec] = {
    "20ft_standard": LoadSpaceSpec(
        id="20ft_standard",
        name="20 ft Standard",
        load_space_type=LoadSpaceType.CONTAINER,
        length=5898,
        width=2352,
        height=2393,
        max_weight=28180,
        loading_opening_type=LoadingOpeningType.REAR,
    ),
    "40ft_standard": LoadSpaceSpec(
        id="40ft_standard",
        name="40 ft Standard",
        load_space_type=LoadSpaceType.CONTAINER,
        length=12032,
        width=2352,
        height=2393,
        max_weight=26512,
        loading_opening_type=LoadingOpeningType.REAR,
    ),
    "40ft_high_cube": LoadSpaceSpec(
        id="40ft_high_cube",
        name="40 ft High Cube",
        load_space_type=LoadSpaceType.CONTAINER,
        length=12032,
        width=2352,
        height=2698,
        max_weight=26330,
        loading_opening_type=LoadingOpeningType.REAR,
    ),
}


def get_container(container_id: str) -> LoadSpaceSpec:
    if container_id not in CONTAINER_CATALOG:
        raise KeyError(f"Contenedor desconocido: {container_id}")
    return CONTAINER_CATALOG[container_id]


def list_containers() -> list[LoadSpaceSpec]:
    return list(CONTAINER_CATALOG.values())


# LOAD_SPACE_CATALOG (CUBOX 2.0): hoy son los mismos 3 contenedores -Truck y
# Trailer todavia no tienen presets (ver docstring del modulo). Se mantiene
# como diccionario separado de CONTAINER_CATALOG a proposito, para poder
# agregarle presets de Truck/Trailer mas adelante sin tocar la lista que
# expone /api/containers.
LOAD_SPACE_CATALOG: dict[str, LoadSpaceSpec] = dict(CONTAINER_CATALOG)


def get_load_space(load_space_id: str) -> LoadSpaceSpec:
    if load_space_id not in LOAD_SPACE_CATALOG:
        raise KeyError(f"Espacio de carga desconocido: {load_space_id}")
    return LOAD_SPACE_CATALOG[load_space_id]


def list_load_spaces() -> list[LoadSpaceSpec]:
    return list(LOAD_SPACE_CATALOG.values())


def build_custom_load_space(
    name: str,
    load_space_type: LoadSpaceType,
    length: float,
    width: float,
    height: float,
    max_weight: float,
    road_weight_config: RoadWeightConfig | None = None,
) -> LoadSpaceSpec:
    """Construye un LoadSpaceSpec ad-hoc (tipicamente Truck/Trailer/Custom)
    con dimensiones definidas por el usuario. No se guarda en
    LOAD_SPACE_CATALOG -no hay persistencia de espacios de carga todavia; el
    id generado solo necesita ser unico dentro del mismo request/response.

    road_weight_config es opcional (Fase 2B): None (default) preserva el
    comportamiento de Fase 2A -sin distribucion de peso longitudinal."""
    return LoadSpaceSpec(
        id=f"custom-{uuid4().hex[:8]}",
        name=name,
        load_space_type=load_space_type,
        length=length,
        width=width,
        height=height,
        max_weight=max_weight,
        road_weight_config=road_weight_config,
    )
