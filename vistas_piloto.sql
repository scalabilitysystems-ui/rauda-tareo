-- ============================================================================
-- Medición del piloto: ¿el sistema se usó, y sirvió?
--
-- La pregunta que responde no es "¿anda?" (eso lo dicen las pruebas) sino "¿cambió la
-- operación?". Para el día 15 hay que poder decir, con números y sin discutir:
--
--   1. ¿Cuántos conductores abrieron y cerraron su día solos, sin que nadie les cargue nada?
--   2. ¿Cuántos días se cerraron DENTRO del sistema, y no en un Excel?
--   3. ¿El número que declara el conductor coincide con lo que hizo en la calle?
--   4. ¿Cuánto quedó sin liquidar por falta de un dato (el costo de la desprolijidad)?
--
-- Todo se apoya en `vista_tareo_dia`: la misma verdad que ve la operación. Si el piloto mejora el
-- número, se ve acá; si no, también.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/vistas_piloto.sql
-- ============================================================================

BEGIN;

-- Al cambiar columnas hay que recrear: una vista no se puede "reemplazar" con otro orden.
DROP VIEW IF EXISTS vista_piloto_resumen;
DROP VIEW IF EXISTS vista_piloto_dia;

-- ---- Un día, un cliente ----------------------------------------------------
CREATE OR REPLACE VIEW vista_piloto_dia AS
WITH dia AS (
    SELECT cliente_id, dia, conductor_id, estado, pedidos, entregas_tocadas
      FROM vista_tareo_dia
),
jorn AS (
    -- `jornadas` es la prueba de que el conductor abrió el día desde el panel (no de un Excel)
    SELECT conductor_id, dia, (cerrada_en IS NOT NULL) AS cerro
      FROM jornadas
)
SELECT d.cliente_id,
       d.dia,
       count(*)                                            AS filas,
       count(*) FILTER (WHERE d.estado = 'a_pagar')         AS filas_a_pagar,
       count(*) FILTER (WHERE d.estado = 'pendiente')       AS filas_pendientes,
       count(*) FILTER (WHERE d.estado = 'no_trabajo')      AS filas_sin_trabajo,
       count(*) FILTER (WHERE j.conductor_id IS NOT NULL)   AS abrieron_panel,
       count(*) FILTER (WHERE j.cerro)                      AS cerraron_panel,
       count(*) FILTER (WHERE j.conductor_id IS NULL)       AS filas_sin_panel,
       coalesce(sum(coalesce(d.pedidos, 0)), 0)             AS pedidos_declarados,
       coalesce(sum(coalesce(d.entregas_tocadas, 0)), 0)    AS entregas_tocadas,
       -- Lo declarado SOLO en las filas donde usó el panel: comparar contra un día cargado a
       -- mano compararía dos formas de trabajar y el número no significaría nada.
       coalesce(sum(coalesce(d.pedidos, 0))
                FILTER (WHERE j.conductor_id IS NOT NULL), 0) AS pedidos_declarados_panel,
       -- La brecha entre lo declarado y lo que hizo en la calle, en valor absoluto: tocar de
       -- más también es un dato raro.
       coalesce(sum(abs(coalesce(d.pedidos, 0) - coalesce(d.entregas_tocadas, 0)))
                FILTER (WHERE j.conductor_id IS NOT NULL), 0) AS diferencia_absoluta,
       coalesce(sum(d.pedidos) FILTER (WHERE d.estado = 'a_pagar'), 0) AS pedidos_a_pagar,
       (SELECT count(*) FROM liquidaciones l
         WHERE l.cliente_id = d.cliente_id AND l.dia = d.dia
           AND l.cerrado_por IS NOT NULL)                   AS cierres_admin
  FROM dia d
  LEFT JOIN jorn j ON j.conductor_id = d.conductor_id AND j.dia = d.dia
 GROUP BY d.cliente_id, d.dia;

COMMENT ON VIEW vista_piloto_dia IS
'Un día del piloto: adopción (abrieron/cerraron desde el panel) y confiabilidad (declarado vs tocado).';

-- ---- El período completo ---------------------------------------------------
CREATE OR REPLACE VIEW vista_piloto_resumen AS
SELECT d.cliente_id,
       min(d.dia)                                                     AS desde,
       max(d.dia)                                                     AS hasta,
       count(*)                                                       AS dias,
       sum(d.filas)                                                   AS filas,
       sum(d.abrieron_panel)                                          AS abrieron_panel,
       sum(d.cerraron_panel)                                          AS cerraron_panel,
       sum(d.filas_sin_panel)                                         AS filas_sin_panel,
       sum(d.filas_a_pagar)                                           AS filas_a_pagar,
       sum(d.filas_pendientes)                                        AS filas_pendientes,
       sum(d.filas_sin_trabajo)                                       AS filas_sin_trabajo,
       sum(d.pedidos_declarados)                                      AS pedidos_declarados,
       sum(d.pedidos_declarados_panel)                                AS pedidos_declarados_panel,
       sum(d.entregas_tocadas)                                        AS entregas_tocadas,
       sum(d.diferencia_absoluta)                                     AS diferencia_absoluta,
       -- porcentajes redondeados a un decimal, listos para mostrar
       round(100.0 * sum(d.cerraron_panel) / nullif(sum(d.filas), 0), 1)
                                                                      AS pct_cierre_panel,
       round(100.0 * sum(d.diferencia_absoluta)
             / nullif(sum(d.pedidos_declarados_panel), 0), 1)          AS pct_diferencia,
       round(100.0 * sum(d.filas_pendientes) / nullif(sum(d.filas), 0), 1)
                                                                      AS pct_pendiente,
       sum(d.cierres_admin)                                           AS cierres_admin
  FROM vista_piloto_dia d
 GROUP BY d.cliente_id;

COMMENT ON VIEW vista_piloto_resumen IS
'El período en una fila: qué porcentaje cerró desde el panel y cuánto se desvía lo declarado de lo tocado.';

COMMIT;
