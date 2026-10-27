-- ============================================================================
-- Panel del conductor (punto 1): entrada con PIN, jornada, "ya llegué" y "entregué".
--
-- La idea que ordena todo: el panel NO complejiza la operación. El conductor hace
-- dos cosas que ya hace por WhatsApp — avisar que llegó y avisar que entregó — y nada más.
--
-- Lo que el toque NO decide es la plata: el número que se paga sigue saliendo del cierre
-- del día (lo que él declara). Las marcas son para el mapa y para el control.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_conductor.sql
-- ============================================================================

BEGIN;

-- ---- Entrada del conductor ------------------------------------------------
-- PIN corto (4 dígitos) hasheado: nunca se guarda en claro y se puede resetear desde el
-- panel de gestión. Cuatro dígitos son 10.000 combinaciones: sin freno a los intentos
-- eso se adivina en minutos, por eso existe `intentos_pin`.
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS pin_hash            TEXT;
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS pin_salt            TEXT;
ALTER TABLE conductores ADD COLUMN IF NOT EXISTS pin_actualizado_en  TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS sesiones_conductor (
    id            BIGSERIAL PRIMARY KEY,
    conductor_id  BIGINT NOT NULL REFERENCES conductores(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence_en      TIMESTAMPTZ NOT NULL,
    revocada_en   TIMESTAMPTZ,
    ultimo_uso    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS intentos_pin (
    id          BIGSERIAL PRIMARY KEY,
    documento   TEXT NOT NULL,
    correcto    BOOLEAN NOT NULL DEFAULT FALSE,
    cuando      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_intentos_doc ON intentos_pin (documento, cuando DESC);

-- ---- Jornada --------------------------------------------------------------
-- Una por conductor y día. `abierta_en` la pone el servidor, `abierta_telefono` es la hora
-- que manda el celular (sirve cuando carga sin señal). Si difieren más de 10 minutos, el día
-- queda marcado: un reloj corrido mueve la hora de ingreso y con ella la tardanza.
CREATE TABLE IF NOT EXISTS jornadas (
    id                  BIGSERIAL PRIMARY KEY,
    cliente_id          BIGINT NOT NULL REFERENCES clientes(id),
    conductor_id        BIGINT NOT NULL REFERENCES conductores(id),
    dia                 DATE NOT NULL,
    abierta_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    abierta_telefono    TIMESTAMPTZ,
    lat                 NUMERIC(9,6),
    lon                 NUMERIC(9,6),
    precision_m         NUMERIC(8,2),
    distancia_tienda_m  NUMERIC(8,1),
    estado_ubicacion    TEXT,                    -- cerca | lejos | sin_ubicacion
    cerrada_en          TIMESTAMPTZ,
    UNIQUE (conductor_id, dia)
);

-- ---- Cargas de pedidos ----------------------------------------------------
-- "Cargo 20 pedidos" al arrancar y cada vez que vuelve a la tienda. No es obligatorio
-- que haya más de una: a veces sigue con lo que lleva.
CREATE TABLE IF NOT EXISTS cargas (
    id              BIGSERIAL PRIMARY KEY,
    jornada_id      BIGINT NOT NULL REFERENCES jornadas(id) ON DELETE CASCADE,
    vuelta          INTEGER NOT NULL DEFAULT 1,
    pedidos         INTEGER NOT NULL CHECK (pedidos >= 0),
    hora_servidor   TIMESTAMPTZ NOT NULL DEFAULT now(),
    hora_telefono   TIMESTAMPTZ,
    lat             NUMERIC(9,6),
    lon             NUMERIC(9,6),
    clave_cliente   TEXT UNIQUE,                 -- idempotencia: el mismo toque, una sola vez
    UNIQUE (jornada_id, vuelta)
);

-- ---- Marcas del día -------------------------------------------------------
-- Cada toque: entregué un pedido, rechazado, vuelta caída. Es lo que dibuja el mapa.
CREATE TABLE IF NOT EXISTS marcas (
    id              BIGSERIAL PRIMARY KEY,
    jornada_id      BIGINT NOT NULL REFERENCES jornadas(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL CHECK (tipo IN ('entregado', 'rechazado', 'vuelta_caida')),
    hora_servidor   TIMESTAMPTZ NOT NULL DEFAULT now(),
    hora_telefono   TIMESTAMPTZ,
    lat             NUMERIC(9,6),
    lon             NUMERIC(9,6),
    precision_m     NUMERIC(8,2),
    clave_cliente   TEXT UNIQUE,
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_marcas_jornada ON marcas (jornada_id, hora_servidor);
CREATE INDEX IF NOT EXISTS idx_marcas_tipo    ON marcas (tipo);

-- ---- Geocerca de la tienda ------------------------------------------------
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS lat     NUMERIC(9,6);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS lon     NUMERIC(9,6);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS radio_m INTEGER NOT NULL DEFAULT 150;

-- ---- Distancia, calculada por la base -------------------------------------
-- Haversine en metros. Va como función y no como código de la aplicación para que la
-- misma cuenta la use el panel, un informe o una auditoría.
CREATE OR REPLACE FUNCTION distancia_m(lat1 NUMERIC, lon1 NUMERIC, lat2 NUMERIC, lon2 NUMERIC)
RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$
    SELECT round((6371000 * 2 * asin(sqrt(
               power(sin(radians((lat2 - lat1) / 2)), 2) +
               cos(radians(lat1)) * cos(radians(lat2)) *
               power(sin(radians((lon2 - lon1) / 2)), 2)
           )))::numeric, 1)
$$;

COMMIT;
