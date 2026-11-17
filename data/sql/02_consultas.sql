-- 20 consultas SQL del proyecto JUC, para PostgreSQL (3FN + fact_ventas)

-- Q01 | Top 10 productos por facturacion
SELECT p.id_producto, p.nombre AS producto, cat.nombre AS categoria,
                   SUM(f.total) AS facturacion,
                   SUM(f.cantidad) AS unidades,
                   COUNT(DISTINCT f.id_pedido) AS pedidos,
                   ROUND(SUM(f.total) / SUM(f.cantidad), 2) AS precio_promedio
            FROM fact_ventas f
            INNER JOIN productos p   ON p.id_producto = f.id_producto
            INNER JOIN categorias cat ON cat.id_categoria = p.id_categoria
            WHERE f.es_completado = TRUE
            GROUP BY p.id_producto, p.nombre, cat.nombre
            ORDER BY facturacion DESC
            LIMIT 10;

-- Q02 | Top 10 clientes por gasto
WITH por_pedido AS (
                SELECT DISTINCT id_pedido, id_cliente, total_pedido, fecha_pedido
                FROM fact_ventas
                WHERE es_completado = TRUE
            )
            SELECT c.id_cliente, c.nombre AS cliente, ci.nombre AS ciudad, c.es_premium,
                   COUNT(p.id_pedido) AS pedidos,
                   SUM(p.total_pedido) AS gasto,
                   ROUND(AVG(p.total_pedido), 2) AS ticket_promedio,
                   MAX(p.fecha_pedido) AS ultima_compra
            FROM por_pedido p
            INNER JOIN clientes c       ON c.id_cliente = p.id_cliente
            LEFT JOIN dim_ciudades ci   ON ci.id_ciudad = c.id_ciudad
            GROUP BY c.id_cliente, c.nombre, ci.nombre, c.es_premium
            ORDER BY gasto DESC
            LIMIT 10;

-- Q03 | Ventas mes a mes (serie temporal)
SELECT anio_mes,
                   MIN(anio) AS anio, MIN(mes) AS mes,
                   COUNT(DISTINCT id_pedido) AS pedidos,
                   SUM(cantidad) AS unidades,
                   SUM(total) AS facturacion,
                   ROUND(AVG(total_pedido), 2) AS ticket_promedio
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY anio_mes
            ORDER BY anio_mes;

-- Q04 | Facturacion por categoria
SELECT categoria,
                   SUM(total) AS facturacion,
                   SUM(cantidad) AS unidades,
                   COUNT(DISTINCT producto_nombre) AS productos,
                   ROUND(100.0 * SUM(total) / (SELECT SUM(total) FROM fact_ventas
                                                WHERE es_completado = TRUE), 2) AS pct_del_total,
                   ROUND(AVG(porcentaje_descuento), 2) AS descuento_promedio_pct
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY categoria
            ORDER BY facturacion DESC;

-- Q05 | Ticket promedio por canal de venta
WITH por_pedido AS (
                SELECT DISTINCT id_pedido, canal_venta, total_pedido, unidades
                FROM fact_ventas
                WHERE es_completado = TRUE
            )
            SELECT canal_venta,
                   COUNT(*) AS pedidos,
                   SUM(total_pedido) AS facturacion,
                   ROUND(AVG(total_pedido), 2) AS ticket_promedio,
                   ROUND(AVG(unidades), 2) AS unidades_promedio,
                   ROUND(SUM(total_pedido) / SUM(unidades), 2) AS precio_unitario_promedio
            FROM por_pedido
            GROUP BY canal_venta
            ORDER BY facturacion DESC;

-- Q06 | Productos en catalogo que NUNCA se vendieron
SELECT p.id_producto, p.nombre AS producto, cat.nombre AS categoria,
                   p.precio, p.fecha_lanzamiento
            FROM productos p
            LEFT JOIN categorias cat ON cat.id_categoria = p.id_categoria
            LEFT JOIN detalle_pedido d ON d.id_producto = p.id_producto
            WHERE d.id_producto IS NULL
            ORDER BY p.precio DESC;

-- Q07 | Clientes con pedidos cancelados
SELECT c.id_cliente, c.nombre AS cliente, ci.nombre AS ciudad,
                   COUNT(DISTINCT pe.id_pedido) AS pedidos_cancelados,
                   SUM(f.total) AS monto_perdido
            FROM fact_ventas f
            INNER JOIN clientes c     ON c.id_cliente = f.id_cliente
            LEFT JOIN dim_ciudades ci ON ci.id_ciudad = c.id_ciudad
            INNER JOIN pedidos pe     ON pe.id_pedido = f.id_pedido
            WHERE pe.estado = 'cancelado'
            GROUP BY c.id_cliente, c.nombre, ci.nombre
            ORDER BY monto_perdido DESC
            LIMIT 25;

