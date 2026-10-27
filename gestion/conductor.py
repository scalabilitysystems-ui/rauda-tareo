"""
API del conductor: entrar con PIN, abrir el día y marcar entregas.

Qué NO hace: no calcula plata, no cierra el día y no muestra soles. El número que se paga
sale del cierre (lo que él declara). Acá solo se registra lo que hace en la calle, con hora
del servidor y, si la da, la posición.

Las reglas viven en la base (constraints, `distancia_m`, idempotencia por `clave_cliente`):
si un toque llega dos veces porque el celular reintentó, se guarda una sola vez.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, db

router = APIRouter(prefix="/api/conductor", tags=["conductor"])

DIAS_SESION = 30
INTENTOS_MAX = 10
VENTANA_MIN = 15
TIPOS = ("entregado", "rechazado", "vuelta_caida")


# --- modelos de entrada -----------------------------------------------------
class Login(BaseModel):
    """El conductor entra con su DNI: es lo único que ya sabe de memoria."""
    documento: str = Field(min_length=6, max_length=20)
    clave: str = Field(min_length=1, max_length=128)


class Registro(BaseModel):
    """Primera vez: elige su contraseña. No hay código que generar ni repartir."""
    documento: str = Field(min_length=6, max_length=20)
    clave: str = Field(min_length=8, max_length=128)


class Llegada(BaseModel):
    pedidos: int = Field(ge=0, le=500)
    # Perú: lat entre -18 y 0, lon entre -82 y -68. Un punto fuera de eso es basura que después
    # aparece en el mapa y en la distancia a la tienda.
    lat: float | None = Field(default=None, ge=-18.5, le=0.5)
    lon: float | None = Field(default=None, ge=-82, le=-68)
    precision: float | None = None
    hora_telefono: datetime | None = None
    clave_cliente: str | None = None


class Carga(BaseModel):
    pedidos: int = Field(ge=0, le=500)
    lat: float | None = Field(default=None, ge=-18.5, le=0.5)
    lon: float | None = Field(default=None, ge=-82, le=-68)
    hora_telefono: datetime | None = None
    clave_cliente: str | None = None


MOTIVOS = {
    "no_habia_quien_reciba": "No había quién reciba",
    "direccion_erronea": "Dirección errónea",
    "rechazado_por_cliente": "Lo rechazó el cliente",
    "fuera_de_horario": "Fuera de horario",
    "otro": "Otro",
}


class Marca(BaseModel):
    tipo: str
    motivo: str | None = None
    lat: float | None = Field(default=None, ge=-18.5, le=0.5)
    lon: float | None = Field(default=None, ge=-82, le=-68)
    precision: float | None = None
    hora_telefono: datetime | None = None
    clave_cliente: str | None = None


class Cierre(BaseModel):
    """Lo que declara al final del día. Lo que se cobra sale de acá, no de los toques."""
    vueltas: int = Field(ge=1, le=50)
    pedidos: int = Field(ge=0, le=1000)
    sac: int = Field(default=0, ge=0, le=500)
    rechazados: int = Field(default=0, ge=0, le=500)
    vueltas_caidas: int = Field(default=0, ge=0, le=100)
    hora_telefono: datetime | None = None
    clave_cliente: str | None = None


# --- helpers ----------------------------------------------------------------
def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalizar(documento: str) -> str:
    return "".join(ch for ch in documento if ch.isalnum()).upper()


def hoy_lima() -> str:
    return "(now() AT TIME ZONE 'America/Lima')::date"


def conductor_actual(authorization: str = Header("")) -> dict:
    """El conductor de la sesión nace del token, nunca del `cliente_id` que venga en el cuerpo.

    La sesión sale de la tabla `sesiones` (cuentas con usuario y contraseña): si el token no
    existe, venció o la cuenta se desactivó, no hay acceso.
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "falta el token")
    token = authorization.split(" ", 1)[1].strip()
    cuenta = auth.por_token(token)
    if (not cuenta or cuenta["rol"] != "conductor" or not cuenta.get("conductor_id")
            or not cuenta["activa"]):
        raise HTTPException(401, "sesión vencida o inválida")
    fila = db._uno("SELECT id, nombre, documento, cliente_id FROM conductores WHERE id = %s",
                   (cuenta["conductor_id"],))
    if not fila:
        raise HTTPException(401, "conductor no encontrado")
    return {**fila, "conductor_id": fila["id"], "cuenta": cuenta}


