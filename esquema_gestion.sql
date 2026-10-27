-- ============================================================================
-- Migración: del control de cierres a la app de gestión (Rauda).
--
-- Qué agrega, y por qué:
--   clientes ................. la operación pasa a tener más de un cliente
--                              (cadenas y boticas). Todo cuelga de acá.
--   grupos ................... el mapeo grupo de WhatsApp -> centro de tienda,
--                              zona y supervisor. Es lo que hoy se deduce a mano.
--   correcciones_admin ....... lo que corrige operaciones. NO se escribe encima del
--                              valor leído: se guarda aparte, con autor y motivo.
--                              Así la regla dura sigue en pie (el sistema nunca
--                              inventa un dato) y la corrección humana se ve.
--   liquidaciones ............ el resultado del día. El descuento por tardanza
--                              queda NULL mientras la regla no esté por escrito:
--                              una columna vacía es honesta, un cero inventado no.
--
-- Aplicar (idempotente):
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_gestion.sql
-- ============================================================================

BEGIN;

-- ---- Clientes -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clientes (
    id             BIGSERIAL PRIMARY KEY,
    nombre         TEXT NOT NULL UNIQUE,
    tarifa_pedido  NUMERIC(10,2) NOT NULL,
    -- el cliente de prueba no aparece cuando hay una operación real cargada
    es_demo        BOOLEAN NOT NULL DEFAULT FALSE,
    activo         BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Grupos de WhatsApp -> centro ----------------------------------------
CREATE TABLE IF NOT EXISTS grupos (
    grupo_id       TEXT PRIMARY KEY,
    cliente_id     BIGINT NOT NULL REFERENCES clientes(id),
    etiqueta       TEXT,
    codigo_tienda  TEXT,
    zona           TEXT,
    supervisor     TEXT,
    activo         BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---- Multi-cliente en lo que ya existía ----------------------------------
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS cliente_id   BIGINT REFERENCES clientes(id);
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS grupo_id     TEXT REFERENCES grupos(grupo_id);
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS telefono     TEXT;
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS activo       BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE mensajes    ADD COLUMN IF NOT EXISTS cliente_id   BIGINT REFERENCES clientes(id);
ALTER TABLE valores     ADD COLUMN IF NOT EXISTS cliente_id   BIGINT REFERENCES clientes(id);
ALTER TABLE programado  ADD COLUMN IF NOT EXISTS cliente_id   BIGINT REFERENCES clientes(id);
ALTER TABLE programado  ADD COLUMN IF NOT EXISTS hora_planificada TIME;

-- ---- Correcciones del administrativo -------------------------------------
-- Una por campo, día y conductor. El campo corrige la PRESENTACIÓN del dato:
-- el valor leído del mensaje no se toca nunca.
CREATE TABLE IF NOT EXISTS correcciones_admin (
    id           BIGSERIAL PRIMARY KEY,
    cliente_id   BIGINT NOT NULL REFERENCES clientes(id),
    dia          DATE NOT NULL,
    conductor_id BIGINT NOT NULL REFERENCES conductores(id),
    campo        TEXT NOT NULL,
    valor        TEXT,
    autor        TEXT NOT NULL,
    motivo       TEXT,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dia, conductor_id, campo)
);

-- ---- Liquidación ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS liquidaciones (
    id                   BIGSERIAL PRIMARY KEY,
    cliente_id           BIGINT NOT NULL REFERENCES clientes(id),
    dia                  DATE NOT NULL,
    conductor_id         BIGINT NOT NULL REFERENCES conductores(id),
    pedidos_liquidados   INTEGER NOT NULL,
    tarifa               NUMERIC(10,2) NOT NULL,
    bruto                NUMERIC(12,2) NOT NULL,
    -- NULL = la regla no está por escrito todavía. No se inventa un cero.
    descuento_tardanza   NUMERIC(12,2),
    total                NUMERIC(12,2) NOT NULL,
    estado               TEXT NOT NULL,          -- 'calculada' | 'bloqueada' | 'no_liquida'
    bloqueo              TEXT,
    cerrado_por          TEXT,
    calculado_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dia, conductor_id)
);

-- ---- Índices -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_conductores_cliente ON conductores (cliente_id);
CREATE INDEX IF NOT EXISTS idx_valores_cliente_dia ON valores (cliente_id, dia);
CREATE INDEX IF NOT EXISTS idx_correcciones_dia    ON correcciones_admin (dia, conductor_id);
CREATE INDEX IF NOT EXISTS idx_grupos_cliente      ON grupos (cliente_id);

COMMIT;