-- Q08 | Metodos de pago mas usados y su conciliacion
-- Unidad de analisis: un REGISTRO de pago, no una linea de fact_ventas.
-- Conciliado = |monto - total_pedido calculado| <= 5 MXN; NO indica pago aprobado.
SELECT metodo,
       COUNT(DISTINCT id_pedido) AS pedidos,
       ROUND(SUM(monto), 2) AS monto,
       ROUND(100.0 * SUM(CASE WHEN conciliado = TRUE THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0), 2) AS pct_conciliado,
       ROUND(AVG(diferencia_pago), 2) AS diferencia_promedio
FROM public.v_pagos_conciliados
WHERE metodo IS NOT NULL
GROUP BY metodo
ORDER BY monto DESC;

-- Q09 | Productos con mayor descuento promedio
SELECT producto_nombre AS producto, categoria,
                   ROUND(AVG(porcentaje_descuento), 2) AS descuento_promedio_pct,
                   MAX(porcentaje_descuento) AS descuento_maximo_pct,
                   SUM(descuento) AS descuento_total,
                   SUM(total) AS facturacion_neta
            FROM fact_ventas
            WHERE descuento > 0
            GROUP BY producto_nombre, categoria
            ORDER BY descuento_promedio_pct DESC
            LIMIT 20;

-- Q10 | Ciudades con mas facturacion
SELECT ciudad, region,
                   COUNT(DISTINCT id_cliente) AS clientes,
                   SUM(total) AS facturacion,
                   ROUND(AVG(total_pedido), 2) AS ticket_promedio
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY ciudad, region
            ORDER BY facturacion DESC;

-- Q11 | Clientes premium vs estandar
WITH por_pedido AS (
                SELECT DISTINCT id_pedido, id_cliente, total_pedido, es_premium
                FROM fact_ventas
                WHERE es_completado = TRUE
            ),
            totales AS (
                SELECT es_premium,
                       COUNT(DISTINCT id_cliente) AS clientes,
                       SUM(total_pedido) AS facturacion
                FROM por_pedido
                GROUP BY es_premium
            )
            SELECT CASE WHEN es_premium = TRUE THEN 'Premium' ELSE 'Estandar' END
                       AS segmento,
                   clientes,
                   facturacion,
                   ROUND(facturacion / clientes, 2) AS gasto_promedio_cliente,
                   ROUND(100.0 * clientes
                         / (SELECT SUM(clientes) FROM totales), 2) AS pct_clientes,
                   ROUND(100.0 * facturacion
                         / (SELECT SUM(facturacion) FROM totales), 2) AS pct_facturacion
            FROM totales
            ORDER BY facturacion DESC;

-- Q12 | Concentracion de ventas (ley de Pareto)
WITH ventas AS (
                SELECT producto_nombre, SUM(total) AS facturacion
                FROM fact_ventas
                WHERE es_completado = TRUE
                GROUP BY producto_nombre
            ),
            acumulado AS (
                SELECT producto_nombre, facturacion,
                       SUM(facturacion) OVER (ORDER BY facturacion DESC) AS acum,
                       SUM(facturacion) OVER () AS total,
                       ROW_NUMBER() OVER (ORDER BY facturacion DESC) AS posicion
                FROM ventas
            ),
            -- posicion del primer producto que hace cuadrar el 80% del total
            corte AS (
                SELECT MIN(posicion) AS n FROM acumulado WHERE acum >= total * 0.8
            ),
            resumen AS (
                SELECT (SELECT COUNT(*) FROM ventas) AS productos,
                       (SELECT total FROM acumulado LIMIT 1) AS total,
                       -- facturacion ACUMULADA del prefijo que cruza el 80%,
                       -- no la del ultimo producto suelto
                       (SELECT acum FROM acumulado
                        WHERE posicion = (SELECT n FROM corte)) AS parcial
            )
            SELECT productos,
                   ROUND(100.0 * parcial / total, 2) AS pct_ventas_en_primer_80,
                   ROUND(100.0 * (SELECT n FROM corte) / productos, 2)
                       AS pct_productos_para_80
            FROM resumen;

-- Q13 | Productos con mejor margen real
SELECT producto_nombre AS producto, categoria,
                   SUM(cantidad) AS unidades,
                   SUM(subtotal) AS ingresos_brutos,
                   SUM(descuento) AS descuentos,
                   ROUND(SUM(total) * AVG(margen_bruto_producto), 2) AS utilidad_estimada,
                   ROUND(AVG(margen_bruto_producto), 4) AS margen_promedio
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY producto_nombre, categoria
            HAVING SUM(cantidad) >= 5
            ORDER BY utilidad_estimada DESC
            LIMIT 20;

