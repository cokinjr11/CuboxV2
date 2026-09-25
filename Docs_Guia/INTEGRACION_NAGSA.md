# CUBOX ↔ WEB_INTERFACES_CORE — Análisis técnico y plan de integración

> **Documento de traspaso.** Consolida el análisis de ambos sistemas, la investigación de
> estándares, la arquitectura recomendada y el plan de implementación por fases.
>
> | | |
> |---|---|
> | **Fecha del análisis** | 14–17 de septiembre de 2026 |
> | **Autor** | Joaquín Cabrera, con Claude Code |
> | **CUBOX analizado en** | repo `CuboxV1`, rama `Joaquin-Integracion_NAGSA`, commit `d7c9f4c` |
> | **WEB_INTERFACES_CORE analizado en** | repo `WEB_INTERFACES_CORE-RubrosN`, rama `Produccion`, commit `236faa8e` (*Version 155*) |

---

## 0. Cómo usar este documento desde una versión más nueva de CUBOX

Este análisis refleja el estado del código en el commit indicado arriba. Si estás leyéndolo
desde una versión posterior, **verifica primero estos cinco puntos**, que son los que
determinan si el plan sigue siendo válido tal cual:

| # | Qué verificar | Dónde | Por qué importa |
|---|---|---|---|
| 1 | ¿Sigue existiendo el estado global `_current_state`? | `backend/app/api/routes.py` | Es el bloqueante principal de toda la integración (§6.1) |
| 2 | ¿Las rutas están versionadas (`/api/v1/`)? | `backend/app/api/routes.py` | Determina si la Fase 1 ya está parcialmente hecha |
| 3 | ¿`API_BASE` sigue hardcodeado a un dev tunnel? | `frontend/src/api/client.ts` | Debe salir de configuración antes de producción |
| 4 | ¿Cambió el contrato de `PackingResult`? | `backend/app/models/schemas.py` | Define los DTOs del lado .NET |
| 5 | ¿Aparecieron endpoints nuevos? | `backend/app/api/routes.py` | Amplía la superficie del cliente C# |

Si 1 y 2 ya están resueltos, la **Fase 1 del plan está hecha** y se puede arrancar directamente
por la Fase 2.

---

# PARTE I — Análisis de CUBOX

## 1. Qué es y cómo está construido

Optimizador de cubicaje (*container loading*) de ventanas en contenedores marítimos.
MVP local, **sin autenticación y sin base de datos**.

```
Excel (.xlsx) → Frontend (React + R3F) → API REST (FastAPI) → Algoritmo de packing (Python puro, en memoria)
                     ↑                         │
                     └── PackingResult completo ┘   (el backend es la única fuente de verdad)
```

### Stack

| Capa | Tecnología |
|---|---|
| Backend | Python + FastAPI, Pydantic v2, openpyxl (Excel), reportlab (PDF), pytest |
| Frontend | React 19, Three.js 0.185, @react-three/fiber 9, @react-three/drei 10, Axios, Vite 8, TypeScript, oxlint |
| Persistencia | **Ninguna** — estado en memoria de proceso |

### Organización

- **Backend por capas:** `main.py` (bootstrap + CORS) → `api/routes.py` (única capa HTTP) →
  `core/*` (lógica de negocio pura, sin dependencias de FastAPI) → `models/*` (schemas Pydantic).
- **Frontend "god component":** `App.tsx` concentra todo el estado (20+ `useState`) y lo pasa por
  props a componentes presentacionales. No hay Redux ni Context. El único hook custom es
  `useDragEngine`.

## 2. Modelo de datos

| Entidad | Campos clave |
|---|---|
| `WindowItem` | code, description, width/height/thickness (mm), weight (kg), quantity, system, group, stackable, priority (1-5), max_stack_weight, delivery_sequence |
| `ContainerSpec` | id, name, length/width/height (mm), max_weight |
| `PlacedPiece` | posición x/y/z, dimensiones orientadas dx/dy/dz, orientation_label, locked, + atributos heredados de WindowItem |
| `UnloadedItem` | pieza no cargada + `reason` (texto) + `reason_code` (enum estable) |
| `PackingMetrics` | volumen, peso, % utilización, balance izq/der y frente/fondo, centro de masa |
| `PackingResult` | container + placed + unloaded + metrics + load/unload sequence + reserved_zones |

**Catálogo de contenedores** (`models/containers.py`), dimensiones internas en mm:

| id | Largo | Ancho | Alto | Peso máx (kg) |
|---|---|---|---|---|
| `20ft_standard` | 5898 | 2352 | 2393 | 28180 |
| `40ft_standard` | 12032 | 2352 | 2393 | 26512 |
| `40ft_high_cube` | 12032 | 2352 | 2698 | 26330 |

## 3. Regla de negocio crítica: orientación

**Una ventana jamás puede quedar acostada sobre su cara de vidrio (Width × Height).**

Solo existen **4 orientaciones válidas** (2 posiciones base × 2 rotaciones en planta):

- **P1**: base = W×T, vertical = H
- **P2**: base = H×T, vertical = W

Se centraliza en `core/orientation.py: is_valid_orientation`, reutilizada en **los tres caminos
posibles** del sistema:

1. **Algoritmo automático** (`packer.py`) — solo genera esas 4 combinaciones, nunca otra.
2. **Edición manual** (`manual_move.py`) — valida antes de aceptar movimiento, inserción o rotación.
3. **Validación final** (`final_validation.py`) — re-chequea todo el estado antes de exportar.

Cubierto por tests parametrizados que verifican que ninguna orientación prohibida pasa, sin
importar el orden de los ejes.

