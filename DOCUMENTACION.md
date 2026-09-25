# CUBOX 2.0 — Documentación del proyecto

Documento de entrega (handoff) para quien continúe el desarrollo.
Cubre: qué es el producto, cómo se usa, cómo está construido por dentro, en qué
se diferencia de CUBOX V1, y qué condicionales quedan pendientes de definir en
el módulo **Panels & Fragile**.

- **Repositorio V2 (este):** https://github.com/cokinjr11/CuboxV2
- **Repositorio V1 (anterior):** https://github.com/cokinjr11/CuboxV1
- **Último commit documentado:** `b33d28c` — *Phase 5D-7: plan persistence, delivery/priority polish, and Keep Groups/Systems Together Section-3D engine*

> ⚠️ El `README.md` de la raíz está **obsoleto**: describe el MVP original
> ("EasyCargando", solo ventanas en contenedores). Este documento lo reemplaza
> como fuente de verdad.

---

## 1. Qué es CUBOX

Software de **cubicaje / container loading**: recibe una lista de ítems a cargar
(desde Excel), calcula cómo acomodarlos físicamente dentro de un espacio de
carga (contenedor, camión, trailer o espacio personalizado), lo muestra en 3D
interactivo, valida que el resultado sea físicamente y operativamente correcto,
y genera la documentación operativa (guías de carga/descarga en PDF, export a
Excel).

Es una aplicación **local**: sin autenticación, sin nube. Backend FastAPI +
frontend React, ambos corriendo en la misma máquina.

**Dominio original:** ventanería y vidrio (paneles frágiles que no pueden
acostarse sobre la cara de vidrio). CUBOX 2.0 generalizó el modelo para soportar
también cajas sueltas, carga paletizada y carga personalizada.

---

## 2. Contraste V1 → V2

Ambos repositorios comparten el **mismo commit raíz** (`d7c9f4c — Initial commit:
CUBOX container-loading optimizer`). V1 se quedó ahí; V2 continuó con 12 fases
de desarrollo sobre esa misma base.

**Magnitud del cambio:** `git diff d7c9f4c HEAD` → **118 archivos, +29.373 / −852 líneas**.

### 2.1 Qué era V1

MVP funcional y acotado:

- **Un solo tipo de ítem:** ventana (Width × Height × Thickness), con la regla
  dura de que nunca puede apoyarse sobre su cara de vidrio.
- **Un solo tipo de espacio:** contenedor marítimo (3 presets).
- **Un solo motor de packing:** heurística de puntos de anclaje (`packer.py`).
- **Import Excel único**, sin perfiles ni validación previa.
- **Sin wizard, sin pantalla Home, sin persistencia:** se abría la app, se
  subía un Excel, se calculaba, y al recargar se perdía todo.
- 16 módulos en `backend/app/core/`, 18 archivos de test.
- Frontend sin wizard: `ImportPanel` + `Scene3D` + paneles de resultado.

### 2.2 Qué agregó V2

| Área | V1 | V2 |
|---|---|---|
| Tipos de carga (Load Types) | Solo ventanas | **4 perfiles**: Loose Boxes, Palletized Load, Panels & Fragile, Custom Load (+ *Build Pallets* marcado "Coming Soon") |
| Espacios de carga | 3 contenedores | Contenedor / Camión / Trailer / **Custom** con dimensiones propias |
| Dimensiones | `width/height/thickness` (semántica de ventana) | **`Dimensions3D` genérico** (`length/width/height`) canónico; la semántica de panel se conserva como capa de compatibilidad |
| Import Excel | Uno solo, genérico | **Import profile-aware** con template descargable por perfil, validación previa (`ImportPreview`) y reporte de errores/warnings fila por fila |
| Configuración de plan | Ninguna | **Wizard de 5 pasos** + **Handling Rules a nivel de plan** (stackable, orientación, clearance, pasillo central, balance de peso, punto de inicio de carga) |
| Motores de packing | 1 (`packer.py`) | **5 motores** ruteados por Load Type y modo (ver §5) |
| Modos de optimización | Agrupación básica | Best Space / **Keep Groups Together** / **Keep Systems Together** / Prioritize Delivery Sequence — los dos intermedios sobre una arquitectura 3D propia (§6) |
| Apilamiento | Conservador, plano | **Level 2 real** con soporte verificado (80% de apoyo, peso de apilado, sin flotantes) |
| Persistencia | No existe | **Load Plans en SQLite** (`backend/data/cubox.db`), pantalla Home con Recent Plans, autosave, reapertura sin recalcular |
| Reportes | PDF básico | Container Load Report + **Loading Guide** + **Unloading Guide** (completa o por Grupo, PDF o ZIP), con capturas 3D reales |
| Secuencia operativa | Orden espacial | Grafo de dependencias (soporte + bloqueo lateral), detección de ciclos, warnings de Delivery Sequence, guía paso a paso |
| Validación | Básica | **Plan Validation** en 10 categorías con estado READY / READY WITH WARNINGS / NOT READY |
| Peso | Métricas simples | Balance izq/der y frente/fondo, centro de masa, **distribución por eje para vehículos de carretera** |
| Tests | 18 archivos | **53 archivos, 833 tests** |

### 2.3 Historial de fases (contexto de diseño)

Los comentarios del código referencian estas fases constantemente; sirven como
mapa de por qué cada cosa está donde está:

```
d7c9f4c  Initial commit  ················· = CUBOX V1 completo
21c6b12  Phase 1   generalize item and orientation rules
807332e  Phase 2A  generalize load spaces
fb294e1  Phase 2B  road vehicle weight distribution
ddd33f0  Phase 3A  introduce generic dimensions
ad79ed4  Phase 3A.1 make generic dimensions canonical
b0ca0f4  Phase 3B  profile-aware Excel import
502982c  Phase 4   new load plan wizard
9deca84  Phase 5   loose boxes end-to-end
e88cb7f  Phase 6   palletized load end-to-end
7eabde1  Phase 5B/5C  handling rules + Tilt
fe12330  Disable Tilt UI (backend preservado)
b33d28c  Phase 5D-7  persistencia + Keep Groups/Systems Section-3D
```

