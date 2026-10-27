-- ============================================================================
-- Tipos nuevos del tareo: SAC, rechazados y vueltas caídas.
--
-- Va en un archivo aparte y sin transacción a propósito: Postgres no deja usar un valor
-- de enum nuevo dentro de la misma transacción que lo crea. Si esto se mezcla con el
-- resto, falla.
--
--   sudo -u postgres psql -d rauda_dev < <este archivo>
-- ============================================================================

ALTER TYPE tipo_valor ADD VALUE IF NOT EXISTS 'sac';
ALTER TYPE tipo_valor ADD VALUE IF NOT EXISTS 'rechazados';
ALTER TYPE tipo_valor ADD VALUE IF NOT EXISTS 'vueltas_caidas';
