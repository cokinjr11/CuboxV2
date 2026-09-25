# CUBOX — Checklist de integración con WEB_INTERFACES_CORE

Seguimiento del trabajo para que CUBOX pueda **llamarse desde las interfaces
.NET de NAGSA** sin dejar de **funcionar en local** como hasta hoy.

- **Contexto previo:** [`Docs_Guia/INTEGRACION_NAGSA.md`](../Docs_Guia/INTEGRACION_NAGSA.md) (análisis original sobre V1) y [`DOCUMENTACION.md`](../DOCUMENTACION.md) (estado de V2).
- **Rama CUBOX:** `Joaquin-Integracion_NAGSA` (desde `main` @ `6439514`)
- **Rama .NET:** `Joaquin-Integracion_Cubox` (desde `Produccion`) — *pendiente de crear en la Etapa B*

Leyenda: `[ ]` pendiente · `[x]` hecho · 🔍 punto de revisión (se para y se valida antes de seguir)

---

## Decisiones tomadas

| # | Decisión | Motivo |
|---|---|---|
| D1 | **Dos modos de API en el mismo backend:** `/api` (local, con estado, sin cambios visibles) y `/api/v1` (integrado, sin estado) | CUBOX sigue sirviendo en local y a la vez se puede llamar desde .NET |
| D2 | **Mismo estado serializado en los dos modos** (`PlanState` = el JSON que ya usa SQLite) | Un plan puede pasar del SQLite local al histórico de .NET y viceversa |
| D3 | **.NET trata el estado como JSON opaco** (`JsonElement`); solo tipa entradas (ítems, config) y un resumen | Menor superficie de acoplamiento; sigue la convención del repo (records a mano, sin NSwag) |
| D4 | **Una responsabilidad por función/módulo** (ver hallazgos abajo) | Evitar el patrón "guardar cliente + enviar correo" |
| D5 | **Ninguna regla de negocio cambia, solo de lugar.** Los tests existentes son la prueba | La lógica está endurecida con bugs reales corregidos |
| D6 | `optimize.run_optimization()` y `import_items._parse_row()` **quedan fuera** del bloque A | Son motor/import; se tratan aparte (ver "Deuda anotada") |

## Regla de dependencias (objetivo)

```
api/  ──►  local/  ──►  services/  ──►  core/
  └──────────────────────►  services/
local/ ──► core/plan_store.py
```

Nunca al revés: `services/` no conoce HTTP, sesión ni SQLite; `core/` no conoce a nadie de arriba.

## Hallazgos de responsabilidad múltiple (a corregir en el bloque A)

| # | Hoy (`api/routes.py` salvo indicación) | Qué mezcla | Dónde queda | Hecho |
|---|---|---|---|---|
| H1 | `pack()` | calcula + deriva + escribe sesión + resetea historial | `services/packing.pack` + `services/derivation` + `local/session` | [ ] |
| H2 | `create_plan()` | **llama al endpoint `pack()`** + persiste + fija `plan_id` | ruta compone `pack` → `session.replace` → `plan_library.create` | [ ] |
| H3 | `optimize_remaining()` | muta config de sesión + valida locked + convierte + optimiza + deriva + historial | `plan_config.apply_overrides` + `locked_pieces.validate` + `piece_mapping` + `packing.optimize_remaining` | [ ] |
| H4 | 9 endpoints de edición | aplicar edición + historial + derivados + leer sesión | `services/editing.*` (solo aplicar); historial en `session.commit`; derivados en `derivation` | [ ] |
| H5 | `remove_piece()` / `insert_piece()` | construyen `UnloadedItem`/`PlacedPiece` a mano (20 campos) | `services/piece_mapping` | [x] A1 |
| H6 | `get_plan()` | lee SQLite + parsea + reemplaza sesión + deriva + arma config de UI | `plan_library.open` + `session.replace` + `derivation` + mapper de respuesta | [ ] |
| H7 | `save_plan()` | valida sesión + **cambia reglas** + lee SQLite + serializa + escribe | `plan_config.apply_overrides` separado de `plan_library.save` | [ ] |
| H8 | `_guide_pdf()` / guía por grupo | gate + pasos + conteo de imágenes + **nombre del plan desde SQLite** + PDF + respuesta HTTP | `services/reporting` (recibe `plan_label`) + `api/file_responses` | [ ] |
| H9 | `_compute_report_steps()`, `_resolve_export_validation()`, `_refresh_derived()` | lógica que lanza `HTTPException` y lee el global | `services/reporting` / `derivation` con `PlanOpError` | ◐ servicios listos (A2); `routes.py` los usa en A3 |
| H10 | `PlanRepository()` a nivel de módulo | crea el archivo SQLite **al importar** | `local/repository.py` perezoso | [ ] |
| H11 | `main.py` | CORS fijo + montaje de routers + health | `create_app(settings)` | [ ] |
| H12 | `core/plan_service.build_plan_snapshot()` | serializa estado + calcula resumen de listado | `PlanState` (serializar) + `summarize` (resumen); la función queda como composición | [x] A1 |