def _exigir_tienda(conductor_id: int) -> None:
    """Sin tienda asignada no se puede abrir ni cerrar el día.

    El tareo cuelga de la tienda (`valores.grupo_id`): sin ella el INSERT no escribe nada y el
    día se perdía en silencio — el conductor veía "cerrado" y operaciones veía "no trabajó".
    """
    fila = db._uno("SELECT grupo_id FROM conductores WHERE id = %s", (conductor_id,))
    if not fila or not fila["grupo_id"]:
        raise HTTPException(
            409, "Tu usuario no tiene tienda asignada. Avisa a la administración: sin tienda "
                 "no se puede abrir el día.")


def _estado_del_dia(conductor_id: int) -> dict:
    """El día tal como está en la base: jornada, cargas, toques y último punto."""
    jornada = db._uno(
        "SELECT j.*, g.codigo_tienda, g.radio_m,"
        "       (j.abierta_telefono IS NOT NULL"
        "        AND abs(extract(epoch FROM (j.abierta_en - j.abierta_telefono))) > 600)"
        "        AS reloj_sospechoso"
        " FROM jornadas j"
        " LEFT JOIN conductores c ON c.id = j.conductor_id"
        " LEFT JOIN grupos g ON g.grupo_id = c.grupo_id"
        f" WHERE j.conductor_id = %s AND j.dia = {hoy_lima()}"
        " ORDER BY j.id DESC LIMIT 1", (conductor_id,))

    if not jornada:
        return {"jornada": None, "cargas": [], "toques": {}, "ultimo_punto": None}

    cargas = db._consulta(
        "SELECT vuelta, pedidos, hora_servidor FROM cargas"
        " WHERE jornada_id = %s ORDER BY vuelta", (jornada["id"],))
    toques = db._uno(
        "SELECT count(*) FILTER (WHERE tipo = 'entregado')    AS entregados,"
        " count(*) FILTER (WHERE tipo = 'rechazado')    AS rechazados,"
        " count(*) FILTER (WHERE tipo = 'vuelta_caida') AS vueltas_caidas,"
        " max(hora_servidor) FILTER (WHERE tipo = 'entregado') AS ultima_entrega"
        " FROM marcas WHERE jornada_id = %s", (jornada["id"],))
    ultimo = db._uno(
        "SELECT lat, lon, hora_servidor FROM marcas"
        " WHERE jornada_id = %s AND lat IS NOT NULL ORDER BY hora_servidor DESC LIMIT 1",
        (jornada["id"],))
    # lo declarado, tal como lo ve el panel de operaciones (misma vista, mismo número)
    declarado = db._uno(
        "SELECT pedidos, pedidos_regulares, sac, rechazados, vueltas_caidas, vueltas,"
        "       hora_ingreso, hora_salida, estado"
        f" FROM vista_tareo_dia WHERE conductor_id = %s AND dia = {hoy_lima()}",
        (conductor_id,))
    return {
        "jornada": jornada,
        "cargas": cargas,
        "toques": {k: (v or 0) for k, v in (toques or {}).items() if k != "ultima_entrega"},
        "ultima_entrega": (toques or {}).get("ultima_entrega"),
        "ultimo_punto": ultimo,
        "pedidos_cargados": sum(c["pedidos"] for c in cargas),
        "declarado": declarado,
        "cerrada": bool(jornada.get("cerrada_en")),
    }


