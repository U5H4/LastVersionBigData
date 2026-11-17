"""Sincronizacion controlada de ETL local a Supabase public 3FN y MongoDB Atlas.

NUNCA ejecuta DROP, DELETE, TRUNCATE ni reconstruye el esquema public.
Respalda los datos anteriores antes de actualizar; bloquea cambios cuando
los conjuntos de identificadores no coinciden (carga 1:1, sin altas/bajas).

Uso (desde la raiz del proyecto):
  python src/sincronizar_cloud.py --plan
  python src/sincronizar_cloud.py --aplicar-sql --confirmar SINCRONIZAR_SQL
  python src/sincronizar_cloud.py --aplicar-mongo --confirmar SINCRONIZAR_MONGO
  python src/sincronizar_cloud.py --verificar

SQL y MongoDB NO comparten transaccion. Los comandos de escritura son
independientes e idempotentes. Si falla la actualizacion de MongoDB, la
actualizacion SQL (si ya se habia completado) no se revierte automaticamente.
El plan crea tablas TEMPORALES en PostgreSQL dentro de una transaccion que se
revierte; no crea ni modifica objetos permanentes.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import inspect, text

from config import MONGO_ATLAS_URI, MONGO_URI, MONGO_DB, PROCESSED, REPORTS, config_pg
from etl_load import ESQUEMA_SQL_3FN, ORDEN_CARGA, TABLAS_DIR, tablas_fisicas_3fn
from integration import CLAVES_PRIMARIAS
from postgres_3fn import crear_motor

TABLAS = [*ORDEN_CARGA, "fact_ventas"]
COLECCIONES = ("productos", "resenas", "actividad_usuario")
CONF_SQL = "SINCRONIZAR_SQL"
CONF_MONGO = "SINCRONIZAR_MONGO"
# Un evento no dispone de ID propio: se usa una clave natural estable que NO
# depende de la fecha (precisamente uno de los campos corregidos por el ETL).
CLAVES_MONGO = {
    "productos": ("id_producto",),
    "resenas": ("id_resena",),
    "actividad_usuario": ("usuario", "evento", "producto", "dispositivo", "duracion_seg"),
}
REPORTE = REPORTS / "sincronizacion_cloud"


def _ident(s: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", s):
        raise ValueError("Identificador SQL invalido")
    return s


def _momento() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _salvar(nombre: str, datos: dict) -> Path:
    REPORTE.mkdir(parents=True, exist_ok=True)
    ruta = REPORTE / nombre
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return ruta


def _cargar_local() -> tuple[dict[str, pd.DataFrame], dict[str, list[dict]]]:
    """Solo lee productos del ETL que ya se generaron; NO ejecuta ETL."""
    tablas = {}
    for t in ORDEN_CARGA:
        path = TABLAS_DIR / f"{t}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"Falta {path}. Ejecuta SOLO ETL antes de sincronizar")
        tablas[t] = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    fisicas = tablas_fisicas_3fn(tablas)
    path_fact = PROCESSED / "dataset_postgresql.csv"
    path_mongo = PROCESSED / "dataset_mongodb.json"
    if not path_fact.is_file() or not path_mongo.is_file():
        raise FileNotFoundError("Faltan datasets procesados del ETL")
    fisicas["fact_ventas"] = pd.read_csv(path_fact, encoding="utf-8-sig", low_memory=False)
    docs = json.loads(path_mongo.read_text(encoding="utf-8"))
    if set(docs) != set(COLECCIONES):
        raise RuntimeError("Las colecciones locales no coinciden con el contrato")
    for t, df in fisicas.items():
        pk = CLAVES_PRIMARIAS[t]
        if df.empty or pk not in df or df[pk].isna().any() or df[pk].duplicated().any():
            raise RuntimeError(f"IDs vacios, duplicados o ausentes en {t}.{pk}")
    for nombre, col in docs.items():
        if not col or not isinstance(col, list):
            raise RuntimeError(f"Coleccion local vacia o invalida: {nombre}")
        _mapa_mongo(nombre, col)
    return fisicas, docs


def _columnas_sql(conn, tabla: str) -> list[str]:
    """Las columnas de PostgreSQL son el contrato, no el CSV ancho del ETL."""
    cols = [c["name"] for c in inspect(conn).get_columns(tabla, schema="public")]
    if not cols:
        raise RuntimeError(f"No existe la tabla public.{tabla} en Supabase")
    return cols


def _preparar_tablas_temporales(conn, fisicas: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Copia datos a tablas TEMP, verifica IDs y calcula diferencias exactas."""
    planes = {}
    for t in TABLAS:
        t = _ident(t)
        pk = _ident(CLAVES_PRIMARIAS[t])
        columnas = _columnas_sql(conn, t)
        df = fisicas[t]
        if set(columnas) != set(df.columns):
            raise RuntimeError(f"{t}: diferencias de columnas; solo en SQL={sorted(set(columnas)-set(df.columns))}, solo en ETL={sorted(set(df.columns)-set(columnas))}")
        df = df[columnas]
        tmp = _ident("_juc_stage_" + t)
        conn.execute(text(f"CREATE TEMP TABLE {tmp} (LIKE public.{t}) ON COMMIT DROP"))
        # COPY respeta la transaccion SQLAlchemy; Pandas pasa vacios a NULL.
        buffer = io.StringIO()
        df.to_csv(buffer, index=False, header=True, na_rep=r"\N", quoting=csv.QUOTE_MINIMAL,
                  date_format="%Y-%m-%d")
        buffer.seek(0)
        cols = ", ".join(_ident(c) for c in columnas)
        with conn.connection.driver_connection.cursor() as cursor:
            cursor.copy_expert(
                f"COPY pg_temp.{tmp} ({cols}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '\\N')",
                buffer,
            )
        conteo = int(conn.execute(text(f"SELECT COUNT(*) FROM public.{t}")).scalar_one())
        n_local = int(conn.execute(text(f"SELECT COUNT(*) FROM pg_temp.{tmp}")).scalar_one())
        if conteo != n_local:
            raise RuntimeError(f"{t}: cantidad remota {conteo} != local {n_local}; no se permiten ALTAS/BAJAS automáticas")
        # Compara PK como conjuntos, además de los conteos: bloquea ID extra/faltante.
        dist_ids = int(conn.execute(text(f"""
            SELECT COUNT(*) FROM (
                (SELECT {pk} FROM public.{t} EXCEPT SELECT {pk} FROM pg_temp.{tmp})
                UNION ALL
                (SELECT {pk} FROM pg_temp.{tmp} EXCEPT SELECT {pk} FROM public.{t})
            ) x
        """)).scalar_one())
        if dist_ids:
            raise RuntimeError(f"{t}: {dist_ids} identificadores distintos entre local y remoto; ABORTADO")
        # Cuenta filas diferentes entre originales y ETL (incluidos nulos).
        sin_pk = [c for c in columnas if c != pk]
        dcols = ",".join(f"d.{_ident(c)}" for c in sin_pk)
        scols = ",".join(f"s.{_ident(c)}" for c in sin_pk)
        distintos = int(conn.execute(text(f"""
            SELECT COUNT(*) FROM public.{t} d
            JOIN pg_temp.{tmp} s USING ({pk})
            WHERE ROW({dcols}) IS DISTINCT FROM ROW({scols})
        """)).scalar_one())
        planes[t] = {"registros": n_local, "filas_diferentes": distintos,
                      "columnas": len(columnas)}
    return planes


