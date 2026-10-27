-- ============================================================================
-- Sacar de la base los datos de demostración del cliente de ejemplo.
--
-- Quedaron del armado del producto: 9 conductores inventados, un día de tareo con mensajes de
-- WhatsApp y liquidaciones de mentira. Estaban escondidos del panel, pero seguían en la base — y
-- en la base de una operación real no tiene que haber nada inventado.
--
-- La fixture de las pruebas se arma sola (pruebas/seed_demo_gestion.py) en un cliente
-- "Pruebas de gestión (DEMO)" y se borra al terminar. Este cliente viejo no lo usa nadie.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev -f /home/ubuntu/rauda/scripts/borrar_demo.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- Se identifica por nombre, nunca por id.
CREATE TEMP TABLE demo_ids AS
SELECT id FROM clientes WHERE nombre ILIKE '%CadenaDemo%DEMO%';

\echo 'clientes demo encontrados:'
SELECT count(*) FROM demo_ids;

-- Orden de dependencias.
DELETE FROM dudas    WHERE conductor_id IN (SELECT id FROM conductores WHERE cliente_id IN (SELECT id FROM demo_ids));
DELETE FROM tareo    WHERE conductor_id IN (SELECT id FROM conductores WHERE cliente_id IN (SELECT id FROM demo_ids));
-- `chequeos` no cuelga del mensaje: se indexa por día (es del camino WhatsApp, ya archivado)
DELETE FROM chequeos WHERE dia IN (SELECT dia FROM jornadas WHERE cliente_id IN (SELECT id FROM demo_ids));
DELETE FROM valores  WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM liquidaciones       WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM correcciones_admin  WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM programado          WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM sesiones WHERE cuenta_id IN (SELECT id FROM cuentas WHERE cliente_id IN (SELECT id FROM demo_ids));
DELETE FROM cuentas     WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM conductores WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM grupos      WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM tarifas_cliente WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM mensajes    WHERE cliente_id IN (SELECT id FROM demo_ids);
DELETE FROM clientes    WHERE id IN (SELECT id FROM demo_ids);

COMMIT;

\echo '--- estado ---'
SELECT 'clientes' AS tabla, count(*) FROM clientes
UNION ALL SELECT 'conductores', count(*) FROM conductores
UNION ALL SELECT 'cuentas', count(*) FROM cuentas
UNION ALL SELECT 'jornadas', count(*) FROM jornadas
UNION ALL SELECT 'valores', count(*) FROM valores
UNION ALL SELECT 'liquidaciones', count(*) FROM liquidaciones
UNION ALL SELECT 'mensajes', count(*) FROM mensajes;