---

## 3. Flujo de trabajo del usuario

```
HOME  ──► WIZARD (5 pasos) ──► WORKSPACE ──► REPORTES / EXPORT
  ▲                                │
  └──── Recent Plans (reabrir) ────┘
```

### 3.1 Home (`frontend/src/components/Home.tsx`)

Punto de entrada. Ofrece **New Load Plan** (abre el wizard) y la lista de
**Recent Plans** (nombre, espacio de carga, tipo, cargados/total, fecha de
modificación) con Abrir y Eliminar. Reabrir un plan **no recalcula nada**:
restaura la geometría exacta que se guardó.

### 3.2 Wizard (`frontend/src/components/wizard/LoadPlanWizard.tsx`)

Cinco pasos, en este orden:

1. **Load** — *¿Qué estás cargando?* Elige el perfil: Loose Boxes, Palletized
   Load, Panels & Fragile, Custom Load (Build Pallets está deshabilitado).
   Esto determina el `ItemType` y el template de Excel.
2. **Load Space** — *¿Dónde lo cargas?* Contenedor (catálogo), Camión, Trailer
   o espacio Custom (nombre + largo/ancho/alto internos + payload máximo).
3. **Rules** — *Handling Rules* del plan: orientación por defecto (según
   perfil), stackable por defecto, clearance mínimo, pasillo central y su
   ancho, balance de peso, y para Panels el punto de inicio de carga
   (Back Left / Back Right). *En Panels la orientación queda fija en
   "Keep On Edge" y no es editable.*
4. **Import** — Descarga el template del perfil, sube el `.xlsx`, y ve el
   preview: resumen (filas/unidades/peso) + tabla de errores y warnings por
   fila y columna. **No se puede continuar si el import no es válido.**
5. **Review** — Resumen de solo lectura. El botón final dice **Create Load Plan**.

> **Nota:** el *Optimization Mode* **no** está en el wizard; se elige dentro del
> Workspace (`ImportPanel`, sección 3).

### 3.3 Workspace (`frontend/src/App.tsx`)

Pantalla principal, tres columnas: barra lateral izquierda (configuración y
acciones), visor 3D al centro, panel derecho contextual.

- **ImportPanel** — Load Space, **Optimization Mode** (Best Space / Keep Groups
  Together / Keep Systems Together / Prioritize Delivery Sequence), sección
  *Advanced* (stackable, orientación, pasillo, clearance, balance de peso,
  inicio de carga) y el botón **Optimize**.
- **Scene3D** — vista 3D con Three.js: contenedor, piezas, etiquetas, números de
  secuencia, centro de masa, y **arrastre manual** de piezas (snap 30 mm, paso
  10 mm) validado contra el backend. Genera las capturas PNG que usan los PDFs.
- **PieceInspector** — al seleccionar una pieza: dimensiones, orientación (con
  badge Override/Inherited), peso, stackable, prioridad, posición de carga y
  descarga, *Blocked By*, y acciones Lock/Unlock, Rotar (R), Girar (T), Quitar.
- **PlanValidationPanel** — estado READY / READY WITH WARNINGS / NOT READY con
  las 10 categorías de validación; se recalcula automáticamente (debounce 700 ms).
- **SequencePanel** — guía operativa paso a paso (carga o descarga), modo
  automático o manual con N piezas por paso, y resumen de warnings operativos.
- **UnloadedPanel** — ítems que no entraron, con su motivo, y opción de
  colocarlos manualmente arrastrándolos.
- **AlternativesPanel** — soluciones alternativas (hasta 3) con su score.
- **MetricsPanel / ColorBy / Settings / ReportSettings** — métricas, coloreado
  por Grupo/Sistema/Prioridad, tema, y configuración de reportes PDF.

### 3.4 Persistencia

El plan se vuelve persistente en el **primer Optimize exitoso después del
wizard** (`POST /plans`). Desde ahí, cada cambio del resultado hace **autosave**
(debounce 900 ms) y los cambios de Handling Rules se guardan aparte. Si alguna
pieza está **Locked**, Optimize usa `optimize-remaining` para respetarla.

---

## 4. Arquitectura técnica

### 4.1 Stack

| | |
|---|---|
| Backend | Python 3.11+ (probado en 3.14), FastAPI 0.141, Pydantic 2.13, openpyxl, reportlab, pytest |
| Frontend | React 19, TypeScript, Vite 8, `@react-three/fiber` + `drei` (Three.js), axios |
| Persistencia | SQLite (`backend/data/cubox.db`, `schema_version = 1`, **nunca se commitea**) |

### 4.2 Estructura

```
backend/app/
  api/routes.py          33 endpoints (único punto HTTP)
  models/
    schemas.py           modelo de dominio (Pydantic)
    containers.py        catálogo de espacios de carga
    import_schemas.py    tipos del import (ImportPreview, ImportDefaults…)
  core/                  30 módulos — ver 4.4
  tests/                 53 archivos, 833 tests

frontend/src/
  AppRoot.tsx            switcher home | wizard | workspace (sin router)
  App.tsx                el Workspace completo
  components/            paneles + wizard/
  api/client.ts          todas las llamadas HTTP
  types.ts               espejo TS del modelo de dominio
```

### 4.3 Modelo de dominio (`backend/app/models/schemas.py`)

- **`ItemType`**: `box` · `pallet` · `panel` · `custom`
- **`OrientationPolicy`**: `free` · `upright` · `fixed` · **`panel_edge_only`**
  (la regla crítica: un panel jamás se acuesta sobre su cara de vidrio)
- **`OptimizationMode`**: `best_space` · `keep_groups` · `keep_systems` ·
  `prioritize_delivery`
