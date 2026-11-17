"""Evidencia local y sin escrituras a las bases de calidad de datos.

Uso (despues del ETL): python src/auditoria_calidad.py
Genera data/reports/auditoria_calidad_datos.csv y sale con error si detecta
fechas fuera del periodo, estados invalidos o referencias sin integridad.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import FECHA_INICIO, FECHA_FIN, PROCESSED, REPORTS


def auditar(procesados: Path = PROCESSED, reportes: Path = REPORTS) -> pd.DataFrame:
    tablas = procesados / "tablas"
    pedidos = pd.read_csv(tablas / "pedidos.csv")
    pagos = pd.read_csv(tablas / "pagos.csv")
    fact = pd.read_csv(procesados / "dataset_postgresql.csv")
    docs = json.loads((procesados / "dataset_mongodb.json").read_text(encoding="utf-8"))
    inicio, fin = pd.Timestamp(FECHA_INICIO), pd.Timestamp(FECHA_FIN)
    datos: list[dict[str, object]] = []

    def agregar(indicador: str, valor: int | float, criterio: str, ok: bool = True) -> None:
        datos.append({"indicador": indicador, "valor": valor, "criterio": criterio,
                      "estado": "OK" if ok else "ERROR"})

    for nombre, fechas in [
        ("pedidos", pedidos["fecha_pedido"]), ("pagos", pagos["fecha_pago"]),
        ("resenas", pd.Series([r["fecha"] for r in docs["resenas"]])),
        ("actividad_usuario", pd.Series([r["fecha"] for r in docs["actividad_usuario"]])),
    ]:
        fechas_dt = pd.to_datetime(fechas, errors="coerce")
        fuera = int((fechas_dt.isna() | (fechas_dt < inicio) | (fechas_dt > fin + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1))).sum())
        agregar(f"{nombre}: fechas fuera de periodo/invalidas", fuera,
                f"rango permitido: {FECHA_INICIO} a {FECHA_FIN}", fuera == 0)

    estados_validos = {
        "pedidos": (pedidos["estado"], {"completado", "enviado", "pendiente", "cancelado", "desconocido"}),
        "pagos": (pagos["estado_pago"], {"aprobado", "pendiente", "rechazado", "desconocido"}),
    }
    for tabla, (estado, validos) in estados_validos.items():
        invalidos = int((~estado.isin(validos)).sum())
        desconocidos = int(estado.eq("desconocido").sum())
        agregar(f"{tabla}: estados no reconocidos", invalidos,
                "solo estados permitidos", invalidos == 0)
        agregar(f"{tabla}: estados desconocidos", desconocidos,
                "desconocido no equivale a completado/aprobado")

    ids = set(fact["id_pedido"].unique())
    con_detalle = int(pedidos["id_pedido"].isin(ids).sum())
    sin_detalle = len(pedidos) - con_detalle
    agregar("pedidos: todos los encabezados", len(pedidos), "tabla pedidos")
    agregar("pedidos: con detalle", con_detalle, "existen en fact_ventas")
    agregar("pedidos: sin detalle conservado", sin_detalle,
            "no deben desaparecer del denominador de cancelacion")
    agregar("pedidos: cancelados", int(pedidos["estado"].eq("cancelado").sum()),
            "estado literal cancelado")
    salida = pd.DataFrame(datos)
    reportes.mkdir(parents=True, exist_ok=True)
    ruta = reportes / "auditoria_calidad_datos.csv"
    salida.to_csv(ruta, index=False, encoding="utf-8-sig")
    print(salida.to_string(index=False))
    print(f"\nEvidencia guardada: {ruta}")
    return salida


if __name__ == "__main__":
    evidencia = auditar()
    if (evidencia["estado"] != "OK").any():
        raise SystemExit("FALLA DE CALIDAD: revisar auditoria_calidad_datos.csv")
