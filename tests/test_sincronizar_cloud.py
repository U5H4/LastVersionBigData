"""Pruebas de seguridad y contratos del sincronizador Cloud (sin red)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sincronizar_cloud as sync


def test_datasets_locales_3fn_y_documentos():
    tablas, docs = sync._cargar_local()
    assert len(tablas) == 9
    assert len(tablas["pedidos"]) == 3277
    assert len(tablas["detalle_pedido"]) == 3891
    assert len(tablas["fact_ventas"]) == 3891
    assert "total_pedido" not in tablas["pedidos"].columns
    assert "id_ciudad" in tablas["dim_sucursales"].columns
    assert {c: len(d) for c, d in docs.items()} == {
        "productos": 606, "resenas": 2475, "actividad_usuario": 4236,
    }


def test_clave_actividad_ignora_fecha_corregida():
    a = {"usuario": 1, "evento": "producto_visto", "producto": 2,
         "dispositivo": "android", "duracion_seg": 10, "fecha": "2026-09-10"}
    b = {**a, "fecha": "2026-10-09"}
    c = sync._clave(a, sync.CLAVES_MONGO["actividad_usuario"])
    assert c == sync._clave(b, sync.CLAVES_MONGO["actividad_usuario"])


def test_clave_mongo_duplicada_bloquea_sincronizacion():
    docs = [{"id_producto": 3}, {"id_producto": 3}]
    with pytest.raises(RuntimeError, match="duplicada"):
        sync._mapa_mongo("productos", docs)


def test_clave_mongo_nula_bloquea_sincronizacion():
    with pytest.raises(RuntimeError, match="sin clave"):
        sync._mapa_mongo("resenas", [{"id_resena": None}])


def test_mongo_plan_no_modifica_y_detecta_fecha_corregida():
    class Collection:
        def __init__(self, data):
            self.data = data
        def find(self, query):
            assert query == {}
            return self.data
    class FakeMongo:
        def __init__(self, data):
            self.data = {k: Collection(v) for k, v in data.items()}
        def list_collection_names(self):
            return list(self.data)
        def __getitem__(self, k):
            return self.data[k]
    local = {
        "productos": [{"id_producto": 1, "nombre": "X"}],
        "resenas": [{"id_resena": 2, "calificacion": 4}],
        "actividad_usuario": [{"usuario": 3, "evento": "producto_visto", "producto": 1,
                                "dispositivo": "android", "duracion_seg": 10,
                                "fecha": "2026-09-10"}],
    }
    remote = {k: [dict(v[0], _id=f"{k}-id")] for k, v in local.items()}
    remote["actividad_usuario"][0]["fecha"] = "2026-10-09"
    plan, _ = sync._leer_y_planear_mongo(FakeMongo(remote), local)
    assert plan["productos"]["documentos_diferentes"] == 0
    assert plan["resenas"]["documentos_diferentes"] == 0
    assert plan["actividad_usuario"]["documentos_diferentes"] == 1


def test_mongo_bloquea_cambios_de_ids():
    class C:
        def find(self, query):
            return [{"_id": "x", "id_producto": 2}]
    class DB:
        def list_collection_names(self):
            return list(sync.COLECCIONES)
        def __getitem__(self, k):
            return C()
    with pytest.raises(RuntimeError, match="claves no coinciden"):
        sync._leer_y_planear_mongo(DB(), {
            "productos": [{"id_producto": 1}],
            "resenas": [{"id_resena": 1}],
            "actividad_usuario": [{"usuario": 1, "evento": "x", "producto": 1,
                                   "dispositivo": "android", "duracion_seg": 2}],
        })


def test_no_permite_escrituras_sin_confirmacion(monkeypatch):
    monkeypatch.setattr(sync, "_cargar_local", lambda: (_ for _ in ()).throw(
        RuntimeError("NO debe cargar ni conectar")))
    with pytest.raises(RuntimeError, match="SINCRONIZAR_SQL"):
        sync.ejecutar(SimpleNamespace(aplicar_sql=True, aplicar_mongo=False,
                                     confirmar="", verificar=False))
    with pytest.raises(RuntimeError, match="SINCRONIZAR_MONGO"):
        sync.ejecutar(SimpleNamespace(aplicar_sql=False, aplicar_mongo=True,
                                     confirmar="", verificar=False))


def test_exige_host_supabase(monkeypatch):
    monkeypatch.setattr(sync, "config_pg", lambda: {"host": "localhost"})
    with pytest.raises(RuntimeError, match="solo acepta SUPABASE_HOST"):
        sync._exigir_supabase()
    monkeypatch.setattr(sync, "config_pg", lambda: {
        "host": "aws-1-us-east-1.pooler.supabase.com"})
    sync._exigir_supabase()


def test_aplicar_mongo_respaldos_y_actualizacion_sin_eliminar(monkeypatch):
    """Simula Atlas, exige copia completa y preserva los _id existentes."""
    import types
    
    class ReplaceOne:
        def __init__(self, filter, replacement, upsert=False):
            self.filter = filter
            self.replacement = replacement
            assert not upsert

    monkeypatch.setitem(sys.modules, "pymongo", types.SimpleNamespace(ReplaceOne=ReplaceOne))

    class FakeCollection:
        def __init__(self, docs=None):
            self.docs = list(docs or [])
        def find(self, query):
            return [x.copy() for x in self.docs]
        def count_documents(self, query):
            return len(self.docs)
        def insert_many(self, docs):
            self.docs.extend([d.copy() for d in docs])
        def bulk_write(self, ops, ordered=True):
            assert ordered
            for op in ops:
                for i, d in enumerate(self.docs):
                    if d["_id"] == op.filter["_id"]:
                        self.docs[i] = op.replacement
                        break
                else:
                    raise AssertionError("El documento no existe")
            return SimpleNamespace(matched_count=len(ops))

    class FakeDB:
        def __init__(self):
            self.data = {
                "productos": FakeCollection([{"_id": "mongo-1", "id_producto": 10, "nombre": "Viejo"}]),
                "resenas": FakeCollection([{"_id": "mongo-2", "id_resena": 11, "fecha": "2026-04-01"}]),
                "actividad_usuario": FakeCollection([{"_id": "mongo-3", "usuario": 9, "evento": "producto_visto",
                      "producto": 10, "dispositivo": "android", "duracion_seg": 7, "fecha": "2026-10-09"}]),
            }
        def list_collection_names(self):
            return list(self.data)
        def create_collection(self, name):
            assert name not in self.data
            self.data[name] = FakeCollection()
        def __getitem__(self, k):
            return self.data[k]

    class FakeClient:
        def close(self):
            pass
    db = FakeDB()
    monkeypatch.setattr(sync, "_abrir_mongo", lambda: (FakeClient(), db))
    docs = {
        "productos": [{"id_producto": 10, "nombre": "Nuevo"}],
        "resenas": [{"id_resena": 11, "fecha": "2026-04-01"}],
        "actividad_usuario": [{"usuario": 9, "evento": "producto_visto",
                             "producto": 10, "dispositivo": "android", "duracion_seg": 7,
                             "fecha": "2026-09-10"}],
    }
    r = sync.aplicar_mongo(docs)
    assert r["estado"] == "OK"
    assert r["documentos_actualizados"] == {"productos": 1, "resenas": 0, "actividad_usuario": 1}
    assert len(r["respaldo_datos"]) == 3
    assert db["productos"].docs[0]["_id"] == "mongo-1"
    assert db["productos"].docs[0]["nombre"] == "Nuevo"
    assert db["actividad_usuario"].docs[0]["fecha"] == "2026-09-10"
    copia_productos = [x for x in r["respaldo_datos"] if x.endswith("_productos")][0]
    assert db[copia_productos].docs[0]["nombre"] == "Viejo"


def test_identificador_tabla_temporal_y_seguridad():
    assert sync._ident("_juc_stage_pedidos") == "_juc_stage_pedidos"
    assert sync._ident("fact_ventas") == "fact_ventas"
    for bad in ("public.pedidos", "pedidos;DROP TABLE pedidos", "x y", "1inicio", "a-b"):
        with pytest.raises(ValueError, match="Identificador SQL invalido"):
            sync._ident(bad)


def test_mongo_usa_mongo_uri_atlas_cuando_atlas_uri_vacia(monkeypatch):
    """Regresion: config.py y la integracion real admiten MONGO_URI."""
    import types
    urls = []
    class FakeClient:
        def __init__(self, uri, **kwargs):
            urls.append(uri)
        @property
        def admin(self):
            return self
        def command(self, cmd):
            assert cmd == "ping"
            return {"ok": 1}
        def __getitem__(self, db):
            return {"base": db}
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, "pymongo", types.SimpleNamespace(MongoClient=FakeClient))
    monkeypatch.setattr(sync, "MONGO_ATLAS_URI", "")
    monkeypatch.setattr(sync, "MONGO_URI", "mongodb+srv://ejemplo.test/")
    monkeypatch.setattr(sync, "MONGO_DB", "Prueba1")
    monkeypatch.delenv("SIN_MONGO", raising=False)
    client, db = sync._abrir_mongo()
    assert urls == ["mongodb+srv://ejemplo.test/"]
    assert db["base"] == "Prueba1"
    client.close()


def test_mongo_no_permite_fallback_a_local(monkeypatch):
    monkeypatch.setattr(sync, "MONGO_ATLAS_URI", "")
    monkeypatch.setattr(sync, "MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(sync, "MONGO_DB", "Prueba1")
    monkeypatch.delenv("SIN_MONGO", raising=False)
    with pytest.raises(RuntimeError, match=r"mongodb\+srv"):
        sync._abrir_mongo()