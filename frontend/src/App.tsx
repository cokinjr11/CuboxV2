import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import cuboxLogo from "./assets/cubox-logo.png";
import {
  applyMove,
  createPlan,
  exportContainerReportPdf,
  exportExcel,
  exportLoadingGuidePdf,
  exportUnloadingGuidePdf,
  exportUnloadingGuidePdfByGroup,
  fetchContainers,
  getReportSteps,
  importExcel,
  insertPiece,
  lockPiece,
  optimizeRemaining,
  packContainer,
  redo,
  removePiece,
  renamePlan,
  rotatePiece,
  savePlan,
  turnPiece,
  setTilt,
  undo,
  unlockPiece,
  validateReport,
  type PackLoadSpace,
} from "./api/client";
import { downloadBlob } from "./utils/download";
import { extractErrorMessage, extractErrorMessageFromBlobResponse } from "./utils/errors";
import { windowItemsFromRestoredResult } from "./utils/restorePlanItems";
import { AlternativesPanel } from "./components/AlternativesPanel";
import { ColorByControl } from "./components/ColorByControl";
import { ColorLegend } from "./components/ColorLegend";
import { ImportPanel } from "./components/ImportPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { PieceInspector } from "./components/PieceInspector";
import { PlanNameBar } from "./components/PlanNameBar";
import { ReportSettingsModal, type ReportSettings } from "./components/ReportSettingsModal";
import { Scene3D } from "./components/Scene3D";
import { PlanValidationPanel } from "./components/PlanValidationPanel";
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
  OrientationPolicy,
  PackingResult,
  PlanDetail,
  ReportDirection,
  ReportStepsResult,
  ReportValidationResult,
  StepMode,
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

// Configurable Export Validation Override (Part B): preferencia de
// app/usuario, NO de Plan -mismo patron que THEME_STORAGE_KEY (localStorage,
// nunca dentro de PlanSnapshot/build_plan_snapshot, ver auditoria de
// Settings). Default ON (bloquear) = comportamiento seguro de siempre; el
// backend sigue siendo la autoridad real (GuideReportRequest/
// ContainerReportRequest.allow_export_with_errors) -este toggle solo decide
// que le mandamos al backend, nunca bypassea su gate.
const BLOCK_EXPORTS_ON_ERRORS_STORAGE_KEY = "cubox-block-exports-on-errors";

function getStoredBlockExportsOnErrors(): boolean {
  const stored = window.localStorage.getItem(BLOCK_EXPORTS_ON_ERRORS_STORAGE_KEY);
  return stored === null ? true : stored === "true";
}

// initialWorkspace es opcional y aditivo (CUBOX 2.0 Fase 5, reemplaza los 2
// props sueltos de la Fase 4): permite que el New Load Plan Wizard entregue
// items ya importados + el Load Space + las Handling Rules elegidas al
// abrir el workspace, sin cambiar en nada el comportamiento existente
// cuando se omite (<App /> sin props sigue siendo exactamente el flujo
// legacy: arranca vacio y el usuario importa/elige el contenedor a mano).
interface AppProps {
  initialWorkspace?: InitialWorkspaceConfig;
  /** Fase 5D: cuando esta presente, el Workspace se hidrata DIRECTO desde un
   * Load Plan ya guardado (GET /api/plans/{id}) -tiene prioridad sobre
   * initialWorkspace y NUNCA dispara un pack/optimize automatico (seccion
   * 13/14 del pedido: abrir un plan no debe reoptimizar). Mutuamente
   * excluyente con initialWorkspace en la practica (AppRoot solo pasa uno
   * de los dos con datos reales). */
  restoredPlan?: PlanDetail;
  /** Fase 5, seccion 35: boton opcional para volver a Home. */
  onExitToHome?: () => void;
}

