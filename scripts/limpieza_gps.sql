-- ============================================================================
-- Limpieza de puntos GPS con más de 30 días.
--
-- Qué borra: las coordenadas de las jornadas viejas y las marcas de esos días.
-- Qué NO toca: `valores`, `tareo`, `liquidaciones`, `programado`. Lo que se paga queda
-- siempre; lo que se borra es dónde estuvo una persona, que a los 30 días ya no sirve y
-- es un dato personal.
--
-- Corre solo un domingo a la 3:30 (rauda-limpieza.timer). A mano:
--   sudo systemctl start rauda-limpieza.service
-- ============================================================================

-- La fecha se calcula en hora de Lima: `current_date` en el servidor (UTC) cambia de día a las
-- 19:00 de Lima y correría la retención un día para atrás sin que nadie lo note.
\set hoy '(now() AT TIME ZONE ''America/Lima'')::date'

\echo 'limpieza de puntos GPS con mas de 30 dias'

-- 1. Las marcas (entregas, rechazos, vueltas caídas) de jornadas viejas.
DELETE FROM marcas
WHERE jornada_id IN (SELECT id FROM jornadas WHERE dia < (:hoy)::date - 30);

-- 2. Las coordenadas de la jornada: la hora de ingreso se queda, el punto se va.
UPDATE jornadas
SET lat = NULL, lon = NULL, precision_m = NULL
WHERE dia < (:hoy)::date - 30 AND lat IS NOT NULL;

-- 3. Las cargas viejas (pedidos que llevaba): el tareo ya tiene lo declarado en `valores`.
DELETE FROM cargas
WHERE jornada_id IN (SELECT id FROM jornadas WHERE dia < (:hoy)::date - 30);

\echo 'listo'