- **`LoadSpaceType`**: `container` · `truck` · `trailer` · `custom`
- **`WeightBalanceMode`**: `ignore` · `normal` · `important`
- **`LoadingAnchor`**: `back_right` · `back_left`
- **`WindowItem`** (ítem de entrada) y **`PlacedPiece`** (pieza colocada:
  `x,y,z` + `dx,dy,dz` + orientación + dimensiones de origen + `locked` + tilt)
- **`PlanHandlingRules`**: `default_stackable`, `default_orientation_policy`,
  `default_max_stack_weight`, `default_allow_tilt`, `default_max_tilt_angle`

**Convención de coordenadas (crítica, se asume en todo el código):**
`x = 0` es la **puerta**, `x = length` es el **fondo**; `y` grande = derecha;
`z = 0` es el **piso**.

### 4.4 Módulos core relevantes

| Módulo | Rol |
|---|---|
| `optimize.py` | **Único punto de ruteo de motores.** Corre las 7 estrategias, puntúa y elige. |
| `packer.py` | Motor legacy (anclas). Sigue siendo el fallback de seguridad. |
| `packer_v2.py` | Motor EMS para Boxes. |
| `panel_module_solver.py` | Stage 1 de Panels: módulos por profundidad, knapsack acotado, arreglo bilateral. **Nunca se reescribe.** |
| `panel_pocket_reuse.py` | Stage 2: reutiliza los *pockets* residuales entre módulos antes de consumir más largo. |
| `section_coordinator.py` | **Motor Section-First 3D** compartido por Keep Groups y Keep Systems (§6). |
| `level_coordinator.py` | Level 2 (z > 0) con soporte verificado. `MAX_LEVEL = 2` (constante de seguridad). |
| `group_selection.py` | Selección de clusters completos antes del packing (§6.3). |
| `geometry.py` | Validadores canónicos: `check_support`, `check_stack_weight`, `within_container`. **Fuente única de verdad física.** |
| `sequence.py` | Grafos de carga/descarga, bloqueo lateral, detección de ciclos, warnings. |
| `handling_rules.py` | Resolución de valores efectivos (§7). |
| `final_validation.py` | Las 10 categorías de Plan Validation. |
| `import_items.py` / `import_templates.py` | Import profile-aware y generación de templates. |
| `plan_store.py` / `plan_service.py` | Persistencia SQLite y snapshot/restore de planes. |
| `reserved_zones.py`, `road_weight.py`, `manual_move.py`, `history.py`, `scoring.py`, `strategies.py`, `pdf_export.py`, `excel_*.py` | Zonas reservadas, peso por eje, edición manual, undo/redo, scoring, estrategias de orden, reportes. |

---

## 5. Pipeline de optimización y ruteo de motores

Todo pasa por `run_optimization()` en `optimize.py`. Ese es el **único** lugar
donde se decide qué motor se usa; ni el frontend ni `routes.py` duplican esa
lógica.

```
items ──► resolve_effective_item (Handling Rules)
      ──► _select_pack_fn  (elige motor por Load Type + Tilt/Clearance)
      ──► ¿KEEP_GROUPS o KEEP_SYSTEMS + motor Pocket V2?
              SÍ ──► section_cluster_3d  (Section-First 3D)   ─┐
              NO ──► loop de 7 estrategias sobre el motor      │
                     elegido, scoring, top-3 alternativas     ─┘
      ──► PackingResult (+ secuencias, warnings, métricas)
```

### Matriz de ruteo actual

| Load Type | Modo | Motor | Tag |
|---|---|---|---|
| PANEL | Best Space / Prioritize Delivery | Pocket Reuse | `PANEL_MODULE_POCKET_V2` |
| PANEL | **Keep Groups** | Section-First 3D (cluster = Group) | `PANEL_SECTION_GROUP_3D_V3` |
| PANEL | **Keep Systems** | Section-First 3D (cluster = System) | `PANEL_SECTION_SYSTEM_3D_V3` |
| PANEL | Tilt activo | Legacy | `PANEL_LEGACY_TILT_FALLBACK` |
| PANEL | Clearance > 0 | Legacy | `PANEL_LEGACY_CLEARANCE_FALLBACK` |
| BOX | cualquiera | EMS V2 (legacy si clearance > 0) | `BOX_EMS_V2` |
| PALLET / CUSTOM | cualquiera | Legacy | `PALLET_LEGACY` / `CUSTOM_LEGACY` |

**Por qué existen los fallbacks:** ni el EMS de Boxes ni el Module Solver de
Panels *tallan* el espacio libre por clearance (solo lo verifican de forma
reactiva), y el Module Solver no genera orientaciones con Tilt. Mientras eso no
se resuelva, cualquier `clearance > 0` o Tilt activo cae al motor legacy. **No
quitar estos fallbacks sin resolver primero la causa.**

Las 7 estrategias (`strategies.py`) son órdenes de entrada distintos:
`largest_volume`, `largest_footprint`, `tallest_first`, `highest_priority`,
`group_and_size`, `system_and_size`, `longest_dimension`.

---

## 6. Arquitectura Section-First 3D (Keep Groups / Keep Systems)

Es lo más nuevo y lo más específico del proyecto. Un solo motor
(`section_coordinator.py: section_cluster_3d`) parametrizado por **`cluster_key`**:

- Keep Groups Together → `cluster_key = item.group`
- Keep Systems Together → `cluster_key = item.system`

> **No duplicar este motor.** Si hace falta un tercer criterio de agrupación, se
> agrega otra `cluster_key`, no otro coordinador.

### 6.1 Conceptos

- **Section**: banda de profundidad física real (rango en X) que el solver
  cierra. Progresión monotónica **fondo → puerta**.
- **Section-first**: antes de abrir una Section nueva se agota la actual, *piso
  y Level 2*. Preferir altura antes que largo.