function App({ initialWorkspace, restoredPlan, onExitToHome }: AppProps = {}) {
  // Fase 5 bugfix: si el workspace se abrio desde el wizard (initialWorkspace
  // presente) o desde un plan guardado (restoredPlan), los items ya existen
  // de antes -el panel "1. Importar Excel" de aca pasa a ser solo
  // informativo (ver ImportPanel). Solo el flujo legacy directo ("Open
  // Legacy Workspace" sin pasar por ninguno de los 2) sigue necesitando el
  // boton de importar aca.
  const cameFromWizard = initialWorkspace !== undefined || restoredPlan !== undefined;
  const initialContainerId =
    restoredPlan?.container_id ??
    (initialWorkspace && "containerId" in initialWorkspace.loadSpace ? initialWorkspace.loadSpace.containerId : undefined);
  const initialCustomLoadSpace =
    restoredPlan?.custom_load_space ??
    (initialWorkspace && "customLoadSpace" in initialWorkspace.loadSpace ? initialWorkspace.loadSpace.customLoadSpace : undefined) ??
    undefined;

  const [containers, setContainers] = useState<ContainerSpec[]>([]);
  // Fase 5D: un plan restaurado no trae un ImportPreview -se reconstruye
  // WindowItem[] a partir de placed/unloaded (ver utils/restorePlanItems.ts)
  // para que Re-optimize desde cero (sin piezas Locked) siga teniendo algo
  // valido que reenviar, en vez de vaciar el plan.
  const [items, setItems] = useState<WindowItem[]>(
    restoredPlan ? windowItemsFromRestoredResult(restoredPlan.result) : initialWorkspace?.items ?? []
  );
  // Fase 6.1: el ItemType activo del plan es siempre homogeneo dentro de un
  // mismo workspace (un plan solo importa UN perfil -BOX/PALLET/PANEL/
  // CUSTOM-, ver PLANNING_MODE_ITEM_TYPE), asi que alcanza con mirar el
  // primer item importado para adaptar terminologia/metricas (ImportPanel,
  // MetricsPanel, UnloadedPanel) sin agregar un nuevo campo de estado ni
  // tocar el wizard.
  const activeItemType = items[0]?.item_type;
  const [selectedContainerId, setSelectedContainerId] = useState("");
  // Truck/Trailer/Custom Container definido a mano en el wizard (Fase 5) o
  // restaurado de un plan guardado (Fase 5D): cuando esta presente, tiene
  // PRIORIDAD sobre selectedContainerId al empaquetar (ver runOptimize) -es
  // la unica fuente de verdad del Load Space elegido, el dropdown de abajo
  // pasa a ser solo informativo.
  const [customLoadSpace] = useState<CustomLoadSpaceRequestBody | undefined>(initialCustomLoadSpace);
  const [optimizationMode, setOptimizationMode] = useState<OptimizationMode>(restoredPlan?.optimization_mode ?? "best_space");
  const [enableCentralAisle, setEnableCentralAisle] = useState(
    restoredPlan?.enable_central_aisle ?? initialWorkspace?.handlingRules.enableCentralAisle ?? false
  );
  const [aisleWidthMm, setAisleWidthMm] = useState(restoredPlan?.aisle_width_mm ?? initialWorkspace?.handlingRules.aisleWidthMm ?? 500);
  const [clearanceMm, setClearanceMm] = useState(restoredPlan?.clearance_mm ?? initialWorkspace?.handlingRules.clearanceMm ?? 0);
  const [weightBalanceMode, setWeightBalanceMode] = useState<WeightBalanceMode>(
    restoredPlan?.weight_balance_mode ?? initialWorkspace?.handlingRules.weightBalanceMode ?? "normal"
  );
  const [loadingAnchor, setLoadingAnchor] = useState<LoadingAnchor>(
    restoredPlan?.loading_anchor ?? initialWorkspace?.handlingRules.loadingAnchor ?? "back_right"
  );
  // Fase 5B: defaults del PLAN para Handling Rules -antes solo se usaban una
  // vez al importar y se descartaban; ahora viven en el workspace y se
  // reenvian en CADA pack/optimize-remaining (ver runOptimize) para que
  // items "inherit" (sin override propio) reflejen el default ACTUAL, no el
  // que habia al momento del import.
  const [defaultStackable, setDefaultStackable] = useState(
    restoredPlan?.plan_handling_rules?.default_stackable ?? initialWorkspace?.handlingRules.defaultStackable ?? false
  );
  const [orientationPolicy, setOrientationPolicy] = useState<OrientationPolicy>(
    restoredPlan?.plan_handling_rules?.default_orientation_policy ?? initialWorkspace?.handlingRules.orientationPolicy ?? "free"
  );
  // Fase 5C: Tilt permanece deshabilitado en la UI (Fase 5D, seccion 53 del
  // pedido) -incluso si un plan guardado de antes de la desactivacion
  // trajera Tilt habilitado, nunca se restaura a la UI ni se reenvia.
  const [defaultAllowTilt] = useState(false);
  const [defaultMaxTiltAngle] = useState<number | null>(null);
  const [colorBy, setColorBy] = useState<ColorByMode>("default");

  const [result, setResult] = useState<PackingResult | null>(restoredPlan?.result ?? null);
  const [alternatives, setAlternatives] = useState<AlternativeSolution[]>([]);
  const [hasManualEdits, setHasManualEdits] = useState(false);

  // Fase 5D: Recent Plans & Persistence. planId es null hasta que el plan se
  // vuelve persistente por primera vez (createPlan, disparado por el primer
  // Optimize exitoso despues del wizard -seccion 12 del pedido) o hasta que
  // se restaura uno ya guardado. "Open Legacy Workspace" nunca lo setea:
  // esa sesion se queda sin persistir, por diseno (fuera de alcance).
  const [planId, setPlanId] = useState<string | null>(restoredPlan?.plan_id ?? null);
  const [planName, setPlanName] = useState<string>(restoredPlan?.name ?? "");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  // Se salta el PRIMER autosave despues de que (planId, result) esten
  // ambos disponibles -sea porque se acaba de crear el plan (ya se
  // persistio en esa misma llamada) o porque se acaba de restaurar uno
  // (ya coincide exactamente con lo guardado). Cualquier cambio de
  // `result` DESPUES de eso es una edicion real que hay que guardar.
  const skippedFirstAutosaveRef = useRef(false);
  // Mismo criterio que arriba, pero para el autosave INDEPENDIENTE de Plan
  // Handling Rules (Fase 5D correccion final) -su propio ref, porque es un
  // efecto separado con sus propias dependencias.
  const skippedFirstHandlingRulesAutosaveRef = useRef(false);

  // Fase 6C: Plan Validation -resultado del ULTIMO /report/validate
  // disparado proactivamente (ver el useEffect [result] mas abajo), nunca
  // persistido (seccion 19 del pedido: se recalcula siempre, nunca se
  // guarda). `planValidationRequestIdRef` es la guarda anti-respuesta-
  // obsoleta (seccion 16): solo la respuesta de la ULTIMA llamada disparada
  // puede escribir el estado.
  const [planValidation, setPlanValidation] = useState<ReportValidationResult | null>(null);
  const [planValidationLoading, setPlanValidationLoading] = useState(false);
  const planValidationRequestIdRef = useRef(0);

  const [selectedPieceId, setSelectedPieceId] = useState<string | null>(null);
  const [insertingItem, setInsertingItem] = useState<UnloadedItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [showLabels, setShowLabels] = useState(true);
  const [showCenterOfMass, setShowCenterOfMass] = useState(false);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reportSettingsOpen, setReportSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(getStoredTheme);
  const [blockExportsOnErrors, setBlockExportsOnErrors] = useState<boolean>(getStoredBlockExportsOnErrors);

  // Puente hacia la captura PNG limpia del contenedor (Fit View + toDataURL),
  // usada por los reportes PDF -ver Scene3D.tsx.
  const captureRef = useRef<((useGuideCameraView?: boolean) => Promise<string>) | undefined>(undefined);
  const [exportingPdf, setExportingPdf] = useState(false);

  // Fase 6B.1, seccion 1-3 del pedido: Show Steps arranca APAGADO -el
  // Workspace normal (todas las piezas visibles, edicion normal) es la
  // vista por default. La Guia Step-by-Step es un modo de visualizacion
  // OPCIONAL, nunca algo que se active solo -ver seccion 21: separacion
  // clara entre vista normal / vista de guia / captura de reporte.
  const [showSteps, setShowSteps] = useState(false);
  const [guideConfigDirection, setGuideConfigDirection] = useState<ReportDirection>("load");
  const [guideStepMode, setGuideStepMode] = useState<StepMode>("automatic");
  const [guidePiecesPerStep, setGuidePiecesPerStep] = useState(3);
  const [guideStepsResult, setGuideStepsResult] = useState<ReportStepsResult | null>(null);
  const [guideStepsLoading, setGuideStepsLoading] = useState(false);
  const [guideStepsError, setGuideStepsError] = useState("");
  // Paso interactivo actual dentro de la Guia -solo tiene efecto visual
  // mientras showSteps es true (ver sceneGuideStepIndex mas abajo).
  const [guideStepIndex, setGuideStepIndex] = useState(0);

  // Fase 6B.1, seccion 5-7 del pedido: badges de numero de secuencia por
  // pieza -restaurados, INDEPENDIENTES de Show Steps (seccion 6: las 4
  // combinaciones tienen que funcionar). Usa load_sequence/unload_sequence
  // ya calculados -nunca deriva un segundo orden en el frontend.
  const [showSequenceNumbers, setShowSequenceNumbers] = useState(false);
  const [sequenceNumberDirection, setSequenceNumberDirection] = useState<ReportDirection>("load");

  // Fase 6B.1, seccion 14/21 del pedido: estado de captura de reporte
  // COMPLETAMENTE SEPARADO del estado interactivo de arriba -mientras
  // isCapturing es true, Scene3D usa ESTOS valores sin importar en que
  // quedo showSteps/guideStepIndex/guideConfigDirection, y nunca hace falta
  // "guardar y restaurar" nada: el estado interactivo del usuario jamas se
  // toca durante una captura.
  const [isCapturing, setIsCapturing] = useState(false);
  const [captureSteps, setCaptureSteps] = useState<string[][] | undefined>(undefined);
  const [captureStepIndex, setCaptureStepIndex] = useState<number | null>(null);
  const [captureDirection, setCaptureDirection] = useState<ReportDirection | undefined>(undefined);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem(BLOCK_EXPORTS_ON_ERRORS_STORAGE_KEY, String(blockExportsOnErrors));
  }, [blockExportsOnErrors]);

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

  // Fase 5D: Autosave (seccion 24/25 del pedido). Se dispara con CUALQUIER
  // cambio de `result` -eso ya cubre apply-move/insert/remove/rotate/turn/
  // tilt/lock-unlock/undo-redo/optimize/optimize-remaining, todo lo que la
  // seccion 24 pide guardar. Debounce simple (~900ms) para no guardar en
  // cada frame de un arrastre -Scene3D ya solo llama a onCommitMove cuando
  // el movimiento termina, nunca mientras el mouse se mueve, asi que ya
  // llega "asentado" aca.
  useEffect(() => {
    if (!planId || !result) return;
    if (!skippedFirstAutosaveRef.current) {
      skippedFirstAutosaveRef.current = true;
      return;
    }
    setSaveStatus("saving");
    const timeoutId = window.setTimeout(() => {
      savePlan(planId)
        .then(() => setSaveStatus("saved"))
        .catch(() => setSaveStatus("error"));
    }, 900);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, planId]);

  // Fase 5D correccion final, seccion 1/2/3 del pedido: un cambio de Plan
  // Handling Rule (Default Stackable / Default Orientation) NO toca
  // `result` -el backend solo la aplica de verdad en el PROXIMO Optimize
  // (ver runOptimize). Sin este trigger aparte, cambiar la regla y cerrar
  // Cubox sin optimizar perderia la configuracion. Este efecto guarda SOLO
  // la configuracion (savePlan con planHandlingRules) -nunca dispara un
  // pack/optimize, la colocacion existente queda intacta hasta que el
  // usuario pulse Optimize/Re-optimize a proposito (seccion 2).
  useEffect(() => {
    if (!planId) return;
    if (!skippedFirstHandlingRulesAutosaveRef.current) {
      skippedFirstHandlingRulesAutosaveRef.current = true;
      return;
    }
    setSaveStatus("saving");
    const planHandlingRules = {
      default_stackable: defaultStackable,
      default_orientation_policy: orientationPolicy,
      default_allow_tilt: defaultAllowTilt,
      default_max_tilt_angle: defaultMaxTiltAngle,
    };
    const timeoutId = window.setTimeout(() => {
      savePlan(planId, planHandlingRules)
        .then(() => setSaveStatus("saved"))
        .catch(() => setSaveStatus("error"));
    }, 900);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultStackable, orientationPolicy, planId]);

  // Fase 6C, seccion 15/16/18 del pedido: validacion PROACTIVA -se dispara
  // con cualquier cambio de `result` (mismo trigger que el autosave de
  // arriba: Optimize/Re-optimize/Optimize Remaining/manual move-rotate-
  // turn-insert-remove aceptado/reopen de un plan/seleccionar una
  // alternativa), NUNCA con estado de presentacion (Show Steps/Sequence
  // Numbers/navegacion de la guia/camara/ciclo de captura de un reporte)
  // porque ninguno de esos toca `result`. Debounce ~700ms para no pegarle
  // al backend en cada paso intermedio de una edicion -a diferencia del
  // autosave, SIN "skip first": reabrir un plan SI debe disparar la
  // validacion (seccion 18), no solo los cambios subsecuentes.
  useEffect(() => {
    if (!result) {
      setPlanValidation(null);
      setPlanValidationLoading(false);
      return;
    }
    setPlanValidationLoading(true);
    const timeoutId = window.setTimeout(() => {
      const requestId = ++planValidationRequestIdRef.current;
      validateReport()
        .then((validation) => {
          // Guarda contra respuesta obsoleta (seccion 16 del pedido): si la
          // geometria volvio a cambiar mientras esta llamada estaba en
          // vuelo, una respuesta vieja NUNCA debe pisar el resultado nuevo.
          if (planValidationRequestIdRef.current !== requestId) return;
          setPlanValidation(validation);
        })
        .catch(() => {
          if (planValidationRequestIdRef.current !== requestId) return;
          setPlanValidation(null);
        })
        .finally(() => {
          if (planValidationRequestIdRef.current !== requestId) return;
          setPlanValidationLoading(false);
        });
    }, 700);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  // Fase 6B, seccion 2/10/11/23 del pedido: la Guia SIEMPRE consume
  // compute_load_steps/compute_unload_steps a traves de /report/steps (el
  // MISMO endpoint que ya usaba el PDF) -nunca un chunking propio del
  // frontend. Se recalcula (y resetea a Step 1) cada vez que cambia la
  // geometria activa (`result`: Optimize/Re-optimize/Optimize Remaining/
  // manual move/rotate/turn/insert/remove/alternativa elegida -todos
  // terminan en un setResult nuevo) o la configuracion de la Guia
  // (direction/step mode/pieces per step). Corre de inmediato al abrir un
  // plan restaurado (seccion 23: sin necesidad de tocar Optimize) porque
  // `result` ya viene seteado desde el primer render.
  useEffect(() => {
    if (!result) {
      setGuideStepsResult(null);
      setGuideStepIndex(0);
      return;
    }
    let cancelled = false;
    setGuideStepsLoading(true);
    setGuideStepsError("");
    getReportSteps(guideConfigDirection, {
      stepMode: guideStepMode,
      piecesPerStep: guideStepMode === "manual" ? guidePiecesPerStep : undefined,
    })
      .then((data) => {
        if (cancelled) return;
        setGuideStepsResult(data);
        setGuideStepIndex(0);
      })
      .catch((e) => {
        if (cancelled) return;
        setGuideStepsResult(null);
        setGuideStepsError(extractErrorMessage(e, "Error al calcular los pasos de la guia"));
      })
      .finally(() => {
        if (!cancelled) setGuideStepsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [result, guideConfigDirection, guideStepMode, guidePiecesPerStep]);

  async function handleRenamePlan(name: string) {
    if (!planId) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === planName) return;
    try {
      await renamePlan(planId, trimmed);
      setPlanName(trimmed);
    } catch (e: any) {
      setError(extractErrorMessage(e, "No se pudo renombrar el plan"));
    }
  }

  async function handleFileSelected(file: File) {
    setError("");
    try {
      const parsed = await importExcel(file);
      setItems(parsed);
    } catch (e: any) {
      setError(extractErrorMessage(e, "Error al importar el Excel"));
    }
  }

  async function runOptimize() {
    setLoading(true);
    setError("");
    setSelectedPieceId(null);
    setInsertingItem(null);
    // Fase 6B.1, seccion 1/9/22 del pedido: la PRIMERA vista de un plan
    // recien optimizado siempre debe ser el Workspace normal completo,
    // nunca la Guia -Show Steps se apaga a proposito en cada Optimize/
    // Re-optimize (Optimize Remaining y seleccionar una alternativa lo
    // hacen igual, ver mas abajo).
    setShowSteps(false);
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
      const planHandlingRules = {
        default_stackable: defaultStackable,
        default_orientation_policy: orientationPolicy,
        default_allow_tilt: defaultAllowTilt,
        default_max_tilt_angle: defaultMaxTiltAngle,
      };
      const packOptions = {
        optimizationMode,
        enableCentralAisle,
        aisleWidthMm,
        clearanceMm,
        weightBalanceMode,
        loadingAnchor,
        planHandlingRules,
      };
      // Fase 5D, seccion 12 del pedido: un Load Plan se vuelve persistente
      // en el primer Optimize exitoso despues del wizard -es lo primero que
      // realmente toca el backend tras hacer clic en Create Load Plan. Un
      // plan ya persistido (planId existente) o la sesion legacy (nunca
      // vino del wizard) siguen usando packContainer sin crear una fila
      // nueva -Re-optimize/Optimize normal sobre un plan que ya existe.
      let response: { best: PackingResult; alternatives: AlternativeSolution[] };
      if (hasLocked) {
        response = await optimizeRemaining({ optimizationMode, weightBalanceMode, loadingAnchor, planHandlingRules });
      } else if (!planId && cameFromWizard) {
        const created = await createPlan(items, loadSpace, packOptions);
        setPlanId(created.planId);
        setPlanName(created.name);
        response = { best: created.best, alternatives: created.alternatives };
      } else {
        response = await packContainer(items, loadSpace, packOptions);
      }
      setResult(response.best);
      setAlternatives(response.alternatives);
      setHasManualEdits(false);
      // Al optimizar, colorear por System de una vez para poder distinguir
      // los sistemas de inmediato (el usuario puede cambiarlo despues).
      setColorBy("system");
    } catch (e: any) {
      setError(extractErrorMessage(e, "Error al calcular el cubicaje"));
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
    setShowSteps(false);
    try {
      const planHandlingRules = {
        default_stackable: defaultStackable,
        default_orientation_policy: orientationPolicy,
        default_allow_tilt: defaultAllowTilt,
        default_max_tilt_angle: defaultMaxTiltAngle,
      };
      const response = await optimizeRemaining({ optimizationMode, weightBalanceMode, loadingAnchor, planHandlingRules });
      setResult(response.best);
      setAlternatives(response.alternatives);
      setHasManualEdits(false);
    } catch (e: any) {
      setError(extractErrorMessage(e, "Error al reoptimizar las piezas restantes"));
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
      setError(extractErrorMessage(e, "No se pudo cambiar el estado de bloqueo"));
    }
  }

  function handleSelectAlternative(alt: AlternativeSolution) {
    setResult(alt.result);
    setSelectedPieceId(null);
    setInsertingItem(null);
    setShowSteps(false);
  }

  async function handleExportExcel() {
    setError("");
    try {
      // Fase 6C, seccion 13/14/26 del pedido: Excel ahora usa el mismo gate
      // de errores duros que los 3 PDF (_ensure_exportable en el backend) -
      // este preflight explicito evita el mismo bug de 6B.1 (exportExcel usa
      // responseType:"blob", asi que un 422 real llegaria enmascarado como
      // Blob en el catch) y le muestra al usuario la razon real ("Plan is
      // NOT READY", que categorias fallaron) en vez de un generico "Export
      // failed".
      const validation = await validateReport();
      if (!validation.valid && blockExportsOnErrors) {
        setError(
          `Export blocked: this plan contains ${validation.error_count} blocking validation error(s). ` +
            'Turn off "Block exports when validation errors exist" in Settings to export anyway, or open Plan ' +
            "Validation to fix them."
        );
        return;
      }
      const blob = await exportExcel(!validation.valid);
      downloadBlob(blob, "cubox-cubicaje.xlsx");
    } catch (e: any) {
      setError(await extractErrorMessageFromBlobResponse(e, "Error al exportar a Excel"));
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
    // Fase 6B.1, seccion 14/21 del pedido: isCapturing/captureSteps/
    // captureStepIndex/captureDirection son estado TOTALMENTE SEPARADO del
    // interactivo (showSteps/guideStepIndex/guideConfigDirection) -nunca se
    // "toma prestado" ni hay que restaurar nada al terminar, porque el
    // estado del usuario jamas se toco. Ver sceneGuideSteps/
    // sceneGuideStepIndex/sceneGuideDirection mas abajo (Scene3D siempre usa
    // el de captura mientras isCapturing es true, sin importar showSteps).
    setIsCapturing(true);
    try {
      // Fase 6B.1, seccion 11/17 del pedido: chequeo previo (fail-fast).
      // Root cause real encontrado en produccion: sin esto, un plan
      // fisicamente invalido (ej. una pieza sin soporte) recien se detecta
      // DESPUES de capturar decenas de screenshots (minutos de espera),
      // y como los 3 endpoints de PDF usan responseType:"blob", el 422 que
      // devuelve el backend llega como Blob -extractErrorMessage no podia
      // leerlo y siempre mostraba el generico "Error al generar el
      // reporte", sin decir NUNCA cual era el problema real. Reusa
      // /report/validate (ya existia en el backend, nunca se habia llamado
      // desde el frontend) -mismo chequeo que corre validate_for_export
      // internamente en cada endpoint de PDF, solo que ANTES de gastar
      // tiempo capturando.
      const validation = await validateReport();
      if (!validation.valid && blockExportsOnErrors) {
        setError(
          `Export blocked: this plan contains ${validation.error_count} blocking validation error(s). ` +
            'Turn off "Block exports when validation errors exist" in Settings to export anyway, or open Plan ' +
            "Validation to fix them."
        );
        return;
      }
      const allowExportWithErrors = !validation.valid;

      if (settings.reportType === "container") {
        // Encuadre Fit Container completo (nunca "lo cargado hasta este
        // paso"): captureSteps/captureStepIndex/captureDirection quedan en
        // sus defaults (undefined/null/undefined) a proposito.
        const overviewImagePngBase64 = settings.includeOverviewImage ? await captureRef.current?.(false) : undefined;
        const blob = await exportContainerReportPdf({
          meta,
          sortBy: settings.sortBy,
          includeOverviewImage: Boolean(overviewImagePngBase64),
          overviewImagePngBase64: overviewImagePngBase64?.split(",")[1],
          allowExportWithErrors,
        });
        downloadBlob(blob, "cubox-container-report.pdf");
        return;
      }

      const direction: ReportDirection = settings.reportType === "loading_guide" ? "load" : "unload";
      const { steps } = await getReportSteps(direction, {
        stepMode: settings.stepMode,
        piecesPerStep: settings.stepMode === "manual" ? settings.piecesPerStep : undefined,
      });
      if (steps.length === 0) {
        setError("No hay pasos para generar la guia (el contenedor esta vacio).");
        return;
      }
      setCaptureSteps(steps);
      setCaptureDirection(direction);

      const images: string[] = [];
      for (let i = 0; i < steps.length; i++) {
        setCaptureStepIndex(i);
        // Un frame para que React/R3F apliquen el nuevo paso antes de
        // capturar (captureRef.current tambien espera 2 frames mas por su
        // cuenta despues de fijar la camara). Fase 6B.3: que camara usar
        // (computeGuideCameraView) se pasa como ARGUMENTO explicito -true-
        // en vez de que Scene3D lo infiera de guideSteps/guideStepIndex via
        // props/refs. Motivo, bug real encontrado inspeccionando el PDF:
        // React Three Fiber corre su reconciler en un ciclo propio, no
        // sincronizado 1:1 con el commit de React normal, asi que leer esos
        // valores desde dentro de la closure de captura llegaba
        // sistematicamente un paso atrasado (instrumentado: la llamada N
        // siempre traia el valor de la iteracion N-1) -la camara "saltaba"
        // entre pasos en vez de quedar consistente (seccion 14 del pedido).
        // El loop mismo siempre sabe, sin pasar por React, que esto es una
        // captura de guia -pasarlo directo elimina la dependencia de timing.
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        const png = await captureRef.current?.(true);
        if (png) images.push(png.split(",")[1]);
      }

      if (direction === "unload" && settings.unloadingGuideScope === "by_group") {
        const { blob, isZip } = await exportUnloadingGuidePdfByGroup({
          meta,
          stepMode: settings.stepMode,
          piecesPerStep: settings.stepMode === "manual" ? settings.piecesPerStep : undefined,
          stepImagesPngBase64: images,
          groups: settings.selectedGroups,
          allowExportWithErrors,
        });
        const filename = isZip ? "Cubox - Unloading Guides.zip" : `Cubox - Unloading - ${settings.selectedGroups[0]}.pdf`;
        downloadBlob(blob, filename);
        return;
      }

      const exportFn = direction === "load" ? exportLoadingGuidePdf : exportUnloadingGuidePdf;
      const blob = await exportFn({
        meta,
        stepMode: settings.stepMode,
        piecesPerStep: settings.stepMode === "manual" ? settings.piecesPerStep : undefined,
        stepImagesPngBase64: images,
        allowExportWithErrors,
      });
      downloadBlob(blob, direction === "load" ? "cubox-loading-guide.pdf" : "cubox-unloading-guide.pdf");
    } catch (e: any) {
      // Fase 6B.1, seccion 11 del pedido: los 3 endpoints de PDF usan
      // responseType:"blob" -el body de un error tambien llega como Blob,
      // no como JSON ya parseado. Sin este extractor async, cualquier
      // rechazo tardio (ej. una carrera rarisima con /report/validate)
      // volvia a caer en el generico de siempre.
      setError(await extractErrorMessageFromBlobResponse(e, "Error al generar el reporte"));
    } finally {
      setIsCapturing(false);
      setCaptureSteps(undefined);
      setCaptureStepIndex(null);
      setCaptureDirection(undefined);
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
      return { ok: false, reason: extractErrorMessage(e, "No se pudo rotar la pieza") };
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
      return { ok: false, reason: extractErrorMessage(e, "No se pudo girar la pieza") };
    }
  }

  async function handleSetTilt(angle: number): Promise<{ ok: boolean; reason: string }> {
    if (!selectedPieceId) return { ok: false, reason: "" };
    try {
      const updated = await setTilt(selectedPieceId, angle);
      setResult(updated);
      setHasManualEdits(true);
      setAlternatives([]);
      return { ok: true, reason: "" };
    } catch (e: any) {
      return { ok: false, reason: extractErrorMessage(e, "No se pudo cambiar el Tilt") };
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

  // Fase 6B.1, seccion 21 del pedido: resolucion EXPLICITA de que le llega a
  // Scene3D -captura de reporte siempre gana (isCapturing), despues la Guia
  // interactiva (solo si showSteps), y si ninguna aplica, undefined/null
  // (Workspace normal, todas las piezas visibles). Ningun estado "se activa
  // solo": normalView/guideView/reportCapture quedan mutuamente exclusivos
  // por construccion, calculados aca en vez de guardados como estado aparte.
  const sceneGuideSteps = isCapturing ? captureSteps : showSteps ? guideStepsResult?.steps : undefined;
  const sceneGuideStepIndex = isCapturing ? captureStepIndex : showSteps ? guideStepIndex : null;
  const sceneGuideDirection = isCapturing ? captureDirection : showSteps ? guideConfigDirection : undefined;

  // Fase 6B.1, seccion 5-7 del pedido: badges de secuencia, INDEPENDIENTES
  // de showSteps -usa load_sequence/unload_sequence tal cual vienen del
  // backend, nunca deriva un segundo orden aca. Fase 6B.2, seccion 16 del
  // pedido: pero SI se apagan a la fuerza durante isCapturing -el capture
  // debe ser deterministico e independiente de que el usuario tenga
  // encendido el toggle interactivo de Sequence Numbers en ese momento; no
  // son parte del diseno actual del PDF.
  const sceneShowSequence = isCapturing ? false : showSequenceNumbers;
  const sceneActiveSequence =
    !isCapturing && showSequenceNumbers && result
      ? sequenceNumberDirection === "load"
        ? result.load_sequence
        : result.unload_sequence
      : [];

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
        {planId && <PlanNameBar name={planName} status={saveStatus} onRename={handleRenamePlan} />}
        <ImportPanel
          containers={containers}
          items={items}
          importedViaWizard={cameFromWizard}
          selectedContainerId={selectedContainerId}
          customLoadSpace={customLoadSpace}
          optimizationMode={optimizationMode}
          enableCentralAisle={enableCentralAisle}
          aisleWidthMm={aisleWidthMm}
          clearanceMm={clearanceMm}
          weightBalanceMode={weightBalanceMode}
          loadingAnchor={loadingAnchor}
          defaultStackable={defaultStackable}
          orientationPolicy={orientationPolicy}
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
          onDefaultStackableChange={setDefaultStackable}
          onOrientationPolicyChange={setOrientationPolicy}
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
            <MetricsPanel metrics={result.metrics} activeItemType={activeItemType} />
          </>
        )}
      </aside>

      <main className="viewport">
        <Scene3D
          result={result}
          selectedPieceId={selectedPieceId}
          colorBy={colorBy}
          insertingItem={insertingItem}
          showLabels={showLabels}
          showCenterOfMass={showCenterOfMass}
          showSequence={sceneShowSequence}
          activeSequence={sceneActiveSequence}
          guideSteps={sceneGuideSteps}
          guideStepIndex={sceneGuideStepIndex}
          guideDirection={sceneGuideDirection}
          captureRef={captureRef}
          onSelectPiece={setSelectedPieceId}
          onCommitMove={handleCommitMove}
          onCommitInsert={handleCommitInsert}
        />
      </main>

      <aside className="sidebar right">
        {selectedPiece && result && (
          <PieceInspector
            piece={selectedPiece}
            result={result}
            onRotate={handleRotate}
            onTurn={handleTurn}
            onSetTilt={handleSetTilt}
            onRemove={handleRemove}
            onToggleLock={handleToggleLock}
          />
        )}
        {result && alternatives.length > 1 && (
          <AlternativesPanel alternatives={alternatives} onSelect={handleSelectAlternative} />
        )}
        {/* Fase 6C, seccion 20/21 del pedido: resumen a nivel de PLAN, arriba
            del Operational Guide -no reemplaza SequencePanel/UnloadedPanel
            (esos siguen siendo el detalle), solo agrega el status
            READY/READY WITH WARNINGS/NOT READY + categorias. */}
        {result && (
          <PlanValidationPanel
            validation={planValidation}
            loading={planValidationLoading}
            metrics={result.metrics}
            roadWeight={result.road_weight}
          />
        )}
        {result && (
          <SequencePanel
            result={result}
            optimizationMode={optimizationMode}
            showSteps={showSteps}
            onShowStepsChange={setShowSteps}
            direction={guideConfigDirection}
            onDirectionChange={setGuideConfigDirection}
            stepMode={guideStepMode}
            onStepModeChange={setGuideStepMode}
            piecesPerStep={guidePiecesPerStep}
            onPiecesPerStepChange={setGuidePiecesPerStep}
            stepsResult={guideStepsResult}
            stepsLoading={guideStepsLoading}
            stepsError={guideStepsError}
            stepIndex={guideStepIndex}
            onStepChange={setGuideStepIndex}
            showSequenceNumbers={showSequenceNumbers}
            onShowSequenceNumbersChange={setShowSequenceNumbers}
            sequenceNumberDirection={sequenceNumberDirection}
            onSequenceNumberDirectionChange={setSequenceNumberDirection}
          />
        )}
        {result && (
          <UnloadedPanel
            items={result.unloaded}
            insertingItemId={insertingItem?.id ?? null}
            activeItemType={activeItemType}
            onStartPlacing={handleStartPlacing}
            onCancelPlacing={() => setInsertingItem(null)}
          />
        )}
      </aside>

      {settingsOpen && (
        <SettingsModal
          theme={theme}
          onThemeChange={setTheme}
          blockExportsOnErrors={blockExportsOnErrors}
          onBlockExportsOnErrorsChange={setBlockExportsOnErrors}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {reportSettingsOpen && (
        <ReportSettingsModal
          onClose={() => setReportSettingsOpen(false)}
          onGenerate={handleGenerateReport}
          generating={exportingPdf}
          availableGroups={Array.from(new Set((result?.placed ?? []).map((p) => p.group).filter((g) => g !== ""))).sort()}
        />
      )}
    </div>
  );
}

export default App;
