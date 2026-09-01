import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import cuboxLogo from "./assets/cubox-logo.png";
import {
  applyMove,
  exportContainerReportPdf,
  exportExcel,
  exportLoadingGuidePdf,
  exportUnloadingGuidePdf,
  fetchContainers,
  getReportSteps,
  importExcel,
  insertPiece,
  lockPiece,
  optimizeRemaining,
  packContainer,
  redo,
  removePiece,
  rotatePiece,
  turnPiece,
  undo,
  unlockPiece,
  type PackLoadSpace,
} from "./api/client";
import { downloadBlob } from "./utils/download";
import { AlternativesPanel } from "./components/AlternativesPanel";
import { ColorByControl } from "./components/ColorByControl";
import { ColorLegend } from "./components/ColorLegend";
import { ImportPanel } from "./components/ImportPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { PieceInspector } from "./components/PieceInspector";
import { ReportSettingsModal, type ReportSettings } from "./components/ReportSettingsModal";
import { Scene3D } from "./components/Scene3D";
import { SequencePanel } from "./components/SequencePanel";
import { SettingsModal, type Theme } from "./components/SettingsModal";
import { UnloadedPanel } from "./components/UnloadedPanel";
import type {
  AlternativeSolution,
  ColorByMode,
  ContainerSpec,
  CustomLoadSpaceRequestBody,
  InitialWorkspaceConfig,
  LoadingAnchor,
  OptimizationMode,
  PackingResult,
  UnloadedItem,
  WeightBalanceMode,
  WindowItem,
} from "./types";

