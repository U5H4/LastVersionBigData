"""Regresiones para la fuente C directa y para el diseño físico en 3FN."""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import etl_extract as ext
import postgres_3fn as normal


def test_link_con_gid():
    assert ext.url_exportacion_sheet(
        "https://docs.google.com/spreadsheets/d/ABC_123/edit#gid=786"
    ) == "https://docs.google.com/spreadsheets/d/ABC_123/export?format=csv&gid=786"


def test_no_acepta_otro_dominio():
    with pytest.raises(ValueError):
        ext.url_exportacion_sheet("https://example.com/spreadsheets/d/ABC/edit")


def test_sheet_valida_campos(monkeypatch):
    monkeypatch.setattr(ext.pd, "read_csv", lambda *_args, **_kw: pd.DataFrame(
        [{"id_sucursal": "1", "nombre": "Sucursal", "ciudad": "Puebla", "region": "Centro"}]
    ))
    assert len(ext.leer_google_sheet(
        "https://docs.google.com/spreadsheets/d/ABC/edit#gid=0", "sucursales"
    )) == 1


def test_sheet_detecta_columna_faltante(monkeypatch):
    monkeypatch.setattr(ext.pd, "read_csv", lambda *_args, **_kw: pd.DataFrame(
        [{"id_sucursal": "1", "nombre": "Sucursal"}]
    ))
    with pytest.raises(ValueError, match="no contiene columnas"):
        ext.leer_google_sheet(
            "https://docs.google.com/spreadsheets/d/ABC/edit#gid=0", "sucursales"
        )


def test_esquema_3fn_no_copia_columnas_derivadas():
    ddl = "\n".join(normal.DDL)
    # Estas columnas no deben figurar en los CREATE TABLE; existen en vistas.
    definiciones = ddl.split("CREATE OR REPLACE VIEW")[0]
    for extra in ("antiguedad_dias", "costo_estimado", "total_pedido", "anio_mes",
                  "porcentaje_descuento", "diferencia_pago", "region_origen"):
        assert extra not in definiciones
    assert "FOREIGN KEY" not in ddl or "REFERENCES" in ddl


def test_crea_solo_esquema_3fn():
    assert all("public." not in ddl for ddl in normal.DDL)
    assert len(normal.TABLAS) == 8
