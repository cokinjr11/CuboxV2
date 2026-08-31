"""Catalogo inicial de contenedores maritimos estandar (dimensiones internas en mm)."""

from app.models.schemas import ContainerSpec

CONTAINER_CATALOG: dict[str, ContainerSpec] = {
    "20ft_standard": ContainerSpec(
        id="20ft_standard",
        name="20 ft Standard",
        length=5898,
        width=2352,
        height=2393,
        max_weight=28180,
    ),
    "40ft_standard": ContainerSpec(
        id="40ft_standard",
        name="40 ft Standard",
        length=12032,
        width=2352,
        height=2393,
        max_weight=26512,
    ),
    "40ft_high_cube": ContainerSpec(
        id="40ft_high_cube",
        name="40 ft High Cube",
        length=12032,
        width=2352,
        height=2698,
        max_weight=26330,
    ),
}


def get_container(container_id: str) -> ContainerSpec:
    if container_id not in CONTAINER_CATALOG:
        raise KeyError(f"Contenedor desconocido: {container_id}")
    return CONTAINER_CATALOG[container_id]


def list_containers() -> list[ContainerSpec]:
    return list(CONTAINER_CATALOG.values())