def _comprobar_identidad_sql(conn, fisicas) -> dict:
    """No hace commits: crea TEMP tables que desaparecen con ROLLBACK."""
    conn.execute(text("SET LOCAL search_path TO public, pg_catalog"))
    return _preparar_tablas_temporales(conn, fisicas)


def _exigir_supabase() -> None:
    """Impide escribir por accidente a un PostgreSQL que no sea Supabase."""
    cfg = config_pg()
    host = (cfg.get("host") or "").lower().rstrip(".")
    if not (host.endswith(".supabase.com") or host.endswith(".supabase.co")):
        raise RuntimeError("Este sincronizador solo acepta SUPABASE_HOST de Supabase")


def plan_sql(fisicas: dict[str, pd.DataFrame]) -> dict:
    _exigir_supabase()
    motor = crear_motor()  # Sin respaldo SQLite.
    try:
        with motor.connect() as conn:
            # Inicia transaccion y hace rollback al salir del with sin commit.
            resultado = _comprobar_identidad_sql(conn, fisicas)
            conn.rollback()
        return {"motor": "PostgreSQL", "base": "Supabase", "tablas": resultado}
    finally:
        motor.dispose()


def aplicar_sql(fisicas: dict[str, pd.DataFrame]) -> dict:
    _exigir_supabase()
    motor = crear_motor()
    respaldo = _ident("juc_sync_backup_" + _momento())
    try:
        with motor.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO public, pg_catalog"))
            # Evita modificaciones concurrentes mientras se verifican y actualizan IDs.
            for t in TABLAS:
                conn.execute(text(f"LOCK TABLE public.{_ident(t)} IN SHARE ROW EXCLUSIVE MODE"))
            plan = _preparar_tablas_temporales(conn, fisicas)
            modificaciones = {t: p["filas_diferentes"] for t, p in plan.items()}
            if not any(modificaciones.values()):
                return {"estado": "SIN_CAMBIOS", "tablas": plan, "respaldo": None}
            # Snapshot de solo DATOS (no DDL), dentro de la misma transaccion.
            conn.execute(text(f"CREATE SCHEMA {respaldo}"))
            for t in TABLAS:
                conn.execute(text(f"CREATE TABLE {respaldo}.{t} AS TABLE public.{t}"))
            for t in TABLAS:
                pk = _ident(CLAVES_PRIMARIAS[t])
                cols = [c for c in _columnas_sql(conn, t) if c != pk]
                setters = ", ".join(f"{c} = s.{c}" for c in cols)
                a = ",".join(f"d.{c}" for c in cols)
                b = ",".join(f"s.{c}" for c in cols)
                if modificaciones[t]:
                    conn.execute(text(f"""
                        UPDATE public.{t} AS d SET {setters}
                        FROM pg_temp._juc_stage_{t} AS s
                        WHERE d.{pk}=s.{pk}
                          AND ROW({a}) IS DISTINCT FROM ROW({b})
                    """))
            # Comprueba igualdad de todos los datos y que no hay cambios de PK.
            despues = {}
            for t in TABLAS:
                pk = _ident(CLAVES_PRIMARIAS[t])
                cols = [c for c in _columnas_sql(conn, t) if c != pk]
                a = ",".join(f"d.{c}" for c in cols)
                b = ",".join(f"s.{c}" for c in cols)
                diferencias = int(conn.execute(text(f"""
                    SELECT COUNT(*) FROM public.{t} d
                    JOIN pg_temp._juc_stage_{t} s USING ({pk})
                    WHERE ROW({a}) IS DISTINCT FROM ROW({b})
                """)).scalar_one())
                if diferencias:
                    raise RuntimeError(f"public.{t}: {diferencias} filas distintas tras UPDATE; ROLLBACK")
                despues[t] = {"registros": plan[t]["registros"],
                              "filas_actualizadas": modificaciones[t]}
        return {"estado": "OK", "respaldo": respaldo,
                "nota_respaldo": "Snapshot de DATOS, no incluye DDL ni indices",
                "tablas": despues}
    finally:
        motor.dispose()


