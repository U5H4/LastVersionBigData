"""Auditoria Q08 contra la tabla real de pagos en Supabase. SOLO LECTURA.

Uso: python src/auditar_q08_cloud.py
Comparacion por metodo: Q08 (vista 1 fila por pago) vs public.pagos.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from config import REPORTS
from integracion_real import conectar_postgres
from validar_postgres_cloud import cargar_consultas, consultar

SALIDA = REPORTS / "validacion_postgres_cloud"
ORO = """
SELECT metodo, COUNT(DISTINCT id_pedido) AS pedidos,
       COUNT(*) AS registros_pago, ROUND(SUM(monto), 2) AS monto
FROM public.pagos
WHERE metodo IS NOT NULL
GROUP BY metodo ORDER BY metodo
"""
ANTERIOR = """
SELECT metodo, ROUND(SUM(monto), 2) AS monto_repetido
FROM public.fact_ventas
WHERE metodo IS NOT NULL
GROUP BY metodo ORDER BY metodo
"""


def monto_decimal(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"))


def comparar(q08: list[tuple], pagos: list[tuple], previo: list[tuple]) -> list[dict]:
    """Compara por metodo el conteo de pedidos y monto en su nivel correcto."""
    por_q = {str(r[0]): r for r in q08}
    por_p = {str(r[0]): r for r in pagos}
    por_prev = {str(r[0]): r for r in previo}
    salida = []
    for metodo in sorted(set(por_q) | set(por_p)):
        q = por_q.get(metodo)
        p = por_p.get(metodo)
        n_q = int(q[1]) if q else -1
        n_p = int(p[1]) if p else -1
        real = monto_decimal(p[3]) if p else Decimal("0.00")
        obtenido = monto_decimal(q[2]) if q else Decimal("0.00")
        repetido = monto_decimal(por_prev[metodo][1]) if metodo in por_prev else Decimal("0.00")
        coincide = q is not None and p is not None and n_q == n_p and real == obtenido
        salida.append({
            "metodo": metodo,
            "pedidos_q08": n_q,
            "pedidos_reales": n_p,
            "registros_pago": int(p[2]) if p else -1,
            "monto_q08": str(obtenido),
            "monto_pagos": str(real),
            "diferencia_q08": str(obtenido - real),
            "monto_fact_ventas_antes": str(repetido),
            "diferencia_neta_sql_anterior_menos_pagos": str(repetido - real),
            "coincide": coincide,
        })
    return salida


def ejecutar(salida: Path = SALIDA) -> dict:
    sql = next(q["sql"] for q in cargar_consultas() if q["id"] == "Q08")
    if "v_pagos_conciliados" not in sql.lower():
        raise ValueError("Q08 debe consultar la vista v_pagos_conciliados")
    engine, _ = conectar_postgres(exigir_cloud=True)
    try:
        _, q = consultar(engine, sql)
        _, p = consultar(engine, ORO)
        _, old = consultar(engine, ANTERIOR)
    finally:
        engine.dispose()
    datos = comparar(q, p, old)
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / "03_auditoria_q08.csv"
    columnas = list(datos[0]) if datos else ["metodo", "coincide"]
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(datos)
    correcto = bool(datos) and all(r["coincide"] for r in datos)
    resumen = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "estado": "OK" if correcto else "ERROR_DE_CONCILIACION_Q08",
        "solo_lectura": True,
        "metodos_revisados": len(datos),
        "pedidos_distintos_por_metodo": sum(r["pedidos_q08"] for r in datos),
        "total_monto_q08": str(sum((Decimal(r["monto_q08"]) for r in datos), Decimal(0))),
        "total_monto_pagos": str(sum((Decimal(r["monto_pagos"]) for r in datos), Decimal(0))),
        "diferencia_neta_total_sql_anterior_menos_pagos": str(sum((Decimal(r["diferencia_neta_sql_anterior_menos_pagos"]) for r in datos), Decimal(0))),
        "archivo": str(ruta),
        "nota": "La diferencia del SQL anterior es neta: fact_ventas repite montos en pedidos multilínea y omite pedidos sin detalle. Conciliacion tecnica no confirma aprobacion bancaria.",
    }
    (salida / "03_auditoria_q08.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


if __name__ == "__main__":
    reporte = ejecutar()
    print(json.dumps(reporte, ensure_ascii=False, indent=2))
    raise SystemExit(0 if reporte["estado"] == "OK" else 1)
