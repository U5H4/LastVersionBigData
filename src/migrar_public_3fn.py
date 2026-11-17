"""Migra las tablas originales de public a 3FN de forma transaccional.

No consulta CSV ni reconstruye MongoDB. Conserva una COPIA FISICA de las
nueve tablas originales y de su vista en un esquema de respaldo.

  python src/migrar_public_3fn.py --plan
  python src/migrar_public_3fn.py --aplicar --confirmar MIGRAR_PUBLIC_3FN

La migracion preserva fact_ventas como tabla analitica desnormalizada y su
contenido sin alteraciones. Los datos del esquema original se copian a las
nuevas tablas operativas y las cantidades se verifican dentro de la misma
transaccion antes de COMMIT. Cualquier fallo hace ROLLBACK.

AVISO: objetos EXTERNOS (vistas o funciones de aplicaciones ajenas al
proyecto, RLS, triggers personalizados) pueden requerir ajustes. El programa
no modifica conexiones de servicios ni las colecciones MongoDB.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from config import REPORTS
from etl_load import ESQUEMA_SQL_3FN, ORDEN_CARGA
from integration import (
    CLAVES_FORANEAS, CLAVES_FORANEAS_FACT,
    _ddl_indices, _ddl_indices_fact, _ddl_tabla, _ddl_vista_resumen, _ddl_vistas_3fn,
)
from postgres_3fn import crear_motor

TABLAS = [*ORDEN_CARGA, "fact_ventas"]
VISTA = "v_resumen_ventas"
CONFIRMACION = "MIGRAR_PUBLIC_3FN"


def _ident(texto: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", texto):
        raise ValueError("Identificador SQL no permitido")
    return texto


def _plan(conn) -> dict:
    """Verifica origen; todas estas consultas son de solo lectura."""
    conn.execute(text("SET LOCAL search_path TO public, pg_catalog"))
    existentes = set(inspect(conn).get_table_names(schema="public"))
    faltan = set(TABLAS) - existentes
    if faltan:
        raise RuntimeError(f"No están todas las tablas originales en public: {sorted(faltan)}")
    columnas = {tabla: {c["name"] for c in inspect(conn).get_columns(tabla, schema="public")}
                for tabla in TABLAS}
    # Evita duplicar la migracion de la version ya normalizada.
    if "id_ciudad" in columnas["dim_sucursales"] and "ciudad" not in columnas["dim_sucursales"]:
        raise RuntimeError("public ya parece estar en 3FN: migracion no necesaria")
    requeridas = {tabla: set(esq) for tabla, esq in ESQUEMA_SQL_3FN.items()}
    requeridas["dim_sucursales"] -= {"id_ciudad"}
    requeridas["dim_sucursales"].add("ciudad")
    for tabla, cols in requeridas.items():
        faltan_cols = cols - columnas[tabla]
        if faltan_cols:
            raise RuntimeError(f"public.{tabla}: columnas faltantes: {sorted(faltan_cols)}")
    vistas = set(inspect(conn).get_view_names(schema="public"))
    if VISTA not in vistas:
        raise RuntimeError(f"No existe la vista public.{VISTA}")
    # Detener la migracion ante dependencias ajenas al prototipo.
    # Una vista externa que referencia una tabla antigua seguiria apuntando
    # al respaldo por OID, en vez de a la nueva tabla public.
    dependientes = conn.execute(text("""
      SELECT DISTINCT vn.nspname || '.' || v.relname AS vista
      FROM pg_depend dep
      JOIN pg_rewrite rw ON rw.oid = dep.objid
      JOIN pg_class v ON v.oid = rw.ev_class
      JOIN pg_namespace vn ON vn.oid = v.relnamespace
      JOIN pg_class fuente ON fuente.oid = dep.refobjid
      JOIN pg_namespace fn ON fn.oid = fuente.relnamespace
      WHERE dep.refclassid = 'pg_class'::regclass
        AND v.relkind IN ('v','m')
        AND fn.nspname = 'public'
        AND fuente.relname IN ('categorias','dim_ciudades','dim_sucursales',
          'clientes','productos','pedidos','detalle_pedido','pagos','fact_ventas')
        AND NOT (vn.nspname = 'public' AND v.relname = 'v_resumen_ventas')
        AND v.oid <> fuente.oid
    """)).scalars().all()
    if dependientes:
        raise RuntimeError(f"Vistas externas dependen del esquema anterior: {dependientes}")
    externas_fk = conn.execute(text("""
       SELECT DISTINCT fk.conrelid::regclass::text
       FROM pg_constraint fk
       JOIN pg_class padre ON padre.oid = fk.confrelid
       JOIN pg_namespace pn ON pn.oid = padre.relnamespace
       JOIN pg_class hija ON hija.oid = fk.conrelid
       JOIN pg_namespace hn ON hn.oid = hija.relnamespace
       WHERE fk.contype='f' AND pn.nspname='public'
         AND padre.relname IN ('categorias','dim_ciudades','dim_sucursales',
           'clientes','productos','pedidos','detalle_pedido','pagos','fact_ventas')
         AND NOT (hn.nspname='public' AND hija.relname IN (
           'categorias','dim_ciudades','dim_sucursales','clientes',
           'productos','pedidos','detalle_pedido','pagos','fact_ventas'))
    """)).scalars().all()
    if externas_fk:
        raise RuntimeError(f"Hay llaves foraneas de tablas externas: {externas_fk}")
    rls = conn.execute(text("""
       SELECT relname FROM pg_class
       WHERE relnamespace = 'public'::regnamespace
         AND relname IN ('categorias','dim_ciudades','dim_sucursales','clientes',
           'productos','pedidos','detalle_pedido','pagos','fact_ventas')
         AND relrowsecurity
    """)).scalars().all()
    if rls:
        raise RuntimeError(f"Tablas con Row Level Security: migracion manual requerida: {rls}")
    disparadores = conn.execute(text("""
       SELECT DISTINCT tgrelid::regclass::text FROM pg_trigger
       WHERE NOT tgisinternal
         AND tgrelid IN (
           SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace
           AND relname IN ('categorias','dim_ciudades','dim_sucursales',
             'clientes','productos','pedidos','detalle_pedido','pagos','fact_ventas')
         )
    """)).scalars().all()
    if disparadores:
        raise RuntimeError(f"Triggers personalizados requieren migracion manual: {disparadores}")

    faltantes_ciudad = conn.execute(text("""
       SELECT COUNT(*) FROM public.dim_sucursales s
       LEFT JOIN public.dim_ciudades c ON c.nombre = s.ciudad
       WHERE c.id_ciudad IS NULL
    """)).scalar_one()
    if faltantes_ciudad:
        raise RuntimeError(f"Hay {faltantes_ciudad} sucursales sin ciudad valida")
    conteos = {
        t: int(conn.execute(text(f"SELECT COUNT(*) FROM public.{t}")).scalar_one())
        for t in TABLAS
    }
    if any(n == 0 for n in conteos.values()):
        raise RuntimeError("La base tiene tablas sin datos; no se realizara migracion")
    return {"conteos": conteos, "tabla_analitica": "fact_ventas",
            "tablas_3fn": list(ORDEN_CARGA), "vista": VISTA,
            "alerta": "Los datos cloud existentes se conservan; no se importa el ETL local corregido"}


def _aplicar(conn, respaldo: str, conteos: dict) -> dict:
    """Una sola transaccion: renombrar a backup, crear, cargar, comprobar."""
    respaldo = _ident(respaldo)
    conn.execute(text("SET LOCAL search_path TO public, pg_catalog"))
    conn.execute(text(f"CREATE SCHEMA {respaldo}"))
    # Las dependencias de la vista antigua siguen apuntando a las tablas del backup.
    conn.execute(text(f"ALTER VIEW public.{VISTA} SET SCHEMA {respaldo}"))
    for tabla in reversed(TABLAS):
        conn.execute(text(f"ALTER TABLE public.{tabla} SET SCHEMA {respaldo}"))

    for tabla in ORDEN_CARGA:
        conn.execute(text(_ddl_tabla(tabla, "postgresql")))
    for hija, col, padre, col_padre in CLAVES_FORANEAS:
        conn.execute(text(
            f"ALTER TABLE public.{hija} ADD CONSTRAINT fk_{hija}_{col} "
            f"FOREIGN KEY ({col}) REFERENCES public.{padre}({col_padre})"
        ))
    for tabla in ORDEN_CARGA:
        columnas = list(ESQUEMA_SQL_3FN[tabla])
        nombres = ", ".join(columnas)
        if tabla == "dim_sucursales":
            consulta = (f"SELECT s.id_sucursal, s.nombre, c.id_ciudad, "
                        f"s.metros_cuadrados, s.fecha_apertura "
                        f"FROM {respaldo}.dim_sucursales s "
                        f"JOIN {respaldo}.dim_ciudades c ON c.nombre = s.ciudad")
        else:
            consulta = f"SELECT {nombres} FROM {respaldo}.{tabla}"
        conn.execute(text(f"INSERT INTO public.{tabla} ({nombres}) {consulta}"))

    # Mantener la estructura y TODAS las columnas de fact_ventas.
    # LIKE INCLUDING ALL copia tipos, PK/indices y checks, pero no copia FKs.
    conn.execute(text(
        f"CREATE TABLE public.fact_ventas (LIKE {respaldo}.fact_ventas INCLUDING ALL)"
    ))
    conn.execute(text(f"INSERT INTO public.fact_ventas SELECT * FROM {respaldo}.fact_ventas"))
    for col, padre, col_padre in CLAVES_FORANEAS_FACT:
        conn.execute(text(
            f"ALTER TABLE public.fact_ventas ADD CONSTRAINT fk_fact_ventas_{col} "
            f"FOREIGN KEY ({col}) REFERENCES public.{padre}({col_padre})"
        ))
    for ddl in _ddl_vistas_3fn("postgresql"):
        conn.execute(text(ddl))
    conn.execute(text(_ddl_vista_resumen("postgresql")))
    for ddl in _ddl_indices() + _ddl_indices_fact():
        conn.execute(text(ddl))

    verificados = {}
    for tabla in TABLAS:
        actual = int(conn.execute(text(f"SELECT COUNT(*) FROM public.{tabla}")).scalar_one())
        if actual != conteos[tabla]:
            raise RuntimeError(f"{tabla}: antes {conteos[tabla]}, despues {actual}. Rollback")
        verificados[tabla] = actual
    diferencia_fact = conn.execute(text(f"""
       SELECT COUNT(*) FROM (
         (SELECT * FROM public.fact_ventas EXCEPT ALL
          SELECT * FROM {respaldo}.fact_ventas)
         UNION ALL
         (SELECT * FROM {respaldo}.fact_ventas EXCEPT ALL
          SELECT * FROM public.fact_ventas)
       ) x
    """)).scalar_one()
    if diferencia_fact:
        raise RuntimeError("fact_ventas no coincide exactamente con el respaldo. Rollback")
    # Verificacion estructural en public, sin heuristicas.
    cols = set(conn.execute(text("""
       SELECT column_name FROM information_schema.columns
       WHERE table_schema='public' AND table_name='pedidos'
    """)).scalars().all())
    if "total_pedido" in cols or "anio_mes" in cols:
        raise RuntimeError("La tabla pedidos conserva derivados; rollback")
    return verificados


def ejecutar(aplicar: bool, confirmacion: str = "") -> None:
    if aplicar and confirmacion != CONFIRMACION:
        raise RuntimeError(f"Para aplicar se requiere: --confirmar {CONFIRMACION}")
    motor = crear_motor()
    try:
        if aplicar:
            # La transaccion atomica cubre TODA la migracion.
            with motor.begin() as conn:
                plan = _plan(conn)
                respaldo = "juc_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                resultado = _aplicar(conn, respaldo, plan["conteos"])
            evidencia = {"estado": "OK", "respaldo": respaldo,
                         "registros": resultado, "fecha_utc": datetime.now(timezone.utc).isoformat(),
                         "observacion": plan["alerta"]}
            ruta = REPORTS / "migracion_public_3fn_evidencia.json"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps(evidencia, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(evidencia, indent=2, ensure_ascii=False))
        else:
            with motor.connect() as conn:
                plan = _plan(conn)
            print("SOLO PLAN: no se ha modificado Supabase")
            print(json.dumps(plan, indent=2, ensure_ascii=False))
    finally:
        motor.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migracion transaccional a 3FN de public")
    parser.add_argument("--plan", action="store_true", help="Comprueba precondiciones; no escribe")
    parser.add_argument("--aplicar", action="store_true", help="Crea backup y migra public en una transaccion")
    parser.add_argument("--confirmar", default="", help="Confirmacion deliberada requerida al aplicar")
    args = parser.parse_args()
    ejecutar(args.aplicar, args.confirmar)
