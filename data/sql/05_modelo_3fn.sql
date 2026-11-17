-- Modelo fisico 3FN, aislado en juc_3fn; no modifica public.
-- Ejecutar despues de que public este poblado.

CREATE SCHEMA IF NOT EXISTS juc_3fn;

CREATE TABLE IF NOT EXISTS juc_3fn.categorias (
       id_categoria INTEGER PRIMARY KEY,
       nombre VARCHAR(80) NOT NULL UNIQUE,
       descripcion VARCHAR(200)
    );

CREATE TABLE IF NOT EXISTS juc_3fn.dim_ciudades (
       id_ciudad INTEGER PRIMARY KEY,
       nombre VARCHAR(80) NOT NULL UNIQUE,
       region VARCHAR(50)
    );

CREATE TABLE IF NOT EXISTS juc_3fn.dim_sucursales (
       id_sucursal INTEGER PRIMARY KEY,
       nombre VARCHAR(120) NOT NULL UNIQUE,
       id_ciudad INTEGER NOT NULL REFERENCES juc_3fn.dim_ciudades(id_ciudad),
       metros_cuadrados INTEGER CHECK (metros_cuadrados >= 0),
       fecha_apertura DATE
    );

CREATE TABLE IF NOT EXISTS juc_3fn.clientes (
       id_cliente INTEGER PRIMARY KEY,
       nombre VARCHAR(160) NOT NULL,
       correo VARCHAR(160) NOT NULL,
       telefono VARCHAR(20),
       id_ciudad INTEGER REFERENCES juc_3fn.dim_ciudades(id_ciudad),
       fecha_alta DATE,
       canal_registro VARCHAR(30),
       es_premium BOOLEAN
    );

CREATE TABLE IF NOT EXISTS juc_3fn.productos (
       id_producto INTEGER PRIMARY KEY,
       nombre VARCHAR(200) NOT NULL UNIQUE,
       id_categoria INTEGER NOT NULL REFERENCES juc_3fn.categorias(id_categoria),
       precio NUMERIC(12,2) NOT NULL CHECK (precio > 0),
       margen_bruto NUMERIC(5,4) CHECK (margen_bruto BETWEEN 0 AND 1),
       id_proveedor INTEGER,
       activo BOOLEAN,
       fecha_lanzamiento DATE
    );

CREATE TABLE IF NOT EXISTS juc_3fn.pedidos (
       id_pedido INTEGER PRIMARY KEY,
       id_cliente INTEGER NOT NULL REFERENCES juc_3fn.clientes(id_cliente),
       fecha_pedido DATE NOT NULL,
       canal_venta VARCHAR(30),
       id_sucursal INTEGER REFERENCES juc_3fn.dim_sucursales(id_sucursal),
       estado VARCHAR(20)
    );

CREATE TABLE IF NOT EXISTS juc_3fn.detalle_pedido (
       id_detalle INTEGER PRIMARY KEY,
       id_pedido INTEGER NOT NULL REFERENCES juc_3fn.pedidos(id_pedido),
       id_producto INTEGER NOT NULL REFERENCES juc_3fn.productos(id_producto),
       cantidad INTEGER NOT NULL CHECK (cantidad > 0),
       precio_unitario NUMERIC(12,2) NOT NULL CHECK (precio_unitario >= 0),
       descuento NUMERIC(12,2) NOT NULL DEFAULT 0
           CHECK (descuento >= 0 AND descuento <= cantidad * precio_unitario)
    );

CREATE TABLE IF NOT EXISTS juc_3fn.pagos (
       id_pago INTEGER PRIMARY KEY,
       id_pedido INTEGER NOT NULL REFERENCES juc_3fn.pedidos(id_pedido),
       metodo VARCHAR(30) NOT NULL,
       monto NUMERIC(14,2) NOT NULL CHECK (monto >= 0),
       fecha_pago DATE,
       estado_pago VARCHAR(20)
    );

CREATE OR REPLACE VIEW juc_3fn.v_lineas_venta AS
       SELECT d.id_detalle, d.id_pedido, p.id_cliente, d.id_producto,
              pr.id_categoria, p.id_sucursal, p.fecha_pedido,
              EXTRACT(YEAR FROM p.fecha_pedido)::INTEGER AS anio,
              EXTRACT(MONTH FROM p.fecha_pedido)::INTEGER AS mes,
              TO_CHAR(p.fecha_pedido, 'YYYY-MM') AS anio_mes,
              p.estado,
              (p.estado IN ('completado','enviado')) AS es_completado,
              d.cantidad, d.precio_unitario, d.descuento,
              (d.cantidad * d.precio_unitario)::NUMERIC(14,2) AS subtotal,
              (d.cantidad * d.precio_unitario - d.descuento)::NUMERIC(14,2) AS total,
              pr.precio, pr.margen_bruto,
              (pr.precio * (1 - pr.margen_bruto))::NUMERIC(12,2) AS costo_estimado
       FROM juc_3fn.detalle_pedido d
       JOIN juc_3fn.pedidos p ON p.id_pedido = d.id_pedido
       JOIN juc_3fn.productos pr ON pr.id_producto = d.id_producto;

