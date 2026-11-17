"""Interpreta los 3 EXPLAIN ANALYZE ya generados sobre Supabase.

No se inventan tiempos: si falta un archivo, el comando devuelve error.
Uso: python src/interpretar_explain.py
"""
from __future__ import annotations
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from config import REPORTS

SALIDA = REPORTS / "validacion_postgres_cloud"
IDS = ("E01", "E02", "E03")


def parsear(texto: str) -> dict:
    def numero(campo: str):
        m = re.search(r"^\s*" + re.escape(campo) + r":\s*([\d.]+)\s*ms\s*$", texto, re.M | re.I)
        return float(m.group(1)) if m else None
    index_types = re.findall(r"\b(?:Index Only Scan|Index Scan|Bitmap Index Scan)\b", texto, re.I)
    seq_types = re.findall(r"\bSeq Scan\b", texto, re.I)
    return {
        "planning_ms": numero("Planning Time"),
        "execution_ms": numero("Execution Time"),
        "index_scan": len(index_types),
        "seq_scan": len(seq_types),
        "bitmap_heap_scan": len(re.findall(r"\bBitmap Heap Scan\b", texto, re.I)),
        "joins": len(re.findall(r"\b(?:Hash Join|Merge Join|Nested Loop)\b", texto, re.I)),
        "indices_nominales": sorted(set(re.findall(r"\busing\s+([a-z_][a-z0-9_]*)", texto, re.I))),
    }


def revisar(carpeta: Path = SALIDA) -> dict:
    datos = []
    for code in IDS:
        ruta = carpeta / f"{code}_explain_analyze.txt"
        if not ruta.is_file():
            raise FileNotFoundError(f"Falta un EXPLAIN ANALYZE real: {ruta}")
        interpretacion = parsear(ruta.read_text(encoding="utf-8"))
        if interpretacion["execution_ms"] is None:
            raise ValueError(f"{ruta} no contiene la linea Execution Time")
        datos.append({"prueba": code, "archivo": ruta.name, **interpretacion})
    carpeta.mkdir(parents=True, exist_ok=True)
    with (carpeta / "04_resumen_planes.csv").open("w", newline="", encoding="utf-8-sig") as f:
        nombres = ["prueba", "archivo", "planning_ms", "execution_ms", "index_scan", "seq_scan", "bitmap_heap_scan", "joins", "indices_nominales"]
        writer=csv.DictWriter(f, fieldnames=nombres)
        writer.writeheader()
        for d in datos:
            writer.writerow({**d, "indices_nominales": ", ".join(d["indices_nominales"])})
    md = ["# Interpretacion de EXPLAIN ANALYZE en Supabase", "", "Planes reales de PostgreSQL (NO se fuerza el indice).", "", "| Prueba | Planif. (ms) | Ejec. (ms) | Index Scans | Seq Scans | Indices identificados |", "|---|---:|---:|---:|---:|---|"]
    for d in datos:
        ix=", ".join(d["indices_nominales"]) or "ninguno detectado"
        md.append(f"| {d['prueba']} | {d['planning_ms']} | {d['execution_ms']} | {d['index_scan']} | {d['seq_scan']} | {ix} |")
    md.extend(["", "**Interpretacion:** Index Scan/Bitmap Index Scan indica uso de indice; Seq Scan es una lectura secuencial y puede ser optima en tablas pequenas. Estos planes no prueban una mejora porcentual sin una comparacion controlada equivalente.", "", "Fuente: E01/E02/E03_explain_analyze.txt guardados tras ejecutar validar_postgres_cloud.py --ejecutar."])
    (carpeta / "04_interpretacion_planes.md").write_text("\n".join(md)+"\n", encoding="utf-8")
    resultado={"estado":"OK", "fecha_utc":datetime.now(timezone.utc).isoformat(),"planes":datos}
    (carpeta / "04_interpretacion_planes.json").write_text(json.dumps(resultado,ensure_ascii=False,indent=2),encoding="utf-8")
    return resultado

if __name__ == "__main__":
    print(json.dumps(revisar(), ensure_ascii=False, indent=2))
