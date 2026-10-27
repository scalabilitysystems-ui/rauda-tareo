-- ============================================================================
-- Cierre del día desde el panel del conductor.
--
-- La idea: el cierre NO crea un mundo nuevo. Escribe los mismos `valores` que produce la
-- lectura de WhatsApp, marcados con `origen = 'panel'`. Así el tareo, la liquidación y el
-- panel de operaciones siguen funcionando sin una segunda implementación de nada.
--
-- La regla dura se mantiene y se puede probar: un valor de WhatsApp exige su mensaje
-- (`mensaje_id NOT NULL`); un valor del panel exige que el conductor haya abierto el día.
-- Ninguno de los dos puede existir sin un origen declarado.
--
-- Aplicar después de esquema_cierre_tipos.sql:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_cierre.sql
-- ============================================================================

BEGIN;

-- ---- Origen del valor -----------------------------------------------------
ALTER TABLE valores ADD COLUMN IF NOT EXISTS origen TEXT NOT NULL DEFAULT 'whatsapp';
ALTER TABLE valores DROP CONSTRAINT IF EXISTS valores_origen_check;
ALTER TABLE valores ADD CONSTRAINT valores_origen_check
    CHECK (origen IN ('whatsapp', 'panel', 'correccion_admin'));

-- ---- El mensaje deja de ser obligatorio, pero solo para el panel ----------
ALTER TABLE valores ALTER COLUMN mensaje_id DROP NOT NULL;

ALTER TABLE valores DROP CONSTRAINT IF EXISTS valores_origen_mensaje_check;
ALTER TABLE valores ADD CONSTRAINT valores_origen_mensaje_check
    CHECK (
        (origen = 'whatsapp' AND mensaje_id IS NOT NULL)
        OR (origen = 'panel' AND mensaje_id IS NULL)
        OR (origen = 'correccion_admin')
    );

-- ---- Trazabilidad del cierre ---------------------------------------------
-- Quién declaró y cuándo, en la propia fila del valor: no hace falta abrir otra tabla para
-- saber que ese 45 lo declaró el conductor desde el panel a las 18:40.
ALTER TABLE valores ADD COLUMN IF NOT EXISTS declarado_en TIMESTAMPTZ;

-- ---- Cierre de la jornada -------------------------------------------------
ALTER TABLE jornadas ADD COLUMN IF NOT EXISTS cerrada_telefono TIMESTAMPTZ;
ALTER TABLE jornadas ADD COLUMN IF NOT EXISTS vueltas_declaradas INTEGER;
ALTER TABLE jornadas ADD COLUMN IF NOT EXISTS cierre_servidor TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_valores_origen ON valores (origen);

COMMIT;
