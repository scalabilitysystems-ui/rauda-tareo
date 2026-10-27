-- ============================================================================
-- Limpieza para arrancar la operación: dejar SOLO lo que hace falta para trabajar.
--
-- Qué borra:
--   · Los días de prueba del panel del conductor (jornadas, marcas, cargas y los valores que
--     escribió el panel). Todo eso es recorrido de pruebas: GPS inventados, entregas marcadas
--     para probar y un día abierto a mano.
--   · Las cuentas de prueba (demo, auditoría, sin cliente) y los intentos de login.
--
-- Qué NO borra, a propósito:
--   · Los conductores, sus DNI y sus cuentas: son la gente real que va a operar hoy.
--   · Los clientes, tiendas y `programado`: la operación que se va a trabajar.
--   · `auditoria`: es la traza de lo que pasó. Se conserva (y la app ya no puede borrarla).
--   · Las tablas del camino WhatsApp (`mensajes`, etc.): quedan como archivo, sin datos de prueba.
--
-- Se puede correr dos veces sin daño: si no hay nada para borrar, no hace nada.
-- Conteos ANTES y DESPUÉS impresos en el journal: la limpieza no se cree, se verifica.
-- ============================================================================

\set ON_ERROR_STOP on

\echo '--- ANTES ---'
SELECT 'jornadas' AS tabla, count(*) FROM jornadas
UNION ALL SELECT 'marcas', count(*) FROM marcas
UNION ALL SELECT 'cargas', count(*) FROM cargas
UNION ALL SELECT 'valores', count(*) FROM valores
UNION ALL SELECT 'cuentas', count(*) FROM cuentas
UNION ALL SELECT 'sesiones', count(*) FROM sesiones;

BEGIN;

-- 1. Lo que escribió el panel del conductor: días de prueba, con sus toques y sus cargas.
--    `marcas` y `cargas` caen por CASCADE con la jornada.
DELETE FROM valores WHERE origen = 'panel';
DELETE FROM jornadas;

-- 2. Las jornadas pudieron dejar liquidaciones de prueba con firma.
--    (Se borran solo las que quedaron sin pedidos liquidados: las reales no existen todavía.)
DELETE FROM liquidaciones WHERE pedidos_liquidados = 0 AND cerrado_por IS NOT NULL;

-- 3. Cuentas de prueba: demo, las de la auditoría y cualquier resto sin persona detrás.
--    Se borran por patrón de usuario y por nombre, nunca por id.
--    NOTA: `auditoria.cuenta_id` es ON DELETE SET NULL — la traza queda, con el nombre del autor.
DELETE FROM sesiones
 WHERE cuenta_id IN (
     SELECT id FROM cuentas
      WHERE usuario = 'demo'
         OR usuario LIKE 'aud.%'
         OR usuario LIKE 'e2e.%'
         OR usuario LIKE 't.%'
         OR usuario LIKE 'mar.%'
         OR usuario LIKE 'prueba%'
         OR nombre IN ('Demostración', 'Admin Auditoría', 'Sin Cliente', 'Verificación'));
DELETE FROM cuentas
 WHERE usuario = 'demo'
    OR usuario LIKE 'aud.%'
    OR usuario LIKE 'e2e.%'
    OR usuario LIKE 't.%'
    OR usuario LIKE 'mar.%'
    OR usuario LIKE 'prueba%'
    OR nombre IN ('Demostración', 'Admin Auditoría', 'Sin Cliente', 'Verificación');

-- 3 bis. Sesiones de los días de prueba. Los tokens de los ingresos de prueba quedaron vivos
--        (una prueba, un token); acá se borran los del conductor, que son todos de prueba, y
--        los de la administración que se revocaron (admin incluido). Los vigentes de la administración y
--        operaciones se respetan: son sus ingresos reales.
DELETE FROM sesiones
 WHERE cuenta_id IN (SELECT id FROM cuentas WHERE rol = 'conductor')
    OR revocada_en IS NOT NULL;

-- 4. Intentos de login y de PIN: son de las pruebas, no de personas.
DELETE FROM intentos_login;
DELETE FROM intentos_pin;

-- 4 bis. Los grupos de tienda eran andamio del demo. La operación real se carga desde el panel
--        (Configuración → Tiendas): si quedan estos, el tareo muestra una tienda que no existe.
UPDATE conductores SET grupo_id = NULL WHERE grupo_id IN ('g-t-214@g.us', 'g-t-308@g.us');
DELETE FROM grupos WHERE grupo_id IN ('g-t-214@g.us', 'g-t-308@g.us');

-- 4 ter. `programado` de la operación real: sin eso el tareo inventa "no trabajó" en días que
--        no son de la operación. La planificación del día se carga desde el panel.
--        (El cliente de prueba conserva el suyo: es su andamio, y las pruebas lo necesitan.)
DELETE FROM programado
 WHERE cliente_id IN (SELECT id FROM clientes WHERE NOT es_demo);

-- 5. Rastro de prueba en las tablas viejas del camino WhatsApp (no hay datos reales: 0 filas
--    esperadas, pero si alguna prueba dejó algo, se va).
DELETE FROM chequeos;
DELETE FROM dudas;
DELETE FROM tareo;

COMMIT;

\echo '--- DESPUÉS ---'
SELECT 'jornadas' AS tabla, count(*) FROM jornadas
UNION ALL SELECT 'marcas', count(*) FROM marcas
UNION ALL SELECT 'cargas', count(*) FROM cargas
UNION ALL SELECT 'valores', count(*) FROM valores
UNION ALL SELECT 'cuentas', count(*) FROM cuentas
UNION ALL SELECT 'sesiones', count(*) FROM sesiones
UNION ALL SELECT 'conductores', count(*) FROM conductores
UNION ALL SELECT 'auditoria', count(*) FROM auditoria;

\echo '--- quién puede entrar ---'
SELECT usuario, rol, nombre, activa,
       (clave_hash IS NULL) AS falta_elegir_clave
  FROM cuentas ORDER BY rol, nombre;
