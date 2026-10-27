-- Demostración del esquema: la regla dura aplicada en la base, y la vista FALSO FLETE.
-- Todo corre dentro de transacciones que terminan en ROLLBACK: no queda nada cargado.
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/pruebas/demo_esquema.sql

\set ON_ERROR_STOP off

\echo '=== 1) LA REGLA DURA EN LA BASE: un valor sin mensaje de origen NO entra ==='
BEGIN;
INSERT INTO mensajes (id, grupo_id, capturado_en) VALUES ('M1', 'g@g.us', now());
INSERT INTO conductores (documento, nombre, documento_confirmado)
    VALUES ('70123456', 'Fulano Prueba', true);
-- Sin mensaje_id: la base tiene que rebotarlo.
INSERT INTO valores (conductor_id, dia, tipo, valor, grupo_id)
    VALUES ((SELECT id FROM conductores WHERE documento = '70123456'),
            '2026-10-05', 'pedidos', '40', 'g@g.us');
\echo '^^ ARRIBA: "null value in column mensaje_id ... violates not-null constraint" (correcto)'
ROLLBACK;

\echo ''
\echo '=== 2) LA BASE DEJA MARCAR EL DOCUMENTO PROVISORIO (el QA lo convierte en FALLA) ==='
BEGIN;
INSERT INTO conductores (documento, nombre, documento_confirmado, codigo_provisorio)
    VALUES ('Pro2025', 'Dora Cuatro', false, 'Pro2025');
SELECT documento, nombre, documento_confirmado,
       CASE WHEN documento_confirmado THEN 'liquidable' ELSE 'NO liquidable' END AS estado
FROM conductores WHERE codigo_provisorio = 'Pro2025';
ROLLBACK;

\echo ''
\echo '=== 3) VISTA FALSO FLETE: programado contra real por conductor ==='
BEGIN;
INSERT INTO mensajes (id, grupo_id, capturado_en) VALUES ('M2', 'g@g.us', now());
INSERT INTO conductores (documento, nombre, documento_confirmado)
    VALUES ('71234567', 'Mengano Prueba', true);
INSERT INTO programado (conductor_id, dia, pedidos_programados)
    VALUES ((SELECT id FROM conductores WHERE documento = '71234567'), '2026-10-05', 30);
INSERT INTO valores (conductor_id, dia, tipo, valor, mensaje_id, grupo_id, estado)
    VALUES ((SELECT id FROM conductores WHERE documento = '71234567'),
            '2026-10-05', 'pedidos', '27', 'M2', 'g@g.us', 'confirmado');
SELECT c.nombre, f.pedidos_programados, f.pedidos_reales, f.diferencia
FROM falso_flete f JOIN conductores c ON c.id = f.conductor_id;
ROLLBACK;

\echo ''
\echo '(todo se deshizo con ROLLBACK: rauda_dev quedó vacía)'
