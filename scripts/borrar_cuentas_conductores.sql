-- ============================================================================
-- Borra las cuentas de conductor.
--
-- Por qué: la cuenta del conductor ya no la crea nadie desde afuera. Nace sola la primera vez
-- que la persona entra con su DNI y elige su contraseña (auth.reclamar). Cargar al conductor en
-- Configuración —nombre, DNI y tienda— es todo lo que hace falta.
--
-- Qué NO borra: la persona (`conductores`), su DNI, su tienda ni su historia.
-- Qué borra: las cuentas con rol 'conductor' y sus sesiones.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev -f /home/ubuntu/rauda/scripts/borrar_cuentas_conductores.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

DELETE FROM sesiones
 WHERE cuenta_id IN (SELECT id FROM cuentas WHERE rol = 'conductor');

DELETE FROM cuentas WHERE rol = 'conductor';

COMMIT;

\echo '--- quién puede entrar ahora ---'
SELECT usuario, rol, nombre, activa FROM cuentas ORDER BY rol, usuario;

\echo '--- conductores cargados (sin cuenta hasta que entren) ---'
SELECT documento, nombre, activo FROM conductores ORDER BY nombre;
