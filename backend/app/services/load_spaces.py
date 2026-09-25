"""Integracion NAGSA, A2: resolver el Load Space de un pedido de cubicaje.
Unica responsabilidad: convertir (container_id | custom_load_space) en un
LoadSpaceSpec concreto. Antes: api/routes.py:_resolve_load_space."""

from __future__ import annotations

from app.models.containers import build_custom_load_space, get_container
from app.models.schemas import CustomLoadSpaceRequest, LoadSpaceSpec
from app.services.errors import invalid_input, not_found


def resolve_load_space(container_id: str | None, custom_load_space: CustomLoadSpaceRequest | None) -> LoadSpaceSpec:
    """custom_load_space (Truck/Trailer/Container/Custom sin catalogo) tiene
    prioridad; si no vino, se resuelve container_id contra el catalogo
    existente -comportamiento identico al de antes de que custom_load_space
    existiera."""
    if custom_load_space is not None:
        c = custom_load_space
        return build_custom_load_space(c.name, c.load_space_type, c.length, c.width, c.height, c.max_weight, c.road_weight_config)
    if container_id is not None:
        try:
            return get_container(container_id)
        except KeyError as e:
            raise not_found(str(e))
    raise invalid_input("Se requiere container_id o custom_load_space")
