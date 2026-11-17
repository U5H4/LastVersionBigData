-- Modelo public 3FN + capa fact_ventas para PostgreSQL
-- Para una base existente usar src/migrar_public_3fn.py. NO ejecutar este DDL directamente sobre tablas antiguas.

CREATE TABLE categorias (
    id_categoria INTEGER PRIMARY KEY,
    nombre VARCHAR(80) NOT NULL,
    descripcion VARCHAR(200),
    CONSTRAINT uq_categorias_nombre UNIQUE (nombre)
);

CREATE TABLE dim_ciudades (
    id_ciudad INTEGER PRIMARY KEY,
    nombre VARCHAR(80) NOT NULL,
    region VARCHAR(50),
    CONSTRAINT uq_ciudades_nombre UNIQUE (nombre)
);

CREATE TABLE dim_sucursales (
    id_sucursal INTEGER PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL,
    id_ciudad INTEGER NOT NULL,
    metros_cuadrados INTEGER,
    fecha_apertura DATE,
    CONSTRAINT uq_sucursales_nombre UNIQUE (nombre),
    CONSTRAINT chk_suc_metros CHECK (metros_cuadrados >= 0)
);

CREATE TABLE clientes (
    id_cliente INTEGER PRIMARY KEY,
    nombre VARCHAR(160) NOT NULL,
    correo VARCHAR(160) NOT NULL,
    telefono VARCHAR(20),
    id_ciudad INTEGER,
    fecha_alta DATE,
    canal_registro VARCHAR(30),
    es_premium BOOLEAN
);

CREATE TABLE productos (
    id_producto INTEGER PRIMARY KEY,
    nombre VARCHAR(200) NOT NULL,
    id_categoria INTEGER NOT NULL,
    precio NUMERIC(12,2) NOT NULL,
    margen_bruto NUMERIC(5,4),
    id_proveedor INTEGER,
    activo BOOLEAN,
    fecha_lanzamiento DATE,
    CONSTRAINT uq_productos_nombre UNIQUE (nombre),
    CONSTRAINT chk_prod_precio CHECK (precio > 0),
    CONSTRAINT chk_prod_margen CHECK (margen_bruto >= 0 AND margen_bruto <= 1)
);

CREATE TABLE pedidos (
    id_pedido INTEGER PRIMARY KEY,
    id_cliente INTEGER NOT NULL,
    fecha_pedido DATE NOT NULL,
    canal_venta VARCHAR(30),
    id_sucursal INTEGER,
    estado VARCHAR(20)
);

CREATE TABLE detalle_pedido (
    id_detalle INTEGER PRIMARY KEY,
    id_pedido INTEGER NOT NULL,
    id_producto INTEGER NOT NULL,
    cantidad INTEGER NOT NULL,
    precio_unitario NUMERIC(12,2) NOT NULL,
    descuento NUMERIC(12,2) NOT NULL,
    CONSTRAINT chk_det_cantidad CHECK (cantidad > 0),
    CONSTRAINT chk_det_precio CHECK (precio_unitario >= 0),
    CONSTRAINT chk_det_descuento CHECK (descuento >= 0 AND descuento <= cantidad * precio_unitario)
);

CREATE TABLE pagos (
    id_pago INTEGER PRIMARY KEY,
    id_pedido INTEGER NOT NULL,
    metodo VARCHAR(30) NOT NULL,
    monto NUMERIC(14,2) NOT NULL,
    fecha_pago DATE,
    estado_pago VARCHAR(20),
    CONSTRAINT chk_pago_monto CHECK (monto >= 0)
);

ALTER TABLE clientes ADD CONSTRAINT fk_clientes_id_ciudad FOREIGN KEY (id_ciudad) REFERENCES dim_ciudades(id_ciudad);

ALTER TABLE dim_sucursales ADD CONSTRAINT fk_dim_sucursales_id_ciudad FOREIGN KEY (id_ciudad) REFERENCES dim_ciudades(id_ciudad);

