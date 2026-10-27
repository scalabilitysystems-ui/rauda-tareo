-- ============================================================================
-- Paradas y motivo del rechazo.
--
-- Dos conceptos que faltaban, en español y sin agregarle pasos al conductor:
--   * `parada` — el número de parada de cada toque dentro del día. NO se pregunta: se deriva
--     del orden en que marcó (row_number), así el conductor sigue tocando una sola vez.
--   * `motivo` — por qué se rechazó o no se pudo entregar. Es el dato que le sirve al negocio
--     (dirección mal, no había nadie, lo rechazó el cliente) y el que hoy se pierde.
--
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_paradas.sql
-- ============================================================================

BEGIN;

ALTER TABLE marcas ADD COLUMN IF NOT EXISTS motivo TEXT;

ALTER TABLE marcas DROP CONSTRAINT IF EXISTS marcas_motivo_check;
ALTER TABLE marcas ADD CONSTRAINT marcas_motivo_check CHECK (
    motivo IS NULL OR motivo IN (
        'no_habia_quien_reciba',
        'direccion_erronea',
        'rechazado_por_cliente',
        'fuera_de_horario',
        'otro'
    )
);

CREATE INDEX IF NOT EXISTS idx_marcas_motivo ON marcas (motivo) WHERE motivo IS NOT NULL;

-- ---- Paradas --------------------------------------------------------------
-- Un renglón por toque, con su número de parada dentro del día. `parada` corre solo sobre las
-- entregas y los rechazos (los toques de vuelta caída no son paradas de reparto).
CREATE OR REPLACE VIEW vista_paradas AS
SELECT m.id                     AS marca_id,
       j.cliente_id,
       j.conductor_id,
       j.dia,
       j.id                     AS jornada_id,
       m.tipo,
       m.motivo,
       m.hora_servidor,
       m.lat,
       m.lon,
       m.precision_m,
       CASE WHEN m.tipo IN ('entregado', 'rechazado')
            THEN row_number() OVER (
                     PARTITION BY m.jornada_id, (m.tipo IN ('entregado', 'rechazado'))
                     ORDER BY m.hora_servidor, m.id)
       END                      AS parada
FROM marcas m
JOIN jornadas j ON j.id = m.jornada_id;

COMMIT;