> ⚠️ **Esta regla no se toca sin autorización explícita.** Está documentada como tal en el código.

## 4. Algoritmo de cubicaje

Heurística de **puntos de anclaje** (*corner points*). No es un solver óptimo exacto.

1. Cada `WindowItem` se expande en instancias individuales (quantity → N piezas con id único).
2. Se ordenan según una de **7 estrategias** (`strategies.py`): `largest_volume`,
   `largest_footprint`, `tallest_first`, `highest_priority`, `group_and_size`,
   `system_and_size`, `longest_dimension`.
3. Se mantiene una lista de candidatos `(x,y,z)`; cada pieza colocada genera nuevos candidatos
   (al lado en X, al lado en Y, encima si es apilable), más un candidato extra tras cada zona
   reservada (fix documentado para que el pasillo central no deje un lado sin candidatos).
4. Los candidatos se evalúan en orden `(x,z,y)` con un **espejo de X**, de modo que el contenedor
   se llena sólido desde el fondo hacia la puerta — simula la carga real.
5. Para cada candidato × cada orientación válida se valida: dentro de límites, sin colisión, sin
   invadir zona reservada (con clearance), soporte suficiente (≥80% de área si z>0) y peso apilado
   permitido. Primera combinación válida gana.
6. `optimize.py` corre las 7 estrategias, puntúa cada resultado (`scoring.py`) y devuelve la mejor
   + hasta 3 alternativas.
7. Piezas que no caben quedan `unloaded` con un `reason_code`
   (`ORIENTATION_CONFLICT`, `MAX_WEIGHT_EXCEEDED`, `NO_VALID_SPACE`, etc.).

**Scoring** (`scoring.py`): combinación ponderada de 7 componentes — piezas cargadas, prioridad,
% volumen, % piso, agrupamiento, accesibilidad de pasillos y balance de peso. Los pesos cambian
según el modo elegido (Best Space / Keep Groups / Keep Systems) y según `WeightBalanceMode`.

**Cobertura de tests:** ~17 archivos en `backend/app/tests/` cubren orientación, colisiones,
soporte/apilamiento, clearance, scoring por modo, secuencia de carga/descarga, zonas reservadas,
edición manual, lock, optimize-remaining, validación final, import/export Excel y PDF.

## 5. Flujo funcional end-to-end

```
1. Usuario sube Excel (Code, Width, Height, Thickness, Weight, Quantity, System, Group,
   Stackable, Priority)
       → POST /api/import-excel → parsea a WindowItem[]

2. Elige contenedor + modo de optimización (Best Space / Keep Groups / Keep Systems)
   + opciones avanzadas (pasillo central, clearance, balance de peso, esquina de anclaje)

3. Click "Calcular cubicaje"
       → POST /api/pack → corre las 7 estrategias, puntúa, devuelve mejor + alternativas

4. Visualización 3D (Scene3D + PieceMesh):
   - Contenedor en wireframe, marcador de puerta, pasillo (si aplica)
   - Piezas coloreadas por system/group/priority/code
   - Secuencia de carga/descarga navegable paso a paso

5. Interacción manual:
   - Arrastrar → validación visual en vivo (espejo simplificado de las reglas del backend)
     → al soltar, POST /api/apply-move o /api/insert-piece → el backend revalida TODO
     → si rechaza, el frontend descarta el cambio (el backend es la autoridad final)
   - Rotar / Girar (siempre dentro de las 4 orientaciones válidas)
   - Bloquear (lock) para protegerla de un recálculo
   - Quitar → pasa a "no cargadas"

6. "Optimize Remaining": re-empaqueta solo lo no bloqueado, respetando piezas locked

7. Undo/Redo sobre historial en memoria (hasta 30 pasos)

8. Exportar:
   - Excel (Summary + Packing List + Unloaded Items)
   - PDF: Container Load Report / Loading Guide / Unloading Guide
     → el frontend orquesta capturas 3D paso a paso y las envía como base64
     → el backend re-valida el estado completo antes de generar (nunca exporta estado inválido)
```

## 6. Hallazgos que afectan la integración

### 6.1 🔴 BLOQUEANTE — Estado global de proceso

El backend guarda el resultado activo en un **diccionario global** (`_current_state` en
`backend/app/api/routes.py`). Funciona con un usuario local. **Detrás de IIS con varios usuarios
concurrentes, se pisan las sesiones entre sí**: si dos personas calculan, ambas ven el resultado
de la última.

Todos los endpoints de edición manual (`apply-move`, `rotate-piece`, `undo`, exportar…) dependen
de ese estado.

**Sin resolver esto, el sistema no es productivizable.** Ver Fase 1 del plan.

### 6.2 🟠 URL del backend hardcodeada

`frontend/src/api/client.ts:18` apunta a un dev tunnel
(`https://xntmh1xr-8000.brs.devtunnels.ms/api`), con la línea de `localhost:8000` comentada y sin
variable de entorno.

Además, el mensaje de error de conexión sigue diciendo `"No se pudo conectar con el backend
(http://localhost:8000)"` — desactualizado respecto a la URL real.

### 6.3 🟡 `validateMove` definida pero no usada

Existe en el cliente API pero no se llama desde ningún flujo. Parece un endpoint de *preview* que
quedó sin conectar a la UI.

### 6.4 🟡 CORS permisivo

Acepta `localhost`/`127.0.0.1` en cualquier puerto vía `allow_origin_regex`. Hay que restringirlo
antes de producción.

### 6.5 🟡 Los PDF dependen de capturas del navegador

