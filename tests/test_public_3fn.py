"""Pruebas de las tablas public normalizadas y de la migracion segura."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import integration as sql
import migrar_public_3fn as migracion
from etl_load import ORDEN_CARGA, ESQUEMA_SQL_3FN, tablas_fisicas_3fn


def test_ocho_tablas_3fn_sin_campos_redundantes():
    redundantes = {
        "clientes": {"anio_alta", "mes_alta", "antiguedad_dias", "ciudad"},
        "productos": {"costo_estimado", "utilidad_bruta", "rango_precio", "categoria"},
        "pedidos": {"anio", "mes", "mes_nombre", "trimestre", "anio_mes",
                    "es_completado", "total_pedido", "unidades", "lineas",
                    "categorias_distintas", "ticket_promedio_linea"},
        "detalle_pedido": {"subtotal", "total", "porcentaje_descuento"},
        "pagos": {"diferencia_pago", "conciliado"},
        "dim_sucursales": {"ciudad", "region", "region_origen"},
    }
    assert len(ESQUEMA_SQL_3FN) == len(ORDEN_CARGA) == 8
    for tabla, columnas in redundantes.items():
        assert not (set(ESQUEMA_SQL_3FN[tabla]) & columnas), tabla


def test_valida_ciudad_sucursal_faltante():
    from test_integration import _tablas_pequenas
    tablas = _tablas_pequenas()
    tablas["dim_sucursales"].loc[0, "ciudad"] = "Ciudad inexistente"
    with pytest.raises(ValueError, match="Sucursales sin ciudad"):
        tablas_fisicas_3fn(tablas)


def test_script_migracion_no_se_aplica_sin_confirmacion():
    with pytest.raises(RuntimeError, match="--confirmar"):
        migracion.ejecutar(True)


def test_script_migracion_no_admite_identificadores_inyectados():
    with pytest.raises(ValueError):
        migracion._ident("public;DROP TABLE clientes")


def test_vistas_reconstruyen_importes_y_eliminan_redudancia(tmp_path):
    from test_integration import _tablas_pequenas, fact
    # Fixture manual y datos de linea coherentes.
    tablas = _tablas_pequenas()
    fact_df = fact.__wrapped__()  # fixture sin invoking pytest directamente
    eng = create_engine(f"sqlite:///{tmp_path/'juc.db'}")
    integrador = sql.IntegradorSQL(eng, "sqlite", "sqlite", tmp_path/"reportes")
    integrador.crear_esquema(fact_df)
    integrador.cargar(tablas, fact_df)
    vistas = set(inspect(eng).get_view_names())
    assert {"v_detalle_calculado", "v_pedidos_calculados",
            "v_pagos_conciliados", "v_resumen_ventas"} <= vistas
    with eng.connect() as c:
        assert float(c.execute(text("SELECT total FROM v_detalle_calculado WHERE id_detalle=2")).scalar_one()) == 650
        assert float(c.execute(text("SELECT total_pedido FROM v_pedidos_calculados WHERE id_pedido=1")).scalar_one()) == 25650
        assert c.execute(text("SELECT COUNT(*) FROM v_pedidos_calculados")).scalar_one() == 3
        assert c.execute(text("SELECT COUNT(*) FROM fact_ventas")).scalar_one() == len(fact_df)
        # La tabla principal solo tiene datos base.
        assert "total_pedido" not in {x["name"] for x in inspect(eng).get_columns("pedidos")}


def test_el_esquema_declara_fk_obligatorias():
    cols = sql.COLUMNAS_NOT_NULL
    assert {"id_pedido", "id_producto"} <= set(cols["detalle_pedido"])
    assert "id_cliente" in cols["pedidos"]
    assert "id_ciudad" in cols["dim_sucursales"]
    assert ("dim_sucursales", "id_ciudad", "dim_ciudades", "id_ciudad") in sql.CLAVES_FORANEAS