ALTER TABLE productos ADD CONSTRAINT fk_productos_id_categoria FOREIGN KEY (id_categoria) REFERENCES categorias(id_categoria);

ALTER TABLE pedidos ADD CONSTRAINT fk_pedidos_id_cliente FOREIGN KEY (id_cliente) REFERENCES clientes(id_cliente);

ALTER TABLE pedidos ADD CONSTRAINT fk_pedidos_id_sucursal FOREIGN KEY (id_sucursal) REFERENCES dim_sucursales(id_sucursal);

ALTER TABLE detalle_pedido ADD CONSTRAINT fk_detalle_pedido_id_pedido FOREIGN KEY (id_pedido) REFERENCES pedidos(id_pedido);

ALTER TABLE detalle_pedido ADD CONSTRAINT fk_detalle_pedido_id_producto FOREIGN KEY (id_producto) REFERENCES productos(id_producto);

ALTER TABLE pagos ADD CONSTRAINT fk_pagos_id_pedido FOREIGN KEY (id_pedido) REFERENCES pedidos(id_pedido);

CREATE TABLE fact_ventas (
    id_detalle INTEGER PRIMARY KEY,
    id_pedido INTEGER,
    id_cliente INTEGER,
    id_producto INTEGER,
    id_categoria INTEGER,
    id_sucursal INTEGER,
    fecha_pedido DATE,
    anio INTEGER,
    mes INTEGER,
    mes_nombre VARCHAR(15),
    trimestre INTEGER,
    anio_mes CHAR(7),
    cliente_nombre VARCHAR(160),
    correo VARCHAR(160),
    ciudad VARCHAR(80),
    region VARCHAR(50),
    canal_registro VARCHAR(30),
    es_premium BOOLEAN,
    fecha_alta_cliente DATE,
    producto_nombre VARCHAR(200),
    categoria VARCHAR(80),
    descripcion_categoria VARCHAR(200),
    rango_precio VARCHAR(20),
    precio_lista NUMERIC(12,2),
    margen_bruto_producto NUMERIC(5,4),
    costo_estimado NUMERIC(12,2),
    utilidad_bruta NUMERIC(12,2),
    cantidad INTEGER,
    precio_unitario NUMERIC(12,2),
    descuento NUMERIC(12,2),
    porcentaje_descuento NUMERIC(5,2),
    subtotal NUMERIC(14,2),
    total NUMERIC(14,2),
    canal_venta VARCHAR(30),
    estado VARCHAR(20),
    es_completado BOOLEAN,
    total_pedido NUMERIC(14,2),
    unidades INTEGER,
    lineas INTEGER,
    categorias_distintas INTEGER,
    ticket_promedio_linea NUMERIC(12,2),
    metodo VARCHAR(30),
    monto NUMERIC(14,2),
    estado_pago VARCHAR(20),
    diferencia_pago NUMERIC(14,2),
    conciliado BOOLEAN,
    id_ciudad INTEGER
);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_pedido FOREIGN KEY (id_pedido) REFERENCES pedidos(id_pedido);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_producto FOREIGN KEY (id_producto) REFERENCES productos(id_producto);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_cliente FOREIGN KEY (id_cliente) REFERENCES clientes(id_cliente);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_categoria FOREIGN KEY (id_categoria) REFERENCES categorias(id_categoria);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_sucursal FOREIGN KEY (id_sucursal) REFERENCES dim_sucursales(id_sucursal);

ALTER TABLE fact_ventas ADD CONSTRAINT fk_fact_ventas_id_ciudad FOREIGN KEY (id_ciudad) REFERENCES dim_ciudades(id_ciudad);

CREATE VIEW v_detalle_calculado AS
           SELECT d.*,
                  ROUND(d.cantidad * d.precio_unitario,2) AS subtotal,
                  ROUND(d.cantidad * d.precio_unitario - d.descuento,2) AS total,
                  CASE WHEN d.cantidad * d.precio_unitario > 0 THEN
                        ROUND(100.0 * d.descuento / (d.cantidad * d.precio_unitario),2)
                       ELSE 0 END AS porcentaje_descuento
           FROM detalle_pedido d;

