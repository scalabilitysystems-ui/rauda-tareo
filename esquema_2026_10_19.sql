-- ============================================================================
-- Migración del 19/10/2026: separar el cliente de demostración de la operación real.
--
-- Por qué existe este archivo: la columna se agregó a mano en la base durante el desarrollo, y
-- el respaldo restaurado NO arrancaba (`db.clientes()` pedía `es_demo` y la columna no estaba).
-- Un esquema que solo vive en la base y no en el repo es un respaldo que no sirve.
--
-- Aplicar sobre una base existente:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_2026_10_19.sql
-- ============================================================================

BEGIN;

ALTER TABLE clientes ADD COLUMN IF NOT EXISTS es_demo BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN clientes.es_demo IS
'TRUE para el cliente de prueba: no aparece en el panel cuando hay una operación real cargada.';

-- El demo se reconoce por el nombre; si alguien lo renombra, se marca a mano.
UPDATE clientes SET es_demo = TRUE WHERE nombre ILIKE '%DEMO%';

COMMIT;
