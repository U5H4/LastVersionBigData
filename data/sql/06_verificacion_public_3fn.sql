-- Ejecutar solo DESPUES de la migracion en SQL Editor de Supabase.
-- Deben aparecer ocho tablas fisicas operativas y una fact de analitica.
SELECT table_name,
       COUNT(*) AS columnas
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('categorias', 'dim_ciudades', 'dim_sucursales',
    'clientes', 'productos', 'pedidos', 'detalle_pedido', 'pagos')
GROUP BY table_name
ORDER BY table_name;

SELECT table_name FROM information_schema.views
WHERE table_schema='public'
AND table_name IN ('v_detalle_calculado', 'v_pedidos_calculados',
    'v_pagos_conciliados', 'v_resumen_ventas')
ORDER BY table_name;

SELECT tc.table_name, tc.constraint_name, tc.constraint_type
FROM information_schema.table_constraints tc
WHERE tc.table_schema='public'
  AND tc.table_name IN ('categorias', 'dim_ciudades', 'dim_sucursales',
     'clientes', 'productos', 'pedidos', 'detalle_pedido', 'pagos')
ORDER BY tc.table_name, tc.constraint_type;

SELECT 'pedidos' AS tabla, COUNT(*) AS registros FROM public.pedidos
UNION ALL SELECT 'detalle_pedido', COUNT(*) FROM public.detalle_pedido
UNION ALL SELECT 'fact_ventas', COUNT(*) FROM public.fact_ventas;

-- El detalle puede calcular importes sin duplicarlos fisicamente.
SELECT id_detalle, id_pedido, cantidad, precio_unitario, descuento, subtotal, total
FROM public.v_detalle_calculado ORDER BY id_detalle LIMIT 5;