# --- rutas ------------------------------------------------------------------
@router.post("/login")
def login(datos: Login, request: Request):
    """Entra con el usuario y la contraseña que él mismo creó.

    Antes era DNI + PIN de 4 números: eso se presta, se comparte y se olvida, y el PIN lo
    tenía que inventar la administración. Ahora la persona crea su clave una vez (con un
    código de un solo uso) y entra con ella.
    """
    ip = request.client.host if request.client else None
    documento = _normalizar(datos.documento)
    try:
        token, cuenta = auth.entrar(documento, datos.clave, ip=ip,
                                    huella=request.headers.get("user-agent", "")[:120])
    except PermissionError:
        raise HTTPException(429, "Demasiados intentos: esperá 15 minutos y probá de nuevo.")
    except ValueError:
        raise HTTPException(401, "Usuario o contraseña incorrectos.")

    if cuenta["rol"] != "conductor" or not cuenta.get("conductor_id"):
        raise HTTPException(403, "Esta cuenta no es de conductor: entrá desde el panel.")

    auth.auditar(cuenta, "ingreso")
    return {"token": token, "nombre": cuenta["nombre"], "vence": cuenta["vence"].isoformat(),
            "conductor_id": cuenta["conductor_id"]}


@router.post("/registrar")
def registrar(datos: Registro, request: Request):
    """Primera vez: elige su contraseña y queda adentro en el mismo paso.

    Sin códigos: su identidad es el DNI que la administración ya cargó. El reclamo queda en
    `auditoria`, así operaciones ve quién entró por primera vez y cuándo.
    """
    try:
        cuenta = auth.reclamar(datos.documento, datos.clave)
    except ValueError as error:
        # Se anota el intento fallido: el freno de 5 por hora se apoya en esta tabla.
        auth._anotar_intento(_normalizar(datos.documento), False,
                             request.client.host if request.client else None)
        raise HTTPException(400, str(error))

    ip = request.client.host if request.client else None
    token, cuenta = auth.entrar(cuenta["usuario"], datos.clave, ip=ip,
                                huella=request.headers.get("user-agent", "")[:120])
    return {"token": token, "nombre": cuenta["nombre"], "vence": cuenta["vence"].isoformat(),
            "conductor_id": cuenta["conductor_id"]}


@router.get("/hoy")
def hoy(conductor: dict = Depends(conductor_actual)):
    estado = _estado_del_dia(conductor["conductor_id"])
    return {"nombre": conductor["nombre"], **estado}