- **Level 2**: una única capa por encima del piso (`MAX_LEVEL = 2`). El límite
  es deliberado: `check_stack_weight` es un chequeo **directo, no transitivo**,
  lo cual es completo y correcto para exactamente 2 niveles. **No habilitar
  Level 3+ sin resolver antes la propagación de carga transitiva.**
- **Completion frontier**: cuando un cluster termina, se registra su Section de
  finalización. El siguiente cluster puede usar los pockets y las superficies de
  esa Section, pero **jamás retroceder** a Sections anteriores ya congeladas.
- **Sin reentrada**: la cronología válida es `A → A → A+B → B → B+C → C`.
  Prohibido `A → B → A`. Se mide con `compute_group_contiguity_metrics()`
  (`*_reentry_count` debe ser 0).

### 6.2 Handoff entre clusters

Una misma Section puede contener **dos o tres clusters** — pero solo en la
frontera de transición, y solo después de que el cluster dueño ya completó.
Esto es intencional y está cubierto por tests; **no es un bug** aunque rompa la
vieja regla de "un módulo nunca mezcla grupos" (esa era la regla del Stage 1).

### 6.3 Completo antes que parcial (dos fases, nunca mezcladas)

- **Fase A — iterativa:** mientras algún cluster excluido pueda alcanzar el
  **100%** contra la geometría real restante, se compromete y se recalcula la
  geometría. Se repite.
- **Fase B — parcial de cola:** solo cuando ningún cluster puede completar, se
  admite **un único** cluster parcial, elegido por payload útil. Después de él
  no empieza ningún otro cluster.

La prioridad de negocio es **payload de clusters completos**, no *cantidad* de
nombres de cluster: un cluster grande completo le gana a varios chiquitos.

### 6.4 Chequeos de seguridad que no se deben "optimizar"

El chequeo de ciclos de secuencia usa el grafo **canónico completo**, filtrado
solo por solapamiento en Y. **No volver a acotarlo por distancia en X**: el
bloqueo lateral (`abox.max_x <= bbox.x` + solape Y-Z) no tiene cota de
distancia, y una ventana en X descarta aristas reales (hay un test que lo
demuestra: `test_long_range_dependency.py`).

---

## 7. Handling Rules — jerarquía de precedencia

Definida en `handling_rules.py` y aplicada **una sola vez** en
`run_optimization`. De mayor a menor prioridad:

```
1. Override explícito del ítem   (celda del Excel con valor)
2. Default del plan              (PlanHandlingRules, del wizard)
3. Default de sistema            (por ItemType; PANEL → stackable = True)
```

Clave para entender el import: una celda **vacía** en el Excel deja
`stackable_override = None` (= *heredar*), **nunca** un `False` materializado.
Eso permite que al cambiar el default del plan después del import, el valor se
re-resuelva correctamente.

**Tilt (inclinación)** usa una cadena distinta y más corta: es **solo a nivel de
plan**, no tiene columna de Excel ni override de ítem. La UI está deshabilitada
pero el backend lo soporta.

---

## 8. API (33 endpoints, `backend/app/api/routes.py`)

| Grupo | Endpoints |
|---|---|
| Catálogo | `GET /containers`, `GET /load-spaces`, `GET /state` |
| Import | `POST /import-excel` (legacy), `POST /import-items-excel`, `GET /import-template/{profile}` |
| Packing | `POST /pack`, `POST /optimize-remaining` |
| Edición manual | `POST /validate-move`, `/apply-move`, `/remove-piece`, `/insert-piece`, `/rotate-piece`, `/turn-piece`, `/set-tilt`, `/lock-piece`, `/unlock-piece`, `/undo`, `/redo` |
| Reportes | `GET /export-excel`, `POST /report/validate`, `/report/steps`, `/report/container-pdf`, `/report/loading-guide-pdf`, `/report/unloading-guide-pdf`, `/report/unloading-guide-pdf-by-group`, `GET /report/unload-groups` |
| Planes | `POST /plans`, `GET /plans`, `GET /plans/{id}`, `PUT /plans/{id}`, `PATCH /plans/{id}`, `DELETE /plans/{id}` |

Los PDFs requieren que el **frontend** envíe las capturas 3D en base64: el
backend no renderiza 3D, solo arma el documento.

---

## 9. Visualización y manipulación 3D

Toda la parte 3D vive en el frontend. El backend **no renderiza nada**: solo
emite geometría en milímetros y valida lo que el usuario intenta hacer.

### 9.1 Stack de librerías

| Librería | Versión | Para qué se usa |
|---|---|---|
| `three` | ^0.185 | Motor 3D base (WebGL). Se usa directo para `Plane`, `Raycaster`, `Vector2/3`, `MeshBasicMaterial`. |
| `@react-three/fiber` (r3f) | ^9.7 | Renderer de React para Three.js: el `<Canvas>`, el grafo de escena como JSX, raycasting de eventos (`onPointerDown`, `onPointerMissed`), hook `useThree`. |
| `@react-three/drei` | ^10.7 | Helpers listos: `OrbitControls`, `Grid`, `Text` (troika), `Billboard`, `Edges`. |
| `three-stdlib` | (transitiva) | Solo para el **tipo** `OrbitControls` en TypeScript. |

**No se usan** `Html`, `Line`, ni ninguna librería de física. Las colisiones las
resuelve el backend (§9.8).

### 9.2 Sistema de coordenadas y escala

Los dos sistemas **no coinciden** y la conversión es explícita:

| | Backend (mm) | Three.js (unidades de escena) |
|---|---|---|
| Eje largo (puerta→fondo) | `x` | `X` |
| Eje ancho | `y` | **`Z`** |
| Eje alto | `z` | **`Y`** (Three.js es Y-up) |
| Origen | esquina mínima del contenedor | **centro** del contenedor |
| Anclaje de la pieza | esquina mínima (`x,y,z`) | **centro** de la caja |

