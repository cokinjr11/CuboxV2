import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useThree } from "@react-three/fiber";
import { Billboard, Edges, Grid, OrbitControls, Text } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { PieceMesh, type PieceVisualState } from "./PieceMesh";
import { LoadUnitBody } from "./LoadUnitBody";
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

// Redesign correction: el viewport interactivo en pantalla es angosto/casi
// cuadrado (panel 3D compartido con la barra lateral) -capturar al tamano
// nativo del canvas daba un PNG en ese mismo aspect ratio (~0.9, mas alto
// que ancho). El slot del step card en el PDF es panoramico
// (_STEP_IMAGE_WIDTH/_STEP_IMAGE_HEIGHT en pdf_export.py = 560/215 ~ 2.6),
// y _fit_image_dims preserva aspect ratio (object-fit: contain) -un PNG
// casi cuadrado metido en un slot panoramico deja ~65% del ancho del slot
// en blanco, que es exactamente el sintoma reportado ("mucho espacio en
// blanco alrededor") aunque la camara ya este bien encuadrada. Debe
// coincidir con pdf_export.py:_STEP_IMAGE_WIDTH/_STEP_IMAGE_HEIGHT.
const GUIDE_CAPTURE_ASPECT = 560 / 215;
// FOV VERTICAL de la captura de guia (no el FOV interactivo de siempre,
// fov=45 -ver <Canvas> mas abajo, sin cambios). Con GUIDE_CAPTURE_ASPECT
// panoramico (~2.6), un FOV vertical ancho da un FOV horizontal enorme
// (>100 grados) que aleja visualmente el contenedor en el eje largo -30
// grados verticales da un FOV horizontal ~70 grados, un angulo "3/4"
// razonable para la vista diagonal sin fisheye.
const GUIDE_CAPTURE_VERTICAL_FOV_DEG = 30;

/** Distancia MINIMA (a lo largo de `direction`, partiendo de `center`) tal
 * que los 8 vertices del contenedor (caja L x H x W centrada en X/Z,
 * apoyada en Y=0) entran dentro del frustum de una camara con el FOV
 * vertical/aspect dados -el vertice mas restrictivo (horizontal o
 * vertical) define el resultado. Reemplaza los multiplicadores empiricos
 * de versiones anteriores (0.68, 0.5, ...): esos se habian tuneado contra
 * el aspect ratio ACCIDENTAL del canvas en pantalla (variable segun el
 * tamano de ventana del navegador) -al fijar GUIDE_CAPTURE_ASPECT/FOV para
 * la captura, la distancia correcta cambia con ellos, y calcularla
 * geometricamente (en vez de volver a tantear a mano) garantiza que
 * SIEMPRE llene el cuadro al maximo sin recortar, para cualquier tamano de
 * contenedor. */
function _minDistanceToFitContainer(
  L: number,
  H: number,
  W: number,
  direction: THREE.Vector3,
  verticalFovDeg: number,
  aspect: number
): number {
  const halfVFov = THREE.MathUtils.degToRad(verticalFovDeg) / 2;
  const halfHFov = Math.atan(Math.tan(halfVFov) * aspect);

  const forward = direction.clone().negate();
  const worldUp = new THREE.Vector3(0, 1, 0);
  const right = new THREE.Vector3().crossVectors(forward, worldUp).normalize();
  const up = new THREE.Vector3().crossVectors(right, forward).normalize();

  const center = new THREE.Vector3(0, H / 2, 0);
  let minT = 0;
  for (const x of [-L / 2, L / 2]) {
    for (const y of [0, H]) {
      for (const z of [-W / 2, W / 2]) {
        const rel = new THREE.Vector3(x, y, z).sub(center);
        const relRight = rel.dot(right);
        const relUp = rel.dot(up);
        const relForward = rel.dot(forward);
        // Camara en center + direction*t: la componente right/up de
        // cualquier punto respecto de la camara es CONSTANTE (=relRight/
        // relUp, direction es perpendicular a right/up por construccion),
        // solo la componente forward (profundidad) crece con t. Se
        // necesita |componente| / profundidad <= tan(mitad del FOV).
        const tHoriz = Math.abs(relRight) / Math.tan(halfHFov) - relForward;
        const tVert = Math.abs(relUp) / Math.tan(halfVFov) - relForward;
        minT = Math.max(minT, tHoriz, tVert);
      }
    }
  }
  return minT;
}