El pipeline de reportes requiere que el frontend React tome screenshots del canvas 3D paso a paso
y los envíe en base64. Esto **no se puede mover a .NET fácilmente** — condiciona la Fase 6.

---

# PARTE II — Análisis de WEB_INTERFACES_CORE

## 7. Estructura

Solución .NET 8 (SDK 8.0.400), 6 proyectos:

```
WEB_INTERFACES_CORE.sln
├── MODELOS/            ← DTOs, ViewModels, entidades (clases cls*)
├── SERVICIOS/          ← Lógica de negocio (INT_Xxx / Services_Xxx)
├── REPOSITORIO/        ← DbContexts EF Core (HANA + SQL Server)
├── ServiceLayer/       ← Integración SAP B1
├── PROJECT_Shared/     ← Helpers (SessionCookies, DecryptCookies)
└── WEB_INTERFACES_CORE/← Proyecto web MVC
```

Convención de carpetas en todas las capas: `COMPANY/<SUBMÓDULO>/<MÓDULO>/`.

## 8. Patrones clave del proyecto

### 8.1 ✅ El precedente exacto: `ClaudeApiService`

**`SERVICIOS/COMPANY/INTERCOMPANY/SHAREPOINT/ClaudeApiService.cs`** es un typed HttpClient que
**ya consume una API FastAPI remota**. Es el molde directo para CUBOX.

Registro en `Program.cs:148-159`:

```csharp
builder.Services.AddHttpClient<ClaudeApiService>((sp, client) =>
{
    var opts = sp.GetRequiredService<IOptions<ClaudeApiOptions>>().Value;
    if (!string.IsNullOrWhiteSpace(opts.BaseUrl))  client.BaseAddress = new Uri(opts.BaseUrl);
    if (!string.IsNullOrWhiteSpace(opts.ApiKey))   client.DefaultRequestHeaders.Add("x-api-key", opts.ApiKey);
    client.Timeout = TimeSpan.FromSeconds(opts.TimeoutSeconds > 0 ? opts.TimeoutSeconds : 180);
});
```

Estructura del servicio:

```csharp
// DTOs como records inmutables, arriba del servicio, en el mismo archivo
public record ClaudeChatRequest(string Message, string? SessionId, string? UserName = null, string? Context = null);
public record ClaudeChatResponse(string Reply, string SessionId, List<ClaudeOutputFile> OutputFiles);

public class ClaudeApiService              // ← clase concreta SIN interfaz
{
    private readonly HttpClient _http;
    public ClaudeApiService(HttpClient http) { _http = http; }   // inyección directa

    public async Task<ClaudeChatResponse> ChatAsync(ClaudeChatRequest request, CancellationToken ct = default)
    {
        var response = await _http.PostAsJsonAsync("/api/chat", request, ct);
        await AsegurarExitoAsync(response, "/api/chat", ct);
        return await response.Content.ReadFromJsonAsync<ClaudeChatResponse>(cancellationToken: ct)
            ?? throw new InvalidOperationException("La API no devolvio respuesta.");
    }
}
```

**Manejo de errores que incluye el body** (`ClaudeApiService.cs:89-97`) — crítico, porque los
422 de FastAPI traen la causa real en el cuerpo:

```csharp
private static async Task AsegurarExitoAsync(HttpResponseMessage response, string endpoint, CancellationToken ct)
{
    if (response.IsSuccessStatusCode) return;
    string body = string.Empty;
    try { body = await response.Content.ReadAsStringAsync(ct); } catch { }
    if (body != null && body.Length > 1500) body = body.Substring(0, 1500) + "…";
    throw new HttpRequestException(
        $"La API respondio {(int)response.StatusCode} ({response.ReasonPhrase}) en {endpoint}. Detalle: {body}");
}
```

> **Timeout por defecto de 180s.** El equipo ya se topó con el problema de las APIs Python lentas.
> Aplica igual al packing (7 estrategias).

### 8.2 ⛔ Anti-patrón vigente (no imitar)

`SERVICIOS/COMPANY/INTERCOMPANY/EGRESO_DIMASIMMA/SEgresoDimasimma.cs:410-412`:

```csharp
using var client = new HttpClient();                    // ← socket exhaustion
var resp = client.PostAsync(url, content).Result;       // ← bloqueante
```

### 8.3 Program.cs

- **Serilog** configurado desde `appsettings.json`.
- **~60 `AddScoped<IXxx, Xxx>()` planos**, uno por módulo. Sin ensamblado-scanning.
- **`AddHttpClient` ya se usa** en 3 formas: typed sin config (`MailchimpService`), named
  (`graph-onenote`), y typed + `IOptions` (`ClaudeApiService`).
- **Options pattern** solo para lo moderno (`GraphApiOptions`, `ClaudeApiOptions`,
  `PdfChromeOptions`), cada uno con `public const string SectionName`. El resto usa
  `config["Clave:Sub"]` leído en el constructor del controller.
- **No hay** `AddSession`, `AddAuthentication`, `AddCors`. La sesión son **cookies cifradas
  emitidas por la app WebForms legacy**, descifradas en `ValidateCookiesMiddleWare`.
- **Una sola ruta convencional**, sin Areas: `{controller=Presupuesto}/{action=Index}`.

### 8.4 Convenciones obligatorias