Constantes en `frontend/src/config.ts` — única fuente de verdad:

```ts
export const SNAP_TOLERANCE_MM = 30;   // imán a caras vecinas
export const MOVEMENT_STEP_MM  = 10;   // rejilla de movimiento
export const SCENE_SCALE = 1 / 1000;   // mm → unidades three.js
```

Conversión real (`DragBox.tsx:64-66`), con swap **y↔z** y recentrado:

```ts
cx = (x + dx / 2 - container.length / 2) * SCENE_SCALE;  // three X
cy = (z + dz / 2)                        * SCENE_SCALE;  // three Y (altura)
cz = (y + dy / 2 - container.width  / 2) * SCENE_SCALE;  // three Z
// la geometría se construye como [dx*S, dz*S, dy*S]
```

Inversa (durante el arrastre, `useDragEngine.ts:83-84`):

```ts
x = hit.x / SCENE_SCALE + container.length / 2;
y = hit.z / SCENE_SCALE + container.width  / 2;
```

> ⚠️ `SCENE_SCALE` se aplica **en cada componente**, no con un `<group scale>`
> global. Si agregas un elemento nuevo a la escena, tenés que escalarlo vos.
> La misma conversión está repetida en `DragBox`, `CenterOfMassMarker`,
> `PalletBody` y el pivote de labels de `PieceMesh`.

### 9.3 Anatomía de la escena (`Scene3D.tsx`)

```tsx
<Canvas
  camera={{ position: [d, d * 0.8, d], fov: 45 }}   // d = max(L,W,H)*SCENE_SCALE*1.3
  gl={{ preserveDrawingBuffer: true, antialias: true }}
  dpr={[1, 2]}
  onPointerMissed={() => onSelectPiece(null)}
/>
```

- **`preserveDrawingBuffer: true` es obligatorio** — sin eso `toDataURL()`
  devuelve un canvas en blanco y los PDFs salen sin imagen (§9.9).
- **Luces**: `ambientLight` 0.7 + dos `directionalLight` (`[10,15,10]` a 0.8 y
  `[-10,8,-10]` a 0.3). Sin sombras.
- **`OrbitControls`**: `makeDefault`, `zoomToCursor`, y mapeo tipo CAD —
  **izquierdo = rotar, medio = pan, derecho = pan**. Se **desactiva por
  completo** (`enabled={!drag}`) mientras se arrastra una pieza. No hay límites
  polares ni de distancia.
- **Elementos auxiliares**: `ContainerFrame` (caja invisible + `Edges` gris),
  `DoorMarker` (franja naranja en `x=0` + billboard "PUERTA"), `Grid` del piso,
  `AisleMarker` (dibuja las `reserved_zones` que devuelve el backend) y
  `CenterOfMassMarker` (esfera rosa con `depthTest:false` y `renderOrder=999`
  para verse a través de la pila).

> **Parche anti-"orbit fantasma"** (`Scene3D.tsx:353-370`): si el usuario suelta
> el botón fuera del canvas, `OrbitControls` se queda escuchando `pointermove`
> para siempre. Un listener global despacha un `pointerup` sintético. No
> eliminar sin reproducir el bug.

### 9.4 Renderizado de piezas

Despacho por tipo en `LoadUnitBody`: si `item_type === "pallet"` → `PalletBody`
(divide el **mismo** bounding box en base de madera `#7a5230` — altura
`clamp(60 mm, 8 % · dz, 15 % · dz)` — más el bulto coloreado encima; **no agrega
altura**); en cualquier otro caso → `DragBox`.

Cada pieza es `meshStandardMaterial` transparente + `<Edges>`. El color sale de
`colorFromString()` (hash del string → `hsl(h, 60 %, 55 %)`) aplicado a la clave
que elija el usuario en *Color By* (system / group / priority / code).

**Prioridad de color** (gana el primero que aplique):

| Estado | Color |
|---|---|
| Arrastrando, posición válida | verde `#3ddc84` |
| Arrastrando, posición inválida | rojo `#ff4d4f` |
| Seleccionada | naranja `#ff6b35` |
| Paso actual de la guía | naranja `#ff6b35` |
| Atenuada por la guía | gris azulado `#6c7f8f` |
| Normal | color por `colorBy` |

Bordes: `#e0b429` si está **locked**, blanco si está seleccionada, `#1a1a1a`
normal. Opacidad: `0.82` normal, `0.5` atenuada, `0.15` futura sin guía.

**Etiquetas**: `<Text>` de drei (troika). Van dentro de un `<group>` con el
mismo pivote y rotación que la pieza, para quedar "impresas" sobre la cara y
acompañar la inclinación. El color del texto lo decide `pickLabelColors()`
(`utils/contrast.ts`) por **luminancia WCAG** con umbral 0.55: blanco con
contorno negro sobre fondo oscuro, o al revés.

**Guía paso a paso**: `guideStateById` mapea cada pieza a `past` / `current` /
`future`, y el comportamiento es **asimétrico** según la dirección — en carga,
las futuras se **ocultan** (`return null`) y las pasadas quedan translúcidas; en
descarga, al revés. Los badges numéricos de secuencia son un toggle aparte.

### 9.5 Inclinación (Tilt) — dos geometrías distintas

Este es el punto más sutil del renderizado. El backend guarda **dos cosas
diferentes** para una pieza inclinada:

- `base_dx / base_dy / base_dz` → la geometría **física real** de la caja.
- `dx / dy / dz` → la **envolvente AABB** de esa caja ya inclinada, calculada
  por `apply_tilt()` (`backend/app/core/orientation.py:76`):

```
nuevo_dz            = base_dz·cos(θ) + T·sin(θ)
nuevo_eje_thickness = base_dz·sin(θ) + T·cos(θ)      θ = |tilt_angle|
```

