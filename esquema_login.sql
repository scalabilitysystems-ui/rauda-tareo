-- ============================================================================
-- Login propio: cuentas con usuario y contraseña, sesiones y auditoría.
--
-- Por qué: hoy el panel de gestión entra con una clave compartida (Basic Auth) y eso hace
-- que aparezca el cartel gris del navegador — feo, y sin distinguir quién hizo qué. Y los
-- conductores entraban con un PIN de 4 números que les daba la administración: un PIN de
-- 4 dígitos se comparte, se presta y se olvida. Acá cada uno crea su clave la primera vez.
--
-- Aplicar:
--   sudo -u postgres psql -d rauda_dev < /home/ubuntu/rauda/esquema_login.sql
-- ============================================================================

BEGIN;

-- ---- Cuentas ---------------------------------------------------------------
-- `usuario` es el nombre con el que entra (sin espacios, sin mayúsculas: se normaliza al
-- guardar). La contraseña NUNCA se guarda: se guarda su hash con sal por cuenta.
CREATE TABLE IF NOT EXISTS cuentas (
    id                BIGSERIAL PRIMARY KEY,
    usuario           TEXT        NOT NULL UNIQUE,
    -- rol: admin (todo), operaciones (día y liquidación), conductor (solo su día)
    rol               TEXT        NOT NULL CHECK (rol IN ('admin', 'operaciones', 'conductor')),
    -- el alcance por cliente: admin ve todos; operaciones y conductor, los suyos
    cliente_id        BIGINT      REFERENCES clientes(id),
    -- a qué conductor corresponde la cuenta (solo rol conductor)
    conductor_id      BIGINT      REFERENCES conductores(id),
    nombre            TEXT        NOT NULL,
    clave_hash        TEXT,                      -- NULL = todavía no creó su clave
    clave_salt        TEXT,
    clave_actualizada_en TIMESTAMPTZ,
    -- primer ingreso: la cuenta nace con un código de activación y la persona crea su clave
    codigo_activacion_hash TEXT,                 -- hash del código de un solo uso
    codigo_vence_en   TIMESTAMPTZ,
    activa            BOOLEAN     NOT NULL DEFAULT TRUE,
    creada_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_ingreso    TIMESTAMPTZ,
    CONSTRAINT cuentas_conductor_coherente
        CHECK ((rol = 'conductor') = (conductor_id IS NOT NULL)),
    CONSTRAINT cuentas_usuario_minusculas CHECK (usuario = lower(usuario)),
    CONSTRAINT cuentas_usuario_formato CHECK (usuario ~ '^[a-z0-9._-]{3,40}$')
);

-- Un conductor tiene una sola cuenta (no dos "juan" para la misma persona).
CREATE UNIQUE INDEX IF NOT EXISTS cuentas_conductor_unico
    ON cuentas (conductor_id) WHERE conductor_id IS NOT NULL;

-- ---- Sesiones --------------------------------------------------------------
-- El token va hasheado: si alguien lee la base, no puede entrar con lo que ve.
-- `huella` permite revocar sesiones de un aparato sin tocar las demás.
CREATE TABLE IF NOT EXISTS sesiones (
    id            BIGSERIAL PRIMARY KEY,
    cuenta_id     BIGINT      NOT NULL REFERENCES cuentas(id) ON DELETE CASCADE,
    token_hash    TEXT        NOT NULL UNIQUE,
    huella        TEXT,                          -- user-agent resumido, para mostrar "dónde entré"
    creada_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence_en      TIMESTAMPTZ NOT NULL,
    revocada_en   TIMESTAMPTZ,
    ultimo_uso    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sesiones_cuenta ON sesiones (cuenta_id, vence_en DESC);

-- ---- Intentos (freno a la fuerza bruta) ------------------------------------
-- Por usuario, no por DNI: lo que se protege es la cuenta.
CREATE TABLE IF NOT EXISTS intentos_login (
    id           BIGSERIAL PRIMARY KEY,
    usuario      TEXT        NOT NULL,
    correcto     BOOLEAN     NOT NULL DEFAULT FALSE,
    ip           TEXT,
    cuando       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_intentos_login ON intentos_login (usuario, cuando DESC);

-- ---- Quién hizo qué --------------------------------------------------------
-- El día se cierra con nombre y apellido. La auditoría no es un log de archivo: es una
-- tabla, para poder responder "quién cambió esto" desde el panel.
CREATE TABLE IF NOT EXISTS auditoria (
    id          BIGSERIAL PRIMARY KEY,
    -- `SET NULL`: la auditoría **no se borra** aunque se borre la cuenta (queda el nombre);
    -- si cascadeara, borrar una cuenta borraría la prueba de lo que hizo.
    cuenta_id   BIGINT      REFERENCES cuentas(id) ON DELETE SET NULL,
    usuario     TEXT        NOT NULL,
    accion      TEXT        NOT NULL,            -- ingreso | cierre | correccion | clave
    cliente_id  BIGINT      REFERENCES clientes(id) ON DELETE SET NULL,
    dia         DATE,
    detalle     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    cuando      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_auditoria_dia ON auditoria (cliente_id, dia, cuando DESC);

COMMIT;
