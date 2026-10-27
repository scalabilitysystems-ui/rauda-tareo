-- ============================================================================
-- Tolerancia de tardanza, configurable por cliente.
--
-- Regla de negocio configurable por cliente: **a partir de los 10 minutos** de diferencia
-- sobre el bloque planificado se considera tardanza. Antes el número 15 estaba escrito adentro de
-- las vistas, así que cambiarlo era una migración y no una decisión del negocio.
--
-- El descuento sigue sin calcularse: la regla dice cuándo empieza a contar la tardanza, no cuánto
-- se descuenta. Un cero ahí sería un dato inventado sobre el sueldo de una persona.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_tolerancia.sql
-- ============================================================================

BEGIN;

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS minutos_tolerancia INTEGER NOT NULL DEFAULT 10
    CHECK (minutos_tolerancia >= 0 AND minutos_tolerancia <= 120);

COMMENT ON COLUMN clientes.minutos_tolerancia IS
'Minutos de diferencia sobre el bloque planificado a partir de los cuales se marca tardanza.';

UPDATE clientes SET minutos_tolerancia = 10 WHERE minutos_tolerancia IS NULL;

COMMIT;
