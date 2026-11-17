"""Parte V/VI: integracion de lecturas REALES PostgreSQL + MongoDB.

No usa dataset_postgresql.csv ni dataset_mongodb.json y NO modifica las BD.

    python src/integracion_real.py                 # PostgreSQL + MongoDB reales
    python src/integracion_real.py --exigir-cloud  # Supabase + Atlas obligatorios

Los CSV generados sirven como evidencia de las consultas y del merge Pandas.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from config import (MONGO_ATLAS_URI, MONGO_DB, MONGO_URI, REPORTS,
                    config_pg)

DESTINO = REPORTS / "integracion_directa"

SQL_VENTAS = """
SELECT id_producto,
       SUM(cantidad) AS cantidad_vendida,
       SUM(total) AS ingresos
FROM fact_ventas
WHERE es_completado = TRUE
GROUP BY id_producto
"""

SQL_PRODUCTOS = """
SELECT p.id_producto, p.nombre, c.nombre AS categoria
FROM productos p
LEFT JOIN categorias c ON c.id_categoria = p.id_categoria
"""

PIPELINE_VISITAS = [
    {"$match": {"evento": "producto_visto"}},
    {"$group": {"_id": "$producto", "numero_visualizaciones": {"$sum": 1}}},
    {"$project": {"_id": 0, "id_producto": "$_id", "numero_visualizaciones": 1}},
]


def conectar_postgres(exigir_cloud: bool):
    if os.getenv("SIN_POSTGRES") == "1" or os.getenv("FORZAR_SQLITE") == "1":
        raise RuntimeError("Desactiva SIN_POSTGRES y FORZAR_SQLITE para consultar PostgreSQL.")
    cfg = config_pg()
    if exigir_cloud and not os.getenv("SUPABASE_HOST"):
        raise RuntimeError("Se requiere SUPABASE_HOST para demostrar PostgreSQL Cloud.")
    if not cfg.get("password"):
        raise RuntimeError("Falta la contrasena PG_PASSWORD o SUPABASE_PASSWORD.")
    # URL.create evita errores cuando la contrasena contiene @, : o #.
    url = URL.create(
        "postgresql+psycopg2", username=cfg["user"], password=cfg["password"],
        host=cfg["host"], port=cfg["port"], database=cfg["database"],
    )
    motor = create_engine(
        url, pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "sslmode": os.getenv("PG_SSLMODE", "require")},
    )
    try:
        with motor.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        motor.dispose()
        raise RuntimeError("No se pudo conectar a PostgreSQL: revisa host, usuario, password, puerto y SSL.") from None
    return motor, {"motor": "PostgreSQL", "host": cfg["host"], "base": cfg["database"]}


def consultar_postgres(motor):
    with motor.connect() as conn:
        tablas = pd.read_sql(text(SQL_PRODUCTOS), conn)
        ventas = pd.read_sql(text(SQL_VENTAS), conn)
    if tablas.empty:
        raise RuntimeError("La tabla productos de PostgreSQL esta vacia. Carga previamente las bases.")
    if ventas.empty:
        raise RuntimeError("fact_ventas no tiene ventas completadas para integrar.")
    return tablas, ventas


def conectar_mongodb(exigir_cloud: bool):
    if os.getenv("SIN_MONGO") == "1":
        raise RuntimeError("Desactiva SIN_MONGO para consultar MongoDB real.")
    uri = MONGO_ATLAS_URI or MONGO_URI
    if exigir_cloud and not uri.startswith("mongodb+srv://"):
        raise RuntimeError("Para demostrar Atlas configura MONGO_ATLAS_URI=mongodb+srv://...")
    from pymongo import MongoClient
    try:
        cliente = MongoClient(uri, serverSelectionTimeoutMS=10000)
        cliente.admin.command("ping")
    except Exception:
        raise RuntimeError("No se pudo conectar a MongoDB: revisa URI, Network Access y usuario.") from None
    return cliente, cliente[MONGO_DB], {"motor": "MongoDB", "base": MONGO_DB, "atlas": uri.startswith("mongodb+srv://")}


def consultar_mongodb(db):
    if db["actividad_usuario"].estimated_document_count() == 0:
        raise RuntimeError("La coleccion actividad_usuario esta vacia. Carga previamente las bases.")
    visitas = pd.DataFrame(list(db["actividad_usuario"].aggregate(PIPELINE_VISITAS)))
    if visitas.empty:
        raise RuntimeError("No hay eventos producto_visto en MongoDB.")
    return visitas


def integrar_frames(productos: pd.DataFrame, ventas: pd.DataFrame,
                     visitas: pd.DataFrame) -> pd.DataFrame:
    """Une el catalogo SQL, las ventas SQL y las visualizaciones Mongo.

    Mantiene productos sin venta o sin visitas y no multiplica las filas.
    """
    p = productos[["id_producto", "nombre", "categoria"]].copy()
    v = ventas[["id_producto", "cantidad_vendida", "ingresos"]].copy()
    n = visitas[["id_producto", "numero_visualizaciones"]].copy()
    for df in (p, v, n):
        df["id_producto"] = pd.to_numeric(df["id_producto"], errors="raise").astype("int64")
        if df["id_producto"].duplicated().any():
            raise ValueError("Hay ids_producto duplicados antes de combinar fuentes.")
    resultado = p.merge(v, on="id_producto", how="left", validate="one_to_one")
    resultado = resultado.merge(n, on="id_producto", how="left", validate="one_to_one")
    for col in ("cantidad_vendida", "ingresos", "numero_visualizaciones"):
        resultado[col] = pd.to_numeric(resultado[col], errors="coerce").fillna(0)
    resultado["cantidad_vendida"] = resultado["cantidad_vendida"].astype("int64")
    resultado["numero_visualizaciones"] = resultado["numero_visualizaciones"].astype("int64")
    resultado["ingresos"] = resultado["ingresos"].round(2)
    resultado["nivel_interes"] = "sin_visitas"
    positivas = resultado.loc[resultado.numero_visualizaciones > 0, "numero_visualizaciones"]
    umbral_alto = float(positivas.quantile(.75)) if not positivas.empty else 0
    resultado.loc[resultado.numero_visualizaciones > 0, "nivel_interes"] = "visitas_normales"
    resultado.loc[resultado.numero_visualizaciones > umbral_alto, "nivel_interes"] = "muchas_visitas"
    resultado["clasificacion"] = "otras"
    resultado.loc[(resultado.nivel_interes == "muchas_visitas") &
                  (resultado.cantidad_vendida == 0), "clasificacion"] = "muy_visto_sin_ventas"
    resultado.loc[(resultado.numero_visualizaciones == 0) &
                  (resultado.cantidad_vendida > 0), "clasificacion"] = "ventas_sin_visitas"
    resultado.loc[(resultado.nivel_interes == "muchas_visitas") &
                  (resultado.cantidad_vendida > 0), "clasificacion"] = "muy_visto_y_vendido"
    return resultado.sort_values(["numero_visualizaciones", "cantidad_vendida"],
                                 ascending=[False, False]).reset_index(drop=True)


def generar_resumen(cruce: pd.DataFrame) -> dict:
    mas_visto = cruce.sort_values(["numero_visualizaciones", "id_producto"], ascending=[False, True]).iloc[0]
    mas_vendido = cruce.sort_values(["cantidad_vendida", "id_producto"], ascending=[False, True]).iloc[0]
    # Correlacion entre productos (no causalidad ni conversion de usuarios).
    pearson = cruce["numero_visualizaciones"].corr(cruce["cantidad_vendida"], method="pearson")
    spearman = cruce["numero_visualizaciones"].corr(cruce["cantidad_vendida"], method="spearman")
    def valor(x):
        return round(float(x), 5) if pd.notna(x) else None
    return {
        "productos_catalogo": int(len(cruce)),
        "producto_mas_visto": {"id_producto": int(mas_visto.id_producto),
                                "nombre": str(mas_visto.nombre),
                                "visualizaciones": int(mas_visto.numero_visualizaciones)},
        "producto_mas_vendido": {"id_producto": int(mas_vendido.id_producto),
                                  "nombre": str(mas_vendido.nombre),
                                  "unidades": int(mas_vendido.cantidad_vendida)},
        "correlacion_pearson": valor(pearson),
        "correlacion_spearman": valor(spearman),
        "muchas_visitas_sin_ventas": int((cruce.clasificacion == "muy_visto_sin_ventas").sum()),
        "ventas_sin_visitas": int((cruce.clasificacion == "ventas_sin_visitas").sum()),
        "nota": "Los eventos producto_visto no son sesiones unicas. La correlacion no demuestra causalidad."
    }


def ejecutar(exigir_cloud: bool = False, destino: Path = DESTINO) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    motor, meta_sql = conectar_postgres(exigir_cloud)
    try:
        catalogo, ventas = consultar_postgres(motor)
    finally:
        motor.dispose()
    cliente, db, meta_mongo = conectar_mongodb(exigir_cloud)
    try:
        visitas = consultar_mongodb(db)
        mongo_total = db["actividad_usuario"].count_documents({})
    finally:
        cliente.close()
    cruce = integrar_frames(catalogo, ventas, visitas)
    resumen = generar_resumen(cruce)
    ventas.to_csv(destino / "01_ventas_desde_postgresql.csv", index=False, encoding="utf-8-sig")
    visitas.to_csv(destino / "02_visitas_desde_mongodb.csv", index=False, encoding="utf-8-sig")
    cruce.to_csv(destino / "03_integracion_sql_nosql.csv", index=False, encoding="utf-8-sig")
    evidencia = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "consulta_sql": SQL_VENTAS.strip(),
        "pipeline_mongo": PIPELINE_VISITAS,
        "modo": "bases_reales_sin_fallback",
        "cloud_exigido": exigir_cloud,
        "postgresql": {**meta_sql, "productos_leidos": len(catalogo), "productos_con_ventas": len(ventas)},
        "mongodb": {**meta_mongo, "eventos_totales": mongo_total, "productos_con_visitas": len(visitas)},
        "computo": {"google_colab": bool(os.getenv("COLAB_RELEASE_TAG")),
                    "python": platform.python_version()},
        "resultado": resumen,
    }
    (destino / "04_evidencia_conexiones.json").write_text(
        json.dumps(evidencia, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"estado": "OK", "carpeta": str(destino), "resultado": resumen},
                     ensure_ascii=False, indent=2))
    return evidencia


def main() -> None:
    parser = argparse.ArgumentParser(description="Integracion REAL PostgreSQL + MongoDB: solo lectura.")
    parser.add_argument("--exigir-cloud", action="store_true",
                        help="exige Supabase + MongoDB Atlas, sin fallbacks locales")
    args = parser.parse_args()
    ejecutar(exigir_cloud=args.exigir_cloud)


if __name__ == "__main__":
    main()