Revisado y **sin problemas** (no se toca): `core/*` no importa FastAPI ni lee estado global; `plan_store.py` solo SQL; `history.py` solo undo/redo; `manual_move.py`/`geometry.py` solo validan; `pdf_export.py`/`excel_export.py` solo construyen documentos.

---

## Estructura objetivo (backend)

```
app/
  settings.py              leer configuración del entorno. Nada más.
  main.py                  create_app(settings): monta routers/CORS/seguridad según configuración

  services/                LÓGICA DE APLICACIÓN — sin HTTP, sin sesión, sin SQLite. Puras.
    plan_state.py          modelo PlanState + serialización/deserialización
    derivation.py          derive_result(state) → PackingResult (métricas, secuencias, warnings, road weight)
    packing.py             pack(request) · optimize_remaining(state, items) → estados candidatos
    locked_pieces.py       validate_locked_pieces(state)
    plan_config.py         apply_overrides(state, cambios)
    editing.py             move/remove/insert/tilt/rotate/turn/lock/unlock → estado nuevo
    piece_mapping.py       PlacedPiece ↔ UnloadedItem ↔ WindowItem
    reporting.py           steps() · export_gate() · build_*_pdf/excel(state, …, plan_label) → bytes
    errors.py              PlanOpError(code, message)

  local/                   SOLO MODO LOCAL
    session.py             LocalSession: PlanState + historial + plan_id
    plan_library.py        crear/abrir/guardar/renombrar/borrar/listar planes (SQLite)
    repository.py          repositorio SQLite perezoso

  api/                     SOLO HTTP
    routes.py              /api   — mismas rutas y respuestas de hoy
    routes_v1.py           /api/v1 — sin estado
    error_mapping.py       PlanOpError → HTTP (400/404/409/422 actuales)
    file_responses.py      respuestas de archivo (PDF/ZIP/xlsx, Content-Disposition)
    security.py            x-api-key para v1
```

**Compatibilidad obligatoria con los tests:** `app.api.routes` debe seguir exponiendo `_current_state` (mismas claves), `_get_active_state` y `get_plan_repository`.

---

## Etapa A — CUBOX (Python)

### A0. Preparación
- [x] Crear rama `Joaquin-Integracion_NAGSA` desde `main`
- [x] Línea base: suite completa en verde — **833 passed en 119 s** (Python 3.14.4 global; no hay venv en `backend/`)
- [x] Este checklist en `docs/`

### A1. Estado y conversiones — `services/plan_state.py` + `services/piece_mapping.py`
- [x] Crear paquete `app/services/` (docstring con las reglas de la capa)
- [x] `core/plan_schema.py`: `SCHEMA_VERSION` + `UnsupportedSchemaVersionError` + `CorruptPlanStateError` salen de `plan_store.py` (no son de SQLite); `plan_store.py` los re-exporta
- [x] `PlanState` (Pydantic): `schema_version`, `load_space`, `plan_handling_rules`, `clearance`, `optimization_mode`, `weight_balance_mode`, `loading_anchor`, `reserved_zones`, `placed`, `unloaded`
- [x] Serialización de `PlanState` produce el mismo JSON que hoy guarda `build_plan_snapshot` (test de igualdad **al parsear**; no byte a byte porque `ReservedZone` hoy guarda enteros como `0` y Pydantic los emite como `0.0` — mismo valor, planes viejos abren igual)
- [x] Deserialización equivalente a `parse_plan_state` (mismos errores: `UnsupportedSchemaVersionError`, `CorruptPlanStateError`)
- [x] `core/plan_service.py` usa `PlanState`; `build_plan_snapshot` queda como composición serializar (`plan_state`) + resumir (`services/plan_summary.py`) (H12)
- [x] `piece_mapping.py`: `placed_to_item`, `unloaded_to_item`, `placed_to_unloaded`, `unloaded_to_placed` (movidas desde `routes.py`, H5)
- [x] `routes.py` usa `piece_mapping` en `optimize_remaining`, `remove_piece`, `insert_piece` y `reserved_zones_to_state` en vez de `_reserved_zones_out` (−110 líneas)
- [x] Tests unitarios: `test_services_plan_state.py` (15) y `test_services_piece_mapping.py` (5), sin HTTP ni SQLite
- [x] Suite completa en verde, sin modificar tests existentes — **853 passed** (833 + 20 nuevos)