/** Fase 6B.3, seccion 10/11 del pedido: camara EXCLUSIVA de los PDF de
 * Loading/Unloading Guide -nunca usada por el boton Fit View, la camara
 * inicial del Canvas, ni la Guia interactiva (esas siguen usando
 * computeFitView tal cual, sin cambios).
 *
 * Solo depende de las dimensiones del contenedor (nunca de las piezas ni
 * del paso actual), asi que el encuadre es IDENTICO en el Step 1, el 13, el
 * 40 y el ultimo -ver seccion 14 del pedido. */
function computeGuideCameraView(container: { length: number; width: number; height: number }) {
  const L = container.length * SCENE_SCALE;
  const W = container.width * SCENE_SCALE;
  const H = container.height * SCENE_SCALE;
  const center = new THREE.Vector3(0, H / 2, 0);
  // Direccion diagonal fija por ANGULO (no por dimensiones del contenedor):
  // una direccion tipo (-L, H*k, W*k) queda dominada por L en un container
  // largo/angosto (40ft: L=12000mm vs W/H~2350/2390mm) y termina CASI
  // axial (mirando derecho por el largo, con un desvio lateral/vertical
  // minusculo en proporcion) -eso obliga a alejar muchisimo la camara solo
  // para que la esquina cercana (la puerta) quede delante de ella, y esa
  // distancia extra (que no aporta nada al encuadre, solo "despeja" la
  // puerta) es lo que dejaba el contenedor chico en el centro del cuadro
  // pese al "fit" exacto. Con yaw/pitch FIJOS en grados (independientes del
  // tamano del contenedor) se obtiene una vista 3/4 real -oblicua, nunca
  // casi de frente- para cualquier contenedor. La MAGNITUD (que tan lejos)
  // no es un multiplicador arbitrario: _minDistanceToFitContainer la
  // resuelve exactamente para el FOV/aspect fijos de esta captura, con un
  // margen chico (12%) de aire.
  const yaw = THREE.MathUtils.degToRad(40); // desvio horizontal respecto de mirar derecho por la puerta
  const pitch = THREE.MathUtils.degToRad(15); // elevacion -que tan arriba esta la camara
  const direction = new THREE.Vector3(-Math.cos(yaw) * Math.cos(pitch), Math.sin(pitch), Math.sin(yaw) * Math.cos(pitch));
  const fitDistance = _minDistanceToFitContainer(L, H, W, direction, GUIDE_CAPTURE_VERTICAL_FOV_DEG, GUIDE_CAPTURE_ASPECT);
  const position = center.clone().add(direction.clone().multiplyScalar(fitDistance * 1.06));
  return { center, position };
}

