-- Corporativo JUC: auditoria de estructura + rendimiento en Supabase.
-- SOLO LECTURA. No crea/elimina indices, tablas, vistas ni datos.

-- 1. Comprobar vistas de las tablas operativas en 3FN.
SELECT table_name AS vista
FROM information_schema.views
WHERE table_schema = 'public'
  AND table_name IN (
    'v_detalle_calculado', 'v_pedidos_calculados',
    'v_pagos_conciliados', 'v_resumen_ventas'
  )
ORDER BY table_name;

-- 2. Mostrar indices de interes para la evidencia.
SELECT tablename AS tabla, indexname AS indice, indexdef AS definicion
FROM pg_indexes
WHERE schemaname='public'
  AND indexname IN (
    'ix_fact_anio_mes', 'ix_fact_producto', 'ix_pedidos_fecha_pedido'
  )
ORDER BY indexname;

-- 3. Consultar registros sin modificar datos.
SELECT 'categorias' AS tabla, COUNT(*) AS registros FROM public.categorias
UNION ALL SELECT 'dim_ciudades', COUNT(*) FROM public.dim_ciudades
UNION ALL SELECT 'dim_sucursales', COUNT(*) FROM public.dim_sucursales
UNION ALL SELECT 'clientes', COUNT(*) FROM public.clientes
UNION ALL SELECT 'productos', COUNT(*) FROM public.productos
UNION ALL SELECT 'pedidos', COUNT(*) FROM public.pedidos
UNION ALL SELECT 'detalle_pedido', COUNT(*) FROM public.detalle_pedido
UNION ALL SELECT 'pagos', COUNT(*) FROM public.pagos
UNION ALL SELECT 'fact_ventas', COUNT(*) FROM public.fact_ventas;

-- 4. EXPLAIN ANALYZE: filtro por mes (indice disponible si lo elige el optimizador).
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*)
FROM public.fact_ventas
WHERE anio_mes = '2024-08';

-- 5. EXPLAIN ANALYZE: filtro por producto.
EXPLAIN (ANALYZE, BUFFERS)
SELECT SUM(cantidad)
FROM public.fact_ventas
WHERE id_producto = 324;

-- 6. Para las 20 consultas analiticas, ver data/sql/02_consultas.sql.