### A2. Servicios — resto de `services/`
- [x] `errors.py`: `PlanOpError(kind, detail)` con `PlanOpErrorKind` semántico (INVALID_INPUT / NOT_FOUND / CONFLICT / NOT_EXPORTABLE) — sin números HTTP; el mapeo a 400/404/409/422 va en `api/error_mapping.py` (A3)
- [x] `derivation.py`: `derive_result(state)` + `complete_result()` + `EvaluatedPlan(state, result)` (ex `_refresh_derived` + `_apply_sequence_fields` + road weight) (H9)
- [x] `load_spaces.py`: `resolve_load_space()` (ex `_resolve_load_space`) — módulo propio, no es responsabilidad de packing
- [x] `packing.py`: `pack(request)` y `optimize_remaining(state)` → `PackOutcome` (mejor plan + alternativas + `state_for(alternativa)`), sin tocar sesión (H1, H3)
- [x] `locked_pieces.py`: validación de piezas bloqueadas (H3)
- [x] `plan_config.py`: `apply_overrides()` (None = no cambiar) y `set_plan_handling_rules()` (reemplazo explícito, None = sin reglas) — dos semánticas distintas que hoy usan optimize-remaining y el autosave (H3, H7)
- [x] `editing.py`: `validate_move`, `move_piece`, `remove_piece`, `insert_piece`, `set_tilt`, `rotate_piece`, `turn_piece`, `lock_piece`, `unlock_piece` — copia profunda, nunca muta la entrada (H4)
- [x] `reporting.py`: `validate_plan`, `export_gate`, `export_override`, `compute_steps`, `report_steps`, `unload_groups`, `require_groups`, `build_excel`, `build_container_report`, `build_loading_guide`, `build_unloading_guide`, `build_unloading_guides_by_group` (devuelve `{group: pdf}`; el ZIP es de `api/file_responses`). Reciben `PlanLabel` en vez de leer SQLite (H8, H9)
- [x] Tests unitarios de cada servicio (sin HTTP): `test_services_units.py` (22)
- [x] Test de la regla de dependencias: `test_services_layering.py` — falla si un módulo de `services/` importa FastAPI, sesión, SQLite o `app.api`/`app.local`
- [x] **Paridad temporal** `test_services_parity_legacy.py` (32): mismas operaciones por `/api` actual y por `services/` → mismo JSON y mismos errores (pack en los 4 modos, custom load space, optimize-remaining con overrides, las 9 ediciones con éxito y error, validación, pasos auto/manual, grupos, gate de export, precondiciones de guías). Se reemplaza en A6 por paridad `/api` vs `/api/v1`
- [x] Suite completa en verde — **919 passed** (853 + 22 unitarios + 32 paridad + 12 de capas); ningún test existente modificado

### A3. Modo local — `local/` + `api/routes.py` delgado
- [ ] Mover `core/plan_service.py` → `local/` (traduce `PlanRow` ↔ estado: es del modo local). **Hoy es la única dependencia `core → services`**, temporal desde A1
- [ ] `local/session.py`: `LocalSession` (get / replace / commit con historial / undo / redo / plan_id) sobre el dict `_current_state`
- [ ] `local/repository.py`: repositorio SQLite perezoso (H10)
- [ ] `local/plan_library.py`: create / open / save / rename / delete / list_recent (H2, H6, H7)
- [ ] `api/error_mapping.py` y `api/file_responses.py`
- [ ] `api/routes.py` reescrito como adaptador: leer request → servicio/sesión/biblioteca → respuesta (H1–H9)
- [ ] Decidir atomicidad de `/api/optimize-remaining`: hoy aplica los overrides a la sesión ANTES de validar las piezas bloqueadas, así que un 409 deja la configuración cambiada. Opción A: mantenerlo idéntico. Opción B: atómico (si falla, no cambia nada)
- [ ] Nombres de compatibilidad intactos (`_current_state`, `_get_active_state`, `get_plan_repository`)
- [ ] Smoke manual del frontend actual (wizard → optimize → editar → PDF → reabrir plan)
- [ ] 🔍 **Revisión A1–A3: suite completa en verde sin modificar ningún test existente**

