import { useEffect, useState } from "react";
import type {
  PackingMetrics,
  ReportValidationResult,
  RoadWeightMetrics,
  ValidationCategory,
  ValidationIssue,
} from "../types";

// Fase 6C, seccion 5/20 del pedido: mismas categorias chicas y estables que
// ValidationCategory en el backend, en el orden en que se muestran -
// DATA_INTEGRITY queda afuera (ver final_validation.py:_DISPLAY_CATEGORIES,
// nunca deberia dispararse en un plan real).
const CATEGORY_ORDER: ValidationCategory[] = [
  "load_space",
  "collision_clearance",
  "support_stability",
  "orientation",
  "stack_weight",
  "payload",
  "road_weight",
  "operational_sequence",
  "delivery_sequence",
  "unloaded_items",
];

const CATEGORY_LABELS: Record<ValidationCategory, string> = {
  data_integrity: "Data Integrity",
  load_space: "Load Space",
  collision_clearance: "Collision & Clearance",
  support_stability: "Support / Stability",
  orientation: "Orientation",
  stack_weight: "Stack Weight",
  payload: "Payload",
  road_weight: "Road Weight",
  operational_sequence: "Operational Sequence",
  delivery_sequence: "Delivery Sequence",
  unloaded_items: "Unloaded Items",
};

const STATUS_LABELS: Record<ReportValidationResult["status"], string> = {
  ready: "READY",
  ready_with_warnings: "READY WITH WARNINGS",
  not_ready: "NOT READY",
};

// Fase 6C, seccion 23 del pedido: estas 2 categorias YA tienen su propio
// panel de detalle (SequencePanel/UnloadedPanel) -Plan Validation resume,
// nunca repite las filas individuales aca.
const CATEGORIES_WITH_EXTERNAL_DETAIL: ValidationCategory[] = ["delivery_sequence", "unloaded_items"];
const EXTERNAL_DETAIL_HINT: Partial<Record<ValidationCategory, string>> = {
  delivery_sequence: "View in Operational Warnings",
  unloaded_items: "View Unloaded Items",
};

interface Props {
  validation: ReportValidationResult | null;
  loading: boolean;
  metrics: PackingMetrics;
  roadWeight: RoadWeightMetrics | null | undefined;
}

export function PlanValidationPanel({ validation, loading, metrics, roadWeight }: Props) {
  // Fase 6C, seccion 22 del pedido: mismo patron ya probado en Phase 6A
  // (summary count + "View details") -arranca colapsado en cada resultado
  // nuevo, igual criterio que SequencePanel con `result`.
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => {
    setDetailsOpen(false);
  }, [validation]);

  if (!validation) {
    return (
      <div className="panel">
        <h2>Plan Validation</h2>
        <p className="hint">{loading ? "Validating..." : "No plan to validate yet."}</p>
      </div>
    );
  }

  const issuesByCategory = new Map<ValidationCategory, ValidationIssue[]>();
  for (const issue of [...validation.error_issues, ...validation.warning_issues]) {
    const list = issuesByCategory.get(issue.category) ?? [];
    list.push(issue);
    issuesByCategory.set(issue.category, list);
  }
  const categoryByName = new Map(validation.categories.map((c) => [c.category, c]));

  // Categorias con detalle INTERNO (sin panel propio en otro lado) y al
  // menos un issue -son las unicas candidatas a "View details" mas abajo.
  const detailCategories = CATEGORY_ORDER.filter((cat) => {
    if (CATEGORIES_WITH_EXTERNAL_DETAIL.includes(cat)) return false;
    const status = categoryByName.get(cat);
    return status && (status.status === "error" || status.status === "warning");
  });

  return (
    <div className="panel">
      <div className="plan-validation-header">
        <h2>Plan Validation</h2>
        {loading && <span className="hint">Validating...</span>}
      </div>
      <p className={validation.status === "not_ready" ? "error" : validation.status === "ready" ? "success" : "error"}>
        {validation.status === "ready" ? "✓ " : validation.status === "not_ready" ? "✕ " : "⚠ "}
        {STATUS_LABELS[validation.status]}
      </p>

      <ul className="plan-validation-categories">
        {CATEGORY_ORDER.map((cat) => {
          const status = categoryByName.get(cat);
          if (!status) return null;
          const label = CATEGORY_LABELS[cat];

          if (status.status === "not_applicable") {
            return (
              <li key={cat} className="plan-validation-row hint">
                — {label}: N/A
              </li>
            );
          }
          if (status.status === "pass") {
            return (
              <li key={cat} className="plan-validation-row">
                ✓ {label}
                {cat === "road_weight" && roadWeight && <RoadWeightBreakdown roadWeight={roadWeight} />}
                {cat === "payload" && <PayloadBreakdown metrics={metrics} />}
              </li>
            );
          }

          const icon = status.status === "error" ? "✕" : "⚠";
          const count = status.count;
          const noun = count === 1 ? "issue" : "issues";
          const externalHint = EXTERNAL_DETAIL_HINT[cat];

          return (
            <li key={cat} className="plan-validation-row">
              {icon} {label} — {count} {noun}
              {externalHint && <span className="hint plan-validation-external-hint"> [{externalHint}]</span>}
              {cat === "road_weight" && roadWeight && <RoadWeightBreakdown roadWeight={roadWeight} />}
              {cat === "payload" && <PayloadBreakdown metrics={metrics} />}
            </li>
          );
        })}
      </ul>

      {detailCategories.length > 0 && (
        <>
          <button className="btn-link" onClick={() => setDetailsOpen((v) => !v)}>
            {detailsOpen ? "▾ Hide details" : "▸ View details"}
          </button>
          {detailsOpen && (
            <div className="plan-validation-details">
              {detailCategories.map((cat) => (
                <div key={cat}>
                  <p className="hint plan-validation-detail-heading">{CATEGORY_LABELS[cat]}</p>
                  <ul className="sequence-warning-details">
                    {(issuesByCategory.get(cat) ?? []).map((issue, i) => (
                      <li key={i}>{issue.message}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RoadWeightBreakdown({ roadWeight }: { roadWeight: RoadWeightMetrics }) {
  return (
    <ul className="plan-validation-road-weight">
      {roadWeight.supports.map((s) => (
        <li key={s.id} className={s.overloaded ? "error" : "hint"}>
          {s.name}: {Math.round(s.total_load_kg)} kg / {Math.round(s.max_load_kg)} kg
        </li>
      ))}
    </ul>
  );
}

function PayloadBreakdown({ metrics }: { metrics: PackingMetrics }) {
  return (
    <span className="hint plan-validation-payload">
      {" "}
      ({Math.round(metrics.total_weight)} kg / {Math.round(metrics.max_payload)} kg)
    </span>
  );
}
