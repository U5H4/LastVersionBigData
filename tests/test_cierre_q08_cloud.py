"""Cobertura para Q08 y la interpretacion de EXPLAIN, sin Colab."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import auditar_q08_cloud as q8
import interpretar_explain as exp
from validar_postgres_cloud import cargar_consultas


def test_q08_usa_vista_pagos_no_fact_ventas():
    sql = next(q["sql"] for q in cargar_consultas() if q["id"] == "Q08")
    assert "FROM public.v_pagos_conciliados" in sql
    assert "FROM fact_ventas" not in sql
    assert "COUNT(DISTINCT id_pedido)" in sql


def test_q08_no_duplica_monto_y_revela_sobreconteo_anterior():
    q = [("tarjeta", 2, Decimal("300.00"), Decimal("100"), Decimal("0.00"))]
    pagos = [("tarjeta", 2, 2, Decimal("300.00"))]
    anterior = [("tarjeta", Decimal("550.00"))]
    salida = q8.comparar(q, pagos, anterior)
    assert len(salida)==1 and salida[0]["coincide"]
    assert salida[0]["diferencia_neta_sql_anterior_menos_pagos"] == "250.00"


def test_q08_detecta_diferencia():
    q=[("efectivo", 2, Decimal("520.00"), 100, 0)]
    p=[("efectivo", 2, 2, Decimal("500.00"))]
    a=[("efectivo", Decimal("520.00"))]
    assert not q8.comparar(q,p,a)[0]["coincide"]


def test_explain_detecta_indice_y_tiempo():
    plan="Index Scan using ix_fact_producto on public.fact_ventas  (actual time=0.1..0.2)\nPlanning Time: 0.106 ms\nExecution Time: 0.239 ms\n"
    x=exp.parsear(plan)
    assert x["index_scan"] == 1 and x["seq_scan"] == 0
    assert x["execution_ms"] == .239
    assert "ix_fact_producto" in x["indices_nominales"]


def test_explain_detecta_seq_scan():
    x=exp.parsear("Seq Scan on public.fact_ventas\nExecution Time: 0.500 ms\n")
    assert x["seq_scan"] == 1 and x["index_scan"] == 0


def test_interpretacion_requiere_archivos_reales(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        exp.revisar(tmp_path)


def test_interpretacion_exporta_evidencias(tmp_path):
    for idx in ("E01", "E02", "E03"):
        (tmp_path / f"{idx}_explain_analyze.txt").write_text("Seq Scan on productos\nPlanning Time: 0.2 ms\nExecution Time: 0.8 ms\n",encoding="utf-8")
    salida=exp.revisar(tmp_path)
    assert salida["estado"] == "OK" and len(salida["planes"])==3
    assert (tmp_path / "04_interpretacion_planes.md").is_file()