@router.post("/llegada")
def llegada(datos: Llegada, conductor: dict = Depends(conductor_actual)):
    """Abre la jornada del día y anota la carga inicial.

    Idempotente: si el celular reintenta (o el conductor toca dos veces), no se abre otra
    jornada — la base tiene UNIQUE (conductor_id, dia).
    """
    _exigir_tienda(conductor["conductor_id"])
    with db.conexion() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO jornadas (cliente_id, conductor_id, dia, abierta_telefono, lat, lon,"
            " precision_m, distancia_tienda_m, estado_ubicacion)"
            " SELECT c.cliente_id, c.id, " + hoy_lima() + ", %s, %s, %s, %s,"
            "        CASE WHEN %s::numeric IS NULL OR g.lat IS NULL THEN NULL::numeric"
            "             ELSE distancia_m(%s::numeric, %s::numeric, g.lat, g.lon) END,"
            "        CASE WHEN %s::numeric IS NULL OR g.lat IS NULL THEN 'sin_ubicacion'"
            "             WHEN distancia_m(%s::numeric, %s::numeric, g.lat, g.lon) <= g.radio_m"
            "             THEN 'cerca' ELSE 'lejos' END"
            " FROM conductores c"
            " LEFT JOIN grupos g ON g.grupo_id = c.grupo_id"
            " WHERE c.id = %s"
            " ON CONFLICT (conductor_id, dia) DO NOTHING"
            " RETURNING id, estado_ubicacion, distancia_tienda_m",
            (datos.hora_telefono, datos.lat, datos.lon, datos.precision,
             datos.lat, datos.lat, datos.lon,
             datos.lat, datos.lat, datos.lon,
             conductor["conductor_id"]))
        nueva = cur.fetchone()

        if nueva:
            cur.execute(
                "INSERT INTO cargas (jornada_id, vuelta, pedidos, hora_telefono, lat, lon,"
                " clave_cliente) VALUES (%s, 1, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (nueva["id"], datos.pedidos, datos.hora_telefono, datos.lat, datos.lon,
                 datos.clave_cliente))
            # La hora de ingreso entra al tareo como un valor más, con origen 'panel': así el
            # panel de operaciones la ve sin saber (ni importarle) de dónde vino.
            cur.execute(
                "INSERT INTO valores (cliente_id, conductor_id, dia, tipo, valor, mensaje_id,"
                " grupo_id, estado, origen, declarado_en)"
                " SELECT j.cliente_id, j.conductor_id, j.dia, 'hora_ingreso',"
                "        to_char(j.abierta_en AT TIME ZONE 'America/Lima', 'HH24:MI'), NULL,"
                "        g.grupo_id, 'confirmado', 'panel', j.abierta_en"
                " FROM jornadas j"
                " JOIN conductores c ON c.id = j.conductor_id"
                " JOIN grupos g ON g.grupo_id = c.grupo_id"
                " WHERE j.id = %s"
                " ON CONFLICT (conductor_id, dia, tipo) DO UPDATE"
                "   SET valor = EXCLUDED.valor, origen = 'panel', mensaje_id = NULL,"
                "       declarado_en = EXCLUDED.declarado_en", (nueva["id"],))
            if cur.rowcount == 0:
                # Sin tienda no hay grupo en `valores`, y sin ese dato el día no aparece en el
                # tareo: se pierde en silencio. Mejor no abrir el día y decirlo.
                raise HTTPException(
                    409, "Tu usuario no tiene tienda asignada. Avisa a la administración: "
                         "sin tienda no se puede abrir el día.")
        con.commit()

    return {"repetida": nueva is None, **_estado_del_dia(conductor["conductor_id"])}


@router.post("/carga")
def carga(datos: Carga, conductor: dict = Depends(conductor_actual)):
    """Cargo más pedidos (volvió a la tienda). No es obligatorio que haya una segunda carga."""
    with db.conexion() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO cargas (jornada_id, vuelta, pedidos, hora_telefono, lat, lon,"
            " clave_cliente)"
            " SELECT j.id, coalesce(max(c.vuelta), 0) + 1, %s, %s, %s, %s, %s"
            " FROM jornadas j LEFT JOIN cargas c ON c.jornada_id = j.id"
            f" WHERE j.conductor_id = %s AND j.dia = {hoy_lima()}"
            " GROUP BY j.id"
            " ON CONFLICT DO NOTHING"
            " RETURNING id",
            (datos.pedidos, datos.hora_telefono, datos.lat, datos.lon, datos.clave_cliente,
             conductor["conductor_id"]))
        if not cur.fetchone():
            raise HTTPException(409, "todavía no abriste el día")
        con.commit()
    return _estado_del_dia(conductor["conductor_id"])


