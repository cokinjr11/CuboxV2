"""Exportacion a Excel del resultado de cubicaje actual (seccion 34 de V2)."""

import io

from openpyxl import Workbook

from app.models.schemas import ItemType, PackingResult, PlacedPiece


def _is_palletized_plan(placed: list[PlacedPiece]) -> bool:
    """Boxes Inside (Fase 6.2) solo tiene sentido para Palletized Load -un
    plan es siempre homogeneo en item_type, alcanza con la primera pieza."""
    return bool(placed) and placed[0].item_type == ItemType.PALLET


def _has_tilt_capable_pieces(placed: list[PlacedPiece]) -> bool:
    """Fase 5C-FINAL, seccion 36 del pedido: no agregar la columna Tilt si
    ningun item del plan la soporta. Tilt es PLAN-LEVEL ONLY -es uniforme
    para todas las piezas PANEL del plan (no varia pieza por pieza como
    antes), pero se sigue mirando por pieza para no asumir nada del resto
    del modelo."""
    return any(p.allow_tilt for p in placed)


def _format_signed_tilt(tilt_angle: float) -> str:
    """Fase 5C-FINAL, seccion 36 del pedido: el signo NUNCA se descarta -
    +12°/-12° son direcciones de inclinacion distintas y operacionalmente
    relevantes para reproducir el Load Plan. "" para 0 (sin inclinar)."""
    return f"{tilt_angle:+g}°" if tilt_angle else ""


def build_export_workbook(state: PackingResult) -> bytes:
    wb = Workbook()
    is_pallet_plan = _is_palletized_plan(state.placed) or (
        not state.placed and bool(state.unloaded) and state.unloaded[0].item_type == ItemType.PALLET
    )
    has_tilt = _has_tilt_capable_pieces(state.placed)

    summary = wb.active
    summary.title = "Summary"
    summary.append(["Container", state.container.name])
    summary.append(["Loaded", state.metrics.loaded_pieces])
    summary.append(["Unloaded", state.metrics.unloaded_pieces])
    summary.append(["Volume Utilization %", state.metrics.used_volume_pct])
    summary.append(["Floor Utilization %", state.metrics.floor_utilization_pct])
    summary.append(["Total Weight (kg)", state.metrics.total_weight])
    summary.append(["Max Payload (kg)", state.metrics.max_payload])
    summary.append(["Weight Utilization %", state.metrics.weight_utilization_pct])
    summary.append(["Number of Groups", state.metrics.number_of_groups])
    summary.append(["Number of Systems", state.metrics.number_of_systems])

    packing_list = wb.create_sheet("Packing List")
    packing_header = [
        "Load Order", "Unload Order", "Code", "Description", "Width", "Height", "Thickness", "Weight",
        "Group", "System", "Priority",
    ]
    if is_pallet_plan:
        packing_header.append("Boxes Inside")
    if has_tilt:
        packing_header.append("Tilt")
    packing_list.append(packing_header)
    load_order = {piece_id: i + 1 for i, piece_id in enumerate(state.load_sequence)}
    unload_order = {piece_id: i + 1 for i, piece_id in enumerate(state.unload_sequence)}
    for p in state.placed:
        row = [
            load_order.get(p.id, ""),
            unload_order.get(p.id, ""),
            p.code,
            p.description,
            p.source_width,
            p.source_height,
            p.source_thickness,
            p.weight,
            p.group,
            p.system,
            p.priority,
        ]
        if is_pallet_plan:
            row.append(p.boxes_inside)
        if has_tilt:
            row.append(_format_signed_tilt(p.tilt_angle))
        packing_list.append(row)

    unloaded_sheet = wb.create_sheet("Unloaded Items")
    unloaded_header = ["Code", "Description", "Width", "Height", "Thickness", "Weight", "Reason"]
    if is_pallet_plan:
        unloaded_header.append("Boxes Inside")
    unloaded_sheet.append(unloaded_header)
    for u in state.unloaded:
        row = [u.code, u.description, u.width, u.height, u.thickness, u.weight, u.reason]
        if is_pallet_plan:
            row.append(u.boxes_inside)
        unloaded_sheet.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
