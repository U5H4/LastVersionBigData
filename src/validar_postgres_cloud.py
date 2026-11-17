"""Validacion de SOLO LECTURA de las 20 consultas SQL sobre Supabase.

Uso (desde Proyecto_BigData-main):
    python src/validar_postgres_cloud.py --plan
    python src/validar_postgres_cloud.py --ejecutar

No ejecuta DDL, DML ni cambios de indices. Toda consulta se procesa
con una transaccion PostgreSQL marcada READ ONLY y timeout de 90 segundos.
Exporta CSV/JSON y tres planes EXPLAIN ANALYZE (solo SELECT).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from config import REPORTS, ROOT
from integracion_real import conectar_postgres

ARCHIVO_SQL = ROOT / "data" / "sql" / "02_consultas.sql"
SALIDA = REPORTS / "validacion_postgres_cloud"
TABLAS = (
    "categorias", "dim_ciudades", "dim_sucursales", "clientes",
    "productos", "pedidos", "detalle_pedido", "pagos", "fact_ventas",
)
VISTAS = (
    "v_detalle_calculado", "v_pedidos_calculados",
    "v_pagos_conciliados", "v_resumen_ventas",
)
INDICES_REVISAR = ("ix_fact_anio_mes", "ix_fact_producto", "ix_pedidos_fecha_pedido")
CONTEOS_REFERENCIA = {
    "categorias": 6, "dim_ciudades": 10, "dim_sucursales": 12,
    "clientes": 1862, "productos": 606, "pedidos": 3277,
    "detalle_pedido": 3891, "pagos": 3277, "fact_ventas": 3891,
}
ENCABEZADO = re.compile(r"(?m)^--\s*(Q\d{2})\s*\|\s*([^\r\n]+)\s*$")
LIMITE_MS = 90_000


# El archivo fuente solo puede contener 20 instrucciones de lectura.
def validar_select(sentencia: str) -> str:
    """Rechaza instrucciones extra y consultas no SELECT, incluidos WITH DML."""
    sin_comentarios = re.sub(r"(?m)^\s*--[^\n]*$", "", sentencia).strip()
    sql = sin_comentarios.removesuffix(";").strip()
    if not re.match(r"\A(?:SELECT|WITH)\b", sql, re.I):
        raise ValueError("El archivo SQL contiene una instruccion que no comienza por SELECT/WITH")
    if ";" in sql:
        raise ValueError("Solo se permite una sentencia SQL por consulta")
    # SQL analitico existente: SELECT simples y WITH ... SELECT (sin CTE de escritura).
    if re.search(r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|DROP|ALTER|CALL|COPY)\b", sql, re.I):
        raise ValueError("No se permiten instrucciones de escritura en la validacion")
    if re.search(r"\b(?:INTO|FOR\s+UPDATE|FOR\s+SHARE)\b", sql, re.I):
        raise ValueError("No se permiten bloqueos ni SELECT INTO")
    return sql


def cargar_consultas(ruta: Path = ARCHIVO_SQL) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el SQL de consultas: {ruta}")
    contenido = ruta.read_text(encoding="utf-8-sig")
    separadores = list(ENCABEZADO.finditer(contenido))
    salida = []
    for i, m in enumerate(separadores):
        fin = separadores[i + 1].start() if i + 1 < len(separadores) else len(contenido)
        salida.append({
            "id": m.group(1),
            "titulo": m.group(2).strip(),
            "sql": validar_select(contenido[m.end():fin]),
        })
    ids = [q["id"] for q in salida]
    previstos = [f"Q{i:02d}" for i in range(1, 21)]
    if ids != previstos:
        raise ValueError(f"Se esperaban exactamente las consultas Q01 a Q20; encontradas: {ids}")
    return salida


def tx_solo_lectura(conn) -> None:
    """Se invoca al INICIO de cada transaccion antes de consultar."""
    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    conn.exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
    conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{LIMITE_MS}ms'")


def consultar(engine, sql: str, params: dict | None = None) -> tuple[list[str], list[tuple]]:
    """Cada instruccion corre en su transaccion READ ONLY independiente."""
    with engine.connect() as conn:
        with conn.begin():
            tx_solo_lectura(conn)
            res = conn.execute(text(sql), params or {})
            columnas = list(res.keys())
            filas = [tuple(row) for row in res.fetchall()]
    return columnas, filas


def escribir_csv(ruta: Path, columnas: list[str], filas: list[tuple]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columnas)
        for fila in filas:
            writer.writerow([x.isoformat() if hasattr(x, "isoformat") else x for x in fila])


def obtener_esquema(engine) -> dict[str, Any]:
    # Valores de nombres de tabla son constantes, no entradas del usuario.
    _, vistas_raw = consultar(engine, """
        SELECT table_name FROM information_schema.views
        WHERE table_schema = 'public' ORDER BY table_name
    """)
    vistas = {r[0] for r in vistas_raw}
    cols, ix_raw = consultar(engine, """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes WHERE schemaname = 'public'
        ORDER BY tablename, indexname
    """)
    indices = [dict(zip(cols, row)) for row in ix_raw]
    nombres_indices = {r["indexname"] for r in indices}
    _, tablas_raw = consultar(engine, """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'
    """)
    tablas_fisicas = {r[0] for r in tablas_raw}
    conteos = {}
    for tabla in TABLAS:
        if tabla in tablas_fisicas:
            _, filas = consultar(engine, f"SELECT COUNT(*) FROM public.{tabla}")
            conteos[tabla] = int(filas[0][0])
    faltan_tablas = sorted(set(TABLAS) - tablas_fisicas)
    faltan_vistas = sorted(set(VISTAS) - vistas)
    faltan_indices = sorted(set(INDICES_REVISAR) - nombres_indices)
    avisos = [
        f"{t}: conteo Cloud {conteos[t]} distinto a referencia {ref}"
        for t, ref in CONTEOS_REFERENCIA.items()
        if t in conteos and conteos[t] != ref
    ]
    return {
        "tablas": conteos,
        "vistas_encontradas": sorted(set(VISTAS) & vistas),
        "indices": indices,
        "indices_revisados": sorted(set(INDICES_REVISAR) & nombres_indices),
        "faltan_tablas": faltan_tablas,
        "faltan_vistas": faltan_vistas,
        "faltan_indices": faltan_indices,
        "avisos": avisos,
        "estructura_ok": not (faltan_tablas or faltan_vistas or faltan_indices),
    }


def consulta_explain(sql: str) -> str:
    return "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + validar_select(sql)


# Evidencia del plan PostgreSQL. NO se fuerza al optimizador, NO se eliminan indices.
PRUEBAS_EXPLAIN = (
    ("E01", "Indice por periodo", "SELECT COUNT(*) FROM public.fact_ventas WHERE anio_mes = '2024-08'"),
    ("E02", "Indice por producto", "SELECT SUM(cantidad) FROM public.fact_ventas WHERE id_producto = 324"),
    ("E03", "JOIN de tablas 3FN (Q19)", None),
)


def guardar_json(ruta: Path, registro: Any) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(registro, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def ejecutar(plan: bool = False, ejecutar_sql: bool = False,
             ruta_sql: Path = ARCHIVO_SQL, salida: Path = SALIDA) -> dict[str, Any]:
    if plan == ejecutar_sql:
        raise ValueError("Selecciona exactamente --plan o --ejecutar")
    consultas = cargar_consultas(ruta_sql)
    # Conectar SIEMPRE a Supabase real. No admite modo SQLite alterno.
    motor, _conexion = conectar_postgres(exigir_cloud=True)
    try:
        esquema = obtener_esquema(motor)
        marca = datetime.now(timezone.utc).isoformat()
        base = {
            "fecha_utc": marca,
            "motor": "PostgreSQL (Supabase)",
            "modo": "SOLO_LECTURA",
            "consultas_detectadas": len(consultas),
            "tablas": esquema["tablas"],
            "vistas_encontradas": esquema["vistas_encontradas"],
            "indices_revisados": esquema["indices_revisados"],
            "indices_totales_public": len(esquema["indices"]),
            "faltan_tablas": esquema["faltan_tablas"],
            "faltan_vistas": esquema["faltan_vistas"],
            "faltan_indices": esquema["faltan_indices"],
            "avisos": esquema["avisos"],
        }
        escribir_csv(salida / "00_indices_public.csv",
                    ["tabla", "indice", "definicion"],
                    [(i["tablename"], i["indexname"], i["indexdef"])
                     for i in esquema["indices"]])
        if plan:
            base["estado"] = "PLAN_OK_SIN_CAMBIOS" if esquema["estructura_ok"] else "REVISAR_ESTRUCTURA"
            guardar_json(salida / "plan_validacion.json", base)
            return base
        if not esquema["estructura_ok"]:
            base["estado"] = "BLOQUEADO_POR_ESTRUCTURA"
            guardar_json(salida / "resultado_validacion.json", base)
            return base

        resultados: list[dict] = []
        for q in consultas:
            try:
                columnas, filas = consultar(motor, q["sql"])
                escribir_csv(salida / "consultas" / f"{q['id']}.csv", columnas, filas)
                resultados.append({"consulta": q["id"], "titulo": q["titulo"],
                                   "estado": "OK", "filas_resultado": len(filas),
                                   "archivo": f"consultas/{q['id']}.csv"})
            except Exception as exc:
                # Es un reporte de validacion; continuar para localizar todas las fallas.
                resultados.append({"consulta": q["id"], "titulo": q["titulo"],
                                   "estado": "ERROR", "error": type(exc).__name__ + ": " + str(exc).splitlines()[0][:250]})

        escribir_csv(salida / "01_resumen_20_consultas.csv",
                    ["consulta", "titulo", "estado", "filas_resultado", "error"],
                    [(r["consulta"], r["titulo"], r["estado"],
                      r.get("filas_resultado", ""), r.get("error", "")) for r in resultados])

        q19 = next(q["sql"] for q in consultas if q["id"] == "Q19")
        planes = []
        for clave, titulo, sql in PRUEBAS_EXPLAIN:
            try:
                _, filas = consultar(motor, consulta_explain(sql or q19))
                lineas = [str(r[0]) for r in filas]
                nombre = f"{clave}_explain_analyze.txt"
                (salida / nombre).write_text("\n".join(lineas) + "\n", encoding="utf-8")
                planes.append({"prueba": clave, "titulo": titulo, "estado": "OK",
                               "archivo": nombre,
                               "indices_en_plan": sorted({i["indexname"] for i in esquema["indices"]
                                                         if i["indexname"] in "\n".join(lineas)})})
            except Exception as exc:
                planes.append({"prueba": clave, "titulo": titulo, "estado": "ERROR",
                               "error": type(exc).__name__ + ": " + str(exc).splitlines()[0][:250]})
        escribir_csv(salida / "02_resumen_explain.csv",
                    ["prueba", "titulo", "estado", "indices_en_plan", "error"],
                    [(p["prueba"], p["titulo"], p["estado"],
                      ", ".join(p.get("indices_en_plan", [])), p.get("error", "")) for p in planes])
        buenas = sum(x["estado"] == "OK" for x in resultados)
        planes_ok = sum(x["estado"] == "OK" for x in planes)
        base.update({
            "estado": "OK" if buenas == 20 and planes_ok == 3 else "CON_ERRORES",
            "consultas_ok": buenas,
            "consultas_total": 20,
            "explain_ok": planes_ok,
            "explain_total": 3,
            "consultas": resultados,
            "planes_explain": planes,
            "evidencias": str(salida),
        })
        guardar_json(salida / "resultado_validacion.json", base)
        return base
    finally:
        motor.dispose()


def main() -> None:
    cli = argparse.ArgumentParser(description="Valida SQL real de Supabase sin modificar datos")
    grupo = cli.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--plan", action="store_true", help="Solo verifica tablas, vistas e indices")
    grupo.add_argument("--ejecutar", action="store_true", help="Ejecuta las 20 SELECT y los EXPLAIN ANALYZE")
    args = cli.parse_args()
    resultado = ejecutar(plan=args.plan, ejecutar_sql=args.ejecutar)
    # Evita volcar las 20 consultas completas en consola.
    resumen = {k: v for k, v in resultado.items() if k not in ("consultas", "planes_explain")}
    print(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))
    if resultado["estado"] not in ("OK", "PLAN_OK_SIN_CAMBIOS"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