### A4. Modo integrado — `api/routes_v1.py`
Contrato general: entrada `{state, …parámetros}` → salida `{state, result}`. Fuera de v1: `/plans`, `/undo`, `/redo`, `/state`.

**v1a — camino corto**
- [ ] `GET /api/v1/health` → `{status, version, schema_version}`
- [ ] `GET /api/v1/load-spaces`
- [ ] `GET /api/v1/import-template/{profile}`
- [ ] `POST /api/v1/import/preview` (multipart)
- [ ] `POST /api/v1/pack` → `{state, result, alternatives[{strategy, score, breakdown, state, result}]}`
- [ ] `POST /api/v1/result` (rehidratar un estado guardado)
- [ ] `POST /api/v1/validate`
- [ ] `GET /api/v1/openapi.json` (solo el contrato v1)

**v1b — edición**
- [ ] `POST /api/v1/optimize-remaining`
- [ ] `POST /api/v1/edit/validate-move`
- [ ] `POST /api/v1/edit/apply-move`
- [ ] `POST /api/v1/edit/remove-piece`
- [ ] `POST /api/v1/edit/insert-piece`
- [ ] `POST /api/v1/edit/set-tilt`
- [ ] `POST /api/v1/edit/rotate-piece`
- [ ] `POST /api/v1/edit/turn-piece`
- [ ] `POST /api/v1/edit/lock-piece`
- [ ] `POST /api/v1/edit/unlock-piece`

**v1c — reportes**
- [ ] `POST /api/v1/report/steps`
- [ ] `POST /api/v1/report/unload-groups`
- [ ] `POST /api/v1/report/container-pdf`
- [ ] `POST /api/v1/report/loading-guide-pdf`
- [ ] `POST /api/v1/report/unloading-guide-pdf`
- [ ] `POST /api/v1/report/unloading-guide-pdf-by-group`
- [ ] `POST /api/v1/export-excel`

### A5. Configuración y seguridad
- [ ] `settings.py` (solo `os.environ`, sin dependencias nuevas)
- [ ] `CUBOX_API_KEY` → si tiene valor, `/api/v1` exige `x-api-key` (401 si falta o no coincide)
- [ ] `CUBOX_CORS_ORIGINS` → lista separada por comas; default = regex localhost actual
- [ ] `CUBOX_ENABLE_LOCAL_API` → `false` desmonta `/api` y no crea SQLite
- [ ] `main.py` → `create_app(settings)` (H11)
- [ ] Frontend: `VITE_API_BASE` en `client.ts` (default `http://localhost:8000/api`) + `.env.example`

### A6. Tests nuevos
- [ ] Contrato: `openapi_v1.json` guardado en el repo; el test falla si cambia sin regenerarlo
- [ ] Sin estado: dos clientes intercalados no interfieren y `_current_state` no cambia
- [ ] Paridad: mismos ítems por `/api` y `/api/v1` → mismas piezas y métricas
- [ ] Seguridad: 401 sin clave cuando está configurada; 200 con clave
- [ ] Ida y vuelta: estado de v1 ↔ SQLite local
- [ ] Modo servidor: con `CUBOX_ENABLE_LOCAL_API=false`, `/api/*` responde 404 y no se crea `cubox.db`
- [ ] Medición de payload con `examples/Muestra Real.xlsx` (anotar KB de `pack` y de una edición)
- [ ] 🔍 **Revisión v1a** (probado desde `/docs` o curl)

### A7. Documentación
- [ ] `DOCUMENTACION.md`: modos local/integrado, mapa de capas, regla de dependencias, contrato v1, variables de entorno, cómo levantar en modo servidor
- [ ] `README.md` raíz → puntero a `DOCUMENTACION.md`
- [ ] Este checklist actualizado

---

## Etapa B — Service en .NET (WEB_INTERFACES_CORE)