La envolvente es **simétrica**: inclinar +15° o −15° produce exactamente la misma
AABB. Por eso el signo se conserva por separado en `tilt_angle` y **nunca** se
intenta deducir desde `dx/dy/dz`.

**El frontend dibuja la geometría física, no la envolvente** (`DragBox.tsx:68-81`):
toma `base_*`, la rota sobre su propio centro por `tilt_angle`, con el mapeo
`tilt_axis === "y"` → rotación en X de Three.js, `"x"` → rotación en Z.
La AABB solo existe para que el packer y las validaciones razonen con cajas
alineadas a ejes.

### 9.6 Selección y arrastre (`hooks/useDragEngine.ts`)

- **Picking**: raycasting nativo de r3f vía `onPointerDown` en cada mesh.
  `handlePointerDown` hace `stopPropagation()` para que el evento no llegue a
  `OrbitControls`, selecciona la pieza y, si no está bloqueada, inicia el
  arrastre. Deselección: `onPointerMissed` del `<Canvas>`.
- **Proyección**: el puntero se lanza contra un **plano fijo del piso**
  (`THREE.Plane` con normal `(0,1,0)` y constante `0`) — *no* contra un plano a
  la altura de la pieza.
- **Nueva posición XY**: se conserva el offset de agarre (`grabOffsetX/Y`) para
  que la pieza no salte al cursor, y cada eje pasa por `snapAxis()`:
  1. redondea a múltiplos de `MOVEMENT_STEP_MM` (10 mm);
  2. busca el borde más cercano entre las caras de las piezas vecinas (y esas
     caras menos el tamaño propio, para alinear por el otro lado) dentro de
     `SNAP_TOLERANCE_MM` (30 mm);
  3. hace clamp a `[0, tamaño_contenedor − tamaño_pieza]`.
- **Altura Z**: la decide `findRestingZ()` (`geometry/geometry.ts:114`): reúne
  los niveles candidatos (el piso, más el tope de cada pieza que solape en XY),
  los prueba **de mayor a menor** contra `withinContainer` + `hasCollision` +
  `checkSupport`, y devuelve el primero válido. Si ninguno sirve, deja la pieza
  en el piso marcada como inválida, para que el usuario **vea dónde choca**.
- **Drag-to-insert**: no usa HTML5 drag. `UnloadedPanel` marca un
  `insertingItem`; la escena dibuja un *ghost* (opacidad 0.6, verde/rojo) en el
  centro del piso y su `onPointerDown` arranca un arrastre en modo `insert`.
  `Escape` cancela.
- **Commit**: al soltar, solo si el puntero se movió más de 4 px y la posición
  es válida, se llama a `POST /apply-move` o `POST /insert-piece`. Si el backend
  rechaza, la UI vuelve al último estado confirmado.

### 9.7 Validación dual: espejo optimista + autoridad real

`frontend/src/geometry/geometry.ts` es un **espejo en TypeScript** de
`backend/app/core/geometry.py` (`boxesOverlap`, `withinContainer`,
`checkSupport` con el mismo 80 % mínimo de apoyo).

```
Durante el arrastre  → solo el espejo TS   (60 fps, sin red, feedback inmediato)
Al soltar / commit   → SIEMPRE el backend  (validate_placement = fuente de verdad)
```

> ⚠️ **Si cambian las reglas físicas en `geometry.py`, hay que replicarlas a
> mano en `geometry.ts`.** No hay generación automática. Si divergen, el usuario
> ve verde y el backend rechaza (o peor: ve rojo algo que sí era válido).

### 9.8 API de manipulación (el backend es la autoridad)

Todos estos endpoints terminan en la **misma** función:
`core/manual_move.py: validate_placement()`, que reutiliza exactamente las
reglas del algoritmo automático. El usuario no puede violar a mano nada que el
optimizador respete.

| Endpoint | Body | Qué hace |
|---|---|---|
| `POST /validate-move` | `piece_id, x,y,z, dx,dy,dz` | Devuelve `{valid, reason}` sin modificar nada |
| `POST /apply-move` | idem | Valida y **aplica** (con undo) |
| `POST /insert-piece` | `unloaded_id, x,y,z, dx,dy,dz` | Coloca una pieza que estaba sin cargar |
| `POST /remove-piece` | `piece_id` | La manda a Unloaded Items |
| `POST /rotate-piece` | `piece_id` | Cicla a la siguiente orientación válida (tecla **R**) |
| `POST /turn-piece` | `piece_id` | Cicla en el otro sentido (tecla **T**) |
| `POST /set-tilt` | `piece_id, tilt_angle` (±30° máx) | Cambia la inclinación |
| `POST /lock-piece` · `/unlock-piece` | `piece_id` | Fija la pieza (Optimize la respeta) |
| `POST /undo` · `/redo` | — | Historial de edición |

**Orden de validación en `validate_placement`** (importa: define *qué* error ve
el usuario, porque devuelve el primero que falla):

1. Orientación válida según la política (`panel_edge_only`, `upright`, …)
2. Dentro del contenedor
3. No invade zonas reservadas (pasillo central)
4. **Sin colisión** → ver §9.9
5. Respeta la separación mínima (clearance)
6. Apoyo suficiente (`check_support`, ≥ 80 %)
7. Peso de apilado (`check_stack_weight`)
8. Peso por eje si el espacio de carga es un vehículo de carretera

`rotate` y `turn` mantienen `x,y,z` y solo cambian `dx,dy,dz` + la etiqueta de
orientación; si la nueva orientación no valida, devuelven **409** y no cambian
nada.

**Colisión con piezas inclinadas** (`core/tilt_collision.py`) — dos fases:

```
AABB broad phase (barato)
   └─ sin solape de envolventes ......... no hay colisión, listo
   └─ hay solape Y alguna pieza inclinada
        └─ narrow phase OBB/SAT (15 ejes: 3+3 caras + 9 productos cruzados)
             └─ penetración real > tol ... COLISIÓN
             └─ solo contacto ........... válido
```

