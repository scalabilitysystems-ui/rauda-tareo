-- ============================================================================
-- Vista del mapa: dónde está cada conductor y qué tocó durante el día.
--
-- El mapa NO inventa nada: dibuja lo que el conductor mandó (el punto del "ya llegué" y el
-- de cada toque). No hay tracking continuo — la PWA no puede mandar ubicación con el celular
-- bloqueado. El recorrido se arma uniendo los puntos, y se ve la hora de cada uno.
--
-- Privacidad: los puntos se borran a los 30 días (cron `rauda-limpieza.timer`). El tareo y la
-- liquidación no se tocan nunca: son plata.
--
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/vistas_mapa.sql
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS vista_mapa_dia;
DROP VIEW IF EXISTS vista_mapa_puntos;

-- ---- Puntos ---------------------------------------------------------------
-- Un renglón por evento con ubicación: llegada, entrega, rechazo o vuelta caída.
CREATE VIEW vista_mapa_puntos AS
    SELECT j.cliente_id,
           j.conductor_id,
           j.dia,
           'llegada'::text      AS tipo,
           j.abierta_en         AS hora,
           j.lat, j.lon, j.precision_m,
           j.estado_ubicacion,
           NULL::bigint         AS marca_id
    FROM jornadas j
    WHERE j.lat IS NOT NULL
UNION ALL
    SELECT j.cliente_id, j.conductor_id, j.dia, m.tipo, m.hora_servidor,
           m.lat, m.lon, m.precision_m, NULL, m.id
    FROM marcas m
    JOIN jornadas j ON j.id = m.jornada_id
    WHERE m.lat IS NOT NULL;

-- ---- Estado por conductor y día -------------------------------------------
-- El último punto, el recorrido completo y lo que tocó. Es lo que el mapa necesita por fila.
CREATE VIEW vista_mapa_dia AS
WITH puntos AS (
    SELECT p.*, row_number() OVER (PARTITION BY p.conductor_id, p.dia
                                   ORDER BY p.hora DESC NULLS LAST) AS ultimo
    FROM vista_mapa_puntos p
),
ultimo AS (
    SELECT * FROM puntos WHERE ultimo = 1
),
recorrido AS (
    SELECT conductor_id, dia,
           array_agg(lat ORDER BY hora) AS lats,
           array_agg(lon ORDER BY hora) AS lons
    FROM vista_mapa_puntos
    GROUP BY conductor_id, dia
)
SELECT j.cliente_id,
       j.conductor_id,
       j.dia,
       c.nombre,
       c.documento,
       g.codigo_tienda,
       g.zona,
       g.supervisor,
       j.abierta_en,
       j.cerrada_en,
       j.estado_ubicacion,
       j.distancia_tienda_m,
       u.lat                        AS ultima_lat,
       u.lon                        AS ultima_lon,
       u.hora                       AS ultima_hora,
       -- la tienda, para dibujar la geocerca
       g.lat                        AS tienda_lat,
       g.lon                        AS tienda_lon,
       g.radio_m,
       coalesce(r.lats, '{}')       AS lats,
       coalesce(r.lons, '{}')       AS lons,
       (SELECT count(*) FROM vista_mapa_puntos p
         WHERE p.conductor_id = j.conductor_id AND p.dia = j.dia)       AS n_puntos,
       (SELECT count(*) FROM marcas m
         JOIN vista_mapa_puntos p2 ON p2.marca_id = m.id AND p2.tipo = 'entregado'
         WHERE m.jornada_id = j.id)                                     AS entregas_con_punto,
       (SELECT count(*) FROM marcas m WHERE m.jornada_id = j.id
                                        AND m.tipo = 'entregado')       AS entregas_tocadas
FROM jornadas j
JOIN conductores c ON c.id = j.conductor_id
LEFT JOIN grupos g ON g.grupo_id = c.grupo_id
LEFT JOIN ultimo u ON u.conductor_id = j.conductor_id AND u.dia = j.dia
LEFT JOIN recorrido r ON r.conductor_id = j.conductor_id AND r.dia = j.dia;

COMMIT;