function isTypingInField() {
  const tag = document.activeElement?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

const THEME_STORAGE_KEY = "cubox-theme";

function getStoredTheme(): Theme {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "light" ? "light" : "dark";
}

// initialWorkspace es opcional y aditivo (CUBOX 2.0 Fase 5, reemplaza los 2
// props sueltos de la Fase 4): permite que el New Load Plan Wizard entregue
// items ya importados + el Load Space + las Handling Rules elegidas al
// abrir el workspace, sin cambiar en nada el comportamiento existente
// cuando se omite (<App /> sin props sigue siendo exactamente el flujo
// legacy: arranca vacio y el usuario importa/elige el contenedor a mano).
interface AppProps {
  initialWorkspace?: InitialWorkspaceConfig;
  /** Fase 5, seccion 35: boton opcional para volver a Home. Sin persistencia
   * todavia, asi que quien lo dispare debe confirmar que se pierde el
   * trabajo no guardado -esa confirmacion vive en AppRoot, no aca. */
  onExitToHome?: () => void;
}

function App({ initialWorkspace, onExitToHome }: AppProps = {}) {
  const initialContainerId = initialWorkspace && "containerId" in initialWorkspace.loadSpace ? initialWorkspace.loadSpace.containerId : undefined;
  const initialCustomLoadSpace =
    initialWorkspace && "customLoadSpace" in initialWorkspace.loadSpace ? initialWorkspace.loadSpace.customLoadSpace : undefined;

  const [containers, setContainers] = useState<ContainerSpec[]>([]);
  const [items, setItems] = useState<WindowItem[]>(initialWorkspace?.items ?? []);
  const [selectedContainerId, setSelectedContainerId] = useState("");
  // Truck/Trailer/Custom Container definido a mano en el wizard (Fase 5):
  // cuando esta presente, tiene PRIORIDAD sobre selectedContainerId al
  // empaquetar (ver runOptimize) -es la unica fuente de verdad del Load
  // Space elegido, el dropdown de abajo pasa a ser solo informativo.
  const [customLoadSpace] = useState<CustomLoadSpaceRequestBody | undefined>(initialCustomLoadSpace);
  const [optimizationMode, setOptimizationMode] = useState<OptimizationMode>("best_space");
  const [enableCentralAisle, setEnableCentralAisle] = useState(initialWorkspace?.handlingRules.enableCentralAisle ?? false);
  const [aisleWidthMm, setAisleWidthMm] = useState(initialWorkspace?.handlingRules.aisleWidthMm ?? 500);
  const [clearanceMm, setClearanceMm] = useState(initialWorkspace?.handlingRules.clearanceMm ?? 0);
  const [weightBalanceMode, setWeightBalanceMode] = useState<WeightBalanceMode>(initialWorkspace?.handlingRules.weightBalanceMode ?? "normal");
  const [loadingAnchor, setLoadingAnchor] = useState<LoadingAnchor>(initialWorkspace?.handlingRules.loadingAnchor ?? "back_right");
  const [colorBy, setColorBy] = useState<ColorByMode>("default");

  const [result, setResult] = useState<PackingResult | null>(null);
  const [alternatives, setAlternatives] = useState<AlternativeSolution[]>([]);
  const [hasManualEdits, setHasManualEdits] = useState(false);

  const [selectedPieceId, setSelectedPieceId] = useState<string | null>(null);
  const [insertingItem, setInsertingItem] = useState<UnloadedItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [showLabels, setShowLabels] = useState(true);
  const [showCenterOfMass, setShowCenterOfMass] = useState(false);
  const [showSequence, setShowSequence] = useState(false);
  const [sequenceMode, setSequenceMode] = useState<"load" | "unload">("load");
  const [stepIndex, setStepIndex] = useState<number | null>(null);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reportSettingsOpen, setReportSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  // Puente hacia la captura PNG limpia del contenedor (Fit View + toDataURL),
  // usada por los reportes PDF -ver Scene3D.tsx.
  const captureRef = useRef<(() => Promise<string>) | undefined>(undefined);
  const [exportingPdf, setExportingPdf] = useState(false);
  // Mientras se genera una Loading/Unloading Guide, Scene3D renderiza el paso
  // guideStepIndex de guideSteps (pasado/actual/futuro) en vez del estado
  // interactivo normal -se restauran a null/undefined al terminar.
  const [guideSteps, setGuideSteps] = useState<string[][] | undefined>(undefined);
  const [guideStepIndex, setGuideStepIndex] = useState<number | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    fetchContainers()
      .then((list) => {
        setContainers(list);
        const preferred = initialContainerId && list.some((c) => c.id === initialContainerId) ? initialContainerId : list[0]?.id;
        if (preferred) setSelectedContainerId(preferred);
      })
      .catch(() => setError("No se pudo conectar con el backend (http://localhost:8000)."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleFileSelected(file: File) {
    setError("");
    try {
      const parsed = await importExcel(file);
      setItems(parsed);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Error al importar el Excel");
    }
  }

  async function runOptimize() {
    setLoading(true);
    setError("");
    setSelectedPieceId(null);
    setInsertingItem(null);
    setStepIndex(null);
    try {
      // Si hay piezas Locked, un recalculo completo desde el Excel importado
      // las trataria como piezas nuevas mas y las volveria a colocar donde
      // el algoritmo quiera -eso es exactamente el bug reportado: Locked
      // deja de significar algo apenas se vuelve a pulsar Optimize-. Locked
      // debe ser inmune tanto a Optimize/Re-optimize como a Optimize
      // Remaining, asi que en ese caso se usa el mismo camino que Optimize
      // Remaining (mantiene las Locked fijas y reoptimiza el resto).
      const hasLocked = result?.placed.some((p) => p.locked) ?? false;
      const loadSpace: PackLoadSpace = customLoadSpace ? { customLoadSpace } : { containerId: selectedContainerId };
      const response = hasLocked
        ? await optimizeRemaining({ optimizationMode, weightBalanceMode, loadingAnchor })
        : await packContainer(items, loadSpace, {
            optimizationMode,
            enableCentralAisle,
            aisleWidthMm,
            clearanceMm,
            weightBalanceMode,
            loadingAnchor,
          });
      setResult(response.best);
      setAlternatives(response.alternatives);
      setHasManualEdits(false);
      // Al optimizar, colorear por System de una vez para poder distinguir
      // los sistemas de inmediato (el usuario puede cambiarlo despues).
      setColorBy("system");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Error al calcular el cubicaje");
    } finally {
      setLoading(false);
    }
  }

  async function handlePack() {
    await runOptimize();
  }

  async function handleReoptimize() {
    if (hasManualEdits) {
      const confirmed = window.confirm("Re-optimizing will replace your current manual layout.");
      if (!confirmed) return;
    }
    await runOptimize();
  }

  async function handleOptimizeRemaining() {
    setLoading(true);
    setError("");
    try {
      const response = await optimizeRemaining({ optimizationMode, weightBalanceMode, loadingAnchor });
      setResult(response.best);
      setAlternatives(response.alternatives);
      setHasManualEdits(false);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Error al reoptimizar las piezas restantes");
    } finally {
      setLoading(false);
    }
  }

  async function handleToggleLock() {
    if (!selectedPieceId) return;
    const piece = result?.placed.find((p) => p.id === selectedPieceId);
    if (!piece) return;
    try {
      const updated = piece.locked ? await unlockPiece(selectedPieceId) : await lockPiece(selectedPieceId);
      setResult(updated);
      setAlternatives([]);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "No se pudo cambiar el estado de bloqueo");
    }
  }

  function handleSelectAlternative(alt: AlternativeSolution) {
    setResult(alt.result);
    setSelectedPieceId(null);
    setInsertingItem(null);
    setStepIndex(null);
  }

  async function handleExportExcel() {
    try {
      const blob = await exportExcel();
      downloadBlob(blob, "cubox-cubicaje.xlsx");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Error al exportar a Excel");
    }
  }

  // Disparador minimo del Container Load Report (V4.6) -placeholder hasta
  // que el Report Settings modal (V4.10) reemplace este boton con las
  // opciones reales de Sort By / Project Name / Customer / etc.
  // Genera el reporte PDF elegido en el Report Settings modal. Container
  // Load Report toma un unico snapshot (Fit View); Loading/Unloading Guide
  // toman un snapshot por paso, avanzando guideStepIndex y dejando que
  // Scene3D re-renderice pasado/actual/futuro antes de cada captura -ver
  // captureRef/guideStateById en Scene3D.tsx.
  async function handleGenerateReport(settings: ReportSettings) {
    setExportingPdf(true);
    setError("");
    const meta = { projectName: settings.projectName, customer: settings.customer };
    try {
      if (settings.reportType === "container") {
        const overviewImagePngBase64 = settings.includeOverviewImage ? await captureRef.current?.() : undefined;
        const blob = await exportContainerReportPdf({
          meta,
          sortBy: settings.sortBy,
          includeOverviewImage: Boolean(overviewImagePngBase64),
          overviewImagePngBase64: overviewImagePngBase64?.split(",")[1],
        });
        downloadBlob(blob, "cubox-container-report.pdf");
        return;
      }

      const direction = settings.reportType === "loading_guide" ? "load" : "unload";
      const steps = await getReportSteps(direction, {
        stepMode: settings.stepMode,
        piecesPerStep: settings.stepMode === "manual" ? settings.piecesPerStep : undefined,
      });
      if (steps.length === 0) {
        setError("No hay pasos para generar la guia (el contenedor esta vacio).");
        return;
      }
      setGuideSteps(steps);

      const images: string[] = [];
      for (let i = 0; i < steps.length; i++) {
        setGuideStepIndex(i);
        // Un frame para que React/R3F apliquen el nuevo paso antes de
        // capturar (captureRef.current tambien espera 2 frames mas por su
        // cuenta despues de fijar la camara).
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        const png = await captureRef.current?.();
        if (png) images.push(png.split(",")[1]);
      }

      const exportFn = direction === "load" ? exportLoadingGuidePdf : exportUnloadingGuidePdf;
      const blob = await exportFn({
        meta,
        stepMode: settings.stepMode,
        piecesPerStep: settings.stepMode === "manual" ? settings.piecesPerStep : undefined,
        stepImagesPngBase64: images,
      });
      downloadBlob(blob, direction === "load" ? "cubox-loading-guide.pdf" : "cubox-unloading-guide.pdf");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Error al generar el reporte");
    } finally {
      setGuideSteps(undefined);
      setGuideStepIndex(null);
      setExportingPdf(false);
      setReportSettingsOpen(false);
    }
  }

  const handleCommitMove = useCallback(
    async (pieceId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => {
      try {
        const updated = await applyMove(pieceId, x, y, z, dx, dy, dz);
        setResult(updated);
        setHasManualEdits(true);
        setAlternatives([]);
      } catch {
        /* el backend rechazo el movimiento; el estado local no cambia */
      }
    },
    []
  );

  const handleCommitInsert = useCallback(
    async (unloadedId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => {
      try {
        const updated = await insertPiece(unloadedId, x, y, z, dx, dy, dz);
        setResult(updated);
        setInsertingItem(null);
        setSelectedPieceId(unloadedId);
        setHasManualEdits(true);
        setAlternatives([]);
      } catch {
        /* el backend rechazo la insercion; la pieza sigue en Unloaded Items */
      }
    },
    []
  );

  async function handleRotate(): Promise<{ ok: boolean; reason: string }> {
    if (!selectedPieceId) return { ok: false, reason: "" };
    try {
      const updated = await rotatePiece(selectedPieceId);
      setResult(updated);
      setHasManualEdits(true);
      setAlternatives([]);
      return { ok: true, reason: "" };
    } catch (e: any) {
      return { ok: false, reason: e?.response?.data?.detail || "No se pudo rotar la pieza" };
    }
  }

  async function handleTurn(): Promise<{ ok: boolean; reason: string }> {
    if (!selectedPieceId) return { ok: false, reason: "" };
    try {
      const updated = await turnPiece(selectedPieceId);
      setResult(updated);
      setHasManualEdits(true);
      setAlternatives([]);
      return { ok: true, reason: "" };
    } catch (e: any) {
      return { ok: false, reason: e?.response?.data?.detail || "No se pudo girar la pieza" };
    }
  }

  async function handleRemove() {
    if (!selectedPieceId) return;
    try {
      const updated = await removePiece(selectedPieceId);
      setResult(updated);
      setSelectedPieceId(null);
      setHasManualEdits(true);
      setAlternatives([]);
    } catch {
      /* nada que hacer: quitar una pieza colocada siempre deberia ser valido */
    }
  }

  async function handleUndo() {
    try {
      setResult(await undo());
    } catch {
      /* no hay nada que deshacer */
    }
  }

  async function handleRedo() {
    try {
      setResult(await redo());
    } catch {
      /* no hay nada que rehacer */
    }
  }

  function handleStartPlacing(item: UnloadedItem) {
    setInsertingItem(item);
    setSelectedPieceId(null);
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (isTypingInField()) return;

      if (e.key === "Escape") {
        setInsertingItem(null);
        setSelectedPieceId(null);
        return;
      }
      if ((e.key === "r" || e.key === "R") && selectedPieceId) {
        handleRotate();
        return;
      }
      if ((e.key === "t" || e.key === "T") && selectedPieceId) {
        handleTurn();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        handleUndo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))) {
        e.preventDefault();
        handleRedo();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPieceId]);

  const selectedPiece = result?.placed.find((p) => p.id === selectedPieceId) ?? null;
  const activeSequence = result ? (sequenceMode === "load" ? result.load_sequence : result.unload_sequence) : [];

  return (
    <div className="app-layout">
      <aside className="sidebar left">
        <div className="app-header">
          <img src={cuboxLogo} alt="CUBOX" className="app-logo" />
          <div style={{ display: "flex", gap: 6 }}>
            {onExitToHome && (
              <button type="button" className="settings-btn" title="Volver a Home" onClick={onExitToHome}>
                🏠
              </button>
            )}
            <button
              type="button"
              className="settings-btn"
              title="Ajustes"
              onClick={() => setSettingsOpen(true)}
            >
              ⚙
            </button>
          </div>
        </div>
        <ImportPanel
          containers={containers}
          items={items}
          selectedContainerId={selectedContainerId}
          customLoadSpace={customLoadSpace}
          optimizationMode={optimizationMode}
          enableCentralAisle={enableCentralAisle}
          aisleWidthMm={aisleWidthMm}
          clearanceMm={clearanceMm}
          weightBalanceMode={weightBalanceMode}
          loadingAnchor={loadingAnchor}
          loading={loading}
          error={error}
          onFileSelected={handleFileSelected}
          onContainerChange={setSelectedContainerId}
          onOptimizationModeChange={setOptimizationMode}
          onEnableCentralAisleChange={setEnableCentralAisle}
          onAisleWidthChange={setAisleWidthMm}
          onClearanceChange={setClearanceMm}
          onWeightBalanceModeChange={setWeightBalanceMode}
          onLoadingAnchorChange={setLoadingAnchor}
          onPack={handlePack}
        />
        {result && (
          <>
            <div className="undo-redo-row">
              <button className="btn" onClick={handleUndo}>
                Undo (Ctrl+Z)
              </button>
              <button className="btn" onClick={handleRedo}>
                Redo (Ctrl+Y)
              </button>
            </div>
            <div className="undo-redo-row">
              <button className="btn" onClick={handleReoptimize} disabled={loading}>
                Re-optimize
              </button>
              <button className="btn" onClick={handleOptimizeRemaining} disabled={loading}>
                Optimize Remaining
              </button>
            </div>
            <button className="btn" onClick={handleExportExcel}>
              Export to Excel
            </button>
            <button className="btn" onClick={() => setReportSettingsOpen(true)}>
              Reports (PDF)...
            </button>
            <ColorByControl value={colorBy} onChange={setColorBy} />
            <label className="checkbox-row">
              <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
              Mostrar Code + Description + Group sobre las piezas
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={showCenterOfMass}
                onChange={(e) => setShowCenterOfMass(e.target.checked)}
              />
              Show Center of Mass
            </label>
            <ColorLegend placed={result.placed} colorBy={colorBy} />
            <MetricsPanel metrics={result.metrics} />
          </>
        )}
      </aside>

      <main className="viewport">
        <Scene3D
          result={result}
          selectedPieceId={selectedPieceId}
          colorBy={colorBy}
          insertingItem={insertingItem}
          showSequence={showSequence}
          showLabels={showLabels}
          showCenterOfMass={showCenterOfMass}
          activeSequence={activeSequence}
          stepIndex={stepIndex}
          guideSteps={guideSteps}
          guideStepIndex={guideStepIndex}
          captureRef={captureRef}
          onSelectPiece={setSelectedPieceId}
          onCommitMove={handleCommitMove}
          onCommitInsert={handleCommitInsert}
        />
      </main>

      <aside className="sidebar right">
        {selectedPiece && (
          <PieceInspector
            piece={selectedPiece}
            onRotate={handleRotate}
            onTurn={handleTurn}
            onRemove={handleRemove}
            onToggleLock={handleToggleLock}
          />
        )}
        {result && alternatives.length > 1 && (
          <AlternativesPanel alternatives={alternatives} onSelect={handleSelectAlternative} />
        )}
        {result && (
          <SequencePanel
            result={result}
            show={showSequence}
            onToggleShow={() => setShowSequence((v) => !v)}
            mode={sequenceMode}
            onModeChange={setSequenceMode}
            stepIndex={stepIndex}
            onStepChange={setStepIndex}
          />
        )}
        {result && (
          <UnloadedPanel
            items={result.unloaded}
            insertingItemId={insertingItem?.id ?? null}
            onStartPlacing={handleStartPlacing}
            onCancelPlacing={() => setInsertingItem(null)}
          />
        )}
      </aside>

      {settingsOpen && (
        <SettingsModal theme={theme} onThemeChange={setTheme} onClose={() => setSettingsOpen(false)} />
      )}
      {reportSettingsOpen && (
        <ReportSettingsModal
          onClose={() => setReportSettingsOpen(false)}
          onGenerate={handleGenerateReport}
          generating={exportingPdf}
        />
      )}
    </div>
  );
}

export default App;