- [ ] Crear rama `Joaquin-Integracion_Cubox` desde `Produccion`
- [ ] `SERVICIOS/COMPANY/INTERCOMPANY/CUBOX/CuboxApiOptions.cs` (`SectionName = "CuboxApi"`, `BaseUrl`, `ApiKey`, `TimeoutSeconds = 180`, sin fallback a localhost)
- [ ] `CuboxApiService.cs`: typed HttpClient sin interfaz (molde `ClaudeApiService`), un método por endpoint v1, records en el mismo archivo, `AsegurarExitoAsync` con body; estado/resultado como `JsonElement`; archivos como `(byte[], nombre, tipo)`
- [ ] `ICuboxService.cs` / `CuboxService.cs` (scoped): `CalcularCubicajeAsync`, `ValidarExcelAsync`, `AplicarEdicionAsync`, `GenerarReporteAsync` — sin HANA/SQL por ahora
- [ ] Registro en `Program.cs` (`Configure<>`, `AddHttpClient<>`, `AddScoped<>`) junto al bloque de Claude
- [ ] Sección `CuboxApi` en `appsettings.json` con valores vacíos (la clave real va en la config del servidor, no en el repo)
- [ ] `CuboxController` mínimo sin vista: `Health` y `Calcular` con envelope `{ success, … }`
- [ ] 🔍 **Revisión B:** `Calcular` devuelve un cubicaje real contra CUBOX con `CUBOX_ENABLE_LOCAL_API=false` y clave
- [ ] Extender el service con v1b y v1c a medida que se entregan

## Etapa C — Cuando se defina la interfaz (fuera de alcance por ahora)

- [ ] Modo embebido del frontend (usa v1, estado en el navegador, undo/redo en cliente)
- [ ] Vista con iframe + `postMessage` (visor ↔ portal)
- [ ] Despliegue en servidor (IIS + HttpPlatformHandler, o NSSM)
- [ ] Fila en `DB_SEGURIDAD.PANTALLA` (`Cubox/Index.NCore`)
- [ ] Origen de datos desde HANA + histórico de cubicajes en .NET
- [ ] Documentos oficiales (si requieren membrete/numeración)

---

## Deuda anotada (fuera del bloque A)

- [ ] 🐞 **Bug encontrado en A1 (existe en `main`, no lo introduce este trabajo): el motor de paneles `PANEL_MODULE_POCKET_V2` excede el payload máximo.** 20ft (28.180 kg) con 16 paneles de 2.500 kg: sin pasillo coloca 12 = 30.000 kg; con pasillo central coloca 16 = 40.000 kg. El motor de cajas (`BOX_EMS_V2`) sí respeta el límite (11 = 27.500 kg). Plan Validation lo detecta (NOT READY, bloquea exports por default), pero el optimizador no debería producirlo. Corregir por orquestación externa (convención: no reescribir `panel_module_solver.py`) y con test de regresión. Repro: `run_optimization` con esos ítems, `OptimizationMode.BEST_SPACE`.

- [ ] `core/optimize.run_optimization()` (144 líneas: resolución de reglas + selección de motor + dos caminos + preselección Keep Groups + merge de excluidos + dedupe). Extracción mecánica a helpers privados, sin cambio de comportamiento.
- [ ] 🐢 **Lentitud encontrada en A2 (se reproduce también por la ruta actual `/api`, así que no la introduce `services/`; falta confirmarla sobre `main`):** `optimize-remaining` sobre un plan armado con Keep Systems tarda ~25 s con 13 paneles (20ft, pasillo central, `default_stackable`), mientras que `pack` con los mismos ítems tarda 0,02 s y `optimize-remaining` sobre un plan Best Space también 0,02 s. Mismo motor (`PANEL_MODULE_POCKET_V2`). Repro: `_PANELS` de `test_services_parity_legacy.py` con `optimization_mode=keep_systems` → lock 1 pieza → `optimize_remaining` con `best_space`. Pack Keep Systems con esos ítems: ~4,7 s
- [ ] `core/import_items._parse_row()` (188 líneas). Se aborda junto con §12 de `DOCUMENTACION.md` (condicionales marco/hoja).
- [ ] `WEB_INTERFACES_CORE/appsettings.json` tiene la API key de `ClaudeApi` commiteada. No repetir con `CuboxApi`.

## Bitácora

| Fecha | Paso | Nota |
|---|---|---|
| 2026-09-25 | A0 | Rama creada, checklist inicial. Línea base 833 passed |
| 2026-09-25 | A1 | `core/plan_schema.py`, `services/plan_state.py`, `services/plan_summary.py`, `services/piece_mapping.py`; `plan_service`/`plan_store`/`routes` adaptados. 853 passed. Encontrado bug de payload en motor de paneles (ver Deuda) |
| 2026-09-25 | A2 | `services/`: errors, derivation, load_spaces, plan_config, locked_pieces, packing, editing, reporting. Paridad con `/api` probada (32 tests). 919 passed. `/api` todavía no usa estos servicios (eso es A3). Anotada lentitud de optimize-remaining tras Keep Systems |
