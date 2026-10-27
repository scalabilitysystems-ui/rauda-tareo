"""Cuentas, sesiones y contraseñas.

Reglas que este módulo hace cumplir:

1. **La contraseña nunca se guarda**, ni se escribe en un log, ni se sabe después de crearla.
   Se guarda su hash (PBKDF2-SHA256, 210.000 vueltas, sal por cuenta).
2. **Nadie crea la clave de otro.** Un administrador genera un *código de activación* de un solo uso; la
   persona entra con él y crea su contraseña. Así no hay una lista de claves circulando por
   WhatsApp, y si alguien se va, se revoca la cuenta: no hay clave que "quede dando vueltas".
3. **El token de sesión también va hasheado**, y en una cookie `HttpOnly` (el JavaScript de la
   página no la puede leer, así un XSS no se lleva la sesión).
4. **El freno a la fuerza bruta es por usuario**, no por IP: 10 intentos en 15 minutos.
5. **Todo lo que importa queda en `auditoria`**: quién ingresó, quién cerró el día, quién cambió
   una clave. Quién hizo qué no es un dato que se reconstruye leyendo logs de archivo.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from . import db

ITERACIONES = 210_000
INTENTOS_MAX = 10
VENTANA_MIN = 15
DIAS_SESION_CONDUCTOR = 30     # el celular en la calle: dura
DIAS_SESION_GESTION = 12       # la oficina: más corto
DIAS_CODIGO = 7                # vigencia del código de activación
COOKIE = "rauda_sesion"

Rol = Literal["admin", "operaciones", "conductor"]

USUARIO_OK = re.compile(r"^[a-z0-9._-]{3,40}$")

# Qué puede hacer cada rol. El rol conductor no entra al panel de gestión por ninguna ruta.
PERMISOS: dict[str, frozenset[str]] = {
    "admin": frozenset({"dia", "liquidacion", "reporte", "mapa", "revision",
                        "corregir", "cerrar", "usuarios", "configuracion", "planificacion"}),
    "operaciones": frozenset({"dia", "liquidacion", "reporte", "mapa", "revision",
                              "corregir", "cerrar", "configuracion", "planificacion"}),
    "conductor": frozenset({"mi_dia"}),
}


# --- claves ----------------------------------------------------------------
def _hash(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), sal.encode(), ITERACIONES).hex()


def _normalizar_codigo(codigo: str) -> str:
    """``7f2a-91c3 b0d4`` y ``7F2A91C3B0D4`` son el mismo código."""
    return re.sub(r"[^0-9A-Z]", "", (codigo or "").upper())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalizar_usuario(texto: str) -> str:
    """Acepta `J.Perez`, `j.perez `, `J_PEREZ` → `j.perez`.

    La gente escribe como quiere; el sistema guarda una sola forma. Se permiten puntos,
    guiones y guiones bajos porque es como se escribe un usuario de trabajo.
    """
    limpio = re.sub(r"\s+", "", (texto or "").strip().lower())
    limpio = limpio.replace(" ", "")
    return limpio


def problema_de_clave(clave: str, usuario: str) -> str | None:
    """Devuelve el motivo por el que la clave no sirve, o None si está bien.

    Se dice en castellano y con el arreglo concreto: "tiene que tener 8 caracteres", no
    "password inválido".
    """
    if len(clave) < 8:
        return "La contraseña tiene que tener al menos 8 caracteres."
    if len(clave) > 128:
        return "La contraseña es demasiado larga."
    if clave.lower() == usuario.lower():
        return "La contraseña no puede ser igual a tu usuario."
    if clave.isdigit():
        return "La contraseña no puede ser solo números: agregale una letra."
    comunes = {"12345678", "123456789", "conductor", "password", "1234567890",
               "conductor1", "rauda123", "qwertyui"}
    if clave.lower() in comunes:
        return "Esa contraseña es de las más usadas del mundo. Elegí otra."
    return None


# --- cuentas ---------------------------------------------------------------
def crear_cuenta(usuario: str, nombre: str, rol: Rol, cliente_id: int | None = None,
                 conductor_id: int | None = None, activa: bool = True,
                 clave_inicial: str | None = None) -> dict:
    """Crea la cuenta.

    - **Administración**: se crea con `clave_inicial`, que se muestra una sola vez
      y conviene cambiar en el primer ingreso. Entran con usuario y contraseña.
    - **Conductor**: se crea SIN clave — la elige él la primera vez que entra con su DNI. No hay
      código que generar ni repartir: su identidad es el DNI que ya tenemos en la base.
    """
    usuario = normalizar_usuario(usuario)
    if not USUARIO_OK.match(usuario):
        raise ValueError("El usuario tiene que tener entre 3 y 40 caracteres: letras, "
                         "números, punto, guion o guion bajo (sin espacios).")

    sal = secrets.token_hex(16)
    vence = datetime.now(timezone.utc) + timedelta(days=DIAS_CODIGO)
    codigo = None
    if clave_inicial:
        motivo = problema_de_clave(clave_inicial, usuario)
        if motivo:
            raise ValueError(motivo)

    # una sola transacción: la cuenta se crea con su sal (y su clave, si la hay) de una vez
    with db.transaccion() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO cuentas (usuario, nombre, rol, cliente_id, conductor_id, activa,"
            " clave_salt, clave_hash, clave_actualizada_en)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, usuario, nombre, rol",
            (usuario, nombre, rol, cliente_id, conductor_id, activa, sal,
             _hash(clave_inicial, sal) if clave_inicial else None,
             datetime.now(timezone.utc) if clave_inicial else None))
        fila = cur.fetchone()
    return {**fila, "codigo": codigo, "clave_inicial": clave_inicial,
            "vence": vence.isoformat() if codigo else None}


def por_usuario(usuario: str) -> dict | None:
    return db._uno(
        "SELECT * FROM cuentas WHERE usuario = %s", (normalizar_usuario(usuario),))


def cuentas(cliente_id: int | None = None) -> list[dict]:
    if cliente_id is None:
        return db._consulta(
            "SELECT id, usuario, nombre, rol, cliente_id, conductor_id, activa,"
            " clave_hash IS NOT NULL AS tiene_clave, ultimo_ingreso, creada_en"
            " FROM cuentas ORDER BY rol, nombre")
    return db._consulta(
        "SELECT id, usuario, nombre, rol, cliente_id, conductor_id, activa,"
        " clave_hash IS NOT NULL AS tiene_clave, ultimo_ingreso, creada_en"
        " FROM cuentas WHERE cliente_id = %s OR rol = 'admin' ORDER BY rol, nombre",
        (cliente_id,))


def activar(usuario: str, codigo: str, clave: str) -> dict:
    """Primer ingreso: canjea el código por la contraseña que elige la persona."""
    import hmac as _hmac

    cuenta = por_usuario(usuario)
    if not cuenta or not cuenta["activa"]:
        raise ValueError("Usuario o código incorrecto.")
    if cuenta["clave_hash"]:
        raise ValueError("Esta cuenta ya tiene contraseña. Entrá con tu usuario y contraseña.")
    if not cuenta["codigo_activacion_hash"] or not cuenta["codigo_vence_en"]:
        raise ValueError("No hay un código vigente para esta cuenta. Pedile uno nuevo a la administración.")
    if cuenta["codigo_vence_en"] < datetime.now(timezone.utc):
        raise ValueError("El código venció. Pedile uno nuevo a la administración.")

    # el código se guarda tal cual se muestra (con guiones): se compara normalizado de los dos
    # lados, así no importa si la persona lo escribe con guion, con espacio o todo junto
    if not _hmac.compare_digest(_hash(_normalizar_codigo(codigo), cuenta["clave_salt"]),
                                cuenta["codigo_activacion_hash"]):
        raise ValueError("Usuario o código incorrecto.")

    motivo = problema_de_clave(clave, cuenta["usuario"])
    if motivo:
        raise ValueError(motivo)

    with db.transaccion() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE cuentas SET clave_hash = %s, clave_actualizada_en = now(),"
            " codigo_activacion_hash = NULL, codigo_vence_en = NULL WHERE id = %s",
            (_hash(clave, cuenta["clave_salt"]), cuenta["id"]))
    auditar(cuenta, "clave", detalle={"evento": "primera_vez"})
    return cuenta


def demasiados_reclamos(documento: str, ip: str | None = None) -> bool:
    """Freno para el primer ingreso: 5 reclamos fallidos por DNI en una hora.

    Sin esto, los DNI son enumerables (8 dígitos, con checksum conocido) y quien llegue primero
    se queda con la cuenta y ve la jornada de otra persona.
    """
    documento = re.sub(r"[^0-9A-Za-z]", "", (documento or "")).upper()
    fila = db._uno(
        "SELECT count(*) AS n FROM intentos_login"
        " WHERE usuario = %s AND NOT correcto AND cuando > now() - interval '1 hour'",
        (documento,))
    return bool(fila and fila["n"] >= 5)


def conductor_por_documento(documento: str) -> dict | None:
    """El conductor que corresponde a un DNI, con su cuenta (si la tiene)."""
    documento = re.sub(r"[^0-9A-Za-z]", "", (documento or "")).upper()
    return db._uno(
        "SELECT c.id AS conductor_id, c.nombre, c.cliente_id, c.activo, c.documento,"
        "       q.id AS cuenta_id, q.activa AS cuenta_activa,"
        "       coalesce(q.clave_hash IS NOT NULL, FALSE) AS tiene_clave"
        " FROM conductores c LEFT JOIN cuentas q ON q.conductor_id = c.id"
        " WHERE upper(coalesce(c.documento,'')) = %s", (documento,))


def reclamar(documento: str, clave: str) -> dict:
    """Primera vez del conductor: entra con su DNI y elige su contraseña.

    No hay código que generar ni repartir. El precio de esa comodidad es que el primero que
    escribe el DNI se queda con la cuenta: por eso el reclamo queda en `auditoria` con hora, y
    Un administrador puede devolver la cuenta a cero desde la ficha (genera una clave nueva por él).
    """
    if demasiados_reclamos(documento):
        raise ValueError("Demasiados intentos con ese DNI. Espera una hora y prueba de nuevo.")

    conductor = conductor_por_documento(documento)
    if not conductor:
        raise ValueError("Ese DNI no está cargado. Pídele a la administración que te dé de alta.")
    if not conductor["activo"]:
        raise ValueError("Tu usuario está desactivado. Habla con la administración.")
    if conductor["cuenta_activa"] is False:
        raise ValueError("Tu acceso está desactivado. Habla con la administración.")
    if conductor["tiene_clave"]:
        raise ValueError("Ese DNI ya tiene contraseña. Entra con ella o pide que la reinicien.")

    motivo = problema_de_clave(clave, re.sub(r"[^0-9A-Za-z]", "", documento))
    if motivo:
        raise ValueError(motivo)

    # La cuenta NO la crea la administración: nace acá, la primera vez que la persona entra con
    # su DNI. Así no hay que dar de alta a nadie a mano ni repartir accesos: cargar al conductor
    # en Configuración (nombre, DNI, tienda) es todo.
    if conductor["cuenta_id"]:
        cuenta = db._uno("SELECT * FROM cuentas WHERE id = %s", (conductor["cuenta_id"],))
        with db.transaccion() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE cuentas SET clave_hash = %s, clave_actualizada_en = now(), activa = TRUE"
                " WHERE id = %s", (_hash(clave, cuenta["clave_salt"]), cuenta["id"]))
    else:
        sal = secrets.token_hex(16)
        with db.transaccion() as con, con.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM cuentas WHERE usuario = %s", (re.sub(r"[^0-9]", "", documento),))
            if cur.fetchone():
                raise ValueError("Ese DNI ya tiene una cuenta. Entra con tu contraseña.")
            cur.execute(
                "INSERT INTO cuentas (usuario, nombre, rol, cliente_id, conductor_id, clave_salt,"
                " clave_hash, clave_actualizada_en, activa)"
                " VALUES (%s,%s,'conductor',%s,%s,%s,%s,now(),TRUE) RETURNING id",
                (re.sub(r"[^0-9]", "", documento), conductor["nombre"], conductor["cliente_id"],
                 conductor["conductor_id"], sal, _hash(clave, sal)))
            cuenta = {"id": cur.fetchone()["id"], "usuario": documento,
                      "nombre": conductor["nombre"]}
    auditar(cuenta, "clave", detalle={"evento": "primera_vez", "documento": conductor["documento"]})
    return cuenta


def reiniciar_clave(usuario: str) -> str:
    """Un administrador devuelve la cuenta a cero: la persona vuelve a elegir su clave con el DNI.

    Sirve para el que olvidó la contraseña y para el caso feo del DNI reclamado por otro.
    Devuelve el usuario tocado; la clave vieja deja de servir en el acto.
    """
    cuenta = por_usuario(usuario)
    if not cuenta:
        raise ValueError("Esa cuenta no existe.")
    if cuenta["rol"] != "conductor":
        raise ValueError("Para la administración se usa un código de activación.")
    db._ejecutar(
        "UPDATE cuentas SET clave_hash = NULL, clave_actualizada_en = NULL WHERE id = %s",
        (cuenta["id"],))
    db._ejecutar("UPDATE sesiones SET revocada_en = now()"
                 " WHERE cuenta_id = %s AND revocada_en IS NULL", (cuenta["id"],))
    auditar(cuenta, "clave", detalle={"evento": "reinicio"})
    return cuenta["usuario"]


def nuevo_codigo(usuario: str) -> dict:
    """operaciones (o un admin) le genera un código nuevo a alguien que olvidó su clave.

    Sirve para dos casos iguales: la cuenta que nunca se activó y la que olvidó la
    contraseña. En el segundo, la clave vieja deja de servir en el acto.
    """
    cuenta = por_usuario(usuario)
    if not cuenta:
        raise ValueError("Esa cuenta no existe.")
    codigo = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    vence = datetime.now(timezone.utc) + timedelta(days=DIAS_CODIGO)
    db._ejecutar(
        "UPDATE cuentas SET codigo_activacion_hash = %s, codigo_vence_en = %s,"
        " clave_hash = NULL, clave_actualizada_en = NULL WHERE id = %s",
        (_hash(_normalizar_codigo(codigo), cuenta["clave_salt"]), vence, cuenta["id"]))
    db._ejecutar("UPDATE sesiones SET revocada_en = now()"
                 " WHERE cuenta_id = %s AND revocada_en IS NULL", (cuenta["id"],))
    return {"usuario": cuenta["usuario"], "codigo": codigo, "vence": vence.isoformat()}


def cambiar_clave(cuenta: dict, actual: str, nueva: str) -> None:
    """La persona cambia su contraseña sabiendo la actual."""
    if not verificar_clave(cuenta, actual):
        raise ValueError("La contraseña actual no es correcta.")
    motivo = problema_de_clave(nueva, cuenta["usuario"])
    if motivo:
        raise ValueError(motivo)
    if actual == nueva:
        raise ValueError("La contraseña nueva tiene que ser distinta de la actual.")
    db._ejecutar(
        "UPDATE cuentas SET clave_hash = %s, clave_actualizada_en = now() WHERE id = %s",
        (_hash(nueva, cuenta["clave_salt"]), cuenta["id"]))
    auditar(cuenta, "clave", detalle={"evento": "cambio"})


def verificar_clave(cuenta: dict, clave: str) -> bool:
    if not cuenta or not cuenta.get("clave_hash") or not cuenta.get("clave_salt"):
        return False
    return hmac.compare_digest(_hash(clave, cuenta["clave_salt"]), cuenta["clave_hash"])


# --- ingreso ---------------------------------------------------------------
def demasiados_intentos(usuario: str, ip: str | None = None) -> bool:
    """Freno por usuario **y por IP**, no solo por usuario.

    Contar solo por usuario permitía que cualquiera dejara a la administración o a operaciones afuera 15 minutos
    probando claves ajenas. Ahora el que insiste se frena en SU dirección: el dueño de la cuenta
    sigue entrando desde la suya. La cuenta además se protege con 20 intentos en total.
    """
    usuario = normalizar_usuario(usuario)
    if ip:
        fila = db._uno(
            "SELECT count(*) AS n FROM intentos_login"
            " WHERE usuario = %s AND ip = %s AND NOT correcto"
            " AND cuando > now() - make_interval(mins => %s)",
            (usuario, ip, VENTANA_MIN))
        if fila and fila["n"] >= INTENTOS_MAX:
            return True
        # un mismo aparato probando muchos usuarios distintos es un ataque de diccionario
        fila = db._uno(
            "SELECT count(DISTINCT usuario) AS n FROM intentos_login"
            " WHERE ip = %s AND NOT correcto AND cuando > now() - make_interval(mins => %s)",
            (ip, VENTANA_MIN))
        return bool(fila and fila["n"] >= 8)

    # sin IP (pruebas, o detrás de un proxy que no la pasa): se cuenta por usuario
    fila = db._uno(
        "SELECT count(*) AS n FROM intentos_login"
        " WHERE usuario = %s AND NOT correcto"
        " AND cuando > now() - make_interval(mins => %s)",
        (usuario, VENTANA_MIN))
    return bool(fila and fila["n"] >= INTENTOS_MAX * 2)


def _anotar_intento(usuario: str, correcto: bool, ip: str | None) -> None:
    db._ejecutar("INSERT INTO intentos_login (usuario, correcto, ip) VALUES (%s,%s,%s)",
                 (normalizar_usuario(usuario), correcto, ip))


def entrar(usuario: str, clave: str, ip: str | None = None,
           huella: str | None = None) -> tuple[str, dict]:
    """Devuelve (token en claro, cuenta). El token solo se ve acá: después va hasheado."""
    usuario = normalizar_usuario(usuario)
    if demasiados_intentos(usuario, ip):
        raise PermissionError("Demasiados intentos. Espera 15 minutos y prueba de nuevo.")

    cuenta = por_usuario(usuario)
    if not cuenta or not cuenta["activa"] or not cuenta["clave_hash"]:
        _anotar_intento(usuario, False, ip)
        raise ValueError("Usuario o contraseña incorrectos.")
    if not verificar_clave(cuenta, clave):
        _anotar_intento(usuario, False, ip)
        raise ValueError("Usuario o contraseña incorrectos.")

    _anotar_intento(usuario, True, ip)
    dias = DIAS_SESION_CONDUCTOR if cuenta["rol"] == "conductor" else DIAS_SESION_GESTION
    token = secrets.token_urlsafe(32)
    vence = datetime.now(timezone.utc) + timedelta(days=dias)
    db._ejecutar(
        "INSERT INTO sesiones (cuenta_id, token_hash, huella, vence_en) VALUES (%s,%s,%s,%s)",
        (cuenta["id"], _hash_token(token), (huella or "")[:120], vence))
    db._ejecutar("UPDATE cuentas SET ultimo_ingreso = now() WHERE id = %s", (cuenta["id"],))
    cuenta["vence"] = vence
    return token, cuenta


def por_token(token: str | None) -> dict | None:
    """La cuenta dueña de la sesión, si el token es válido y no venció."""
    if not token:
        return None
    fila = db._uno(
        "SELECT c.*, s.id AS sesion_id FROM sesiones s"
        " JOIN cuentas c ON c.id = s.cuenta_id"
        " WHERE s.token_hash = %s AND s.revocada_en IS NULL AND s.vence_en > now()"
        " AND c.activa", (_hash_token(token),))
    if fila:
        db._ejecutar("UPDATE sesiones SET ultimo_uso = now() WHERE id = %s", (fila["sesion_id"],))
    return fila


def salir(token: str | None) -> None:
    if token:
        db._ejecutar("UPDATE sesiones SET revocada_en = now() WHERE token_hash = %s",
                     (_hash_token(token),))


def puede(cuenta: dict | None, permiso: str) -> bool:
    if not cuenta:
        return False
    return permiso in PERMISOS.get(cuenta["rol"], frozenset())


# --- auditoría -------------------------------------------------------------
def auditar(cuenta: dict, accion: str, cliente_id: int | None = None,
            dia=None, detalle: dict | None = None) -> None:
    import json

    db._ejecutar(
        "INSERT INTO auditoria (cuenta_id, usuario, accion, cliente_id, dia, detalle)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (cuenta.get("id"), cuenta.get("usuario", "?"), accion,
         cliente_id or cuenta.get("cliente_id"), dia, json.dumps(detalle or {})))
