# Corporativo JUC — Pipeline de datos

**ETL + PostgreSQL + MongoDB + Cloud Computing, de fuentes crudas a un reporte interactivo.**
Proyecto de ingeniería de datos sobre una empresa ficticia de electrónica (México),
reproducible con una sola orden y una semilla fija (`SEED = 20260904`).

| En un vistazo | |
|---|---|
| Datos | 3 fuentes (CSV / JSON / Google Sheets→`.xlsx`) con errores intencionales |
| Limpieza | 10 transformaciones auditables + evidencia (93 filas) |
| Modelo | PostgreSQL 3FN (9 tablas: 8 operativas + fact_ventas, más la vista v_resumen_ventas) · MongoDB (3 colecciones, 12 índices) |
| Preguntas de negocio | 20 SQL + 12 pipelines + 15 `find`/`count` (**47/47 OK**) |
| Indicadores | 22 KPIs |
| Visualizaciones | 6 gráficos Plotly interactivos en `reporte.html` |
| Pruebas | 213 (pytest) |
| Cloud | **Supabase** (PostgreSQL 17) + **MongoDB Atlas** M0 — o modo local sin credenciales |

Para el recorrido técnico y la reproducibilidad, usa este README y la evidencia
generada en `data/reports/`. Este README es la guía mínima para ejecutarlo.

## Inicio rápido (sin credenciales)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\run_all.py
```

El orquestador ejecuta `generar -> etl -> integracion -> analisis -> graficos` y
**se detiene en la primera etapa que falle**. Al terminar abre
**`data/reports/reporte.html`** (doble clic): el reporte con los 6 gráficos, o combina
lo que ya se generó si prefieres revisar paso a paso.

### Comandos esenciales

```powershell
.\.venv\Scripts\python.exe src\run_all.py --listar        # ver etapas
.\.venv\Scripts\python.exe src\run_all.py --sin-generar   # reutilizar data/raw
.\.venv\Scripts\python.exe src\run_all.py --solo analisis graficos
.\.venv\Scripts\python.exe src\run_all.py --con-tests     # pipeline + pytest
.\.venv\Scripts\python.exe -m pytest -q                   # solo la suite (213)
```

## Configuracion para mi companero (conexion Cloud)

El repositorio no trae credenciales. Para conectar Supabase y Atlas:

```powershell
copy .env.example .env   # luego edita .env localmente; nunca lo agregues a git
```

Completa solo estos valores:

- `SUPABASE_HOST`, `SUPABASE_USER`, `SUPABASE_PASSWORD`
- `MONGO_ATLAS_URI` (MongoDB Atlas) o `MONGO_URI`
- `GOOGLE_SHEETS_SUCURSALES_URL` (opcional, o publica una hoja con acceso por enlace)

Valores esperados actuales:

- Supabase: Session Pooler (`aws-0-<region>.pooler.supabase.com`, `PG_SSLMODE=require`).
- Atlas: la base se llama `Prueba1` (`MONGO_DB=Prueba1`).

Validacion en modo **solo lectura**, recomendada antes de correr cualquier otra cosa:

```powershell
.\.venv\Scripts\python.exe src\validar_postgres_cloud.py --plan
.\.venv\Scripts\python.exe src\validar_postgres_cloud.py --ejecutar
.\.venv\Scripts\python.exe src\integracion_real.py --exigir-cloud
```

No ejecutar `run_all.py` sin `--solo etl analisis graficos` ni los scripts `--aplicar-*`
si no quieres reconstruir/sincronizar las bases en la nube.

Modo local sin credenciales: fuerza local con `SIN_POSTGRES=1`, `SIN_MONGO=1` (o `FORZAR_SQLITE=1`).
`.env` nunca se versiona; `.env.example` es la plantilla.

## Estructura

```text
src/            generate_sources → etl_extract → etl_transform → etl_load
                → integration → analysis → viz   (+ run_all.py, config.py, utils.py)
tests/          213 pruebas (analysis, integration, run_all, transform)
data/raw/       fuentes crudas (CSV / JSON / Sheets)
data/processed/ tablas normalizadas 3FN + datasets para cada motor
data/sql/       DDL, consultas, EXPLAIN ANALYZE (layout del enunciado)
data/nosql/     colecciones NDJSON, índices, pipelines y consultas
data/reports/   evidencia + KPIs + reporte.html
data/figures/   6 HTML interactivos + plotly.min.js
```

## Resultados de la última corrida (semilla `SEED = 20260904`)

| Indicador | Valor |
|---|---|
| Facturación | $127,336,206.51 |
| Pedidos completados / totales | 1,483 / 2,259 |
| Ticket promedio | $85,863.93 |
| Cancelación | 34.35% |
| Margen real ponderado | 29.46% |
| Clientes activos | 1,314 |
| Productos vendidos | 514 de 606 (Pareto: 124 explican el 80%) |

## Notas

- **Datos sintéticos**: las conclusiones describen la empresa ficticia, no un negocio real.
- Verificado localmente con 213 pruebas y 6 figuras/reportes generados correctamente.
- **Con las credenciales configuradas, el pipeline puede escribir/leer Supabase y Atlas**.
  Usa `--solo etl analisis graficos` para el recorrido reproducible de solo procesamiento,
  o el notebook de Colab, que fue preparado para validar la nube en modo solo lectura.
- Reporte interactivo: **`data/reports/reporte.html`** · evidencia: `data/reports/`.
- `informe.md` / `MEJORAS_PRIORIDAD_MEDIA.md` no forman parte de esta versión del repositorio
  y no deben tratarse como dependencias de ejecución.