CREATE VIEW v_pedidos_calculados AS
            SELECT p.*,
                   EXTRACT(YEAR FROM p.fecha_pedido)::INTEGER AS anio, EXTRACT(MONTH FROM p.fecha_pedido)::INTEGER AS mes,
                   TO_CHAR(p.fecha_pedido, 'YYYY-MM') AS anio_mes,
                   CASE WHEN p.estado IN ('completado','enviado')
                        THEN TRUE ELSE FALSE END AS es_completado,
                   COALESCE(ROUND(SUM(d.total),2),0) AS total_pedido,
                   COALESCE(SUM(d.cantidad),0) AS unidades,
                   COUNT(d.id_detalle) AS lineas,
                   COUNT(DISTINCT pr.id_categoria) AS categorias_distintas,
                   ROUND(SUM(d.total) / NULLIF(COUNT(d.id_detalle),0),2)
                       AS ticket_promedio_linea
            FROM pedidos p
            LEFT JOIN v_detalle_calculado d ON d.id_pedido = p.id_pedido
            LEFT JOIN productos pr ON pr.id_producto = d.id_producto
            GROUP BY p.id_pedido, p.id_cliente, p.fecha_pedido,
                     p.canal_venta, p.id_sucursal, p.estado;

CREATE VIEW v_pagos_conciliados AS
            SELECT pg.*,
                   ROUND(pg.monto - COALESCE(pe.total_pedido,0),2) AS diferencia_pago,
                   CASE WHEN ABS(pg.monto - COALESCE(pe.total_pedido,0)) <= 5
                        THEN TRUE ELSE FALSE END AS conciliado
            FROM pagos pg
            LEFT JOIN v_pedidos_calculados pe ON pe.id_pedido = pg.id_pedido;

CREATE VIEW v_resumen_ventas AS
        SELECT f.anio_mes,
               MIN(f.mes_nombre) AS mes_nombre,
               COUNT(DISTINCT f.id_pedido) AS pedidos,
               COUNT(DISTINCT f.id_cliente) AS clientes,
               COUNT(DISTINCT f.id_producto) AS productos,
               SUM(f.cantidad) AS unidades,
               ROUND(SUM(f.total), 2) AS facturacion,
               ROUND(SUM(f.total) / NULLIF(COUNT(DISTINCT f.id_pedido),0), 2)
                   AS ticket_promedio
        FROM fact_ventas f
        WHERE f.es_completado = TRUE
        GROUP BY f.anio_mes;

CREATE INDEX ix_pedidos_id_cliente ON pedidos (id_cliente);

CREATE INDEX ix_pedidos_fecha_pedido ON pedidos (fecha_pedido);

CREATE INDEX ix_pedidos_estado ON pedidos (estado);

CREATE INDEX ix_pedidos_canal_venta ON pedidos (canal_venta);

CREATE INDEX ix_detalle_pedido_id_producto ON detalle_pedido (id_producto);

CREATE INDEX ix_detalle_pedido_id_pedido ON detalle_pedido (id_pedido);

CREATE INDEX ix_pagos_id_pedido ON pagos (id_pedido);

CREATE INDEX ix_dim_sucursales_id_ciudad ON dim_sucursales (id_ciudad);

CREATE INDEX ix_clientes_id_ciudad ON clientes (id_ciudad);

CREATE INDEX ix_clientes_es_premium ON clientes (es_premium);

CREATE INDEX ix_productos_id_categoria ON productos (id_categoria);

CREATE INDEX ix_fact_anio_mes ON fact_ventas (anio_mes);

CREATE INDEX ix_fact_categoria ON fact_ventas (categoria);

CREATE INDEX ix_fact_producto ON fact_ventas (id_producto);

CREATE INDEX ix_fact_cliente ON fact_ventas (id_cliente);

CREATE INDEX ix_fact_estado ON fact_ventas (estado);

CREATE INDEX ix_fact_canal ON fact_ventas (canal_venta);