-- Q14 | Pedidos con pago NO conciliado
SELECT id_pedido, id_cliente, estado, estado_pago, metodo,
                   total_pedido, monto, diferencia_pago,
                   ROUND(100.0 * ABS(diferencia_pago) / total_pedido, 2) AS pct_diferencia
            FROM fact_ventas
            WHERE conciliado = FALSE AND total_pedido > 0
            ORDER BY ABS(diferencia_pago) DESC
            LIMIT 25;

-- Q15 | Clientes nuevos vs recurrentes
WITH primera AS (
                SELECT id_cliente, MIN(fecha_pedido) AS primera_compra
                FROM fact_ventas
                WHERE es_completado = TRUE
                GROUP BY id_cliente
            ),
            perfil AS (
                SELECT f.id_cliente,
                       COUNT(DISTINCT f.id_pedido) AS pedidos,
                       SUM(f.total) AS gasto
                FROM fact_ventas f
                WHERE f.es_completado = TRUE
                GROUP BY f.id_cliente
            )
            SELECT CASE WHEN pe.pedidos = 1 THEN 'Una sola compra' ELSE 'Recurrente' END AS tipo,
                   COUNT(*) AS clientes,
                   ROUND(AVG(pe.pedidos), 2) AS pedidos_promedio,
                   ROUND(SUM(pe.gasto), 2) AS facturacion,
                   ROUND(AVG(pe.gasto), 2) AS gasto_promedio
            FROM perfil pe
            GROUP BY CASE WHEN pe.pedidos = 1 THEN 'Una sola compra' ELSE 'Recurrente' END
            ORDER BY facturacion DESC;

-- Q16 | Trimestre x categoria (pivot de mix)
SELECT anio, trimestre, categoria,
                   SUM(total) AS facturacion,
                   SUM(cantidad) AS unidades
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY anio, trimestre, categoria
            ORDER BY anio, trimestre, facturacion DESC;

-- Q17 | Productos de alto valor y baja rotacion
SELECT producto_nombre AS producto, categoria, rango_precio,
                   precio_lista, SUM(cantidad) AS unidades,
                   ROUND(SUM(total), 2) AS facturacion,
                   COUNT(DISTINCT id_cliente) AS clientes_distintos
            FROM fact_ventas
            WHERE es_completado = TRUE
            GROUP BY producto_nombre, categoria, rango_precio, precio_lista
            HAVING SUM(cantidad) <= 2
            ORDER BY precio_lista DESC
            LIMIT 25;

-- Q18 | Cobertura por sucursal y estado del pedido
SELECT id_sucursal, estado,
                   COUNT(DISTINCT id_pedido) AS pedidos,
                   SUM(total) AS facturacion,
                   ROUND(AVG(total_pedido), 2) AS ticket_promedio
            FROM fact_ventas
            GROUP BY id_sucursal, estado
            ORDER BY id_sucursal, facturacion DESC;

-- Q19 | Detalle 360 del pedido (JOIN de 7 tablas)
SELECT c.nombre AS cliente, ci.nombre AS ciudad, cat.nombre AS categoria,
                   pr.nombre AS producto, pe.id_pedido, pe.fecha_pedido,
                   d.cantidad, d.precio_unitario, d.descuento,
                   ROUND(d.cantidad * d.precio_unitario - d.descuento, 2) AS total,
                   pg.metodo AS metodo_pago, pg.estado_pago
            FROM detalle_pedido d
            INNER JOIN pedidos pe     ON pe.id_pedido = d.id_pedido
            INNER JOIN clientes c     ON c.id_cliente = pe.id_cliente
            LEFT JOIN dim_ciudades ci ON ci.id_ciudad = c.id_ciudad
            INNER JOIN productos pr   ON pr.id_producto = d.id_producto
            INNER JOIN categorias cat ON cat.id_categoria = pr.id_categoria
            LEFT JOIN pagos pg  ON pg.id_pedido = pe.id_pedido
            WHERE pe.estado IN ('completado', 'enviado')
            ORDER BY pe.fecha_pedido DESC, pe.id_pedido
            LIMIT 20;

-- Q20 | Resumen mensual via vista v_resumen_ventas
SELECT anio_mes, mes_nombre, pedidos, clientes, productos,
                   unidades, facturacion, ticket_promedio
            FROM v_resumen_ventas
            ORDER BY anio_mes;