| Convención | Detalle |
|---|---|
| **Ruta de vista absoluta** | Las vistas viven en `Views/COMPANY/...`, Razor no las descubre: `return View("~/Views/COMPANY/INTERCOMPANY/CUBOX/Index.cshtml", model);` |
| **Envelope JSON uniforme** | `{ success = true, <payload> }` / `{ success = false, message = ex.Message }`, siempre con `try/catch`. Nunca status code de error desde la acción |
| **Atributos de ruta** | Solo `[HttpGet]`/`[HttpPost]`. Cero `[Route]`, cero `[ApiController]` |
| **Sesión** | `_Session.GetDatosSesion()` dentro de cada acción, nunca cacheada. `Sesion.BD` (esquema HANA) va como primer parámetro a casi todos los métodos de servicio |
| **camelCase JSON** | `AddControllersWithViews()` baja solo el primer carácter: `U_CAMPO` → `u_CAMPO` |

### 8.5 El menú vive en HANA, no en el código

El menú está en la app WebForms legacy (`INICIO.aspx`). Para registrar una pantalla .NET Core hay
que insertar en la tabla `PANTALLA` de `DB_SEGURIDAD` (HANA) una fila con **sufijo `.NCore`**:

```
URL = 'Cubox/Index.NCore'   +   ESTADO = 'A'   +   asignación al rol/usuario
```

Normalización en `REPOSITORIO/COMPANY/SEGURIDAD/PermisosPantallaRepository.cs:84-95`.

**Política falla-abierto** (`PermisosPantallaService.cs:38-51`): si el path no está en el universo
de pantallas, se permite. Es decir, **un controller nuevo funciona sin tocar la BD** — pero no
aparecerá en el menú ni se podrá restringir por permisos.

### 8.6 🎯 El layout ya soporta iframe

`Views/Shared/_Layout.cshtml:208-222`: si la página detecta que corre dentro de un iframe (o el
user-agent contiene `NagsaApp`), añade la clase `.in-iframe` al `<html>` y **oculta el header con
`display:none !important`**.

**El mecanismo de embebido ya existe y está en producción.**

### 8.7 Frontend del portal

- **Bootstrap 3.0.0** (`wwwroot/lib/Content/bootstrap.css:5`), con markup mezclado BS3 + BS5.
- **jQuery no se carga en el layout** (líneas comentadas); cada vista lo carga por su cuenta.
  Conviven jQuery 1.10.2 y 3.7.1.
- **No hay build de frontend**: ni npm, ni webpack, ni libman activo.
- **No existe ninguna librería de visualización** — ni Three.js, ni charts, ni canvas.
- Las librerías de terceros se cargan **por CDN desde la propia vista** (PowerBI client, SheetJS,
  jsPDF, select2, driver.js, Font Awesome).
- Las vistas son monolitos con JS inline: `PRESUPUESTO/Index.cshtml` tiene **19.124 líneas**,
  `RubrosN/Index.cshtml` **5.297**.
- Helpers globales SweetAlert2 en el layout: `mostrarMensaje()`, `mostrarAlert()`,
  `mostrarConfirmacion()`, `mostrarExitoGuardado()`, `mostrarConfirmacionDanger()`.

### 8.8 Precedente de integración con Python

**No se ejecuta Python localmente en ningún punto** (`grep Process.Start` → 0 resultados).

`ScriptPyProvider` es un **repositorio de contenido** de scripts `.py`, no un ejecutor: resuelve
el archivo desde una carpeta configurable (`ClaudeApi:ScriptsPyPath`) con fallback a recurso
embebido, y el `.py` **viaja por HTTP a un servicio remoto que lo ejecuta en su sandbox**.

> El precedente no es "ejecutar Python desde .NET", sino **"delegar Python a un servicio HTTP
> externo"** — exactamente la arquitectura de CUBOX.

### 8.9 Herramientas de documentos ya disponibles

`ClosedXML 0.104.2`, `itext7 9.2.0`, `QRCoder 1.6.0`, y **`PuppeteerSharp 20.0.5`** con
`PdfChromeOptions` (Chrome headless). Relevante para la Fase 6.

---

# PARTE III — Estándares e investigación

## 9. Qué dicen otros devs sobre este tipo de acoplamiento

### 9.1 El patrón tiene nombre: Backend for Frontend (BFF)

La app Razor deja de ser "solo UI" y pasa a ser la capa que traduce, agrega y adapta lo que expone
el backend externo para esa UI concreta. Es una variante del API Gateway.

### 9.2 ¿Quién llama a la API: el navegador o el servidor?

**Consenso: el servidor.** Razones que se repiten en todas las fuentes:

- Las credenciales/tokens nunca llegan al navegador.
- La URL real del backend no queda expuesta en DevTools.
- Se aplica la sesión corporativa y se valida antes de reenviar.
- Se acaba el problema de CORS: mismo origen.
- Costo: un salto de red extra — *los beneficios de seguridad y observabilidad lo justifican
  muchas veces sobre*.

El framework de seguridad BFF de Duende lo plantea como regla dura: **toda app en navegador
debería tener una app server-side que maneje la autenticación y asegure el acceso a las APIs**.

### 9.3 El contrato: OpenAPI + generación de cliente

FastAPI genera `/openapi.json` automáticamente. El estándar es **generar el cliente C# desde ahí**
en vez de escribir DTOs a mano:

| Herramienta | Perfil |
|---|---|
| **NSwag** | Maduro, archivo monolítico, el más usado históricamente |
| **Kiota** (Microsoft) | Oficial, orientado a recursos, archivos pequeños navegables y mockeables |
| **Refitter** | Genera interfaces estilo Refit |

Argumento de fondo: *el spec es la fuente de verdad y los tipos C# se generan de él; un diff sobre
los archivos generados muestra exactamente qué cambió en el contrato.* Convierte un cambio en el
backend Python en **error de compilación**, no en excepción de producción.