def _clave(doc: dict, campos: tuple[str, ...]) -> tuple:
    return tuple(doc.get(c) for c in campos)


def _mapa_mongo(nombre: str, docs: list[dict]) -> dict[tuple, dict]:
    campos = CLAVES_MONGO[nombre]
    mapa = {}
    for d in docs:
        if any(c not in d or d[c] is None for c in campos):
            raise RuntimeError(f"MongoDB {nombre}: documento sin clave {campos}")
        k = _clave(d, campos)
        if k in mapa:
            raise RuntimeError(f"MongoDB {nombre}: clave duplicada {k} (no se puede sincronizar sin borrar)")
        mapa[k] = d
    return mapa


def _contenido(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


def _abrir_mongo():
    """Reutiliza las credenciales de integracion_real; exige Atlas, nunca Mongo local."""
    if os.getenv("SIN_MONGO") == "1":
        raise RuntimeError("Desactiva SIN_MONGO para sincronizar Atlas")
    uri = (MONGO_ATLAS_URI or MONGO_URI).strip()
    if not uri.startswith("mongodb+srv://") or not MONGO_DB:
        raise RuntimeError(
            "Configura MONGO_URI o MONGO_ATLAS_URI (mongodb+srv://) y MONGO_DB en .env"
        )
    from pymongo import MongoClient
    c = MongoClient(uri, serverSelectionTimeoutMS=10000,
                    connectTimeoutMS=10000)
    try:
        c.admin.command("ping")
        return c, c[MONGO_DB]
    except Exception:
        c.close()
        raise


def _leer_y_planear_mongo(db, docs: dict[str, list[dict]]) -> tuple[dict, dict]:
    planes = {}
    remotos = {}
    existentes = set(db.list_collection_names())
    for nombre in COLECCIONES:
        if nombre not in existentes:
            raise RuntimeError(f"No existe MongoDB.{nombre}; no se crean colecciones automaticamente")
        col = list(db[nombre].find({}))
        a = _mapa_mongo(nombre, docs[nombre])
        b = _mapa_mongo(nombre, col)
        if set(a) != set(b):
            faltan_en_nube = len(set(a) - set(b))
            extras_nube = len(set(b) - set(a))
            raise RuntimeError(f"MongoDB.{nombre}: claves no coinciden (faltantes nube={faltan_en_nube}, extras nube={extras_nube}). No se borrara ni insertara automaticamente.")
        distintos = sum(_contenido(a[k]) != _contenido(b[k]) for k in a)
        planes[nombre] = {"documentos": len(a), "documentos_diferentes": distintos,
                         "clave_utilizada": list(CLAVES_MONGO[nombre])}
        remotos[nombre] = b
    return planes, remotos


def plan_mongo(docs: dict[str, list[dict]]) -> dict:
    cliente, db = _abrir_mongo()
    try:
        p, _ = _leer_y_planear_mongo(db, docs)
        return {"motor": "MongoDB Atlas", "base": MONGO_DB, "colecciones": p}
    finally:
        cliente.close()


def aplicar_mongo(docs: dict[str, list[dict]]) -> dict:
    from pymongo import ReplaceOne
    cliente, db = _abrir_mongo()
    stamp = _momento()
    try:
        planes, remotos = _leer_y_planear_mongo(db, docs)
        if not any(p["documentos_diferentes"] for p in planes.values()):
            return {"estado": "SIN_CAMBIOS", "colecciones": planes,
                    "respaldos": []}
        # Snapshot previo a la escritura, con _id original (incluye contenido
        # completo). No altera las colecciones originales ni sus indices.
        respaldos = []
        for nombre in COLECCIONES:
            backup = f"juc_backup_{stamp}_{nombre}"
            if backup in db.list_collection_names():
                raise RuntimeError(f"Ya existe el backup MongoDB: {backup}")
            db.create_collection(backup)
            originales = list(remotos[nombre].values())
            if originales:
                for inicio in range(0, len(originales), 500):
                    db[backup].insert_many(originales[inicio:inicio+500])
            if db[backup].count_documents({}) != len(originales):
                raise RuntimeError(f"Backup de MongoDB.{nombre} incompleto; NO se actualizaron los originales")
            respaldos.append(backup)
        actualizados = {}
        for nombre in COLECCIONES:
            origen = _mapa_mongo(nombre, docs[nombre])
            nube = remotos[nombre]
            ops = []
            total = 0
            for k, d in origen.items():
                anterior = nube[k]
                if _contenido(anterior) == _contenido(d):
                    continue
                nuevo = {**d, "_id": anterior["_id"]}
                ops.append(ReplaceOne({"_id": anterior["_id"]}, nuevo, upsert=False))
                if len(ops) >= 500:
                    r = db[nombre].bulk_write(ops, ordered=True)
                    if r.matched_count != len(ops):
                        raise RuntimeError(f"MongoDB.{nombre}: faltaron documentos al actualizar")
                    total += len(ops)
                    ops = []
            if ops:
                r = db[nombre].bulk_write(ops, ordered=True)
                if r.matched_count != len(ops):
                    raise RuntimeError(f"MongoDB.{nombre}: faltaron documentos al actualizar")
                total += len(ops)
            actualizados[nombre] = total
        # Verificacion de contenido 1:1 despues de escribir.
        verificacion, _ = _leer_y_planear_mongo(db, docs)
        if any(p["documentos_diferentes"] for p in verificacion.values()):
            raise RuntimeError("MongoDB: fallo validacion posterior; RESPALDOS CONSERVADOS")
        return {"estado": "OK", "respaldo_datos": respaldos,
                "advertencia": "MongoDB no comparte transaccion con PostgreSQL; respalda las colecciones antes de actualizar",
                "documentos_actualizados": actualizados}
    finally:
        cliente.close()


def ejecutar(args):
    if args.aplicar_sql and args.confirmar != CONF_SQL:
        raise RuntimeError(f"Para aplicar SQL utiliza --confirmar {CONF_SQL}")
    if args.aplicar_mongo and args.confirmar != CONF_MONGO:
        raise RuntimeError(f"Para aplicar Mongo utiliza --confirmar {CONF_MONGO}")
    tablas, docs = _cargar_local()
    if args.aplicar_sql:
        res = aplicar_sql(tablas)
        nombre = f"sql_{_momento()}.json"
    elif args.aplicar_mongo:
        res = aplicar_mongo(docs)
        nombre = f"mongo_{_momento()}.json"
    else:
        sql = plan_sql(tablas)
        mongo = plan_mongo(docs)
        if args.verificar:
            cambios_sql = sum(v["filas_diferentes"] for v in sql["tablas"].values())
            cambios_mongo = sum(v["documentos_diferentes"] for v in mongo["colecciones"].values())
            res = {"estado": "OK" if cambios_sql == cambios_mongo == 0 else "PENDIENTE",
                   "coincide_sql": cambios_sql == 0, "coincide_mongo": cambios_mongo == 0,
                   "postgresql": sql, "mongodb": mongo}
            nombre = "verificacion_final.json"
        else:
            res = {"estado": "PLAN_LISTO_SIN_ESCRITURA", "postgresql": sql, "mongodb": mongo}
            nombre = "plan_sincronizacion.json"
    ruta = _salvar(nombre, res)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    print(f"Evidencia: {ruta}")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Sincronizacion segura ETL -> Supabase y Atlas")
    grupo = cli.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--plan", action="store_true", help="Inspecciona diferencias sin modificar objetos permanentes")
    grupo.add_argument("--aplicar-sql", action="store_true", help="Sincroniza tablas public y fact (transaccional)")
    grupo.add_argument("--aplicar-mongo", action="store_true", help="Sincroniza documentos de Atlas con backups")
    grupo.add_argument("--verificar", action="store_true", help="Comprueba igualdad de datos en ambos destinos")
    cli.add_argument("--confirmar", default="", help="Confirmacion expresa para escrituras")
    ejecutar(cli.parse_args())