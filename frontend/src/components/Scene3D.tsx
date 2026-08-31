import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Canvas, useThree } from "@react-three/fiber";
import { Billboard, Edges, Grid, OrbitControls, Text } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { PieceMesh, type PieceVisualState } from "./PieceMesh";
import { DragBox } from "./DragBox";
import { useDragEngine } from "../hooks/useDragEngine";
import { findRestingZ } from "../geometry/geometry";
import { SCENE_SCALE } from "../config";
import type { ColorByMode, PackingResult, ReservedZone, UnloadedItem } from "../types";

function ContainerFrame({ length, width, height }: { length: number; width: number; height: number }) {
  const L = length * SCENE_SCALE;
  const W = width * SCENE_SCALE;
  const H = height * SCENE_SCALE;
  return (
    <mesh position={[0, H / 2, 0]}>
      <boxGeometry args={[L, H, W]} />
      <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      <Edges color="#7a7a7a" />
    </mesh>
  );
}

/** Marca fija el extremo x=0 (la puerta): franja en el piso + etiqueta
 * flotante, siempre visible (no depende de ningun toggle) para que se sepa
 * de un vistazo hacia donde queda el frente/puerta y hacia donde el fondo. El
 * cubicaje automatico siempre llena desde el fondo (x=length) hacia aca. */
