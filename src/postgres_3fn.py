"""Materializa un modelo 3FN verificable en Supabase, sin tocar `public`.

    python src/postgres_3fn.py                # inspecciona y exporta DDL, sin escribir en BD
    python src/postgres_3fn.py --aplicar      # crea/sincroniza las 8 tablas en `juc_3fn`

Lee los datos de las tablas EXISTENTES en public, no de CSV ni de SQLite.
No usa DROP/DELETE/TRUNCATE, y no modifica las tablas usadas por run_all.py,
las 20 consultas originales o la integracion directa SQL + NoSQL.

El modelo analitico (public.fact_ventas) sigue desnormalizado a proposito.
El esquema `juc_3fn` es un modelo relacional fisico separado, defendible en 3FN.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from config import REPORTS, DATA, config_pg

SCHEMA = "juc_3fn"
TABLAS = (
    "categorias", "dim_ciudades", "dim_sucursales", "clientes", "productos",
    "pedidos", "detalle_pedido", "pagos",
)

# Las dimensiones NO repiten su nombre en las tablas hijas. `id_sucursal`
# apunta a la ciudad en vez de repetir ciudad / region / region_origen.
# Las columnas calculadas del modelo original se obtienen en vistas.
DDL = [
    "CREATE SCHEMA IF NOT EXISTS juc_3fn",
    """CREATE TABLE IF NOT EXISTS juc_3fn.categorias (
       id_categoria INTEGER PRIMARY KEY,
       nombre VARCHAR(80) NOT NULL UNIQUE,
       descripcion VARCHAR(200)
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.dim_ciudades (
       id_ciudad INTEGER PRIMARY KEY,
       nombre VARCHAR(80) NOT NULL UNIQUE,
       region VARCHAR(50)
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.dim_sucursales (
       id_sucursal INTEGER PRIMARY KEY,
       nombre VARCHAR(120) NOT NULL UNIQUE,
       id_ciudad INTEGER NOT NULL REFERENCES juc_3fn.dim_ciudades(id_ciudad),
       metros_cuadrados INTEGER CHECK (metros_cuadrados >= 0),
       fecha_apertura DATE
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.clientes (
       id_cliente INTEGER PRIMARY KEY,
       nombre VARCHAR(160) NOT NULL,
       correo VARCHAR(160) NOT NULL,
       telefono VARCHAR(20),
       id_ciudad INTEGER REFERENCES juc_3fn.dim_ciudades(id_ciudad),
       fecha_alta DATE,
       canal_registro VARCHAR(30),
       es_premium BOOLEAN
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.productos (
       id_producto INTEGER PRIMARY KEY,
       nombre VARCHAR(200) NOT NULL UNIQUE,
       id_categoria INTEGER NOT NULL REFERENCES juc_3fn.categorias(id_categoria),
       precio NUMERIC(12,2) NOT NULL CHECK (precio > 0),
       margen_bruto NUMERIC(5,4) CHECK (margen_bruto BETWEEN 0 AND 1),
       id_proveedor INTEGER,
       activo BOOLEAN,
       fecha_lanzamiento DATE
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.pedidos (
       id_pedido INTEGER PRIMARY KEY,
       id_cliente INTEGER NOT NULL REFERENCES juc_3fn.clientes(id_cliente),
       fecha_pedido DATE NOT NULL,
       canal_venta VARCHAR(30),
       id_sucursal INTEGER REFERENCES juc_3fn.dim_sucursales(id_sucursal),
       estado VARCHAR(20)
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.detalle_pedido (
       id_detalle INTEGER PRIMARY KEY,
       id_pedido INTEGER NOT NULL REFERENCES juc_3fn.pedidos(id_pedido),
       id_producto INTEGER NOT NULL REFERENCES juc_3fn.productos(id_producto),
       cantidad INTEGER NOT NULL CHECK (cantidad > 0),
       precio_unitario NUMERIC(12,2) NOT NULL CHECK (precio_unitario >= 0),
       descuento NUMERIC(12,2) NOT NULL DEFAULT 0
           CHECK (descuento >= 0 AND descuento <= cantidad * precio_unitario)
    )""",
    """CREATE TABLE IF NOT EXISTS juc_3fn.pagos (
       id_pago INTEGER PRIMARY KEY,
       id_pedido INTEGER NOT NULL REFERENCES juc_3fn.pedidos(id_pedido),
       metodo VARCHAR(30) NOT NULL,
       monto NUMERIC(14,2) NOT NULL CHECK (monto >= 0),
       fecha_pago DATE,
       estado_pago VARCHAR(20)
    )""",
    # Valores derivados: una fila por detalle, sin almacenar redundancias.
    """CREATE OR REPLACE VIEW juc_3fn.v_lineas_venta AS
       SELECT d.id_detalle, d.id_pedido, p.id_cliente, d.id_producto,
              pr.id_categoria, p.id_sucursal, p.fecha_pedido,
              EXTRACT(YEAR FROM p.fecha_pedido)::INTEGER AS anio,
              EXTRACT(MONTH FROM p.fecha_pedido)::INTEGER AS mes,
              TO_CHAR(p.fecha_pedido, 'YYYY-MM') AS anio_mes,
              p.estado,
              (p.estado IN ('completado','enviado')) AS es_completado,
              d.cantidad, d.precio_unitario, d.descuento,
              (d.cantidad * d.precio_unitario)::NUMERIC(14,2) AS subtotal,
              (d.cantidad * d.precio_unitario - d.descuento)::NUMERIC(14,2) AS total,
              pr.precio, pr.margen_bruto,
              (pr.precio * (1 - pr.margen_bruto))::NUMERIC(12,2) AS costo_estimado
       FROM juc_3fn.detalle_pedido d
       JOIN juc_3fn.pedidos p ON p.id_pedido = d.id_pedido
       JOIN juc_3fn.productos pr ON pr.id_producto = d.id_producto""",
    """CREATE OR REPLACE VIEW juc_3fn.v_resumen_ventas AS
       SELECT anio_mes,
              COUNT(DISTINCT id_pedido) AS pedidos,
              COUNT(DISTINCT id_cliente) AS clientes,
              COUNT(DISTINCT id_producto) AS productos,
              SUM(cantidad) AS unidades,
              ROUND(SUM(total), 2) AS facturacion,
              ROUND(SUM(total) / NULLIF(COUNT(DISTINCT id_pedido),0), 2) AS ticket_promedio
       FROM juc_3fn.v_lineas_venta
       WHERE es_completado = TRUE
       GROUP BY anio_mes""",
]

# 8 origenes, una transicion conservadora. Los importes derivados no se copian.
SOURCES = {
    "categorias": "SELECT id_categoria, nombre, descripcion FROM public.categorias",
    "dim_ciudades": "SELECT id_ciudad, nombre, region FROM public.dim_ciudades",
    "dim_sucursales": """SELECT s.id_sucursal, s.nombre, c.id_ciudad,
                                 s.metros_cuadrados, s.fecha_apertura
                          FROM public.dim_sucursales s
                          JOIN public.dim_ciudades c ON c.nombre = s.ciudad""",
    "clientes": """SELECT id_cliente, nombre, correo, telefono, id_ciudad,
                          fecha_alta, canal_registro, es_premium FROM public.clientes""",
    "productos": """SELECT id_producto, nombre, id_categoria, precio, margen_bruto,
                           id_proveedor, activo, fecha_lanzamiento FROM public.productos""",
    "pedidos": """SELECT id_pedido, id_cliente, fecha_pedido, canal_venta,
                         id_sucursal, estado FROM public.pedidos""",
    "detalle_pedido": """SELECT id_detalle, id_pedido, id_producto, cantidad,
                                 precio_unitario, descuento FROM public.detalle_pedido""",
    "pagos": """SELECT id_pago, id_pedido, metodo, monto, fecha_pago,
                       estado_pago FROM public.pagos""",
}

PKEYS = {
    "categorias": "id_categoria", "dim_ciudades": "id_ciudad",
    "dim_sucursales": "id_sucursal", "clientes": "id_cliente",
    "productos": "id_producto", "pedidos": "id_pedido",
    "detalle_pedido": "id_detalle", "pagos": "id_pago",
}


def crear_motor():
    """Conecta únicamente a PostgreSQL real; sin fallback SQLite."""
    cfg = config_pg()
    if not cfg["host"] or not cfg["password"]:
        raise RuntimeError("Configura SUPABASE_HOST y SUPABASE_PASSWORD en .env")
    url = URL.create(
        "postgresql+psycopg2",
        username=cfg["user"], password=cfg["password"],
        host=cfg["host"], port=cfg["port"], database=cfg["database"],
    )
    motor = create_engine(
        url, pool_pre_ping=True,
        connect_args={"connect_timeout": 10,
                      "sslmode": os.getenv("PG_SSLMODE", "require")},
    )
    with motor.connect() as conn:
        engine = conn.execute(text("SELECT version()" )).scalar_one()
        if "PostgreSQL" not in engine:
            raise RuntimeError("El motor no es PostgreSQL")
    return motor


def _columnas(sql: str) -> list[str]:
    """Recupera las columnas de SELECT simples sin inspeccionar datos de la BD."""
    inicio = sql.strip().split("FROM", 1)[0]
    return [c.strip().split(".")[-1] for c in inicio[len("SELECT "):].replace("\n", " ").split(",")]


def sentencias_carga() -> list[str]:
    sqls = []
    for tabla in TABLAS:
        origen = SOURCES[tabla]
        columnas = _columnas(origen)
        nombres = ", ".join(columnas)
        setters = ", ".join(f"{c} = EXCLUDED.{c}" for c in columnas if c != PKEYS[tabla])
        sqls.append(
            f"INSERT INTO juc_3fn.{tabla} ({nombres})\n{origen}\nWHERE TRUE\n"
            f"ON CONFLICT ({PKEYS[tabla]}) DO UPDATE SET {setters}"
        )
    return sqls


def validar_fuente(conn) -> dict[str, int]:
    # Tras migrar public, este modulo historico ya no debe resincronizarse:
    # la fuente ahora vive normalizada en el propio esquema principal.
    normalizada = conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='dim_sucursales'
          AND column_name='id_ciudad'
    """)).scalar_one()
    if normalizada:
        raise RuntimeError("public ya se normalizo; no es necesario resincronizar juc_3fn")
    cantidades = {}
    for tabla in TABLAS:
        cantidad = conn.execute(text(f"SELECT COUNT(*) FROM public.{tabla}")).scalar_one()
        if not cantidad:
            raise RuntimeError(f"public.{tabla} esta vacia: ejecuta la integracion original primero")
        cantidades[tabla] = int(cantidad)
    resultado = conn.execute(text(
        "SELECT COUNT(*) FROM public.dim_sucursales s "
        "LEFT JOIN public.dim_ciudades c ON c.nombre=s.ciudad "
        "WHERE c.id_ciudad IS NULL"
    )).scalar_one()
    if resultado:
        raise RuntimeError("Hay sucursales con ciudad sin catalogo: revisar antes de normalizar")
    return cantidades


def reporte_verificacion(conn, esperadas: dict[str, int]) -> pd.DataFrame:
    filas = []
    for tabla in TABLAS:
        observado = int(conn.execute(text(f"SELECT COUNT(*) FROM juc_3fn.{tabla}")).scalar_one())
        filas.append({
            "tabla": tabla, "registros_public": esperadas[tabla],
            "registros_3fn": observado, "coincide": observado == esperadas[tabla],
        })
    return pd.DataFrame(filas)


def ejecutar(aplicar: bool = False):
    archivo = DATA / "sql" / "05_modelo_3fn.sql"
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(
        "-- Modelo fisico 3FN, aislado en juc_3fn; no modifica public.\n"
        "-- Ejecutar despues de que public este poblado.\n\n"
        + ";\n\n".join(DDL + sentencias_carga()) + ";\n",
        encoding="utf-8",
    )
    if not aplicar:
        print(f"DDL exportado a {archivo}. Sin cambios en Supabase.")
        print("Para aplicar y verificar: python src/postgres_3fn.py --aplicar")
        return

    motor = crear_motor()
    try:
        with motor.begin() as conn:
            esperadas = validar_fuente(conn)
            for ddl in DDL[:9]:  # esquema + ocho tablas
                conn.execute(text(ddl))
            for sentencia in sentencias_carga():
                conn.execute(text(sentencia))
            for ddl in DDL[9:]:  # vistas dependen de los datos y las tablas
                conn.execute(text(ddl))
            resultado = reporte_verificacion(conn, esperadas)
            if not resultado.coincide.all():
                raise RuntimeError("Conteos 3FN distintos de public. "
                                   "No se borra nada automaticamente; "
                                   "revisa si hay registros antiguos en juc_3fn.")
        salida = REPORTS / "normalizacion_3fn_verificacion.csv"
        salida.parent.mkdir(parents=True, exist_ok=True)
        resultado.to_csv(salida, index=False, encoding="utf-8-sig")
        (REPORTS / "normalizacion_3fn_evidencia.json").write_text(
            json.dumps({
                "fecha_utc": datetime.now(timezone.utc).isoformat(),
                "motor": "PostgreSQL", "esquema": SCHEMA,
                "origen": "public (consultado directamente)",
                "tablas": {r["tabla"]: int(r["registros_3fn"]) for r in resultado.to_dict("records")},
                "sin_modificar_public": True,
                "modelo_analitico_existente": "public.fact_ventas (se conserva)",
            }, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(resultado.to_string(index=False))
        print(f"OK. Modelo 3FN creado y verificado: {salida}")
    finally:
        motor.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Modelo 3FN paralelo en PostgreSQL/Supabase")
    parser.add_argument("--aplicar", action="store_true", help="Crea/sincroniza juc_3fn y genera evidencias")
    args = parser.parse_args()
    ejecutar(args.aplicar)
