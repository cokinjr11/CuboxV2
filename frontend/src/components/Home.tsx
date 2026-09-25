import { useEffect, useState } from "react";
import cuboxLogo from "../assets/cubox-logo.png";
import { deletePlan, listRecentPlans } from "../api/client";
import { extractErrorMessage } from "../utils/errors";
import type { PlanSummary } from "../types";

interface Props {
  onNewLoadPlan: () => void;
  onOpenLegacyWorkspace: () => void;
  onOpenPlan: (planId: string) => void;
  /** Fase 5D: si abrir el ultimo plan seleccionado fallo (404/409/422 del
   * backend -ver GET /api/plans/{id}), se muestra aca en vez de dejar al
   * usuario en un Home silenciosamente roto. */
  openPlanError?: string;
}

// Fase 5D, seccion 39 del pedido: los timestamps se guardan en UTC; esta es
// la unica conversion a hora local, solo para mostrar.
function formatModified(isoUtc: string): string {
  try {
    const date = new Date(isoUtc);
    return `Modified ${date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
  } catch {
    return "";
  }
}

export function Home({ onNewLoadPlan, onOpenLegacyWorkspace, onOpenPlan, openPlanError }: Props) {
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [listError, setListError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<PlanSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  function reloadPlans() {
    listRecentPlans(10)
      .then(setPlans)
      .catch((e) => setListError(extractErrorMessage(e, "No se pudieron cargar los Recent Plans.")));
  }

  useEffect(() => {
    reloadPlans();
  }, []);

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deletePlan(pendingDelete.plan_id);
      setPlans((prev) => (prev ?? []).filter((p) => p.plan_id !== pendingDelete.plan_id));
      setPendingDelete(null);
    } catch (e) {
      setListError(extractErrorMessage(e, "No se pudo borrar el Load Plan."));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="home-shell">
      <header className="home-topbar">
        <img src={cuboxLogo} alt="CUBOX" />
        <span className="home-topbar-title">Load Planning</span>
      </header>

      <main className="home-main">
        <h1>CUBOX</h1>
        <p className="home-tagline">Plan, prepare and execute your loads.</p>

        <button type="button" className="btn-primary" onClick={onNewLoadPlan}>
          New Load Plan
        </button>

        {openPlanError && <p className="error">{openPlanError}</p>}

        <section className="recent-plans">
          <h2>Recent Plans</h2>
          {listError && <p className="error">{listError}</p>}
          {plans === null && !listError && <div className="recent-plans-empty">Loading...</div>}
          {plans !== null && plans.length === 0 && <div className="recent-plans-empty">No saved plans yet.</div>}
          {plans !== null && plans.length > 0 && (
            <ul className="recent-plans-list">
              {plans.map((plan) => (
                <li key={plan.plan_id} className="recent-plan-card">
                  <div className="recent-plan-card-main">
                    <div className="recent-plan-card-title">{plan.name}</div>
                    <div className="recent-plan-card-meta">
                      {plan.load_space_name} &middot; {plan.load_type}
                    </div>
                    <div className="recent-plan-card-meta">
                      {plan.loaded_items} / {plan.total_items} Loaded
                      {plan.unloaded_items > 0 && <span className="recent-plan-card-unloaded"> &middot; {plan.unloaded_items} Unloaded</span>}
                    </div>
                    <div className="recent-plan-card-modified">{formatModified(plan.updated_at)}</div>
                  </div>
                  <div className="recent-plan-card-actions">
                    <button type="button" className="btn-secondary" onClick={() => onOpenPlan(plan.plan_id)}>
                      Open
                    </button>
                    <button
                      type="button"
                      className="btn-link recent-plan-card-delete"
                      onClick={() => setPendingDelete(plan)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>

      <footer className="home-footer">
        {/* Acceso temporario al workspace CUBOX 1.0 mientras el flujo nuevo
            no cubre todavia optimizacion end-to-end para todos los Load
            Types -ver Fase 4, seccion 25. Se puede quitar mas adelante. */}
        <button type="button" className="btn-link" onClick={onOpenLegacyWorkspace}>
          Open Legacy Workspace
        </button>
      </footer>

      {pendingDelete && (
        <div className="modal-overlay" role="presentation" onClick={() => !deleting && setPendingDelete(null)}>
          <div className="modal-box" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>Delete "{pendingDelete.name}"?</h3>
            <p>This permanently removes the saved Load Plan.</p>
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={() => setPendingDelete(null)} disabled={deleting}>
                Cancel
              </button>
              <button type="button" className="btn-primary" onClick={confirmDelete} disabled={deleting}>
                {deleting ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