function DoorMarker({ length, width }: { length: number; width: number }) {
  const doorX = (-length / 2) * SCENE_SCALE;
  const W = width * SCENE_SCALE;
  return (
    <>
      <mesh position={[doorX + 0.03, 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[0.06, W]} />
        <meshBasicMaterial color="#ffb020" transparent opacity={0.8} />
      </mesh>
      <Billboard position={[doorX - 0.25, 0.4, 0]}>
        <Text fontSize={0.22} color="#ffb020" anchorX="center" anchorY="bottom" outlineWidth={0.008} outlineColor="#000000">
          {"PUERTA\n◂ frente"}
        </Text>
      </Billboard>
    </>
  );
}

/** Marca el pasillo central (Central Aisle) cuando esta activo: franja
 * amarilla en el piso + un volumen sutil y transparente en toda la altura
 * (no un objeto solido) + bordes, para que sea evidente que esta
 * geometricamente centrado. Usa la zona que ya calculo y devolvio el
 * backend (result.reserved_zones) -misma fuente de verdad que el centrado
 * real, en vez de que el frontend recalcule la formula por su cuenta. */
function AisleMarker({
  zone,
  container,
}: {
  zone: ReservedZone;
  container: { length: number; width: number; height: number };
}) {
  const L = container.length * SCENE_SCALE;
  const H = container.height * SCENE_SCALE;
  const aisleWidthScene = zone.width * SCENE_SCALE;
  const centerZ = (zone.y + zone.width / 2 - container.width / 2) * SCENE_SCALE;

  return (
    <group>
      <mesh position={[0, 0.0015, centerZ]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[L, aisleWidthScene]} />
        <meshBasicMaterial color="#ffd54a" transparent opacity={0.2} depthWrite={false} />
      </mesh>
      <mesh position={[0, H / 2, centerZ]}>
        <boxGeometry args={[L, H, aisleWidthScene]} />
        <meshBasicMaterial color="#ffd54a" transparent opacity={0.05} depthWrite={false} />
        <Edges color="#ffd54a" />
      </mesh>
      <Billboard position={[0, H + 0.15, centerZ]} renderOrder={999}>
        <Text fontSize={0.16} color="#ffd54a" anchorX="center" anchorY="bottom" outlineWidth={0.006} outlineColor="#000000">
          AISLE
        </Text>
      </Billboard>
    </group>
  );
}

/** Marcador simple (esfera) en el centro de masa ponderado por peso de todas
 * las piezas cargadas, para detectar de un vistazo si la carga tiende a
 * volcarse hacia un lado. */
function CenterOfMassMarker({
  x,
  y,
  z,
  container,
}: {
  x: number;
  y: number;
  z: number;
  container: { length: number; width: number };
}) {
  const threeX = (x - container.length / 2) * SCENE_SCALE;
  const threeY = z * SCENE_SCALE;
  const threeZ = (y - container.width / 2) * SCENE_SCALE;

  // troika-three-text (lo que usa drei <Text> por dentro) regenera su propio
  // material internamente, asi que el atajo JSX "material-depthTest" no se
  // sostiene tras el primer sync de glifos. Pasando una instancia de
  // material propia via la prop `material`, troika la parcha en vez de
  // reemplazarla, y el depthTest=false sí queda aplicado.
  const labelMaterial = useMemo(() => new THREE.MeshBasicMaterial({ depthTest: false, transparent: true }), []);

  // depthTest=false + renderOrder alto: el centro de masa normalmente queda
  // enterrado dentro de la pila de piezas solidas y seria invisible desde
  // cualquier angulo externo; se dibuja "a traves" de las piezas (como un
  // marcador de rayos X) para que siempre se vea, sea cual sea la posicion
  // de la camara.
  return (
    <Billboard position={[threeX, threeY, threeZ]} renderOrder={999}>
      <mesh renderOrder={999}>
        <sphereGeometry args={[0.09, 16, 16]} />
        <meshBasicMaterial color="#ff3d9a" depthTest={false} transparent opacity={0.95} />
      </mesh>
      <Text
        position={[0, 0.18, 0]}
        fontSize={0.14}
        color="#ff3d9a"
        anchorX="center"
        anchorY="bottom"
        outlineWidth={0.006}
        outlineColor="#000000"
        renderOrder={999}
        material={labelMaterial}
      >
        CG
      </Text>
    </Billboard>
  );
}

/** Espera 2 frames de animacion antes de capturar: r3f agenda su render en su
 * propio loop, asi que fijar la camara/estado de piezas y llamar a
 * toDataURL() en el mismo tick sincronico puede ganarle la carrera al render
 * real (capturar el frame anterior, o uno en blanco). Un solo rAF suele
 * alcanzar, pero dos es un margen barato para algo que corre una vez por
 * captura, no por frame. */
function waitTwoAnimationFrames(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

/** Calcula una posicion y distancia de camara que encuadran el contenedor
 * completo con un margen visual, a partir de sus dimensiones reales -nunca
 * coordenadas fijas- para que Fit View funcione igual sin importar el
 * contenedor seleccionado. */
function computeFitView(container: { length: number; width: number; height: number }) {
  const L = container.length * SCENE_SCALE;
  const W = container.width * SCENE_SCALE;
  const H = container.height * SCENE_SCALE;
  const center = new THREE.Vector3(0, H / 2, 0);
  const distance = Math.max(L, W, H) * 1.3;
  const position = new THREE.Vector3(distance, distance * 0.8, distance);
  return { center, position };
}

interface PlacedLike {
  x: number;
  y: number;
  z: number;
  dx: number;
  dy: number;
  dz: number;
}

/** Encuadre para snapshots de guia (Loading/Unloading): en vez de siempre
 * mostrar el contenedor completo (mucho vacio si recien se empezo a cargar),
 * encuadra la caja envolvente de "lo cargado hasta este paso" con un margen
 * de contexto -asi las piezas se distinguen aunque el contenedor este vacio
 * en su mayoria, pero se sigue viendo donde caen dentro del contenedor. Si
 * todavia no hay nada cargado, cae de vuelta a Fit Container completo. */
function computeFitLoadedArea(
  placedSoFar: PlacedLike[],
  container: { length: number; width: number; height: number }
) {
  if (placedSoFar.length === 0) {
    return computeFitView(container);
  }

  const minX = Math.max(0, Math.min(...placedSoFar.map((p) => p.x)));
  const maxX = Math.min(container.length, Math.max(...placedSoFar.map((p) => p.x + p.dx)));
  const minY = Math.max(0, Math.min(...placedSoFar.map((p) => p.y)));
  const maxY = Math.min(container.width, Math.max(...placedSoFar.map((p) => p.y + p.dy)));
  const minZ = Math.max(0, Math.min(...placedSoFar.map((p) => p.z)));
  const maxZ = Math.min(container.height, Math.max(...placedSoFar.map((p) => p.z + p.dz)));

  // Margen de contexto (no un zoom ciego a las piezas): un poco del
  // contenedor alrededor para poder ubicar el area dentro de el.
  const marginMm = Math.max(container.length, container.width) * 0.15;
  const boxMinX = Math.max(0, minX - marginMm);
  const boxMaxX = Math.min(container.length, maxX + marginMm);
  const boxMinY = Math.max(0, minY - marginMm);
  const boxMaxY = Math.min(container.width, maxY + marginMm);

  const center = new THREE.Vector3(
    ((boxMinX + boxMaxX) / 2 - container.length / 2) * SCENE_SCALE,
    ((minZ + maxZ) / 2) * SCENE_SCALE,
    ((boxMinY + boxMaxY) / 2 - container.width / 2) * SCENE_SCALE
  );

  const spanX = (boxMaxX - boxMinX) * SCENE_SCALE;
  const spanY = (boxMaxY - boxMinY) * SCENE_SCALE;
  const spanZ = Math.max((maxZ - minZ) * SCENE_SCALE, container.height * SCENE_SCALE * 0.3);
  const distance = Math.max(spanX, spanY, spanZ, 0.5) * 1.5;
  const position = new THREE.Vector3(center.x + distance, center.y + distance * 0.8, center.z + distance);
  return { center, position };
}

interface ContentsProps {
  result: PackingResult;
  selectedPieceId: string | null;
  colorBy: ColorByMode;
  insertingItem: UnloadedItem | null;
  showSequence: boolean;
  showLabels: boolean;
  showCenterOfMass: boolean;
  activeSequence: string[];
  stepIndex: number | null;
  /** Pasos de una guia (Loading/Unloading, uno por PDF) para capturar un
   * snapshot por paso: cuando estan presentes, reemplazan por completo el
   * calculo interactivo de visualState basado en `stepIndex`/`activeSequence`
   * -cada elemento es la lista de piece ids que corresponden a ESE paso. */
  guideSteps?: string[][];
  guideStepIndex?: number | null;
  fitViewRef: React.MutableRefObject<(() => void) | undefined>;
  captureRef?: React.MutableRefObject<(() => Promise<string>) | undefined>;
  onSelectPiece: (id: string | null) => void;
  onCommitMove: (pieceId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
  onCommitInsert: (unloadedId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
}

function SceneContents({
  result,
  selectedPieceId,
  colorBy,
  insertingItem,
  showSequence,
  showLabels,
  showCenterOfMass,
  activeSequence,
  stepIndex,
  guideSteps,
  guideStepIndex,
  fitViewRef,
  captureRef,
  onSelectPiece,
  onCommitMove,
  onCommitInsert,
}: ContentsProps) {
  const { container } = result;
  const { drag, beginDrag } = useDragEngine({ result, onCommitMove, onCommitInsert });
  const { camera, gl } = useThree();
  const controlsRef = useRef<OrbitControlsImpl | null>(null);

  // Salvaguarda contra un pointerup que se pierde (visto con eventos
  // sinteticos/headless y en teoria posible si el navegador pierde el pointer
  // capture, p.ej. al abrirse el menu contextual del boton derecho): si eso
  // pasa, OrbitControls se queda escuchando pointermove para siempre y la
  // camara sigue orbitando/paneando con el mouse quieto, sin ningun boton
  // presionado. Aqui se detecta la transicion de "boton presionado" a "sin
  // botones" en cualquier pointermove y se dispara un pointerup sintetico
  // -inofensivo si OrbitControls ya habia terminado el arrastre correctamente,
  // pero fuerza el corte cuando el evento real nunca llego.
  useEffect(() => {
    const canvas = gl.domElement;
    let buttonWasDown = false;
    function handlePointerMove(event: PointerEvent) {
      if (event.buttons !== 0) {
        buttonWasDown = true;
        return;
      }
      if (buttonWasDown) {
        buttonWasDown = false;
        canvas.dispatchEvent(
          new PointerEvent("pointerup", { pointerId: event.pointerId, bubbles: true, cancelable: true })
        );
      }
    }
    window.addEventListener("pointermove", handlePointerMove);
    return () => window.removeEventListener("pointermove", handlePointerMove);
  }, [gl]);

  useEffect(() => {
    fitViewRef.current = () => {
      const { center, position } = computeFitView(container);
      camera.position.copy(position);
      camera.lookAt(center);
      if (controlsRef.current) {
        controlsRef.current.target.copy(center);
        controlsRef.current.update();
      }
    };
  }, [container, camera, fitViewRef]);

  // Snapshot reutilizable para los 3 reportes PDF: el overview del Container
  // Load Report siempre encuadra el contenedor completo (Fit Container). Los
  // pasos de Loading/Unloading Guide en cambio encuadran "lo cargado hasta
  // este paso" (Fit Loaded Area) para que las piezas se distingan aunque el
  // contenedor este vacio en su mayoria -se recalcula en cada render porque
  // depende de guideStepIndex/guideSteps, que cambian en cada paso capturado.
  useEffect(() => {
    if (!captureRef) return;
    captureRef.current = async () => {
      const isGuideCapture = guideSteps && guideStepIndex !== null && guideStepIndex !== undefined;
      const { center, position } = isGuideCapture
        ? computeFitLoadedArea(
            result.placed.filter((p) => new Set(guideSteps.slice(0, guideStepIndex + 1).flat()).has(p.id)),
            container
          )
        : computeFitView(container);
      camera.position.copy(position);
      camera.lookAt(center);
      if (controlsRef.current) {
        controlsRef.current.target.copy(center);
        controlsRef.current.update();
      }
      await waitTwoAnimationFrames();
      return gl.domElement.toDataURL("image/png");
    };
  }, [container, camera, gl, captureRef, guideSteps, guideStepIndex, result]);

  const sequenceOrder = new Map(activeSequence.map((id, i) => [id, i]));

  // Mapa piece id -> "past"/"current"/"future" para un paso de guia (PDF):
  // pasado = union de los pasos anteriores, actual = este paso, futuro =
  // union de los pasos siguientes. Cuando no hay guia activa, cada PieceMesh
  // calcula su propio visualState a partir de stepIndex/sequenceOrder (ver
  // mas abajo).
  const guideStateById =
    guideSteps && guideStepIndex !== null && guideStepIndex !== undefined
      ? (() => {
          const map = new Map<string, PieceVisualState>();
          guideSteps.forEach((step, i) => {
            const state: PieceVisualState = i < guideStepIndex ? "past" : i === guideStepIndex ? "current" : "future";
            step.forEach((pieceId) => map.set(pieceId, state));
          });
          return map;
        })()
      : null;

  const insertDims = insertingItem
    ? { dx: insertingItem.width, dy: insertingItem.thickness, dz: insertingItem.height }
    : null;

  const insertBeingDragged = drag && drag.mode === "insert" && insertingItem && drag.pieceId === insertingItem.id;

  let ghostPos: { x: number; y: number; z: number; valid: boolean } | null = null;
  if (insertingItem && insertDims) {
    if (insertBeingDragged && drag) {
      ghostPos = drag.candidate;
    } else {
      const others = result.placed.map((p) => ({
        id: p.id,
        x: p.x,
        y: p.y,
        z: p.z,
        dx: p.dx,
        dy: p.dy,
        dz: p.dz,
        stackable: p.stackable,
      }));
      // Aparece en el centro del piso del contenedor: es donde la camara ya
      // apunta por defecto, asi que normalmente queda visible y facil de
      // agarrar (en vez de quedar enterrado en la pila que arranca en 0,0,0
      // o recortado contra una pared del extremo opuesto).
      const spawnX = Math.max(0, container.length / 2 - insertDims.dx / 2);
      const spawnY = Math.max(0, container.width / 2 - insertDims.dy / 2);
      ghostPos = findRestingZ(
        spawnX,
        spawnY,
        insertDims.dx,
        insertDims.dy,
        insertDims.dz,
        insertingItem.stackable,
        insertingItem.id,
        others,
        container
      );
    }
  }

  return (
    <>
      <ambientLight intensity={0.7} />
      <directionalLight position={[10, 15, 10]} intensity={0.8} />
      <directionalLight position={[-10, 8, -10]} intensity={0.3} />
      <ContainerFrame length={container.length} width={container.width} height={container.height} />
      <DoorMarker length={container.length} width={container.width} />
      {result.reserved_zones.map((zone) => (
        <AisleMarker key={zone.label} zone={zone} container={container} />
      ))}
      {showCenterOfMass && (
        <CenterOfMassMarker
          x={result.metrics.center_of_mass_x}
          y={result.metrics.center_of_mass_y}
          z={result.metrics.center_of_mass_z}
          container={container}
        />
      )}
      <Grid
        args={[container.length * SCENE_SCALE, container.width * SCENE_SCALE]}
        position={[0, 0, 0]}
        cellColor="#3a3a3a"
        sectionColor="#555"
        fadeDistance={30}
      />
      {result.placed.map((piece) => {
        const isDraggingThis = drag && drag.mode === "move" && drag.pieceId === piece.id;
        const order = sequenceOrder.get(piece.id);
        let visualState: PieceVisualState;
        if (guideStateById) {
          // Captura para una guia PDF: el estado viene de los pasos de la
          // guia, no del slider interactivo de secuencia.
          visualState = guideStateById.get(piece.id) ?? "future";
        } else {
          // "past"/"current"/"future" respecto del paso del slider de
          // secuencia (stepIndex null = sin slider activo -> todo "past").
          visualState =
            stepIndex === null || order === undefined
              ? "past"
              : order < stepIndex
                ? "past"
                : order === stepIndex
                  ? "current"
                  : "future";
        }
        return (
          <PieceMesh
            key={piece.id}
            piece={piece}
            container={container}
            selected={piece.id === selectedPieceId}
            colorBy={colorBy}
            dragOverride={isDraggingThis ? drag.candidate : null}
            sequenceLabel={showSequence && order !== undefined ? order + 1 : null}
            showLabels={showLabels}
            visualState={visualState}
            guideMode={Boolean(guideStateById)}
            onSelect={onSelectPiece}
            onBeginDrag={beginDrag}
          />
        );
      })}
      {insertingItem && insertDims && ghostPos && (
        <DragBox
          x={ghostPos.x}
          y={ghostPos.y}
          z={ghostPos.z}
          dx={insertDims.dx}
          dy={insertDims.dy}
          dz={insertDims.dz}
          container={container}
          color={ghostPos.valid ? "#3ddc84" : "#ff4d4f"}
          edgeColor="#ffffff"
          opacity={0.6}
          onPointerDown={(e) => {
            e.stopPropagation();
            beginDrag(
              "insert",
              insertingItem.id,
              ghostPos!.x,
              ghostPos!.y,
              ghostPos!.z,
              insertDims.dx,
              insertDims.dy,
              insertDims.dz,
              insertingItem.stackable,
              e.clientX,
              e.clientY
            );
          }}
        />
      )}
      {/* Navegacion estilo CAD/EasyCargo: boton izquierdo = Orbit libre (todos
          los angulos, no un plano 2D) sobre espacio vacio; sobre una pieza, el
          propio PieceMesh captura el pointerdown primero y arranca su
          arrastre en vez de dejarlo llegar a OrbitControls (ver nota de
          `enabled={!drag}` abajo). Boton central = Pan (en vez del Dolly por
          defecto de three.js). Boton derecho = Pan tambien (default de
          three.js, se deja explicito). Rueda = Zoom hacia el cursor. */}
      <OrbitControls
        ref={controlsRef}
        makeDefault
        enabled={!drag}
        enablePan
        enableZoom
        enableRotate
        zoomToCursor
        mouseButtons={{ LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN }}
      />
    </>
  );
}

interface Props {
  result: PackingResult | null;
  selectedPieceId: string | null;
  colorBy: ColorByMode;
  insertingItem: UnloadedItem | null;
  showSequence: boolean;
  showLabels: boolean;
  showCenterOfMass: boolean;
  activeSequence: string[];
  stepIndex: number | null;
  /** Pasos de una guia (Loading/Unloading) para renderizar un paso especifico
   * durante la captura de snapshots -ver guideStateById en SceneContents. */
  guideSteps?: string[][];
  guideStepIndex?: number | null;
  /** Ref opcional que el padre (App.tsx/Report Settings) puede pasar para
   * disparar una captura PNG limpia del contenedor bajo demanda -Fit View +
   * espera de 2 frames + toDataURL-, reutilizada por los 3 reportes PDF. */
  captureRef?: React.MutableRefObject<(() => Promise<string>) | undefined>;
  onSelectPiece: (id: string | null) => void;
  onCommitMove: (pieceId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
  onCommitInsert: (unloadedId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
}

export function Scene3D({
  result,
  selectedPieceId,
  colorBy,
  insertingItem,
  showSequence,
  showLabels,
  showCenterOfMass,
  activeSequence,
  stepIndex,
  guideSteps,
  guideStepIndex,
  captureRef,
  onSelectPiece,
  onCommitMove,
  onCommitInsert,
}: Props) {
  const cameraDistance = result
    ? Math.max(result.container.length, result.container.width, result.container.height) * SCENE_SCALE * 1.3
    : 10;
  // Puente entre el boton HTML (fuera del arbol de r3f) y la camara/controles
  // (que solo existen dentro de <Canvas>): SceneContents rellena esta ref con
  // la funcion real de encuadre una vez montada.
  const fitViewRef = useRef<(() => void) | undefined>(undefined);

  return (
    <>
      <Canvas
        camera={{ position: [cameraDistance, cameraDistance * 0.8, cameraDistance], fov: 45 }}
        gl={{ preserveDrawingBuffer: true }}
        onPointerMissed={() => onSelectPiece(null)}
      >
        {result && (
          <SceneContents
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
            fitViewRef={fitViewRef}
            captureRef={captureRef}
            onSelectPiece={onSelectPiece}
            onCommitMove={onCommitMove}
            onCommitInsert={onCommitInsert}
          />
        )}
      </Canvas>
      {result && (
        <button
          type="button"
          className="fit-view-btn"
          title="Center View / Fit Container"
          onClick={() => fitViewRef.current?.()}
        >
          ⤢
        </button>
      )}
    </>
  );
}
