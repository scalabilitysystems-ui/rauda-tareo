-- ============================================================================
-- Vistas del día: el tareo y la liquidación se calculan en la BASE.
--
-- Por qué en SQL y no en el panel:
--   - la regla vive en un solo lugar, y la base la hace cumplir;
--   - cualquier consulta (un informe, una auditoría, un reclamo del conductor)
--     da el mismo número que la pantalla;
--   - con más de un cliente, el filtro por cliente es una columna, no un olvido
--     en el código de la aplicación.
--
-- Aplicar después de esquema_gestion.sql:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/vistas_gestion.sql
-- ============================================================================

BEGIN;

-- Se recrean desde cero: CREATE OR REPLACE no permite cambiar el orden ni el tipo de
-- las columnas de una vista existente.
-- Las vistas del piloto se apoyan en `vista_tareo_dia`: hay que soltarlas primero o el DROP
-- falla y la migración queda a medias (pasó). Se recrean con `vistas_piloto.sql`.
DROP VIEW IF EXISTS vista_piloto_resumen;
DROP VIEW IF EXISTS vista_piloto_dia;

DROP VIEW IF EXISTS vista_controles_dia;
DROP VIEW IF EXISTS vista_resumen_dia;
DROP VIEW IF EXISTS vista_liquidacion_dia;
DROP VIEW IF EXISTS vista_tareo_dia;
DROP VIEW IF EXISTS vista_mensajes_dia;
DROP VIEW IF EXISTS vista_valores_dia;

-- ---- Número a partir de texto ---------------------------------------------
-- Los valores llegan como texto ("45 pedidos", "40"). La regla vieja borraba TODO lo que no
-- fuera dígito, y eso multiplica por diez sin avisar: "45.5" → 455, "-5" → 5. Ahora se acepta
-- solo lo que es un número entero o decimal limpio, y lo demás devuelve NULL (la fila queda
-- pendiente de dato, que es lo honesto: no se inventa un número).
CREATE OR REPLACE FUNCTION a_numero(t TEXT) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN btrim(coalesce(t, '')) ~ '^-?[0-9]+([.][0-9]+)?$'
        THEN btrim(t)::numeric
        ELSE NULL
    END
$$;

-- ---- Valores leídos, uno por columna --------------------------------------
-- Un valor por conductor, día y tipo (UNIQUE en `valores`), así que el pivote es seguro.
CREATE OR REPLACE VIEW vista_valores_dia AS
SELECT v.cliente_id,
       v.conductor_id,
       v.dia,
       count(*)                                                              AS n_valores,
       bool_or(v.estado = 'dudoso')                                          AS algo_dudoso,
       max(v.valor) FILTER (WHERE v.tipo = 'hora_ingreso')                    AS hora_ingreso,
       max(v.valor) FILTER (WHERE v.tipo = 'hora_salida')                     AS hora_salida,
       max(v.valor) FILTER (WHERE v.tipo = 'vuelta')                          AS vueltas,
       max(v.valor) FILTER (WHERE v.tipo = 'pedidos')                         AS pedidos,
       max(v.valor) FILTER (WHERE v.tipo = 'sac')                             AS sac,
       max(v.valor) FILTER (WHERE v.tipo = 'rechazados')                      AS rechazados,
       max(v.valor) FILTER (WHERE v.tipo = 'vueltas_caidas')                  AS vueltas_caidas,
       max(v.origen) FILTER (WHERE v.tipo = 'pedidos')                        AS origen_pedidos,
       max(v.origen) FILTER (WHERE v.tipo = 'vuelta')                         AS origen_vueltas,
       max(v.origen) FILTER (WHERE v.tipo = 'hora_ingreso')                   AS origen_ingreso,
       max(v.mensaje_id) FILTER (WHERE v.tipo = 'hora_ingreso')                AS msg_ingreso,
       max(v.mensaje_id) FILTER (WHERE v.tipo = 'hora_salida')                 AS msg_salida,
       max(v.mensaje_id) FILTER (WHERE v.tipo = 'vuelta')                      AS msg_vueltas,
       max(v.mensaje_id) FILTER (WHERE v.tipo = 'pedidos')                     AS msg_pedidos
FROM valores v
WHERE v.conductor_id IS NOT NULL
GROUP BY v.cliente_id, v.conductor_id, v.dia;

