"""Integracion NAGSA, A2 (H8, H9): validacion final, pasos operativos y
documentos (PDF/Excel) de un plan. Unica responsabilidad: producir la
informacion y los BYTES de cada reporte a partir de un EvaluatedPlan.

NO arma respuestas HTTP (Content-Disposition, ZIP para descarga: eso es
api/file_responses.py) y NO lee SQLite: el nombre/Load Type del plan llega
como `PlanLabel` desde quien llama (modo local: la fila persistida; modo
integrado: el propio request). Antes todo eso estaba mezclado en
api/routes.py:_guide_pdf/_plan_name_and_type/export_*.

Los builders de core/pdf_export.py y core/excel_export.py no cambian."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.excel_export import build_export_workbook
from app.core.final_validation import build_plan_validation_result
from app.core.pdf_export import (
    available_unload_groups,
    build_container_report_pdf,
    build_loading_guide_pdf,
    build_unloading_guide_pdf,
    build_unloading_guide_pdf_for_group,
)
from app.core.sequence import (
    annotate_unload_steps_with_delivery,
    chunk_sequence,
    compute_load_steps,
    compute_unload_steps,
    group_unload_steps_into_delivery_sections,
)
from app.models.schemas import (
    ContainerReportRequest,
    GuideReportRequest,
    PlanValidationStatus,
    ReportDirection,
    ReportStepsRequest,
    ReportStepsResponse,
    ReportValidationResponse,
    StepMode,
)
from app.services.derivation import EvaluatedPlan
from app.services.errors import PlanOpError, PlanOpErrorKind, invalid_input
from app.services.plan_state import reserved_zones_from_state


@dataclass(frozen=True)
class PlanLabel:
    """Datos cosmeticos de portada -nunca condicion de validacion. None =
    plan sin nombre (ej. sesion local no persistida)."""

    name: str | None = None
    load_type: str | None = None


NO_LABEL = PlanLabel()


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------


def validate_plan(plan: EvaluatedPlan) -> ReportValidationResponse:
    """Fase 6C: UNICA funcion de agregacion de Plan Validation
    (build_plan_validation_result) -la consumen el panel Plan Validation del
    Workspace y el preflight de reportes."""
    return build_plan_validation_result(
        plan.result, plan.state.load_space, reserved_zones_from_state(plan.state.reserved_zones), plan.state.clearance
    )


def export_gate(plan: EvaluatedPlan, allow_export_with_errors: bool) -> ReportValidationResponse:
    """Configurable Export Validation Override (Part B): defensa en
    profundidad -cada export revalida el estado. NOT_READY (errores
    bloqueantes) se rechaza por default (NOT_EXPORTABLE);
    `allow_export_with_errors` permite continuar igual -la validacion se
    devuelve siempre para que el documento se marque como generado bajo
    override (nunca como aprobado)."""
    validation = validate_plan(plan)
    if validation.status == PlanValidationStatus.NOT_READY and not allow_export_with_errors:
        raise PlanOpError(PlanOpErrorKind.NOT_EXPORTABLE, {"errors": validation.errors, "status": validation.status.value})
    return validation


def export_override(validation: ReportValidationResponse) -> ReportValidationResponse | None:
    """None para un export normal; la validacion completa solo cuando se
    exporta un NOT_READY bajo override -los builders la usan para marcar
    'EXPORTED WITH VALIDATION ERRORS' (seccion 44: nunca en un export
    normal)."""
    return validation if validation.status == PlanValidationStatus.NOT_READY else None


# ---------------------------------------------------------------------------
# Pasos operativos
# ---------------------------------------------------------------------------


def compute_steps(
    plan: EvaluatedPlan, direction: ReportDirection, step_mode: StepMode, pieces_per_step: int | None
) -> list[list[str]]:
    """Automatic (dependencias + waves) o Manual (chunking fijo) -una sola
    logica para la guia interactiva y para los PDF."""
    if step_mode == StepMode.MANUAL:
        if not pieces_per_step or pieces_per_step <= 0:
            raise invalid_input("pieces_per_step es requerido y debe ser mayor a 0 en modo Manual")
        base_sequence = plan.result.load_sequence if direction == ReportDirection.LOAD else plan.result.unload_sequence
        return chunk_sequence(base_sequence, pieces_per_step)
    if direction == ReportDirection.LOAD:
        return compute_load_steps(plan.result.placed, plan.state.load_space, plan.state.loading_anchor)
    return compute_unload_steps(plan.result.placed)


def report_steps(plan: EvaluatedPlan, req: ReportStepsRequest) -> ReportStepsResponse:
    """Fase 6B, seccion 12/13: para UNLOAD se agrega anotacion de Delivery
    Sequence por paso y agrupacion en secciones contiguas -NUNCA se reordena
    `steps`. None para LOAD (Delivery Sequence es un concepto de descarga)."""
    steps = compute_steps(plan, req.direction, req.step_mode, req.pieces_per_step)
    if req.direction != ReportDirection.UNLOAD:
        return ReportStepsResponse(steps=steps)
    unload_step_info = annotate_unload_steps_with_delivery(steps, plan.result.placed, plan.result.operational_warnings)
    delivery_sections = group_unload_steps_into_delivery_sections(unload_step_info)
    return ReportStepsResponse(steps=steps, unload_step_info=unload_step_info, delivery_sections=delivery_sections)


def unload_groups(plan: EvaluatedPlan) -> list[str]:
    """Groups distintos del plan (mismo criterio que ColorLegend.tsx)."""
    return available_unload_groups(plan.result.placed)


def _require_step_images(steps: list[list[str]], images: list[str]) -> None:
    if len(images) < len(steps):
        raise invalid_input(
            f"Faltan imagenes: se esperaban {len(steps)} snapshots (uno por paso) y llegaron {len(images)}"
        )


# ---------------------------------------------------------------------------
# Documentos (bytes)
# ---------------------------------------------------------------------------


def build_excel(plan: EvaluatedPlan, allow_export_with_errors: bool) -> bytes:
    """Fase 6C, seccion 13: mismo gate de errores duros que los PDF."""
    validation = export_gate(plan, allow_export_with_errors)
    return build_export_workbook(plan.result, export_override(validation))


def build_container_report(plan: EvaluatedPlan, req: ContainerReportRequest, label: PlanLabel = NO_LABEL) -> bytes:
    validation = export_gate(plan, req.allow_export_with_errors)
    if req.include_overview_image and not req.overview_image_png_base64:
        raise invalid_input("Falta la imagen de overview (overview_image_png_base64)")
    return build_container_report_pdf(plan.result, req, label.name, label.load_type, export_override(validation))


def _build_guide(plan: EvaluatedPlan, direction: ReportDirection, req: GuideReportRequest, builder, label: PlanLabel) -> bytes:
    validation = export_gate(plan, req.allow_export_with_errors)
    steps = compute_steps(plan, direction, req.step_mode, req.pieces_per_step)
    _require_step_images(steps, req.step_images_png_base64)
    # Seccion 6 del pedido (Fase 6B.3): la portada SIEMPRE muestra el status
    # de Fase 6C -se pasa `validation` completo, no export_override(). El
    # modo de optimizacion lo usa la seccion de Operational Warnings del
    # Unloading Guide (build_loading_guide_pdf lo ignora).
    return builder(
        plan.result, steps, req.step_images_png_base64, req.meta, plan.state.optimization_mode,
        label.name, label.load_type, validation,
    )


def build_loading_guide(plan: EvaluatedPlan, req: GuideReportRequest, label: PlanLabel = NO_LABEL) -> bytes:
    return _build_guide(plan, ReportDirection.LOAD, req, build_loading_guide_pdf, label)


def build_unloading_guide(plan: EvaluatedPlan, req: GuideReportRequest, label: PlanLabel = NO_LABEL) -> bytes:
    return _build_guide(plan, ReportDirection.UNLOAD, req, build_unloading_guide_pdf, label)


def require_groups(req: GuideReportRequest) -> list[str]:
    """Precondicion de la Group Unloading Guide, separada para que el modo
    local la chequee ANTES de exigir un cubicaje activo (mismo orden de
    siempre)."""
    if not req.groups:
        raise invalid_input("Debe seleccionar al menos un Group para generar la Group Unloading Guide")
    return req.groups


def build_unloading_guides_by_group(
    plan: EvaluatedPlan, req: GuideReportRequest, label: PlanLabel = NO_LABEL
) -> dict[str, bytes]:
    """Load Organization Model Cleanup, seccion 9-14: un PDF INDEPENDIENTE
    por Group seleccionado (nunca fusiona varios Groups en un documento
    falso). Devuelve {group: pdf} -empaquetar 1 PDF suelto o un ZIP es
    responsabilidad de api/file_responses.py."""
    groups = require_groups(req)
    validation = export_gate(plan, req.allow_export_with_errors)
    steps = compute_steps(plan, ReportDirection.UNLOAD, req.step_mode, req.pieces_per_step)
    _require_step_images(steps, req.step_images_png_base64)

    available = set(unload_groups(plan))
    unknown = [g for g in groups if g not in available]
    if unknown:
        raise invalid_input(f"Group(s) no encontrados en el plan activo: {unknown}")

    return {
        group: build_unloading_guide_pdf_for_group(
            plan.result, steps, req.step_images_png_base64, req.meta, plan.state.optimization_mode, group,
            label.name, label.load_type, validation,
        )
        for group in groups
    }