interface ContentsProps {
  result: PackingResult;
  selectedPieceId: string | null;
  colorBy: ColorByMode;
  insertingItem: UnloadedItem | null;
  showLabels: boolean;
  showCenterOfMass: boolean;
  /** Fase 6B.1, seccion 5-7 del pedido: badges de numero de secuencia por
   * pieza -TOTALMENTE INDEPENDIENTES de la guia (guideSteps/guideStepIndex
   * de abajo): solo deciden el numero que se dibuja sobre cada pieza,
   * nunca su color/opacidad/visibilidad. `activeSequence` es
   * load_sequence o unload_sequence, ya resuelto en App.tsx. */
  showSequence: boolean;
  activeSequence: string[];
  /** Fase 6B: pasos de una guia (Loading/Unloading, uno por PDF, o la Guia
   * interactiva del Workspace -mismo estado compartido, ver App.tsx) para
   * renderizar un paso especifico -cada elemento es la lista de piece ids
   * que corresponden a ESE paso. undefined/null = sin guia activa, todo se
   * renderiza normal ("past" -ver mas abajo). */
  guideSteps?: string[][];
  guideStepIndex?: number | null;
  /** Fase 6B, seccion 7 del pedido: CARGA y DESCARGA no son simetricas -en
   * carga, "pasado" (ya cargado) sigue VISIBLE (translucido) y "futuro"
   * (todavia no) se OCULTA; en descarga es al reves, "pasado" (ya retirado)
   * se OCULTA y "futuro" (todavia dentro) sigue VISIBLE (translucido).
   * Default "load" preserva el comportamiento de siempre cuando no se
   * especifica (Container Report, o cualquier captura sin guia activa). */
  guideDirection?: "load" | "unload";
  fitViewRef: React.MutableRefObject<(() => void) | undefined>;
  /** Ref opcional que el padre (App.tsx/Report Settings) puede pasar para
   * disparar una captura PNG limpia del contenedor bajo demanda -Fit View +
   * espera de 2 frames + toDataURL-, reutilizada por los 3 reportes PDF.
   * `useGuideCameraView` (Fase 6B.3): true SOLO para pasos de Loading/
   * Unloading Guide -fuerza el encuadre diagonal consistente entre pasos en
   * vez del Fit Container simetrico de siempre (ver computeGuideCameraView).
   * La VISIBILIDAD de piezas durante la captura sigue siendo la misma logica
   * acumulada de siempre (guideStateById/PieceMesh, sin variante especial de
   * captura -Fase 6C.1, pedido explicito del usuario). */
  captureRef?: React.MutableRefObject<((useGuideCameraView?: boolean) => Promise<string>) | undefined>;
  onSelectPiece: (id: string | null) => void;
  onCommitMove: (pieceId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
  onCommitInsert: (unloadedId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
}

function SceneContents({
  result,
  selectedPieceId,
  colorBy,
  insertingItem,
  showLabels,
  showCenterOfMass,
  showSequence,
  activeSequence,
  guideSteps,
  guideStepIndex,
  guideDirection = "load",
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
  // Operational Guide Print Redesign v2, seccion 17: true SOLO durante la
  // ventana de captura de un Step de Loading/Unloading Guide (nunca en la
  // Guia interactiva ni en el Container Report) -PieceMesh usa esto para
  // simplificar su etiqueta (Code solamente, ver printMode en PieceMesh.tsx).
  // Estado real de React (no un ref) a proposito: el label es JSX declarativo,
  // necesita un re-render de verdad para reflejarse antes de toDataURL().
  const [printMode, setPrintMode] = useState(false);

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

  // Snapshot reutilizable para los 3 reportes PDF. Fase 6B.2, secciones 7-9
  // del pedido: SIEMPRE encuadra el Load Space COMPLETO (Fit Container) y
  // NUNCA la caja envolvente de "lo cargado hasta este paso" -esa variante
  // (computeFitLoadedArea, eliminada en esa fase) hacia que la camara se
  // moviera/alejara de un paso a otro y, combinada con la carga gris
  // acumulada, era parte de por que el PDF se volvia ilegible pasado el
  // Step 12-13. Al depender solo de `container` (constante para todo el
  // plan), la camara queda IDENTICA en el Step 1, el 13, el 40 y el 86 -el
  // operador puede memorizar la perspectiva del contenedor en vez de
  // reorientarse en cada pagina.
  //
  // Fase 6B.3, seccion 1/2/10/11 del pedido: el Container Report (overview
  // unico) sigue usando computeFitView -la misma vista que el boton Fit
  // View, sin cambios. SOLO los pasos de Loading/Unloading Guide usan la
  // camara nueva computeGuideCameraView -exclusiva del PDF, nunca de la
  // Guia interactiva del Workspace (que no reposiciona la camara para nada,
  // ver seccion 15 del pedido 6B.2). `useGuideCameraView` llega como
  // ARGUMENTO explicito de App.tsx (nunca leido de guideSteps/
  // guideStepIndex via props/refs) -bug real encontrado al inspeccionar el
  // PDF: React Three Fiber corre su reconciler en un ciclo propio, no
  // sincronizado 1:1 con el commit de React normal, asi que leer
  // guideStepIndex desde un ref actualizado en el render de SceneContents
  // llegaba sistematicamente UN PASO ATRASADO en cada captura (confirmado
  // instrumentando la closure: la llamada N siempre traia el valor de la
  // iteracion N-1, nunca el actual) -el Step 1 se capturaba con
  // isGuideStepCapture=false (computeFitView) y el resto quedaba corrido
  // por uno. Pasar el booleano directo desde el loop de App.tsx (que ya
  // sabe sincronicamente, sin pasar por React, si esta capturando un guide
  // step o el overview del Container Report) elimina la dependencia de
  // timing por completo.
  useEffect(() => {
    if (!captureRef) return;
    captureRef.current = async (useGuideCameraView) => {
      const { center, position } = useGuideCameraView ? computeGuideCameraView(container) : computeFitView(container);
      camera.position.copy(position);
      camera.lookAt(center);
      if (controlsRef.current) {
        controlsRef.current.target.copy(center);
        controlsRef.current.update();
      }

      // Redesign -Step image mas grande (Parte 1): un FOV mas ancho SOLO
      // durante la captura de Loading/Unloading Guide permite acercar la
      // camara (menos espacio en blanco alrededor del contenedor) sin
      // recortar los extremos -con el FOV=45 de siempre, acercar la camara
      // lo suficiente para que el contenedor se vea grande empezaba a
      // cortar la puerta/el fondo fuera del cuadro. Nunca se toca el FOV
      // de la camara inicial/interactiva (el prop fov=45 del <Canvas>, sin
      // cambios) -se restaura apenas termina esta captura puntual.
      const perspectiveCamera = camera as THREE.PerspectiveCamera;
      const isPerspective = Boolean((perspectiveCamera as { isPerspectiveCamera?: boolean }).isPerspectiveCamera);
      const originalFov = perspectiveCamera.fov;
      if (useGuideCameraView && isPerspective) {
        perspectiveCamera.fov = GUIDE_CAPTURE_VERTICAL_FOV_DEG;
        perspectiveCamera.updateProjectionMatrix();
      }

      if (useGuideCameraView) setPrintMode(true);
      await waitTwoAnimationFrames();

      // Redesign v2, seccion 16 del pedido: la vista interactiva usa
      // dpr=[1,2] (Canvas mas abajo) -pensado para fluidez de orbit/zoom en
      // pantalla, no para nitidez impresa. Para un snapshot de Loading/
      // Unloading Guide (useGuideCameraView=true) se sube el pixelRatio
      // SOLO durante este frame de captura -nunca se toca el prop dpr del
      // Canvas (la experiencia interactiva no cambia en nada) y se
      // restaura el valor original enseguida despues. El Container Report
      // (useGuideCameraView=false) sigue igual que siempre.
      const originalPixelRatio = gl.getPixelRatio();
      const originalSize = gl.getSize(new THREE.Vector2());
      const originalAspect = perspectiveCamera.aspect;
      const targetPixelRatio = useGuideCameraView ? Math.max(originalPixelRatio, 2) * 1.5 : originalPixelRatio;
      let dataUrl: string;
      if (useGuideCameraView) {
        // Redesign correction: fuerza la resolucion de captura al aspect
        // ratio panoramico del slot del PDF (GUIDE_CAPTURE_ASPECT), en vez
        // de heredar el aspect casi cuadrado del viewport en pantalla -sin
        // esto, _fit_image_dims (aspect-preserving) dejaba ~65% del slot en
        // blanco pese a que la camara ya estaba bien encuadrada. Se ajusta
        // camera.aspect junto con el tamano para evitar el estiramiento
        // (flattening) que dejaria una resolucion desalineada del
        // proyeccion de la camara -ambos se restauran enseguida despues.
        const captureHeight = originalSize.y;
        const captureWidth = Math.round(captureHeight * GUIDE_CAPTURE_ASPECT);
        gl.setPixelRatio(targetPixelRatio);
        gl.setSize(captureWidth, captureHeight, false);
        if (isPerspective) {
          perspectiveCamera.aspect = GUIDE_CAPTURE_ASPECT;
          perspectiveCamera.updateProjectionMatrix();
        }
        await waitTwoAnimationFrames();
        dataUrl = gl.domElement.toDataURL("image/png");
        gl.setPixelRatio(originalPixelRatio);
        gl.setSize(originalSize.x, originalSize.y, false);
        if (isPerspective) {
          perspectiveCamera.aspect = originalAspect;
          perspectiveCamera.updateProjectionMatrix();
        }
      } else {
        dataUrl = gl.domElement.toDataURL("image/png");
      }
      if (useGuideCameraView && isPerspective) {
        perspectiveCamera.fov = originalFov;
        perspectiveCamera.updateProjectionMatrix();
      }
      if (useGuideCameraView) setPrintMode(false);
      return dataUrl;
    };
  }, [container, camera, gl, captureRef]);

  // Mapa piece id -> "past"/"current"/"future" para un paso de guia (PDF o
  // -Fase 6B- la Guia interactiva del Workspace): pasado = union de los
  // pasos anteriores, actual = este paso, futuro = union de los pasos
  // siguientes. null = sin guia activa (fetch en curso, error, o container
  // vacio) -todo se renderiza "past" (normal), ver mas abajo.
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

  // Fase 6B.1: restaurado -mapa piece id -> posicion en activeSequence,
  // usado SOLO para el numero de badge (sequenceLabel mas abajo). No
  // interviene en visualState/color/opacidad -eso es exclusivamente
  // guideStateById (guia) de arriba.
  const sequenceOrder = new Map(activeSequence.map((id, i) => [id, i]));

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
        // Sin guia activa (fetch en curso/error/container vacio): todo se ve
        // normal, igual que antes de que existiera la Guia (Fase 6B).
        const visualState: PieceVisualState = guideStateById ? guideStateById.get(piece.id) ?? "future" : "past";
        const order = sequenceOrder.get(piece.id);
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
            guideDirection={guideDirection}
            printMode={printMode}
            onSelect={onSelectPiece}
            onBeginDrag={beginDrag}
          />
        );
      })}
      {insertingItem && insertDims && ghostPos && (
        <LoadUnitBody
          itemType={insertingItem.item_type}
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
  showLabels: boolean;
  showCenterOfMass: boolean;
  /** Fase 6B: pasos de una guia (Loading/Unloading, PDF o la Guia
   * interactiva del Workspace) para renderizar un paso especifico -ver
   * guideStateById en SceneContents. */
  guideSteps?: string[][];
  guideStepIndex?: number | null;
  guideDirection?: "load" | "unload";
  /** Fase 6B.1: badge de numero de secuencia por pieza -independiente del
   * estado visual de la guia (visualState); ver misma prop en ContentsProps. */
  showSequence: boolean;
  activeSequence: string[];
  /** Ref opcional que el padre (App.tsx/Report Settings) puede pasar para
   * disparar una captura PNG limpia del contenedor bajo demanda -Fit View +
   * espera de 2 frames + toDataURL-, reutilizada por los 3 reportes PDF. */
  captureRef?: React.MutableRefObject<((useGuideCameraView?: boolean) => Promise<string>) | undefined>;
  onSelectPiece: (id: string | null) => void;
  onCommitMove: (pieceId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
  onCommitInsert: (unloadedId: string, x: number, y: number, z: number, dx: number, dy: number, dz: number) => Promise<void>;
}

export function Scene3D({
  result,
  selectedPieceId,
  colorBy,
  insertingItem,
  showLabels,
  showCenterOfMass,
  guideSteps,
  guideStepIndex,
  guideDirection,
  showSequence,
  activeSequence,
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
        gl={{ preserveDrawingBuffer: true, antialias: true }}
        dpr={[1, 2]}
        onPointerMissed={() => onSelectPiece(null)}
      >
        {result && (
          <SceneContents
            result={result}
            selectedPieceId={selectedPieceId}
            colorBy={colorBy}
            insertingItem={insertingItem}
            showLabels={showLabels}
            showCenterOfMass={showCenterOfMass}
            guideSteps={guideSteps}
            guideStepIndex={guideStepIndex}
            guideDirection={guideDirection}
            showSequence={showSequence}
            activeSequence={activeSequence}
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