CREATE OR REPLACE VIEW juc_3fn.v_resumen_ventas AS
       SELECT anio_mes,
              COUNT(DISTINCT id_pedido) AS pedidos,
              COUNT(DISTINCT id_cliente) AS clientes,
              COUNT(DISTINCT id_producto) AS productos,
              SUM(cantidad) AS unidades,
              ROUND(SUM(total), 2) AS facturacion,
              ROUND(SUM(total) / NULLIF(COUNT(DISTINCT id_pedido),0), 2) AS ticket_promedio
       FROM juc_3fn.v_lineas_venta
       WHERE es_completado = TRUE
       GROUP BY anio_mes;

INSERT INTO juc_3fn.categorias (id_categoria, nombre, descripcion)
SELECT id_categoria, nombre, descripcion FROM public.categorias
WHERE TRUE
ON CONFLICT (id_categoria) DO UPDATE SET nombre = EXCLUDED.nombre, descripcion = EXCLUDED.descripcion;

INSERT INTO juc_3fn.dim_ciudades (id_ciudad, nombre, region)
SELECT id_ciudad, nombre, region FROM public.dim_ciudades
WHERE TRUE
ON CONFLICT (id_ciudad) DO UPDATE SET nombre = EXCLUDED.nombre, region = EXCLUDED.region;

INSERT INTO juc_3fn.dim_sucursales (id_sucursal, nombre, id_ciudad, metros_cuadrados, fecha_apertura)
SELECT s.id_sucursal, s.nombre, c.id_ciudad,
                                 s.metros_cuadrados, s.fecha_apertura
                          FROM public.dim_sucursales s
                          JOIN public.dim_ciudades c ON c.nombre = s.ciudad
WHERE TRUE
ON CONFLICT (id_sucursal) DO UPDATE SET nombre = EXCLUDED.nombre, id_ciudad = EXCLUDED.id_ciudad, metros_cuadrados = EXCLUDED.metros_cuadrados, fecha_apertura = EXCLUDED.fecha_apertura;

INSERT INTO juc_3fn.clientes (id_cliente, nombre, correo, telefono, id_ciudad, fecha_alta, canal_registro, es_premium)
SELECT id_cliente, nombre, correo, telefono, id_ciudad,
                          fecha_alta, canal_registro, es_premium FROM public.clientes
WHERE TRUE
ON CONFLICT (id_cliente) DO UPDATE SET nombre = EXCLUDED.nombre, correo = EXCLUDED.correo, telefono = EXCLUDED.telefono, id_ciudad = EXCLUDED.id_ciudad, fecha_alta = EXCLUDED.fecha_alta, canal_registro = EXCLUDED.canal_registro, es_premium = EXCLUDED.es_premium;

INSERT INTO juc_3fn.productos (id_producto, nombre, id_categoria, precio, margen_bruto, id_proveedor, activo, fecha_lanzamiento)
SELECT id_producto, nombre, id_categoria, precio, margen_bruto,
                           id_proveedor, activo, fecha_lanzamiento FROM public.productos
WHERE TRUE
ON CONFLICT (id_producto) DO UPDATE SET nombre = EXCLUDED.nombre, id_categoria = EXCLUDED.id_categoria, precio = EXCLUDED.precio, margen_bruto = EXCLUDED.margen_bruto, id_proveedor = EXCLUDED.id_proveedor, activo = EXCLUDED.activo, fecha_lanzamiento = EXCLUDED.fecha_lanzamiento;

INSERT INTO juc_3fn.pedidos (id_pedido, id_cliente, fecha_pedido, canal_venta, id_sucursal, estado)
SELECT id_pedido, id_cliente, fecha_pedido, canal_venta,
                         id_sucursal, estado FROM public.pedidos
WHERE TRUE
ON CONFLICT (id_pedido) DO UPDATE SET id_cliente = EXCLUDED.id_cliente, fecha_pedido = EXCLUDED.fecha_pedido, canal_venta = EXCLUDED.canal_venta, id_sucursal = EXCLUDED.id_sucursal, estado = EXCLUDED.estado;

INSERT INTO juc_3fn.detalle_pedido (id_detalle, id_pedido, id_producto, cantidad, precio_unitario, descuento)
SELECT id_detalle, id_pedido, id_producto, cantidad,
                                 precio_unitario, descuento FROM public.detalle_pedido
WHERE TRUE
ON CONFLICT (id_detalle) DO UPDATE SET id_pedido = EXCLUDED.id_pedido, id_producto = EXCLUDED.id_producto, cantidad = EXCLUDED.cantidad, precio_unitario = EXCLUDED.precio_unitario, descuento = EXCLUDED.descuento;

INSERT INTO juc_3fn.pagos (id_pago, id_pedido, metodo, monto, fecha_pago, estado_pago)
SELECT id_pago, id_pedido, metodo, monto, fecha_pago,
                       estado_pago FROM public.pagos
WHERE TRUE
ON CONFLICT (id_pago) DO UPDATE SET id_pedido = EXCLUDED.id_pedido, metodo = EXCLUDED.metodo, monto = EXCLUDED.monto, fecha_pago = EXCLUDED.fecha_pago, estado_pago = EXCLUDED.estado_pago;