### 9.4 Cómo se hace la llamada HTTP

- **`IHttpClientFactory`** hace pooling, previene *socket exhaustion* y rota handlers
  periódicamente (resuelve el DNS obsoleto).
- **`Microsoft.Extensions.Http.Resilience`** (sucesor de `Microsoft.Extensions.Http.Polly`, sobre
  Polly v8) es lo recomendado por Microsoft para proyectos nuevos.
  `AddStandardResilienceHandler()` aplica rate limiter, timeout total, retry, circuit breaker y
  timeout por intento.

> ⚠️ **Matiz para este proyecto:** WEB_INTERFACES_CORE **no usa Polly**. Introducirlo sería un
> paquete y una convención nuevos. Recomendación: seguir el molde existente (`ClaudeApiService`)
> y agregar resiliencia después si hace falta.

### 9.5 Embeber un SPA: iframe vs build integrado

**Opción build integrado** (Vite compilando a `wwwroot`): *en tiempo de publish los archivos de la
app React se copian a wwwroot y se sirven vía Static File Middleware*. Requiere setear `base` en
`vite.config.js` porque el SPA vive en una sub-ruta.

**Opción iframe:**
- A favor: *aislamiento en su forma más alta* — no se comparten estilos ni el objeto `window`. Y
  *si tienes una app existente con su routing y no quieres cambiar mucho de su setup, el iframe es
  mejor*.
- En contra: *estás descargando y arrancando una aplicación entera de nuevo, pagando latencia de
  red y costo de runtime*; no es responsive; la comunicación por `postMessage` es más incómoda.

Advertencia general: los micro-frontends **no** son ideales *cuando el producto es lo bastante
pequeño como para que un solo SPA no cree cuellos de botella de coordinación*.

### 9.6 Desplegar FastAPI en Windows/IIS

Dos caminos documentados, ambos con IIS como reverse proxy:

- **HttpPlatformHandler v1.2** + `web.config` que lanza y gestiona uvicorn. Microsoft **ya no
  recomienda FastCGI** (wfastcgi sin mantenimiento).
- **NSSM** para correr uvicorn como servicio de Windows, con IIS proxeando.

IIS aporta terminación SSL, manejo de estáticos y seguridad perimetral.

### 9.7 Fuentes

