"""Consultas del panel.

Todo lo que la pantalla muestra sale de una vista de la base: `vista_tareo_dia`,
`vista_liquidacion_dia`, `vista_resumen_dia` y `vista_controles_dia`. Acá no se calcula
ninguna regla de negocio — solo se consulta y se le da forma a lo que la plantilla necesita.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

RAIZ = Path(__file__).resolve().parent.parent
LIMA = timezone(timedelta(hours=-5))

ORDEN_ESTADO = "CASE estado WHEN 'falla' THEN 0 WHEN 'duda' THEN 1 WHEN 'ausente' THEN 2 ELSE 3 END"
ORDEN_CONTROL = ("CASE control WHEN 'captura' THEN 0 WHEN 'identificacion' THEN 1"
                 " WHEN 'numeros' THEN 2 WHEN 'completitud' THEN 3 ELSE 4 END")


def env(nombre: str, defecto: str | None = None) -> str:
    if nombre in os.environ:
        return os.environ[nombre]
    archivo = RAIZ / ".env"
    if archivo.exists():
        for linea in archivo.read_text().splitlines():
            if linea.startswith(nombre + "="):
                return linea.split("=", 1)[1].strip()
    if defecto is None:
        raise RuntimeError(f"falta {nombre} en ~/rauda/.env")
    return defecto


@contextmanager
def conexion():
    con = psycopg.connect(
        host=env("RAUDA_DB_HOST", "127.0.0.1"),
        port=env("RAUDA_DB_PORT", "5432"),
        dbname=env("RAUDA_DB_NAME", "rauda_dev"),
        user=env("RAUDA_DB_USER", "rauda_app"),
        password=env("RAUDA_DB_PASSWORD"),
        row_factory=dict_row,
    )
    try:
        yield con
        con.commit()      # psycopg no commitea solo al cerrar: si no, una escritura se pierde
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _consulta(sql: str, parametros: tuple = ()) -> list[dict]:
    with conexion() as con, con.cursor() as cur:
        cur.execute(sql, parametros)
        return cur.fetchall()


def _uno(sql: str, parametros: tuple = ()) -> dict | None:
    filas = _consulta(sql, parametros)
    return filas[0] if filas else None


def _ejecutar(sql: str, parametros: tuple = ()) -> int:
    """Para escrituras que no devuelven filas (UPDATE / DELETE)."""
    with conexion() as con, con.cursor() as cur:
        cur.execute(sql, parametros)
        return cur.rowcount


# --- catálogo ---------------------------------------------------------------
def clientes(incluir_demo: bool = False) -> list[dict]:
    """Los clientes que se pueden elegir en el panel.

    El cliente de demostración no aparece cuando ya hay una operación real cargada: en el
    piloto, la operación no tiene que ver datos inventados al lado de los suyos. Si no hay ningún
    cliente real, se muestra el demo (para poder seguir mostrando el sistema).
    """
    reales = _consulta(
        "SELECT id, nombre, tarifa_pedido, es_demo FROM clientes"
        " WHERE activo AND NOT es_demo ORDER BY nombre")
    if reales or not incluir_demo:
        return reales or _consulta(
            "SELECT id, nombre, tarifa_pedido, es_demo FROM clientes WHERE activo"
            " ORDER BY es_demo, nombre")
    return _consulta(
        "SELECT id, nombre, tarifa_pedido, es_demo FROM clientes WHERE activo"
        " ORDER BY es_demo, nombre")


def dias_con_datos(cliente_id: int) -> list[date]:
    return [r["dia"] for r in _consulta(
        "SELECT dia FROM vista_resumen_dia WHERE cliente_id=%s ORDER BY dia DESC",
        (cliente_id,))]


# --- el día -----------------------------------------------------------------
def _fila(f: dict) -> dict:
    f = dict(f)
    f["tienda"] = f.pop("codigo_tienda", None)
    motivos = (f.get("motivos") or "").strip()
    f["motivos"] = [m.strip() for m in motivos.split(" · ") if m.strip()]
    f["mensaje_por_campo"] = {
        "hora_ingreso": f.get("msg_ingreso"),
        "hora_salida": f.get("msg_salida"),
        "vueltas": f.get("msg_vueltas"),
        "pedidos": f.get("msg_pedidos"),
    }
    return f


def cargar_dia(cliente_id: int, dia: date) -> dict:
    filas = [_fila(f) for f in _consulta(
        f"SELECT * FROM vista_tareo_dia WHERE cliente_id=%s AND dia=%s"
        f" ORDER BY {ORDEN_ESTADO}, supervisor, nombre", (cliente_id, dia))]

    resumen = _uno(
        "SELECT * FROM vista_resumen_dia WHERE cliente_id=%s AND dia=%s", (cliente_id, dia))

    controles = _consulta(
        f"SELECT control, veredicto, resumen FROM vista_controles_dia"
        f" WHERE cliente_id=%s AND dia=%s ORDER BY {ORDEN_CONTROL}", (cliente_id, dia))

    mensajes = _consulta(
        "SELECT id, de_nombre, texto, hora FROM mensajes"
        " WHERE cliente_id=%s AND (hora AT TIME ZONE 'America/Lima')::date=%s ORDER BY hora",
        (cliente_id, dia))

    return {
        "dia": dia,
        "filas": filas,
        "resumen": resumen or {},
        "controles": controles,
        "mensajes": mensajes,
    }


def mensajes_de(filas: list[dict], conductor_id: int) -> list[dict]:
    """Los mensajes que respaldan la fila: los que el propio tareo declara como origen."""
    fila = next((f for f in filas if f["conductor_id"] == conductor_id), None)
    if not fila:
        return []
    ids = list(fila.get("mensaje_ids") or [])
    if not ids:
        return []
    return _consulta(
        "SELECT id, de_nombre, texto, hora FROM mensajes WHERE id = ANY(%s) ORDER BY hora", (ids,))


# --- liquidación ------------------------------------------------------------
def liquidacion_dia(cliente_id: int, dia: date) -> dict:
    filas = _consulta(
        f"SELECT * FROM vista_liquidacion_dia WHERE cliente_id=%s AND dia=%s"
        f" ORDER BY CASE estado WHEN 'pendiente' THEN 0 WHEN 'no_trabajo' THEN 2 ELSE 1 END,"
        f" supervisor, nombre", (cliente_id, dia))
    for f in filas:
        f["tienda"] = f.pop("codigo_tienda", None)
        f["motivo"] = f.get("motivos") or ""

    totales = _uno(
        "SELECT"
        " count(*) FILTER (WHERE estado = 'a_pagar')                      AS filas_a_pagar,"
        " coalesce(sum(pedidos) FILTER (WHERE estado = 'a_pagar'), 0)     AS pedidos_a_pagar,"
        " coalesce(sum(bruto)   FILTER (WHERE estado = 'a_pagar'), 0)     AS plata_a_pagar,"
        " count(*) FILTER (WHERE estado = 'pendiente')                    AS filas_pendientes,"
        " coalesce(sum(pedidos) FILTER (WHERE estado = 'pendiente'), 0)   AS pedidos_pendientes,"
        " coalesce(sum(pedidos * tarifa) FILTER (WHERE estado = 'pendiente'), 0) AS plata_pendiente,"
        " count(*) FILTER (WHERE estado = 'no_trabajo')                   AS no_trabajo,"
        " count(*) FILTER (WHERE coalesce(tardanza_min, 0) > 15)          AS tardanzas"
        " FROM vista_liquidacion_dia WHERE cliente_id=%s AND dia=%s", (cliente_id, dia))

    return {"filas": filas, **(totales or {})}


def cierre(cliente_id: int, dia: date) -> dict | None:
    """El día ya cerrado: quién lo cerró, cuándo y cuántas filas quedaron registradas."""
    return _uno(
        "SELECT cerrado_por, max(calculado_en) AS cuando, count(*) AS filas,"
        " sum(total) AS total, sum(pedidos_liquidados) AS pedidos"
        " FROM liquidaciones WHERE cliente_id=%s AND dia=%s GROUP BY cerrado_por",
        (cliente_id, dia))


def cerrar(cliente_id: int, dia: date, autor: str) -> None:
    """Cierra el día en una sola sentencia: la liquidación se copia desde la vista, así el
    número que se registra es exactamente el que se ve en pantalla."""
    with conexion() as con, con.cursor() as cur:
        cur.execute("DELETE FROM liquidaciones WHERE cliente_id=%s AND dia=%s", (cliente_id, dia))
        cur.execute(
            "INSERT INTO liquidaciones (cliente_id, dia, conductor_id, pedidos_liquidados,"
            " tarifa, bruto, descuento_tardanza, total, estado, bloqueo, cerrado_por)"
            " SELECT cliente_id, dia, conductor_id, coalesce(pedidos, 0), tarifa,"
            "        coalesce(bruto, 0), descuento_tardanza, coalesce(total, 0), estado,"
            "        nullif(motivos, ''), %s"
            " FROM vista_liquidacion_dia WHERE cliente_id=%s AND dia=%s",
            (autor, cliente_id, dia))
        con.commit()


def hora_local(dt) -> str:
    """Hora de Lima. Acepta datetime o el texto ISO que sale de `datos_para_js`."""
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LIMA).strftime("%H:%M")


# --- mapa -------------------------------------------------------------------
def mapa_dia(cliente_id: int, dia: date) -> dict:
    """Conductores del día con su último punto, su recorrido y la tienda.

    Los puntos salen de `vista_mapa_dia`: uno por "ya llegué" y uno por cada toque. El
    recorrido es la unión en orden de hora — no hay tracking continuo, así que la línea es
    lo que el conductor marcó, no el camino que hizo.
    """
    conductores = _consulta(
        "SELECT * FROM vista_mapa_dia WHERE cliente_id=%s AND dia=%s"
        " ORDER BY nombre", (cliente_id, dia))

    puntos = _consulta(
        "SELECT p.conductor_id, p.tipo, p.hora, p.lat, p.lon, p.precision_m, m.id AS marca_id"
        " FROM vista_mapa_puntos p"
        " LEFT JOIN marcas m ON m.id = p.marca_id"
        " WHERE p.cliente_id=%s AND p.dia=%s ORDER BY p.hora", (cliente_id, dia))

    por_conductor: dict[int, list] = {}
    for p in puntos:
        por_conductor.setdefault(p["conductor_id"], []).append(p)

    for c in conductores:
        c["puntos"] = por_conductor.get(c["conductor_id"], [])

    return {"conductores": [datos_para_js(c) for c in conductores], "puntos": puntos}


def datos_para_js(fila: dict) -> dict:
    """Deja la fila lista para `tojson`: fechas a ISO, Decimal a float.

    Jinja no sabe serializar `datetime` ni `Decimal`, y el error se ve como un 401 genérico
    si el render falla dentro de una ruta protegida. Convertir acá evita las dos cosas.
    """
    salida = {}
    for clave, valor in fila.items():
        if isinstance(valor, datetime):
            salida[clave] = valor.isoformat()
        elif isinstance(valor, Decimal):
            salida[clave] = float(valor)
        elif isinstance(valor, date):
            salida[clave] = valor.isoformat()
        elif isinstance(valor, (list, tuple)):
            salida[clave] = [datos_para_js(x) if isinstance(x, dict)
                             else (x.isoformat() if isinstance(x, datetime) else
                                   (float(x) if isinstance(x, Decimal) else x))
                             for x in valor]
        else:
            salida[clave] = valor
    return salida


@contextmanager
def transaccion():
    """Una transacción sola, para operaciones que necesitan varias sentencias juntas.

    Existe porque abrir una conexión por sentencia puede terminar en otra sesión de Postgres:
    lo que se escribió en una no lo ve la siguiente, y el INSERT queda visible y el UPDATE no.
    """
    with conexion() as con:
        yield con
