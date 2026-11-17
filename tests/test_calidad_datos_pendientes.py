"""Regresiones: estados desconocidos, fechas comerciales y KPIs sin sesgo de fact."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import analysis as A
from config import FECHA_INICIO, FECHA_FIN
from etl_transform import (
    estandarizar_fecha, t03_tratar_nulos, t05_estandarizar_fechas,
    _parsear_una_fecha,
)
from utils import CalidadTracker


def test_estados_nulos_no_inventan_ventas_ni_cobros():
    datos = {
        "pedidos": pd.DataFrame({"id_pedido": [1, 2, 3], "id_cliente": [1, 1, 1],
                                  "estado": [None, "completado", "liquidado"]}),
        "pagos": pd.DataFrame({"id_pago": [1, 2, 3], "id_pedido": [1, 2, 3],
                                "estado_pago": [None, "aprobado", "otro_estado"]}),
    }
    t03_tratar_nulos(datos, CalidadTracker())
    assert datos["pedidos"].estado.tolist() == ["desconocido", "completado", "desconocido"]
    assert datos["pagos"].estado_pago.tolist() == ["desconocido", "aprobado", "desconocido"]


def test_fecha_ambigua_se_resuelve_dentro_del_periodo():
    # En DD/MM indicaria 9/oct/2026 (fuera de periodo); en MM/DD es 10/sep/2026.
    assert _parsear_una_fecha("09/10/2026", pd.Timestamp(FECHA_INICIO),
                              pd.Timestamp(FECHA_FIN)) == pd.Timestamp("2026-09-10")
    # Sin rango, se respeta el comportamiento original mexicano DD/MM.
    assert _parsear_una_fecha("09/10/2026") == pd.Timestamp("2026-10-09")


def test_fecha_iso_fuera_del_periodo_no_se_inventa():
    fechas = {"pedidos": pd.DataFrame({"fecha_pedido": ["2026-10-01", "09/10/2026", "2026-09-30"]})}
    t05_estandarizar_fechas(fechas, CalidadTracker())
    assert fechas["pedidos"].fecha_pedido.tolist() == [
        pd.Timestamp("2026-09-10"), pd.Timestamp("2026-09-30")]


def test_historico_clientes_no_se_recorta_al_periodo_de_ventas():
    datos = {"clientes": pd.DataFrame({"fecha_alta": ["2020-12-31", "2024-04-01"]})}
    t05_estandarizar_fechas(datos, CalidadTracker())
    assert len(datos["clientes"]) == 2


def test_pedidos_todos_incluye_sin_detalles_y_estados_precisos():
    fact = pd.DataFrame({
        "id_detalle": [1, 2, 3], "id_producto": [1, 2, 3], "id_pedido": [1, 1, 2],
        "id_cliente": [1, 1, 2], "estado": ["completado", "completado", "enviado"],
        "es_completado": [True, True, True], "total": [100.0, 20.0, 80.0],
        "precio_unitario": [100.0, 20.0, 80.0], "cantidad": [1, 1, 1],
        "costo_estimado": [70.0, 15.0, 50.0], "descuento": [0.0, 0.0, 0.0],
        "producto_nombre": ["A", "B", "C"], "categoria": ["X", "X", "X"],
        "ciudad": ["Veracruz"] * 3, "anio_mes": ["2026-09"] * 3,
    })
    todos = pd.DataFrame({
        "id_pedido": [1, 2, 3, 4, 5], "id_cliente": [1, 2, 3, 4, 5],
        "estado": ["completado", "enviado", "cancelado", "pendiente", "desconocido"],
    })
    datos = A.kpis_generales(fact, pedidos_fuente=todos).set_index("indicador")["valor"]
    assert datos["Pedidos totales"] == "5"
    assert datos["Pedidos con detalle"] == "2"
    assert datos["Pedidos sin detalle"] == "3"
    assert datos["Pedidos completados"] == "1"
    assert datos["Pedidos enviados"] == "1"
    assert datos["Pedidos pendientes"] == "1"
    assert datos["Pedidos cancelados"] == "1"
    assert datos["Pedidos estado desconocido"] == "1"
    assert datos["Pedidos facturables con detalle"] == "2"
    assert datos["Tasa de cancelacion"] == "20.00%"
    assert datos["Ticket promedio"] == "$100.00"
    assert datos["Clientes activos"] == "2"
    assert datos["Clientes con pedido (todos)"] == "5"



def test_eventos_de_navegacion_tambien_respetan_el_periodo():
    datos = {"actividad": pd.DataFrame({"fecha": ["07/12/2026", "2026-11-20", "2025-05-01"]})}
    t05_estandarizar_fechas(datos, CalidadTracker())
    assert datos["actividad"].fecha.tolist() == [
        pd.Timestamp("2026-07-12"), pd.Timestamp("2025-05-01")]