La tolerancia se trata como **penetración negativa**: dos cajas que se tocan (o
casi) **no** son colisión. Como CUBOX solo permite tilt sobre **un** eje, la
matriz de rotación se construye de un único ángulo conocido — no hacen falta
quaternions ni un motor de física.

### 9.9 Captura de imágenes para los PDF

El backend no renderiza 3D: el frontend le manda capturas PNG en base64.
`Scene3D` expone una función por `captureRef`:

```ts
captureRef.current = async (useGuideCameraView?: boolean) => Promise<string>
```

1. Reposiciona la cámara:
   - **Container Report** → `computeFitView()`: `distancia = max(L,W,H)·1.3`,
     posición `(d, d·0.8, d)`, target `(0, H/2, 0)` — la misma del botón
     *Fit View*.
   - **Guía paso a paso** → `computeGuideCameraView()`: yaw **40°** y pitch
     **15°** fijos, distancia exacta para encuadrar el contenedor con 6 % de
     margen.
2. Solo en modo guía: `fov → 30`, `printMode = true` (la etiqueta se reduce al
   Code), `pixelRatio = max(dpr,2)·1.5` y redimensiona el canvas al **aspect
   560/215** del slot del PDF.
3. Espera **2 `requestAnimationFrame` anidados** después de mover la cámara y
   otros 2 después del resize (4 en total en modo guía). Sin esa espera,
   `toDataURL()` le gana la carrera al render y sale una imagen en blanco.
4. `gl.domElement.toDataURL("image/png")` → `App.tsx` hace `.split(",")[1]`
   para mandar base64 puro.
5. Restaura fov, pixelRatio, tamaño, aspect y `printMode` inmediatamente.

> ⚠️ **Acoplamiento a cuidar**: el aspect `560/215` debe coincidir con
> `_STEP_IMAGE_WIDTH` / `_STEP_IMAGE_HEIGHT` en `backend/app/core/pdf_export.py`.
> Si cambia uno, hay que cambiar el otro o las imágenes salen deformadas.

> El flag `useGuideCameraView` se pasa como **argumento explícito** (no leyendo
> props ni refs) porque el reconciler de r3f lo entregaba un paso atrasado. Está
> documentado en el código; no "simplificarlo".

### 9.10 Performance y limitaciones conocidas

Hoy hay **muy poca optimización** y conviene saberlo antes de escalar:

- **No hay instancing**, **no hay `React.memo`**, **no hay `useFrame`**.
- Cada pieza monta 1-2 `mesh` + `Edges` + hasta 3 `<Text>` de troika (cada uno
  genera su propio atlas de glifos → es lo más caro).
- `guideStateById` y `sequenceOrder` se recalculan en cada render.
- **Punto más caro**: durante un arrastre, `updatePointer` hace `setDrag` en
  cada `pointermove`, lo que provoca un re-render completo de la escena por
  frame.

Mitigaciones que **sí** existen: `OrbitControls` desactivado durante el drag,
`dpr=[1,2]`, ocultar piezas fuera del paso de guía, `showLabels`/`showSequence`
como toggles, y `useCallback` + refs en `useDragEngine` para no reasignar
objetos de Three.js por evento.

**Candidato claro de mejora si se necesita escalar:** memoizar `PieceMesh` y
mover el estado del drag a un ref para no re-renderizar toda la escena por frame.

### 9.11 Deuda detectada

- **`validateMove()` (`POST /validate-move`) está definido en `api/client.ts`
  pero ningún componente lo consume.** El arrastre valida localmente contra el
  espejo TS y hace commit directo con `apply-move`. El endpoint existe y
  funciona en el backend; o se usa (para feedback autoritativo durante el
  arrastre) o se marca como no usado en el frontend.

---

## 10. Cómo levantar el proyecto

```bash
# Backend
cd backend
py -m venv venv
./venv/Scripts/pip install -r requirements.txt
./venv/Scripts/python -m pytest app/tests -q        # 833 tests
./venv/Scripts/python -m uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm install
npm run dev        # Vite en http://localhost:5173
```

API en `http://localhost:8000` · docs interactivos en `/docs`.

---

## 11. Convenciones del proyecto (respetarlas)

1. **`geometry.py` es la única fuente de verdad física.** Nunca reimplementar
   `check_support` ni `check_stack_weight` en otro módulo.
2. **`optimize.py` es el único punto de ruteo.** No duplicar decisiones de motor
   en `routes.py` ni en el frontend.
3. **No reescribir `panel_module_solver.py`.** Está endurecido con varios bugs
   reales corregidos. Se extiende por orquestación externa, no por edición.
4. **Los comentarios citan el pedido/fase que originó cada decisión.** Al tocar
   algo, mantener esa trazabilidad: explican *por qué*, no *qué*.
5. **Los tags `ENGINE_*` no se persisten**, solo se loguean. Nota: hoy no hay
   configuración de logging, así que `logger.info` no sale por consola — si
   necesitas confirmar qué motor corrió, configúralo o verifícalo por la
   geometría del resultado.
6. **Determinismo**: mismos ítems + misma configuración → mismo resultado.

---

## 12. ⚠️ Pendiente de implementar — Panels & Fragile: condicionales de la macro / template Excel

> **Estado: NO IMPLEMENTADO.** Esta sección son **anotaciones de requerimiento**
> para quien tome el proyecto. Hoy CUBOX asume que *cada fila del Excel es un
> bulto ya consolidado*. Lo que sigue define cómo se debe construir ese Excel.

### 11.1 El problema

Una ventana o puerta fabricada no es una sola pieza: está compuesta por varios
**cuadros** (el marco, las hojas). Lo que se carga en el contenedor no son los
cuadros sueltos, sino los **bultos** en que se embalan — y cuántos bultos salen
de cada unidad **depende del sistema**.

La macro (paso previo, en el Excel / ERP de producción) debe traducir:

