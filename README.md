# EasyCargando — Cubicaje de ventanas en contenedores maritimos

MVP local (sin auth, sin cloud). Backend en Python/FastAPI, frontend en React + Three.js.

## Regla critica del negocio

Una ventana (Width x Height x Thickness) JAMAS puede quedar acostada sobre su cara
de vidrio (Width x Height). Solo se permiten las 2 orientaciones descritas en
`backend/app/core/orientation.py`. Esta regla esta cubierta por tests y se aplica
tanto en el algoritmo automatico como en el movimiento manual.

## Requisitos

- Python 3.11+ (probado con 3.14)
- Node.js 18+

## Backend

```bash
cd backend
py -m venv venv
./venv/Scripts/pip install -r requirements.txt
./venv/Scripts/python -m pytest app/tests -v   # correr tests
./venv/Scripts/python -m uvicorn app.main:app --port 8000
```

API en http://localhost:8000 (docs interactivos en http://localhost:8000/docs).

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Abre la URL que imprime Vite (por defecto http://localhost:5173, o el siguiente
puerto libre si ese esta ocupado).

## Uso

1. Selecciona un archivo Excel (.xlsx) con las columnas: Code, Description, Width,
   Height, Thickness, Weight, Quantity, System, Group, Stackable, Priority
   (dimensiones en mm, peso en kg). Hay un ejemplo en `examples/sample_windows.xlsx`.
2. Elige el contenedor y la preferencia de agrupacion.
3. Pulsa "Calcular cubicaje" para ver el resultado en 3D, las metricas y las
   piezas que no entraron (panel derecho).
4. Haz clic en una pieza en la vista 3D para ver sus detalles y moverla
   manualmente (el backend valida la nueva posicion).

## Estado del MVP

Implementado: import de Excel, catalogo de 3 contenedores, algoritmo de
cubicaje (heuristica de puntos de anclaje), apilamiento conservador, preferencia
de agrupacion por Group/System, vista 3D interactiva, movimiento manual validado,
panel de piezas no cargadas, panel de metricas.

Pendiente para iteraciones futuras: algoritmo de empaque mas sofisticado
(mejor aprovechamiento de volumen), persistencia de resultados, exportar
reporte, rotacion libre en el movimiento manual.