@router.post("/cerrar")
def cerrar(datos: Cierre, conductor: dict = Depends(conductor_actual)):
    """Cierra el día con lo que el conductor declara.

    Es el número que se paga. Los toques no entran en esta cuenta: sirven para el mapa y para
    que operaciones vea si el conductor marcó o no. Se puede volver a cerrar mientras la
    administración no haya cerrado el día; después, no.
    """
    _exigir_tienda(conductor["conductor_id"])
    with db.conexion() as con, con.cursor() as cur:
        cur.execute(
            "SELECT j.id FROM jornadas j"
            f" WHERE j.conductor_id = %s AND j.dia = {hoy_lima()} LIMIT 1",
            (conductor["conductor_id"],))
        jornada = cur.fetchone()
        if not jornada:
            raise HTTPException(409, "todavía no abriste el día")

        cur.execute(
            "SELECT 1 FROM liquidaciones WHERE cliente_id=%s AND dia=" + hoy_lima()
            + " LIMIT 1", (conductor["cliente_id"],))
        if cur.fetchone():
            raise HTTPException(409, "la administración ya cerró el día: no se puede cambiar")

        valores = [
            ("pedidos", datos.pedidos),
            ("vuelta", datos.vueltas),
            ("sac", datos.sac),
            ("rechazados", datos.rechazados),
            ("vueltas_caidas", datos.vueltas_caidas),
        ]
        for tipo, valor in valores:
            cur.execute(
                "INSERT INTO valores (cliente_id, conductor_id, dia, tipo, valor, mensaje_id,"
                " grupo_id, estado, origen, declarado_en)"
                " SELECT j.cliente_id, j.conductor_id, j.dia, %s, %s, NULL,"
                "        g.grupo_id, 'confirmado', 'panel', now()"
                " FROM jornadas j"
                " JOIN conductores c ON c.id = j.conductor_id"
                " JOIN grupos g ON g.grupo_id = c.grupo_id"
                " WHERE j.id = %s"
                " ON CONFLICT (conductor_id, dia, tipo) DO UPDATE"
                "   SET valor = EXCLUDED.valor, origen = 'panel', mensaje_id = NULL,"
                "       estado = 'confirmado', declarado_en = now()",
                (tipo, str(valor), jornada["id"]))

        # La hora de salida es la hora del cierre: no se pregunta, se toma del servidor.
        cur.execute(
            "INSERT INTO valores (cliente_id, conductor_id, dia, tipo, valor, mensaje_id,"
            " grupo_id, estado, origen, declarado_en)"
            " SELECT j.cliente_id, j.conductor_id, j.dia, 'hora_salida',"
            "        to_char(now() AT TIME ZONE 'America/Lima', 'HH24:MI'), NULL,"
            "        g.grupo_id, 'confirmado', 'panel', now()"
            " FROM jornadas j"
            " JOIN conductores c ON c.id = j.conductor_id"
            " JOIN grupos g ON g.grupo_id = c.grupo_id"
            " WHERE j.id = %s"
            " ON CONFLICT (conductor_id, dia, tipo) DO UPDATE"
            "   SET valor = EXCLUDED.valor, origen = 'panel', mensaje_id = NULL,"
            "       estado = 'confirmado', declarado_en = now()", (jornada["id"],))

        cur.execute(
            "UPDATE jornadas SET cerrada_en = now(), cerrada_telefono = %s,"
            " vueltas_declaradas = %s, cierre_servidor = now() WHERE id = %s",
            (datos.hora_telefono, datos.vueltas, jornada["id"]))
        con.commit()

    return _estado_del_dia(conductor["conductor_id"])


@router.post("/marca")
def marcar(datos: Marca, conductor: dict = Depends(conductor_actual)):
    """Un toque: entregué / rechazado / vuelta caída. Idempotente por `clave_cliente`."""
    if datos.tipo not in TIPOS:
        raise HTTPException(422, f"tipo inválido: {datos.tipo}")
    motivo = datos.motivo if datos.motivo in MOTIVOS else None
    if datos.tipo == "rechazado" and datos.motivo and motivo is None:
        raise HTTPException(422, f"motivo inválido: {datos.motivo}")
    with db.conexion() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO marcas (jornada_id, tipo, motivo, hora_telefono, lat, lon, precision_m,"
            " clave_cliente)"
            " SELECT j.id, %s, %s, %s, %s, %s, %s, %s"
            " FROM jornadas j"
            f" WHERE j.conductor_id = %s AND j.dia = {hoy_lima()}"
            " ON CONFLICT (clave_cliente) DO NOTHING"
            " RETURNING id",
            (datos.tipo, motivo, datos.hora_telefono, datos.lat, datos.lon, datos.precision,
             datos.clave_cliente, conductor["conductor_id"]))
        if not cur.fetchone():
            con.rollback()
            estado = _estado_del_dia(conductor["conductor_id"])
            if not estado["jornada"]:
                raise HTTPException(409, "todavía no abriste el día")
            return estado
        con.commit()
    return _estado_del_dia(conductor["conductor_id"])