```
piezas de fabricación (marcos + hojas, por sistema)
        │
        ▼   ← aquí van las condicionales de abajo
filas del template PANEL de CUBOX (1 fila = 1 bulto físico)
```

El template PANEL actual espera estas columnas
(`core/import_templates.py`):

```
Code · Quantity · Width · Height · Thickness · Weight ·
Description · System · Group · Stackable · Max Stack Weight ·
Delivery Sequence · Load Priority
```

### 11.2 Regla general

**La condicional se resuelve por SISTEMA.** Para cada unidad fabricada, el
sistema determina (a) cuántas filas se generan y (b) qué cuadro aporta las
medidas.

### 11.3 Sistemas operables — todo va unido → **1 fila, solo el cuadro tipo MARCO**

- Dream Window
- Casement Out
- Tilt & Turn
- Horizontal Roller
- Single Hung

La hoja va montada dentro del marco, se envía como un solo bulto. **Solo vale la
medida del cuadro de tipo marco**; los cuadros de hoja se ignoran para el
cubicaje.

### 11.4 Sistemas fijos — todo va unido → **1 fila, solo el cuadro tipo MARCO**

- Picture Window
- Store Front
- Window Wall
- Picture Window RT

Mismo criterio que el anterior: un solo bulto, la medida es la del marco.

### 11.5 Puertas abatibles (French Door, sistema Vintage) — **depende de las hojas**

| Caso | Bultos | Filas a generar |
|---|---|---|
| **1 hoja** | 1 | Solo el cuadro **MARCO** (va todo unido) |
| **2 hojas** | 2 | (1) cuadro **MARCO** → contiene marco + **hoja pasiva**<br>(2) cuadro de la **HOJA ACTIVA** → va suelta |

La hoja activa es la que viene **marcada como tal** en los datos de origen. El
marco y la hoja pasiva viajan juntos en un bulto; la hoja activa viaja en otro.

### 11.6 SGD / Sliding Glass Door — **hojas desarmadas + marco suelto**

En SGD **siempre** van las hojas desarmadas y el marco suelto. Lo que importa
para el cubicaje es la **medida más larga**: esa define la longitud con la que
se va a armar el bulto.

### 11.7 Puntos que quien implemente esto **debe definir antes de codificar**

Estas ambigüedades no están resueltas y bloquean la implementación:

1. **¿De dónde sale la clasificación?** ¿Qué campo del origen marca un cuadro
   como *tipo marco*, *hoja*, o *hoja activa*? ¿Es un campo explícito del ERP/A+W
   o hay que inferirlo?
2. **Normalización de nombres de sistema.** Los datos reales **no coinciden
   literalmente** con la lista canónica. En el plan real analizado aparecen:
   `KA WINDOW WALL`, `KA FRENCH DOOR`, `VINTAGE`, `Sliding glass`,
   `Picture Window`, `Window Wall`, `Tilt & Turn`, `Casement out KA`, `Totem`.
   Hay prefijos (`KA`), variantes de capitalización (`Casement out` vs
   `Casement Out`), un nombre distinto para SGD (`Sliding glass`) y sistemas que
   no están en ninguna de las 4 familias (`Totem`). **Hace falta una tabla de
   normalización y una política explícita para sistemas desconocidos** (¿fallar
   el import? ¿tratarlo como "todo unido" por defecto?).
3. **Reparto del peso** cuando una unidad se parte en 2 bultos (marco+pasiva vs
   activa): ¿peso real por bulto o prorrateado?
4. **Identidad de las filas generadas**: qué `Code`, `Description`, `Group` y
   `Delivery Sequence` recibe cada bulto, y cómo se relacionan entre sí los
   bultos que salen de la misma unidad.
5. **Cohesión**: si los 2 bultos de una puerta deben viajar juntos,
   probablemente convenga que compartan `Group` — así **Keep Groups Together**
   los mantiene en la misma zona del contenedor. Decidir si esto es obligatorio
   o preferencia.
6. **SGD, detalle geométrico**: cuántas filas exactamente se generan (¿una por
   bulto armado? ¿marco y hojas por separado?) y qué se pone en `Width`,
   `Height` y `Thickness` cuando la regla dice que solo manda "la medida más
   larga".
7. **Espesor del bulto consolidado**: al unir marco + hoja pasiva, el
   `Thickness` del bulto **no** es el del cuadro marco solo. Definir cómo se
   calcula.
8. **Dónde vive la macro**: ¿queda fuera de CUBOX (Excel/ERP) o se incorpora al
   backend como un perfil de import especializado? Si entra a CUBOX, el lugar
   natural es `core/import_items.py` + un template propio, no el solver.

> **Importante:** estas reglas afectan *qué se le entrega a CUBOX*, no cómo
> CUBOX empaqueta. El motor de packing **no debe** aprender de sistemas ni de
> marcos y hojas: sigue recibiendo bultos y sigue siendo agnóstico.

---

## 13. Otros pendientes conocidos

- **Tilt**: backend funcional, UI deshabilitada a propósito (`fe12330`). Si se
  reactiva, recordar que hoy fuerza el fallback al motor legacy.
- **Clearance > 0**: fuerza fallback al legacy en Box y Panel. Resolver requiere
  *tallar* el espacio libre, no solo verificar reactivamente.
- **Build Pallets**: quinto Load Type, marcado "Coming Soon" en el wizard.
- **Level 3+**: bloqueado por diseño hasta resolver la carga transitiva.
- **Performance**: medida, no optimizada. Vía HTTP, ~2,3–4 s por optimización
  (n = 40…400). El costo dominante está en `select_complete_clusters`
  (repacking acotado O(clusters²)), no en el coordinador de Sections.
- **Logging**: no hay configuración; los `logger.info` de motor no se ven.
- **`README.md` raíz**: obsoleto, conviene reemplazarlo por un puntero a este
  documento.