-- ---- Mensajes por conductor y día ----------------------------------------
-- El vínculo es el teléfono: en un grupo escribe gente de toda la tienda, así que el
-- grupo no alcanza para saber de quién es el mensaje.
CREATE OR REPLACE VIEW vista_mensajes_dia AS
SELECT c.cliente_id,
       c.id                                                             AS conductor_id,
       (m.hora AT TIME ZONE 'America/Lima')::date                        AS dia,
       count(*)                                                          AS n_mensajes,
       array_agg(m.id ORDER BY m.hora)                                   AS mensaje_ids
FROM mensajes m
JOIN conductores c ON c.telefono IS NOT NULL
                  AND m.de = c.telefono || '@s.whatsapp.net'
WHERE m.hora IS NOT NULL
GROUP BY c.cliente_id, c.id, (m.hora AT TIME ZONE 'America/Lima')::date;

-- ---- El tareo del día -----------------------------------------------------
-- Una fila por conductor y día. Los valores leídos se muestran tal cual; una
-- corrección administrativa gana en la columna resuelta, pero queda dicho de dónde sale.
CREATE OR REPLACE VIEW vista_tareo_dia AS
WITH dias AS (
    SELECT cliente_id, dia, conductor_id FROM programado WHERE conductor_id IS NOT NULL
    UNION
    SELECT cliente_id, dia, conductor_id FROM valores WHERE conductor_id IS NOT NULL
),
prog AS (
    SELECT conductor_id, dia, pedidos_programados, hora_planificada
    FROM programado
),
corr AS (
    SELECT conductor_id, dia,
           max(valor) FILTER (WHERE campo = 'hora_ingreso')  AS hora_ingreso,
           max(valor) FILTER (WHERE campo = 'hora_salida')   AS hora_salida,
           max(valor) FILTER (WHERE campo = 'vueltas')       AS vueltas,
           max(valor) FILTER (WHERE campo = 'pedidos')       AS pedidos,
           max(valor) FILTER (WHERE campo = 'observacion')   AS observacion,
           max(autor)                                        AS autor_correccion
    FROM correcciones_admin
    GROUP BY conductor_id, dia
),
tocadas AS (
    -- lo que el conductor tocó en el panel: no paga, sirve de control contra lo declarado
    SELECT j.conductor_id, j.dia,
           count(*) FILTER (WHERE m.tipo = 'entregado')    AS entregas,
           count(*) FILTER (WHERE m.tipo = 'rechazado')    AS rechazos,
           count(*) FILTER (WHERE m.tipo = 'vuelta_caida') AS caidas,
           bool_or(j.estado_ubicacion = 'lejos')           AS marco_lejos
    FROM jornadas j
    LEFT JOIN marcas m ON m.jornada_id = j.id
    GROUP BY j.conductor_id, j.dia
),
resuelto AS (
    SELECT d.cliente_id, d.conductor_id, d.dia,
           c.nombre, c.documento, c.documento_confirmado, c.codigo_provisorio, c.telefono,
           g.codigo_tienda, g.zona, g.supervisor,
           p.pedidos_programados, p.hora_planificada,
           l.n_valores, l.algo_dudoso,
           l.hora_ingreso AS hora_ingreso_leida,
           l.hora_salida  AS hora_salida_leida,
           l.vueltas      AS vueltas_leidas,
           l.pedidos      AS pedidos_leidos,
           l.sac, l.rechazados, l.vueltas_caidas,
           l.origen_pedidos, l.origen_vueltas, l.origen_ingreso,
           l.msg_ingreso, l.msg_salida, l.msg_vueltas, l.msg_pedidos,
           coalesce(t.entregas, 0) AS entregas_tocadas,
           coalesce(t.rechazos, 0) AS rechazos_tocados,
           coalesce(t.caidas, 0)   AS caidas_tocadas,
           coalesce(t.marco_lejos, FALSE) AS marco_lejos,
           -- pedidos que se liquidan: los regulares más el SAC (el SAC suma, por regla)
           CASE WHEN coalesce(k.pedidos, l.pedidos, l.sac) IS NULL THEN NULL
                ELSE (coalesce(a_numero(k.pedidos), a_numero(l.pedidos), 0)
                      + coalesce(a_numero(l.sac), 0))::int END AS pedidos_totales,
           coalesce(k.pedidos, l.pedidos) AS pedidos_texto,
           k.hora_ingreso, k.hora_salida, k.vueltas, k.pedidos,
           k.observacion, k.autor_correccion,
           coalesce(m.n_mensajes, 0) AS n_mensajes,
           m.mensaje_ids,
           -- minutos de tardanza sobre el bloque (negativo = entró antes del bloque)
           CASE WHEN p.hora_planificada IS NOT NULL
                     AND coalesce(k.hora_ingreso, l.hora_ingreso) ~ '^[0-9]{1,2}:[0-9]{2}'
                THEN (date_part('hour', coalesce(k.hora_ingreso, l.hora_ingreso)::time) * 60
                      + date_part('minute', coalesce(k.hora_ingreso, l.hora_ingreso)::time))
                     - (date_part('hour', p.hora_planificada) * 60
                        + date_part('minute', p.hora_planificada))
           END::int AS tardanza_min,
           cli.minutos_tolerancia
    FROM dias d
    JOIN conductores c ON c.id = d.conductor_id
    JOIN clientes cli ON cli.id = c.cliente_id
    LEFT JOIN grupos g ON g.grupo_id = c.grupo_id
    LEFT JOIN prog p ON p.conductor_id = d.conductor_id AND p.dia = d.dia
    LEFT JOIN vista_valores_dia l
           ON l.conductor_id = d.conductor_id AND l.dia = d.dia
    LEFT JOIN corr k ON k.conductor_id = d.conductor_id AND k.dia = d.dia
    LEFT JOIN vista_mensajes_dia m
           ON m.conductor_id = d.conductor_id AND m.dia = d.dia
    LEFT JOIN tocadas t ON t.conductor_id = d.conductor_id AND t.dia = d.dia
)
SELECT
    r.cliente_id,
    r.conductor_id,
    r.dia,
    r.nombre,
    r.documento,
    r.documento_confirmado,
    r.codigo_provisorio,
    r.telefono,
    r.codigo_tienda,
    r.zona,
    r.supervisor,
    r.pedidos_programados,
    r.hora_planificada,
    r.minutos_tolerancia,

    -- columnas resueltas: la corrección gana, y la fuente lo dice
    coalesce(r.hora_ingreso, r.hora_ingreso_leida) AS hora_ingreso,
    coalesce(r.hora_salida,  r.hora_salida_leida)  AS hora_salida,
    a_numero(coalesce(r.vueltas, r.vueltas_leidas))::int AS vueltas,
    -- `pedidos` es lo que se liquida: regulares + SAC (el SAC suma, por regla del cliente)
    r.pedidos_totales AS pedidos,
    a_numero(r.pedidos_texto)::int  AS pedidos_regulares,
    a_numero(r.sac)::int            AS sac,
    a_numero(r.rechazados)::int     AS rechazados,
    a_numero(r.vueltas_caidas)::int AS vueltas_caidas,
    r.entregas_tocadas, r.rechazos_tocados, r.caidas_tocadas, r.marco_lejos,
    r.observacion,
    CASE WHEN r.hora_ingreso IS NOT NULL THEN 'correccion'
         WHEN r.hora_ingreso_leida IS NOT NULL AND r.origen_ingreso = 'panel' THEN 'panel'
         WHEN r.hora_ingreso_leida IS NOT NULL THEN 'mensaje' END AS fuente_hora_ingreso,
    CASE WHEN r.hora_salida IS NOT NULL THEN 'correccion'
         WHEN r.hora_salida_leida IS NOT NULL THEN 'mensaje' END AS fuente_hora_salida,
    CASE WHEN r.vueltas IS NOT NULL THEN 'correccion'
         WHEN r.vueltas_leidas IS NOT NULL AND r.origen_vueltas = 'panel' THEN 'panel'
         WHEN r.vueltas_leidas IS NOT NULL THEN 'mensaje' END AS fuente_vueltas,
    CASE WHEN r.pedidos IS NOT NULL THEN 'correccion'
         WHEN r.pedidos_leidos IS NOT NULL AND r.origen_pedidos = 'panel' THEN 'panel'
         WHEN r.pedidos_leidos IS NOT NULL THEN 'mensaje' END AS fuente_pedidos,
    CASE WHEN r.observacion IS NOT NULL THEN 'correccion' END AS fuente_observacion,

    -- el mensaje del que salió cada valor (para poder mostrarlo y auditarlo)
    r.msg_ingreso,
    r.msg_salida,
    r.msg_vueltas,
    r.msg_pedidos,

    -- minutos de tardanza sobre el bloque (negativo = entró antes)
    r.tardanza_min,

    r.n_mensajes,
    r.mensaje_ids,
    r.algo_dudoso,
    r.codigo_provisorio AS codigo_en_uso,
    r.autor_correccion,

    -- ---- veredicto de la fila ----
    CASE
        -- Primero lo más simple: si no hay ningún valor y no escribió, no trabajó. Un documento
        -- provisorio importa cuando hay algo que pagar, no para marcar una ausencia.
        WHEN r.n_valores IS NULL AND r.n_mensajes = 0 THEN 'ausente'
        WHEN NOT (r.documento_confirmado AND r.documento ~ '^[0-9]{8}$') THEN 'falla'
        WHEN r.n_valores IS NULL AND r.n_mensajes > 0 THEN 'falla'
        WHEN r.pedidos_totales IS NULL AND r.n_mensajes > 0 THEN 'falla'
        WHEN r.pedidos_totales IS NULL AND r.n_valores IS NOT NULL
             THEN 'duda'   -- abrió el día pero no cerró: falta el número que se paga
        WHEN r.algo_dudoso AND r.pedidos IS NULL THEN 'duda'
        ELSE 'ok'
    END AS estado,

    array_to_string(array_remove(ARRAY[
        CASE WHEN NOT (r.documento_confirmado AND r.documento ~ '^[0-9]{8}$')
             THEN 'documento provisorio o inválido (' || coalesce(r.codigo_provisorio, r.documento, 'sin DNI')
                  || '): no se puede liquidar hasta que se confirme el DNI' END,
        CASE WHEN r.n_valores IS NULL AND r.pedidos_programados IS NOT NULL AND r.n_mensajes = 0
             THEN 'no reportó. Por regla del cliente, no reportar es no haber ido' END,
        CASE WHEN r.n_valores IS NULL AND r.n_mensajes > 0
             THEN 'mandó mensaje y no se le extrajo ningún valor: plata que no se factura' END,
        CASE WHEN r.pedidos_totales IS NULL AND r.n_mensajes > 0
             THEN 'mandó su cierre y no se le extrajeron pedidos: plata que no se factura' END,
        CASE WHEN r.pedidos_totales IS NULL AND r.n_mensajes = 0 AND r.n_valores IS NOT NULL
             THEN 'marcó ingreso pero no llegó su cierre: falta la vuelta y los pedidos' END,
        CASE WHEN a_numero(r.sac) > 0
             THEN a_numero(r.sac) || ' pedido(s) SAC sumados a los regulares' END,
        CASE WHEN r.marco_lejos
             THEN 'marcó la llegada lejos de la tienda: revisar' END,
        CASE WHEN r.algo_dudoso AND r.pedidos IS NULL
             THEN 'hay un valor marcado como dudoso por el control de números' END,
        CASE WHEN r.pedidos_programados IS NULL AND r.n_valores IS NOT NULL
             THEN 'reportó sin estar programado: se carga igual, pero no tiene bloque' END,
        CASE WHEN r.tardanza_min IS NOT NULL AND r.tardanza_min > r.minutos_tolerancia
             THEN 'ingresó ' || r.tardanza_min || ' min después del bloque (tolerancia '
                  || r.minutos_tolerancia || ' min): descuento pendiente de definir' END
    ], NULL), ' · ') AS motivos