- [Implementing the BFF Pattern with YARP and .NET Minimal APIs](https://medium.com/@amhemanth/implementing-the-backends-for-frontends-bff-pattern-with-microsofts-yarp-and-net-minimal-apis-41c391974f43)
- [A Deep Dive into the Back-End for Front-End Pattern — CODE Magazine](https://www.codemag.com/Article/2203081/A-Deep-Dive-into-the-Back-End-for-Front-End-Pattern)
- [Duende BFF Security Framework](https://docs.duendesoftware.com/bff/)
- [API Proxy Architecture for Web Clients](https://nowah.xyz/blog/api-proxy-architecture-web)
- [Client-Side Proxy vs Server-Side Proxy](https://pradyumnachippigiri.substack.com/p/client-side-proxy-vs-server-side)
- [Kiota vs NSwag vs Refitter](https://codingdroplets.com/kiota-vs-nswag-vs-refitter-dotnet-typed-api-client-2026)
- [Everything you need to know about OpenAPI and API client generation](https://stenbrinke.nl/blog/openapi-api-client-generation/)
- [Build better C# API Clients: OpenAPI and Kiota](https://baundev.com/posts/api-clients-kiota/)
- [Building resilient cloud services with .NET 8 — .NET Blog](https://devblogs.microsoft.com/dotnet/building-resilient-cloud-services-with-dotnet-8/)
- [Resilience Pipelines in .NET With Polly — Milan Jovanović](https://milanjovanovic.tech/blog/building-resilient-cloud-applications-with-dotnet)
- [Use React with ASP.NET Core — Microsoft Learn](https://learn.microsoft.com/en-us/aspnet/core/client-side/spa/react?view=aspnetcore-10.0)
- [Use ASP.NET Core and React with Vite.js](https://blog.codeinside.eu/2023/02/11/aspnet-core-react-with-vitejs/)
- [How Microfrontends Work: From iframes to Module Federation](https://www.freecodecamp.org/news/how-microfrontends-work-iframes-to-module-federation/)
- [How Big Tech Builds Micro Frontends](https://stefanhaas.dev/article/how-big-tech-builds-micro-frontends/)
- [Use YARP to host client and API server on a single origin to avoid CORS](https://swimburger.net/blog/dotnet/use-yarp-to-host-client-and-api-server-on-a-single-origin)
- [YARP CORS — Microsoft Learn](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/servers/yarp/cors?view=aspnetcore-10.0)
- [Running FastAPI Web Apps on IIS with HttpPlatformHandler](https://docs.lextudio.com/blog/running-fastapi-web-apps-on-iis-with-httpplatformhandler/)
- [How to deploy ASGI on IIS? — Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/505512/how-to-deploy-asgi-on-iis)

---

# PARTE IV — Arquitectura recomendada

## 10. Aclaración conceptual previa

> **El backend de CUBOX no tiene vistas.** FastAPI devuelve JSON (`PackingResult` con coordenadas,
> métricas y secuencias). Nunca devuelve HTML.

La "vista" la construye el **frontend React**, una aplicación separada que corre en el navegador
dibujando con WebGL. No son dos piezas, son **tres**:

| Pieza | Qué es | Dónde corre |
|---|---|---|
| CUBOX API (FastAPI) | Motor de cálculo. Entra lista de ventanas → sale layout en coordenadas | Servidor (Python) |
| CUBOX UI (React + Three.js) | Visor 3D interactivo, drag manual, capturas para PDF | Navegador (JS) |
| WEB_INTERFACES_CORE | Portal: identidad, datos, SAP, documentos | Servidor (.NET) |

Un service en .NET llama a la API para obtener **datos**, no vistas. La vista 3D se **hospeda**,
no se pide.

## 11. Por qué NO pasar el front a Blazor

Evaluado y descartado:

- **La parte 3D sigue siendo JavaScript.** Three.js dibuja sobre WebGL desde el navegador. Blazor
  no lo reemplaza: habría que envolverlo en `IJSRuntime` interop. Terminas con JS igual, **más**
  una capa de interop.
- **Es una reescritura completa**, no una migración: 13 componentes React, motor de arrastre con
  raycasting y snapping, lógica de etiquetas por cara, pipeline de captura PNG por paso.
- **Crea el máximo acoplamiento posible.** Cada mejora del visor obligaría a reescribirla a mano
  en Blazor. Es exactamente el escenario de "infierno de actualizaciones" que se quiere evitar.

Blazor sería razonable solo si se fuera a **reemplazar** CUBOX por algo propio.

## 12. El principio rector

> **El costo de cada actualización futura es proporcional a la superficie de acoplamiento.**
> Integra por **contrato**, no por **código**.

Todo lo que "se traiga adentro" (reescribir el front, copiar modelos a mano, replicar reglas de
negocio en C#) es superficie que se rompe en cada update. Todo lo que quede detrás de un contrato
estable (OpenAPI + cliente generado) se actualiza con un comando.

**La respuesta ordenada es aislar más, no integrar más.**

## 13. Arquitectura objetivo

```
┌──────────────────────────────────────────────────────────┐
│  WEB_INTERFACES_CORE (.NET 8)  ← dueño de TODO lo corporativo
│  · Identidad / sesión / permisos                          │
│  · SQL + HANA + SAP Service Layer                         │
│  · Histórico, documentos oficiales                        │
│                                                           │
│  CuboxController                                          │
│      └── ICuboxService ──► CuboxService                   │
│                               └── CuboxApiService         │
│                                   (typed HttpClient)      │
│  Views/COMPANY/.../CUBOX/Index.cshtml                     │
│      └── iframe → visor CUBOX                             │
└───────────────┬──────────────────────┬────────────────────┘
          CONTRATO 1                CONTRATO 2
       (OpenAPI / JSON)         (host ↔ visor, postMessage)
                │                      │
      ┌─────────▼────────┐   ┌─────────▼──────────┐
      │  CUBOX API       │   │  CUBOX UI (React)  │
      │  FastAPI         │   │  visor 3D          │
      │  MOTOR PURO      │   │  sin lógica de     │
      │  sin estado      │   │  negocio propia    │
      │  sin BD          │   └────────────────────┘
      └──────────────────┘
```

### Las tres reglas que lo sostienen

1. **CUBOX nunca toca la base de datos.** Ni SQL, ni HANA, ni SAP. Es un motor: recibe piezas,
   devuelve layout.
2. **.NET es el único dueño** de los datos, la sesión, la persistencia y los documentos.
3. **El contrato es OpenAPI**, y el cliente C# se genera desde `/openapi.json`, no se escribe a mano.

### Reparto de responsabilidades

| Responsabilidad | Quién |
|---|---|
| Leer ventanas de un proyecto/OT (SQL/HANA/SAP) | **.NET** |
| Calcular el cubicaje | **CUBOX API** |
| Guardar histórico de cubicajes | **.NET** (tabla propia) |
| Consultar histórico | **.NET** |
| Documentos oficiales (membrete, numeración) | **.NET** |
| Visualización 3D interactiva | **CUBOX UI** |

### Por qué NO darle acceso a BD a CUBOX

- Duplica credenciales de SQL/HANA en un segundo stack.
- Parte la lógica de negocio en dos lenguajes; nadie sabrá dónde vive cada regla.
- Dos sistemas escribiendo la misma BD = conflictos y auditoría imposible.
- Rompe el aislamiento que da todo el valor.

## 14. Decisión sobre el front: iframe

| Opción | Integración visual | Costo por update de CUBOX | Veredicto |
|---|---|---|---|
| **A. iframe** | Buena (pantalla completa, indistinguible) | **Casi cero**: se despliega CUBOX, .NET no se toca | ✅ **Elegida** |
| **B. Build Vite → `wwwroot`** | Perfecta | Medio: recompilar y republicar .NET en cada update | ❌ Descartada |
| **C. Reescribir en Blazor** | Perfecta | Altísimo: reescribir a mano cada mejora | ❌ Descartada |

**Por qué el iframe gana aquí** (con evidencia, no por preferencia):

1. **El layout ya lo soporta nativamente** (`_Layout.cshtml:208-222` oculta el header en iframe).
2. **La opción B es inviable a bajo costo**: el proyecto no tiene build de frontend (ni npm, ni
   webpack), no tiene ninguna librería de visualización, y las vistas son monolitos con JS inline.
   Introducir un pipeline de Node sería precisamente la fricción estructural a evitar.
3. **Las críticas estándar al iframe no aplican**: es una pantalla interna de escritorio, con un
   canvas 3D que ocupa el viewport completo, sin SEO, que se abre una vez por sesión de trabajo.

**Para que no se note que es un iframe:**
- Servir el visor **bajo el mismo dominio** vía reverse proxy IIS (`tuportal/cubox/...`). Mismo
  origen → la cookie corporativa aplica sola, sin CORS ni segundo login.
- `height: calc(100vh - <alto del header>)`, sin bordes.
- `postMessage` para el handshake mínimo (ej. "guardar" → el host recibe el resultado y lo persiste).

---

# PARTE V — Plan de implementación

| Fase | Qué | Dónde | Bloquea a | Tamaño |
|---|---|---|---|---|
| **0** | Decisiones y ramas | Ambos | Todo | XS |
| **1** | CUBOX stateless + versionado | Python | 2,3,4 | **L** |
| **2** | Desplegar el motor en infra NAGSA | Servidor | 3 | M |
| **3** | Capa .NET (service + controller) | .NET | 4 | M |
| **4** | Vista embebida (iframe) | .NET + React | — | M |
| **5** | Origen de datos + histórico | .NET | — | M |
| **6** | Documentos oficiales | .NET | — | S/L |

Las fases 1–4 son el camino crítico. Las 5 y 6 son valor incremental posterior.

---

## Fase 0 — Preparación

- [ ] Crear rama de trabajo en el repo .NET. **No desarrollar sobre `Produccion`** (limpio, con
      releases numerados `Version 155`).
- [ ] Definir el submódulo: ¿`COMPANY/INTERCOMPANY/CUBOX/` o bajo una empresa concreta?
- [ ] Decidir dónde correrá el motor: IIS + HttpPlatformHandler, o NSSM + IIS proxeando.
- [ ] Resolver las decisiones abiertas (§15).

**Entregable:** ramas creadas y rutas de carpetas acordadas.

---

## Fase 1 — CUBOX stateless 🔴 BLOQUEANTE

Todo el trabajo es en Python. Es la fase más grande y no se puede saltar.

### 1.1 Versionar las rutas
Todo bajo `/api/v1/`. Sin esto, cualquier cambio futuro rompe .NET sin aviso.

### 1.2 Eliminar el estado global

```
ANTES                                  DESPUÉS
POST /pack        → guarda en memoria  POST /v1/pack        {items, config}      → {result}
POST /apply-move  → muta esa memoria   POST /v1/apply-move  {result, piece, pos} → {result}
POST /rotate-piece→ muta esa memoria   POST /v1/rotate-piece{result, piece}      → {result}
GET  /state       → lee esa memoria    (desaparece: el dueño del estado es el cliente)
```

Cada endpoint recibe el estado y devuelve el estado nuevo. El servidor no recuerda nada entre
llamadas.

### 1.3 Undo/redo pasa al cliente
Hoy es un `deque` de 30 snapshots en el servidor. Recomendación: que lo mantenga **el front React
en memoria** — es quien lo usa interactivamente. .NET solo persiste el estado final al guardar.

### 1.4 Seguridad mínima
Header `x-api-key` (mismo esquema que `ClaudeApiService`) y CORS restringido.

### 1.5 Adaptar los tests
`test_api.py`, `test_manual_edit.py`, `test_lock.py`, `test_optimize_remaining.py` y
`test_pdf_export.py` dependen del estado global — hay que reescribirlos contra la firma nueva.

**Lo bueno:** el núcleo de lógica pura (`orientation`, `packer`, `geometry`, `scoring`,
`sequence`, `final_validation`) **no se toca**; sus tests siguen valiendo tal cual, y son la
mayoría.

### 1.6 Congelar el contrato
`/openapi.json` estable + endpoint de health.

**Entregable verificable:** dos clientes distintos calculan y editan en paralelo sin interferirse,
y toda la suite de tests pasa.

**⚠️ Riesgo a medir:** el payload crece — un contenedor de 40ft con cientos de piezas puede generar
un JSON de cientos de KB viajando en cada movimiento manual. Aceptable en red interna, pero
**medirlo con `examples/Muestra Real.xlsx` antes de cerrar el diseño**.

---

## Fase 2 — Desplegar el motor

- [ ] Publicar FastAPI en servidor Windows, con IIS al frente (SSL, estáticos, seguridad perimetral).
- [ ] URL interna estable, sin dev tunnels.
- [ ] Smoke test: health + un `/v1/pack` de prueba desde la red corporativa.

**Entregable:** URL interna estable respondiendo, documentada para `appsettings.json`.

---

## Fase 3 — Capa .NET (sin UI todavía)

Copiar el molde de `ClaudeApiService` casi literal.

1. [ ] `CuboxOptions.cs` con `SectionName = "CuboxApi"` (+ `BaseUrl`, `ApiKey`, `TimeoutSeconds`)
       — molde: `AnthropicOptions.cs:7-22`
2. [ ] Sección `CuboxApi` en `appsettings.json`, **sin fallback a localhost**
3. [ ] `Configure<CuboxOptions>(...)` — molde: `Program.cs:131`
4. [ ] `AddHttpClient<CuboxApiService>((sp, client) => {...})` — molde: `Program.cs:148-159`
5. [ ] `CuboxApiService`: clase concreta sin interfaz, records DTO en el mismo archivo,
       `PostAsJsonAsync`/`ReadFromJsonAsync`, helper `AsegurarExitoAsync` **con el body en la
       excepción**
6. [ ] `ICuboxService`/`CuboxService`: orquestador `AddScoped`, **con** interfaz. Es quien toca
       HANA y persiste — molde: `Services_Rubros.cs:19-31`
7. [ ] `CuboxController`: solo `[HttpGet]`/`[HttpPost]`, envelope `{success, message}`,
       `return View("~/Views/COMPANY/.../CUBOX/Index.cshtml", model)` con ruta absoluta

**Entregable:** una acción del controller devuelve un cubicaje calculado en JSON, verificable sin UI.

**⚠️ `snake_case` vs `PascalCase`:** Python devuelve `used_volume_pct`, C# espera `UsedVolumePct`.
Fijarlo en `JsonSerializerOptions` o generar los DTOs desde `openapi.json` con Kiota/NSwag.

---

## Fase 4 — Vista embebida

1. [ ] `Index.cshtml` con el iframe, a `height: calc(100vh - <header>)`, sin bordes
2. [ ] Servir el front React **bajo el mismo dominio** vía reverse proxy IIS (`tuportal/cubox/...`)
3. [ ] Quitar el `API_BASE` hardcodeado del dev tunnel en `frontend/src/api/client.ts:18` — debe
       venir de configuración
4. [ ] `postMessage` para el handshake mínimo: el visor avisa "guardar" → el host persiste
5. [ ] Registrar la pantalla: fila en `DB_SEGURIDAD.PANTALLA` con `URL = 'Cubox/Index.NCore'` y
       `ESTADO='A'`

**Entregable:** pantalla accesible desde el menú, con el visor 3D funcionando dentro del portal y
la sesión corporativa aplicada.

---

## Fase 5 — Origen de datos y histórico

- [ ] Leer las ventanas desde la OT/proyecto (SP en HANA) en vez de subir Excel a mano. El Excel
      queda como alternativa, no como única vía.
- [ ] Tabla propia en SQL para el histórico: `PackingResult` completo en JSON + metadatos
      (proyecto, usuario, fecha, contenedor).
- [ ] Pantalla de consulta de cubicajes anteriores, con capacidad de reabrir uno sin recalcular.

---

## Fase 6 — Documentos oficiales

> ⚠️ **Los PDF actuales dependen de capturas del canvas 3D** — el front React orquesta screenshots
> paso a paso y los manda al backend. Eso no se mueve a .NET fácilmente.

**Recomendación:** dejar los PDF en CUBOX inicialmente y que .NET solo archive el resultado.
Migrarlos solo si necesitan membrete, numeración corporativa o firma.

Si llega ese momento, las herramientas ya están: **PuppeteerSharp** (Chrome headless) +
`PdfChromeOptions`, ClosedXML, iText7, QRCoder.

---

## Camino corto (alternativa recomendada para entregar antes)

> **Fase 1-mínima:** convertir a stateless **solo `/v1/pack`** (calcular), sin edición manual ni
> reportes. Es una fracción del trabajo.
>
> Con eso se corren las fases 2→3→4 y ya hay: pantalla en el portal, cálculo funcionando,
> resultado visible en 3D y guardable. La edición manual (mover/rotar/lock/undo) se agrega después
> convirtiendo el resto de endpoints, **sin rehacer nada de lo anterior**.

Da una pantalla real en producción antes y valida la arquitectura completa con el mínimo riesgo.

---

## 15. Decisiones abiertas

| # | Decisión | Impacto |
|---|---|---|
| 1 | **¿De dónde salen las ventanas?** ¿Excel manual siempre, o deben leerse de la OT/proyecto? | Alcance de la Fase 5. Si es lo segundo, conviene saberlo desde la Fase 3 para diseñar `ICuboxService` |
| 2 | **¿Los PDF actuales sirven como están**, o necesitan membrete/numeración corporativa? | Define si la Fase 6 es S o L |
| 3 | **¿Cuántos usuarios concurrentes** se esperan? | Si es 1-2, la Fase 1 puede simplificarse; si son 10+, el diseño stateless es obligatorio tal cual |
| 4 | **¿Submódulo y empresa** para las rutas de carpetas? | Todas las rutas de archivos del lado .NET |

---

## 16. Reglas para que las actualizaciones no sean un infierno

1. **Versionar la API desde el día uno** (`/api/v1/`). CUBOX puede sacar `/v2` sin romper nada.
2. **Generar el cliente C# desde `/openapi.json`** (Kiota o NSwag). Un cambio de contrato se vuelve
   error de compilación, no bug en producción.
3. **Un solo punto de acoplamiento**: `CuboxApiService`. Ningún otro archivo .NET debe saber que
   CUBOX existe. Si algún día se reemplaza, se cambia una clase.
4. **Repos y despliegues separados.** CUBOX se actualiza sin tocar ni recompilar
   WEB_INTERFACES_CORE.
5. **Contract tests**: un test que valida que el `openapi.json` publicado sigue cumpliendo lo que
   .NET espera. Si CUBOX rompe el contrato, revienta en CI, no en producción.

**Resultado:** la actualización típica de CUBOX = desplegar CUBOX. Cero cambios en .NET. Solo
cuando cambie el contrato se regenera el cliente y se compila — y ahí el compilador dice
exactamente qué tocar.

---

## 17. Resumen ejecutivo

**Viabilidad: alta.** Lo propuesto es el patrón BFF estándar y encaja con la arquitectura existente.
El precedente exacto (`ClaudeApiService` → API FastAPI remota) **ya está en producción** en
WEB_INTERFACES_CORE.

**Tres correcciones al planteamiento inicial:**

1. El service llama a la API para obtener **datos**, no vistas. La vista 3D se hospeda, no se pide.
2. **No pasar el front a Blazor** — camino de máximo costo y máximo acoplamiento, opuesto al objetivo.
3. **No dar acceso a BD a CUBOX** — histórico y documentos en .NET, donde ya están las herramientas
   y el control.

**El único trabajo pesado real** es volver stateless la API de CUBOX. Sin eso no soporta dos
usuarios concurrentes, y todo lo demás depende de ello.
