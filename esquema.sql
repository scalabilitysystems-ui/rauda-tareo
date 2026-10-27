-- ============================================================================
-- Esquema de datos del control de cierres de reparto (Rauda).
--
-- Por qué existe: el circuito (captura -> extracción -> identificación -> tareo ->
-- liquidación) puede romperse en cualquiera de sus pasos y fallar en silencio. Este
-- esquema guarda, para cada número que termina en una celda del Excel, DE QUÉ MENSAJE
-- salió. Sin esa trazabilidad, la regla dura del cliente ("el sistema nunca completa
-- una celda con un dato que no leyó") es una promesa; con la trazabilidad, es
-- demostrable.
--
-- Aplicar:
--   sudo -u postgres createdb rauda_dev
--   sudo -u postgres psql -d rauda_dev -f /home/ubuntu/rauda/esquema.sql
-- ============================================================================

BEGIN;

-- ---- Tipos -----------------------------------------------------------------
CREATE TYPE tipo_valor    AS ENUM ('pedidos', 'vuelta', 'hora_ingreso', 'hora_salida');
CREATE TYPE estado_valor  AS ENUM ('confirmado', 'dudoso');
CREATE TYPE control_qa    AS ENUM ('captura', 'identificacion', 'numeros', 'completitud', 'fidelidad');
CREATE TYPE veredicto_qa  AS ENUM ('OK', 'DUDA', 'FALLA');
CREATE TYPE estado_duda   AS ENUM ('abierta', 'resuelta', 'descartada');

-- ---- Mensajes crudos -------------------------------------------------------
-- Espejo del cuaderno en disco (datos/<grupo>/<dia>.jsonl). Es la fuente de verdad de
-- la trazabilidad: para decir que un valor "se leyó de verdad", su mensaje tiene que
-- estar acá.
CREATE TABLE mensajes (
    id            TEXT PRIMARY KEY,          -- id del mensaje en WhatsApp (clave de idempotencia)
    grupo_id      TEXT NOT NULL,
    de            TEXT,                      -- jid del remitente
    de_nombre     TEXT,
    hora          TIMESTAMPTZ,               -- hora del mensaje (la que manda el puente)
    texto         TEXT,
    capturado_en  TIMESTAMPTZ NOT NULL
);

-- ---- Conductores -----------------------------------------------------------
CREATE TABLE conductores (
    id                    BIGSERIAL PRIMARY KEY,
    documento             TEXT UNIQUE,       -- DNI de 8 dígitos. Puede ser NULL: todavía no se sabe.
    nombre                TEXT NOT NULL,
    -- Un conductor identificado con un código provisorio (Pro2025, Pro123$$, 123456)
    -- NO puede liquidarse: este flag es lo que lo frena.
    documento_confirmado  BOOLEAN NOT NULL DEFAULT FALSE,
    codigo_provisorio     TEXT,              -- el código crudo mientras no haya DNI
    creado_en             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Valores extraídos -----------------------------------------------------
CREATE TABLE valores (
    id            BIGSERIAL PRIMARY KEY,
    conductor_id  BIGINT REFERENCES conductores(id),
    dia           DATE NOT NULL,
    tipo          tipo_valor NOT NULL,
    valor         TEXT NOT NULL,             -- el valor crudo ("40", "08:00")
    -- Toda la plata está acá: sin mensaje de origen un valor NO EXISTE. El NOT NULL es
    -- la regla dura aplicada en la base, no solo chequeada después.
    mensaje_id    TEXT NOT NULL REFERENCES mensajes(id),
    grupo_id      TEXT NOT NULL,
    estado        estado_valor NOT NULL DEFAULT 'dudoso',
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Un valor por conductor, por día, por tipo: los parciales se resuelven por último,
    -- nunca se suman.
    UNIQUE (conductor_id, dia, tipo)
);

-- ---- Dudas -----------------------------------------------------------------
CREATE TABLE dudas (
    id              BIGSERIAL PRIMARY KEY,
    dia             DATE NOT NULL,
    control         control_qa NOT NULL,
    motivo          TEXT NOT NULL,
    conductor_id    BIGINT REFERENCES conductores(id),
    valor_id        BIGINT REFERENCES valores(id),
    mensaje_id      TEXT REFERENCES mensajes(id),
    detalle         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Para ordenar por lo que está en juego, no alfabéticamente.
    plata_en_juego  NUMERIC(12,2) NOT NULL DEFAULT 0,
    estado          estado_duda NOT NULL DEFAULT 'abierta',
    resolvio        TEXT,                    -- quién la resolvió
    resuelto_en     TIMESTAMPTZ,             -- cuándo
    decision        TEXT,                    -- qué se decidió
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Chequeos --------------------------------------------------------------
-- El resultado de cada control por día, para poder mostrar la tendencia histórica.
CREATE TABLE chequeos (
    id            BIGSERIAL PRIMARY KEY,
    dia           DATE NOT NULL,
    control       control_qa NOT NULL,
    veredicto     veredicto_qa NOT NULL,
    resumen       TEXT,
    evidencia     JSONB NOT NULL DEFAULT '[]'::jsonb,
    ejecutado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Programado ------------------------------------------------------------
-- Los "pedidos programados" del día, que salen de la tabla diaria (AM/PM), por conductor.
CREATE TABLE programado (
    id                   BIGSERIAL PRIMARY KEY,
    conductor_id         BIGINT NOT NULL REFERENCES conductores(id),
    dia                  DATE NOT NULL,
    pedidos_programados  INTEGER NOT NULL,
    UNIQUE (conductor_id, dia)
);

-- ---- Tareo -----------------------------------------------------------------
-- A qué celda del Excel fue cada valor y qué se escribió, para poder auditar.
CREATE TABLE tareo (
    id             BIGSERIAL PRIMARY KEY,
    dia            DATE NOT NULL,
    conductor_id   BIGINT REFERENCES conductores(id),
    fila           INTEGER NOT NULL,         -- fila del Excel
    columna        TEXT NOT NULL,            -- columna del Excel
    celda          TEXT,                     -- "Tareo FAPE 2026!F123"
    -- Puede ser NULL a propósito: si algo se escribió sin valor de origen, queremos poder
    -- guardarlo igual para mostrarlo. El control FIDELIDAD lo marca FALLA.
    valor_id       BIGINT REFERENCES valores(id),
    valor_escrito  TEXT NOT NULL,            -- lo que quedó en la celda
    escrito_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- Índices ---------------------------------------------------------------
CREATE INDEX idx_valores_dia            ON valores (dia);
CREATE INDEX idx_valores_conductor_dia  ON valores (conductor_id, dia);
CREATE INDEX idx_valores_mensaje        ON valores (mensaje_id);
CREATE INDEX idx_tareo_dia              ON tareo (dia);
CREATE INDEX idx_tareo_valor            ON tareo (valor_id);
CREATE INDEX idx_dudas_dia_estado       ON dudas (dia, estado);
CREATE INDEX idx_chequeos_dia_control   ON chequeos (dia, control);

-- ---- Vista: FALSO FLETE ----------------------------------------------------
-- El control que el cliente YA lleva a mano (pestaña 'FALSO FLETE' del Excel): programado
-- contra real por conductor, con la diferencia. El sistema lo reproduce, no lo inventa.
CREATE VIEW falso_flete AS
SELECT p.dia,
       p.conductor_id,
       c.nombre,
       p.pedidos_programados,
       COALESCE(SUM(
           CASE WHEN v.tipo = 'pedidos' AND v.estado = 'confirmado'
                THEN NULLIF(regexp_replace(v.valor, '[^0-9.-]', '', 'g'), '')::numeric
           END), 0) AS pedidos_reales,
       p.pedidos_programados - COALESCE(SUM(
           CASE WHEN v.tipo = 'pedidos' AND v.estado = 'confirmado'
                THEN NULLIF(regexp_replace(v.valor, '[^0-9.-]', '', 'g'), '')::numeric
           END), 0) AS diferencia
FROM programado p
JOIN conductores c ON c.id = p.conductor_id
LEFT JOIN valores v ON v.conductor_id = p.conductor_id AND v.dia = p.dia
GROUP BY p.dia, p.conductor_id, c.nombre, p.pedidos_programados;

COMMIT;
