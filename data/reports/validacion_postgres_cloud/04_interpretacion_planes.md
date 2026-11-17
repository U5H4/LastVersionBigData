# Interpretacion de EXPLAIN ANALYZE en Supabase

Planes reales de PostgreSQL (NO se fuerza el indice).

| Prueba | Planif. (ms) | Ejec. (ms) | Index Scans | Seq Scans | Indices identificados |
|---|---:|---:|---:|---:|---|
| E01 | 0.113 | 0.844 | 1 | 0 | ix_fact_anio_mes |
| E02 | 0.109 | 0.166 | 1 | 0 | ninguno detectado |
| E03 | 4.231 | 0.66 | 7 | 0 | categorias_pkey, clientes_pkey, dim_ciudades_pkey, ix_detalle_pedido_id_pedido, ix_pagos_id_pedido, ix_pedidos_fecha_pedido, productos_pkey |

**Interpretacion:** Index Scan/Bitmap Index Scan indica uso de indice; Seq Scan es una lectura secuencial y puede ser optima en tablas pequenas. Estos planes no prueban una mejora porcentual sin una comparacion controlada equivalente.

Fuente: E01/E02/E03_explain_analyze.txt guardados tras ejecutar validar_postgres_cloud.py --ejecutar.