FROM resuelto r;

-- ---- Liquidación del día --------------------------------------------------
-- El descuento por tardanza queda NULL: la regla no está por escrito y un cero
-- sería un dato inventado sobre el sueldo de una persona.
CREATE OR REPLACE VIEW vista_liquidacion_dia AS
SELECT t.cliente_id,
       t.dia,
       t.conductor_id,
       t.nombre,
       t.documento,
       t.documento_confirmado,
       t.codigo_tienda,
       t.supervisor,
       t.estado                               AS estado_tareo,
       t.motivos,
       t.tardanza_min,
       t.minutos_tolerancia,
       t.pedidos_regulares,
       t.sac,
       t.rechazados,
       t.vueltas_caidas,
       t.entregas_tocadas,
       t.pedidos::numeric                                            AS pedidos,
       -- la tarifa del día: cambiar el precio hoy no reescribe lo que se pagó antes
       tarifa_del_dia(c.id, t.dia)                                   AS tarifa,
       CASE WHEN t.estado = 'ok'
            THEN t.pedidos::numeric * tarifa_del_dia(c.id, t.dia) END AS bruto,
       NULL::numeric                                                 AS descuento_tardanza,
       CASE WHEN t.estado = 'ok'
            THEN t.pedidos::numeric * tarifa_del_dia(c.id, t.dia) END AS total,
       -- Tres canastas, sin vocabulario inventado:
       --   a_pagar   -> la fila está completa y el documento confirmado
       --   pendiente -> el pedido existe pero falta un dato o el cierre: hay que resolverlo
       --   no_trabajo-> no reportó: no es una tarea, es un hecho
       CASE WHEN t.estado = 'ausente' THEN 'no_trabajo'
            WHEN t.estado IN ('falla', 'duda')
                 OR (t.estado = 'ok' AND t.pedidos IS NULL) THEN 'pendiente'
            ELSE 'a_pagar' END                                       AS estado
