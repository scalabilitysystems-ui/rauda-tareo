"""Configuración de la operación, hecha por ellos.

La regla que ordena este módulo: **lo que se puede configurar no se pide por teléfono**. Tiendas,
conductores, tarifa y planificación del día se cargan desde el panel, con las mismas validaciones
que usaría un script — pero sin que nadie de afuera toque la base.

Qué se valida acá y por qué:
  · El DNI, porque es el usuario con el que entra el conductor: 8 números o no hay cuenta.
  · Las coordenadas, porque la geocerca decide si "llegó a la tienda": un punto en Madrid o un
    cero, cero hace que todos lleguen lejos.
  · El radio, porque con 5.000 m cualquiera "llega" desde la otra cuadra.
  · La tarifa con fecha de vigencia, porque cambiarla no puede reescribir lo ya pagado.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from . import auth, db

LIMA = timezone(timedelta(hours=-5))

# Perú: lat entre -18 y 0, lon entre -82 y -68 (mismo rango que valida la API del conductor).
LAT_MIN, LAT_MAX = -18.5, 0.5
LON_MIN, LON_MAX = -82.0, -68.0


def hoy_lima() -> date:
    return datetime.now(LIMA).date()


# --- validaciones -----------------------------------------------------------
def revisar_dni(documento: str) -> str:
    limpio = re.sub(r"[^0-9]", "", documento or "")
    if len(limpio) != 8:
        raise ValueError("El DNI tiene que tener 8 números.")
    return limpio


def revisar_tienda(codigo: str, lat: float | None, lon: float | None,
                   radio_m: int | None) -> tuple[str, float, float, int]:
    codigo = (codigo or "").strip().upper()
    if not codigo:
        raise ValueError("La tienda necesita un código (es lo que aparece en el tareo).")
    if not re.match(r"^[A-Z0-9][A-Z0-9._-]{1,20}$", codigo):
        raise ValueError("El código puede tener letras, números, punto, guion o guion bajo.")
    if lat is None or lon is None:
        raise ValueError("Falta la ubicación: marcá el punto en el mapa.")
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        raise ValueError("El punto está fuera de Perú. Marcá la tienda en el mapa.")
    radio = int(radio_m or 200)
    if not (50 <= radio <= 2000):
        raise ValueError("El radio tiene que estar entre 50 y 2000 metros.")
    return codigo, lat, lon, radio


# --- clientes ---------------------------------------------------------------
def clientes() -> list[dict]:
    """Todos los clientes, con lo que tienen adentro (para no tener que abrirlos uno por uno)."""
    return db._consulta(
        "SELECT c.id, c.nombre, c.activo, c.es_demo,"
        "       tarifa_del_dia(c.id, (now() AT TIME ZONE 'America/Lima')::date) AS tarifa_hoy,"
        "       (SELECT count(*) FROM grupos g WHERE g.cliente_id = c.id AND g.activo) AS tiendas,"
        "       (SELECT count(*) FROM conductores k WHERE k.cliente_id = c.id AND k.activo)"
        "           AS conductores"
        " FROM clientes c WHERE NOT c.es_demo ORDER BY c.activo DESC, c.nombre")


def guardar_cliente(nombre: str, tarifa: float | None, cliente_id: int | None = None,
                    activo: bool = True, autor: str = "") -> int:
    """Alta o edición de un cliente. Si es nuevo, nace con su primera tarifa."""
    nombre = (nombre or "").strip()
    if len(nombre) < 2:
        raise ValueError("El cliente necesita un nombre.")

    with db.transaccion() as con, con.cursor() as cur:
        cur.execute("SELECT id FROM clientes WHERE upper(nombre) = upper(%s) AND id <> %s",
                    (nombre, cliente_id or -1))
        if cur.fetchone():
            raise ValueError(f"Ya hay un cliente llamado {nombre}.")

        if cliente_id:
            cur.execute("UPDATE clientes SET nombre = %s, activo = %s WHERE id = %s",
                        (nombre, activo, cliente_id))
            nuevo = cliente_id
        else:
            cur.execute(
                "INSERT INTO clientes (nombre, tarifa_pedido, es_demo, activo)"
                " VALUES (%s,%s,FALSE,TRUE) RETURNING id",
                (nombre, round(float(tarifa or 0), 2)))
            nuevo = cur.fetchone()["id"]

        if tarifa is not None:
            if not (0 <= float(tarifa) <= 1000):
                raise ValueError("La tarifa tiene que estar entre 0 y 1000 soles.")
            cur.execute(
                "INSERT INTO tarifas_cliente (cliente_id, tarifa, desde, creada_por)"
                " VALUES (%s,%s,(now() AT TIME ZONE 'America/Lima')::date,%s)"
                " ON CONFLICT (cliente_id, desde) DO UPDATE SET tarifa = EXCLUDED.tarifa",
                (nuevo, round(float(tarifa), 2), autor or None))
            cur.execute("UPDATE clientes SET tarifa_pedido = %s WHERE id = %s",
                        (round(float(tarifa), 2), nuevo))
    return nuevo


def mover_conductor(conductor_id: int, cliente_id: int, grupo_id: str | None) -> None:
    """Un conductor puede cambiar de cliente (y de tienda) sin perder su historia.

    Se mueve el vínculo, no los hechos: las jornadas y liquidaciones viejas quedan donde
    ocurrieron. La cuenta lo sigue, porque su DNI sigue siendo su usuario.
    """
    if grupo_id:
        tienda = db._uno("SELECT cliente_id FROM grupos WHERE grupo_id = %s", (grupo_id,))
        if not tienda or tienda["cliente_id"] != cliente_id:
            raise ValueError("Esa tienda no pertenece al cliente elegido.")

    with db.transaccion() as con, con.cursor() as cur:
        cur.execute("UPDATE conductores SET cliente_id = %s, grupo_id = %s WHERE id = %s",
                    (cliente_id, grupo_id, conductor_id))
        cur.execute("UPDATE cuentas SET cliente_id = %s WHERE conductor_id = %s",
                    (cliente_id, conductor_id))


# --- operación (el cliente) -------------------------------------------------
def operacion(cliente_id: int | None = None) -> dict | None:
    """El cliente que se está configurando.

    Si se pide uno concreto, ese. Si no, el primero activo (y si no hay, el de prueba, para que
    la pantalla no se rompa mientras se arma la operación).
    """
    if cliente_id:
        return db._uno(
            "SELECT c.*, tarifa_del_dia(c.id, (now() AT TIME ZONE 'America/Lima')::date)"
            " AS tarifa_hoy FROM clientes c WHERE c.id = %s", (cliente_id,))
    fila = db._uno(
        "SELECT c.*, tarifa_del_dia(c.id, (now() AT TIME ZONE 'America/Lima')::date) AS tarifa_hoy"
        " FROM clientes c WHERE c.activo AND NOT c.es_demo ORDER BY c.id LIMIT 1")
    if fila:
        return fila
    return db._uno(
        "SELECT c.*, tarifa_del_dia(c.id, (now() AT TIME ZONE 'America/Lima')::date) AS tarifa_hoy"
        " FROM clientes c WHERE c.activo ORDER BY c.id LIMIT 1")


def guardar_tarifa(cliente_id: int, tarifa: float, desde: date, autor: str) -> None:
    """Carga una tarifa que rige desde una fecha. Lo anterior no se toca."""
    if tarifa is None or tarifa < 0 or tarifa > 1000:
        raise ValueError("La tarifa tiene que estar entre 0 y 1000 soles.")
    with db.transaccion() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO tarifas_cliente (cliente_id, tarifa, desde, creada_por)"
            " VALUES (%s,%s,%s,%s)"
            " ON CONFLICT (cliente_id, desde) DO UPDATE SET tarifa = EXCLUDED.tarifa,"
            " creada_por = EXCLUDED.creada_por",
            (cliente_id, round(float(tarifa), 2), desde, autor))
        # `clientes.tarifa_pedido` queda como espejo de la última: hay pantallas viejas que la leen
        cur.execute(
            "UPDATE clientes SET tarifa_pedido ="
            " (SELECT tarifa FROM tarifas_cliente WHERE cliente_id = %s"
            "   ORDER BY desde DESC LIMIT 1) WHERE id = %s", (cliente_id, cliente_id))


def guardar_tolerancia(cliente_id: int, minutos: int) -> None:
    """Minutos a partir de los cuales se marca tardanza. Es una decisión del negocio, no del código."""
    minutos = int(minutos)
    if not (0 <= minutos <= 120):
        raise ValueError("La tolerancia tiene que estar entre 0 y 120 minutos.")
    db._ejecutar("UPDATE clientes SET minutos_tolerancia = %s WHERE id = %s",
                 (minutos, cliente_id))


def tarifas(cliente_id: int) -> list[dict]:
    return db._consulta(
        "SELECT tarifa, desde, creada_por, creada_en FROM tarifas_cliente"
        " WHERE cliente_id = %s ORDER BY desde DESC", (cliente_id,))


# --- tiendas ----------------------------------------------------------------
def tiendas(cliente_id: int | None = None) -> list[dict]:
    base = (
        "SELECT g.grupo_id, g.cliente_id, g.etiqueta, g.codigo_tienda, g.zona, g.supervisor,"
        "       g.lat, g.lon, g.radio_m, g.activo,"
        "       (SELECT count(*) FROM conductores c"
        "         WHERE c.grupo_id = g.grupo_id AND c.activo) AS conductores"
        " FROM grupos g")
    if cliente_id:
        return db._consulta(base + " WHERE g.cliente_id = %s ORDER BY g.codigo_tienda",
                            (cliente_id,))
    return db._consulta(base + " ORDER BY g.cliente_id, g.codigo_tienda")


def guardar_tienda(cliente_id: int, codigo: str, direccion: str, zona: str, supervisor: str,
                   lat: float | None, lon: float | None, radio_m: int | None,
                   anterior: str | None = None) -> str:
    codigo, lat, lon, radio = revisar_tienda(codigo, lat, lon, radio_m)
    grupo_id = f"tienda:{codigo.lower()}@rauda"

    with db.transaccion() as con, con.cursor() as cur:
        # El código no se puede repetir: dos tiendas con el mismo código rompen el tareo.
        cur.execute("SELECT grupo_id FROM grupos WHERE codigo_tienda = %s AND grupo_id <> %s"
                    " AND cliente_id = %s", (codigo, grupo_id, cliente_id))
        if cur.fetchone():
            raise ValueError(f"Ya hay una tienda con el código {codigo}.")

        cur.execute(
            "INSERT INTO grupos (grupo_id, cliente_id, etiqueta, codigo_tienda, zona, supervisor,"
            " lat, lon, radio_m) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (grupo_id) DO UPDATE SET etiqueta = EXCLUDED.etiqueta,"
            " codigo_tienda = EXCLUDED.codigo_tienda, zona = EXCLUDED.zona,"
            " supervisor = EXCLUDED.supervisor, lat = EXCLUDED.lat, lon = EXCLUDED.lon,"
            " radio_m = EXCLUDED.radio_m",
            (grupo_id, cliente_id, direccion.strip() or codigo, codigo, zona.strip() or None,
             supervisor.strip() or None, lat, lon, radio))

    return grupo_id


def borrar_tienda(grupo_id: str) -> None:
    """Una tienda con conductores asignados no se borra: se le quita la gente primero."""
    gente = db._uno("SELECT count(*) AS n FROM conductores WHERE grupo_id = %s", (grupo_id,))
    if gente and gente["n"]:
        raise ValueError(f"Esa tienda tiene {gente['n']} conductor(es) asignados. "
                         f"Cambiales la tienda antes de borrarla.")
    db._ejecutar("UPDATE grupos SET activo = FALSE WHERE grupo_id = %s", (grupo_id,))


# --- conductores ------------------------------------------------------------
def conductores(cliente_id: int | None = None) -> list[dict]:
    base = (
        "SELECT c.id, c.documento, c.nombre, c.telefono, c.activo, c.grupo_id,"
        "       g.codigo_tienda, g.zona, c.documento_confirmado,"
        "       q.usuario, coalesce(q.activa, TRUE) AS cuenta_activa,"
        "       coalesce(q.clave_hash IS NOT NULL, FALSE) AS tiene_clave"
        " FROM conductores c"
        " LEFT JOIN grupos g ON g.grupo_id = c.grupo_id"
        " LEFT JOIN cuentas q ON q.conductor_id = c.id")
    if cliente_id:
        return db._consulta(base + " WHERE c.cliente_id = %s ORDER BY c.nombre", (cliente_id,))
    return db._consulta(base + " ORDER BY c.nombre")


def guardar_conductor(cliente_id: int, documento: str, nombre: str, telefono: str,
                      grupo_id: str | None, conductor_id: int | None = None) -> int:
    documento = revisar_dni(documento)
    nombre = (nombre or "").strip()
    if len(nombre) < 4:
        raise ValueError("Falta el nombre completo del conductor.")

    with db.transaccion() as con, con.cursor() as cur:
        # El DNI es único en toda la base: es el usuario con el que entra.
        cur.execute("SELECT id FROM conductores WHERE documento = %s AND id <> %s",
                    (documento, conductor_id or -1))
        otro = cur.fetchone()
        if otro:
            raise ValueError(f"Ese DNI ya está cargado (conductor {otro['id']}).")

        if conductor_id:
            cur.execute(
                "UPDATE conductores SET documento = %s, nombre = %s, telefono = %s,"
                " grupo_id = %s, documento_confirmado = TRUE WHERE id = %s",
                (documento, nombre, telefono or None, grupo_id, conductor_id))
            nuevo = conductor_id
        else:
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, telefono, grupo_id,"
                " documento_confirmado, activo) VALUES (%s,%s,%s,%s,%s,TRUE,TRUE) RETURNING id",
                (cliente_id, documento, nombre, telefono or None, grupo_id))
            nuevo = cur.fetchone()["id"]

        # La cuenta nace con el DNI como usuario. La contraseña la elige él la primera vez.
        cur.execute("SELECT id, usuario FROM cuentas WHERE conductor_id = %s", (nuevo,))
        cuenta = cur.fetchone()
        if cuenta is None:
            cur.execute("SELECT 1 FROM cuentas WHERE usuario = %s", (documento,))
            if cur.fetchone():
                raise ValueError(f"Ya existe una cuenta con el usuario {documento}: "
                                 f"revisá si ese DNI está cargado dos veces.")
            cur.execute(
                "INSERT INTO cuentas (usuario, nombre, rol, cliente_id, conductor_id,"
                " clave_salt, activa) VALUES (%s,%s,'conductor',%s,%s,%s,TRUE)",
                (documento, nombre, cliente_id, nuevo, __import__("secrets").token_hex(16)))
        else:
            cur.execute("UPDATE cuentas SET usuario = %s, nombre = %s WHERE id = %s",
                        (documento, nombre, cuenta["id"]))

    return nuevo


def activar_conductor(conductor_id: int, activo: bool) -> None:
    db._ejecutar("UPDATE conductores SET activo = %s WHERE id = %s", (activo, conductor_id))
    db._ejecutar("UPDATE cuentas SET activa = %s WHERE conductor_id = %s",
                 (activo, conductor_id))
    if not activo:
        db._ejecutar("UPDATE sesiones SET revocada_en = now() WHERE revocada_en IS NULL"
                     " AND cuenta_id IN (SELECT id FROM cuentas WHERE conductor_id = %s)",
                     (conductor_id,))


# --- planificación del día ---------------------------------------------------
def programar(cliente_id: int, dia: date, conductor_ids: list[int],
              pedidos_por_conductor: int | None = None) -> int:
    """Arma el plantel del día: quién trabaja y cuántos pedidos tiene previstos.

    Sin esta lista, el sistema no puede decir "no trabajó": solo ve a los que cargaron algo.
    """
    if not conductor_ids:
        return 0
    with db.transaccion() as con, con.cursor() as cur:
        for conductor_id in conductor_ids:
            cur.execute(
                "INSERT INTO programado (conductor_id, dia, pedidos_programados, cliente_id)"
                " VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (conductor_id, dia) DO UPDATE SET"
                " pedidos_programados = COALESCE(EXCLUDED.pedidos_programados,"
                "                                programado.pedidos_programados)",
                (conductor_id, dia, pedidos_por_conductor, cliente_id))
    return len(conductor_ids)


def quitar_del_dia(conductor_id: int, dia: date) -> None:
    db._ejecutar("DELETE FROM programado WHERE conductor_id = %s AND dia = %s",
                 (conductor_id, dia))


def plantel(cliente_id: int, dia: date) -> list[dict]:
    return db._consulta(
        "SELECT p.conductor_id, p.pedidos_programados, p.hora_planificada,"
        "       c.nombre, c.documento, g.codigo_tienda"
        " FROM programado p"
        " JOIN conductores c ON c.id = p.conductor_id"
        " LEFT JOIN grupos g ON g.grupo_id = c.grupo_id"
        " WHERE p.cliente_id = %s AND p.dia = %s ORDER BY c.nombre", (cliente_id, dia))
