"""Pruebas del validador de 20 consultas Cloud sin efectos sobre Supabase."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import validar_postgres_cloud as vc


def test_carga_20_consultas_reales():
    consultas = vc.cargar_consultas()
    assert len(consultas) == 20
    assert [q['id'] for q in consultas] == [f'Q{i:02d}' for i in range(1, 21)]
    assert all(q['sql'].split()[0].upper() in ('SELECT', 'WITH') for q in consultas)


def test_rechaza_sentencias_dml_y_ddl():
    for sentencia in ('DELETE FROM pedidos', 'DROP TABLE fact_ventas',
                      'WITH borrado AS (DELETE FROM pedidos RETURNING *) SELECT * FROM borrado',
                      'SELECT * FROM pedidos; DROP TABLE pedidos'):
        with pytest.raises(ValueError):
            vc.validar_select(sentencia)


def test_rechaza_multiples_instrucciones_y_bloqueos():
    for sentencia in ('SELECT 1; SELECT 2;', 'SELECT * FROM pedidos FOR UPDATE',
                      'SELECT * INTO temporal FROM pedidos'):
        with pytest.raises(ValueError):
            vc.validar_select(sentencia)


def test_no_consultas_faltantes(tmp_path):
    p = tmp_path / 'consultas.sql'
    p.write_text('-- Q01 | Primera\nSELECT 1;', encoding='utf-8')
    with pytest.raises(ValueError, match='Q01 a Q20'):
        vc.cargar_consultas(p)


def test_explain_solo_lectura():
    sql = vc.consulta_explain('SELECT COUNT(*) FROM public.fact_ventas WHERE id_producto=1;')
    assert sql == ('EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) '
                   'SELECT COUNT(*) FROM public.fact_ventas WHERE id_producto=1')
    with pytest.raises(ValueError):
        vc.consulta_explain('UPDATE pedidos SET estado=\'completado\'')


def test_configura_read_only_antes_de_consultar():
    comandos = []
    class Conn:
        def exec_driver_sql(self, sql):
            comandos.append(sql)
    vc.tx_solo_lectura(Conn())
    assert comandos[0] == 'SET TRANSACTION READ ONLY'
    assert any('statement_timeout' in x for x in comandos)
    assert any('search_path TO public' in x for x in comandos)


def test_archivo_csv_encabezado_y_fila(tmp_path):
    ruta = tmp_path / 'salida.csv'
    vc.escribir_csv(ruta, ['id', 'total'], [(1, '250.00')])
    contenido = ruta.read_text(encoding='utf-8-sig')
    assert 'id,total' in contenido
    assert '1,250.00' in contenido


def test_modos_mutuamente_excluyentes():
    with pytest.raises(ValueError, match='exactamente'):
        vc.ejecutar(plan=False, ejecutar_sql=False)
    with pytest.raises(ValueError, match='exactamente'):
        vc.ejecutar(plan=True, ejecutar_sql=True)
