import cuboxLogo from "../assets/cubox-logo.png";

interface Props {
  onNewLoadPlan: () => void;
  onOpenLegacyWorkspace: () => void;
}

export function Home({ onNewLoadPlan, onOpenLegacyWorkspace }: Props) {
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

        <section className="recent-plans">
          <h2>Recent Plans</h2>
          <div className="recent-plans-empty">No saved plans yet.</div>
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
    </div>
  );
}