FROM vista_tareo_dia t
JOIN clientes c ON c.id = t.cliente_id;

-- ---- Resumen del día ------------------------------------------------------
CREATE OR REPLACE VIEW vista_resumen_dia AS
SELECT l.cliente_id,
       l.dia,
       count(*)                                                        AS conductores,
       count(*) FILTER (WHERE l.estado <> 'no_trabajo')                AS con_reporte,
       count(*) FILTER (WHERE l.estado = 'no_trabajo')                 AS no_trabajo,
       count(*) FILTER (WHERE l.estado = 'a_pagar')                    AS filas_a_pagar,
       count(*) FILTER (WHERE l.estado = 'pendiente')                  AS filas_pendientes,
       -- de las pendientes, cuántas son por dato/documento y cuántas por cierre faltante
       count(*) FILTER (WHERE l.estado = 'pendiente' AND l.estado_tareo = 'falla') AS pendientes_dato,
       count(*) FILTER (WHERE l.estado = 'pendiente' AND l.estado_tareo = 'duda')  AS pendientes_cierre,
       coalesce(sum(l.pedidos) FILTER (WHERE l.estado = 'a_pagar'), 0)  AS pedidos_a_pagar,
       coalesce(sum(l.pedidos) FILTER (WHERE l.estado = 'pendiente'), 0) AS pedidos_pendientes,
       coalesce(sum(l.bruto), 0)                                        AS plata_a_pagar,
       coalesce(sum(l.pedidos) FILTER (WHERE l.estado = 'pendiente'), 0)
           * max(l.tarifa)                                              AS plata_pendiente,
       count(*) FILTER (WHERE coalesce(l.tardanza_min, 0) > l.minutos_tolerancia) AS tardanzas
