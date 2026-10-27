-- ============================================================================
-- Tarifa con vigencia: cambiar el precio NO puede reescribir lo que ya se pagó.
--
-- El problema: la tarifa vivía en `clientes.tarifa_pedido`, una sola columna. El día que se
-- cambia de 3,00 a 3,50, la liquidación de la semana pasada se recalcula con la tarifa
-- nueva y deja de coincidir con lo que se pagó. Nadie se entera hasta que alguien compara.
--
-- La solución: la tarifa rige desde una fecha. Cada jornada toma la que regía ESE día.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_tarifas.sql
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS tarifas_cliente (
    id          BIGSERIAL PRIMARY KEY,
    cliente_id  BIGINT       NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    tarifa      NUMERIC(10,2) NOT NULL CHECK (tarifa >= 0),
    -- desde qué día rige (hora de Lima). Si se carga una para una fecha futura, empieza a regir
    -- esa fecha y lo anterior no se toca.
    desde       DATE         NOT NULL,
    creada_en   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    creada_por  TEXT,
    UNIQUE (cliente_id, desde)
);

COMMENT ON TABLE tarifas_cliente IS
'Tarifa por pedido con vigencia. La que aplica a un día es la última con desde <= día.';

-- La tarifa que ya estaba cargada se convierte en la primera fila de la historia.
INSERT INTO tarifas_cliente (cliente_id, tarifa, desde, creada_por)
SELECT c.id, c.tarifa_pedido, DATE '2020-01-01', 'migración'
  FROM clientes c
 WHERE NOT EXISTS (SELECT 1 FROM tarifas_cliente t WHERE t.cliente_id = c.id);

-- ---- La tarifa que rige un día ---------------------------------------------
CREATE OR REPLACE FUNCTION tarifa_del_dia(p_cliente BIGINT, p_dia DATE)
RETURNS NUMERIC
LANGUAGE sql STABLE AS $$
    SELECT t.tarifa
      FROM tarifas_cliente t
     WHERE t.cliente_id = p_cliente
       AND t.desde <= p_dia
     ORDER BY t.desde DESC
     LIMIT 1
$$;

COMMENT ON FUNCTION tarifa_del_dia IS
'La tarifa vigente para un día. Si no hay ninguna con `desde <= día`, devuelve NULL y la fila no liquida.';

COMMIT;