FROM vista_liquidacion_dia l
GROUP BY l.cliente_id, l.dia, l.minutos_tolerancia;

-- ---- Controles del día ----------------------------------------------------
-- Los cinco controles del circuito, uno por fila, calculados en la base.
CREATE OR REPLACE VIEW vista_controles_dia AS
WITH base AS (
    SELECT t.cliente_id, t.dia,
           count(*)                                                                 AS filas,
           count(*) FILTER (WHERE NOT (t.documento_confirmado
                                       AND t.documento ~ '^[0-9]{8}$'))             AS sin_dni,
           count(*) FILTER (WHERE t.algo_dudoso)                                     AS dudosos,
           count(*) FILTER (WHERE t.estado = 'falla'
                              AND coalesce(t.motivos, '') LIKE '%no se le extraj%')  AS sin_extraer,
           count(*) FILTER (WHERE t.fuente_pedidos = 'correccion')                    AS correcciones,
           coalesce(sum(t.n_mensajes), 0)                                            AS mensajes
    FROM vista_tareo_dia t
    GROUP BY t.cliente_id, t.dia
)
SELECT cliente_id, dia, 'captura' AS control, '—' AS veredicto,
       mensajes || ' mensajes capturados en el día' AS resumen
FROM base
UNION ALL
SELECT cliente_id, dia, 'identificacion',
       CASE WHEN sin_dni > 0 THEN 'FALLA' ELSE 'OK' END,
       sin_dni || ' de ' || filas || ' filas sin DNI confirmado'
FROM base
UNION ALL
SELECT cliente_id, dia, 'numeros',
       CASE WHEN dudosos > 0 THEN 'DUDA' ELSE 'OK' END,
       dudosos || ' valor(es) marcado(s) fuera de rango o dudosos'
FROM base
UNION ALL
SELECT cliente_id, dia, 'completitud',
       CASE WHEN sin_extraer > 0 THEN 'FALLA' ELSE 'OK' END,
       sin_extraer || ' cierre(s) capturado(s) sin pedidos extraídos'
FROM base
UNION ALL
SELECT cliente_id, dia, 'fidelidad',
       'OK'::text,
       'todo valor tiene mensaje de origen · ' || correcciones || ' corrección(es) a mano'
FROM base;

COMMIT;
