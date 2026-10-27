"""
Datos de demostración del lado de gestión (Rauda).

Por qué existe: la captura real todavía no tiene un solo mensaje (el número no está en
los grupos), y hay que poder mostrarle a la administración y a operaciones lo que van a usar. Los datos
son inventados y el cliente se llama "Cadena de ejemplo (DEMO)" a propósito: nunca se mezcla
con datos reales ni se confunde al mirar la pantalla.

Qué planta, además de los casos normales (para que se vea cómo se comporta el control):
  - un conductor con documento provisorio (Pro2025)  -> FALLA, no se puede liquidar
  - una conductora que no reportó                    -> ausente (no es duda)
  - un conductor que mandó mensaje sin interpretar    -> FALLA de completitud
  - una conductora con parcial y después completo     -> se resuelve por ÚLTIMO
  - un conductor con pedidos fuera de rango           -> DUDA de números
  - un conductor que reportó sin estar programado     -> aparece igual
  - un conductor que ingresó 22 minutos tarde         -> tardanza a la vista

Uso:  ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_demo_gestion.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

RAIZ = Path(__file__).resolve().parent.parent
DIA = date(2026, 10, 16)
CLIENTE = "Pruebas de gestión (DEMO)"
LIMA = timezone(timedelta(hours=-5))


def env(nombre: str, defecto: str | None = None) -> str:
    if nombre in os.environ:
        return os.environ[nombre]
    archivo = RAIZ / ".env"
    if archivo.exists():
        for linea in archivo.read_text().splitlines():
            if linea.startswith(nombre + "="):
                return linea.split("=", 1)[1].strip()
    if defecto is None:
        raise SystemExit(f"falta {nombre} en ~/rauda/.env")
    return defecto


def conectar():
    return psycopg.connect(
        host=env("RAUDA_DB_HOST", "127.0.0.1"),
        port=env("RAUDA_DB_PORT", "5432"),
        dbname=env("RAUDA_DB_NAME", "rauda_dev"),
        user=env("RAUDA_DB_USER", "rauda_app"),
        password=env("RAUDA_DB_PASSWORD"),
    )


def dni_demo(documento: str) -> str:
    """Un DNI de mentira que no choque con una persona real.

    Los documentos del plantel son únicos: si el demo los reusara, la base los rechazaría (y si no
    lo hiciera, dos personas compartirían cuenta). Se les pone el prefijo 99 y se recortan a 8:
    70012341 -> 99700123.
    """
    limpio = "".join(ch for ch in (documento or "") if ch.isdigit())
    if len(limpio) == 8:
        return "99" + limpio[:6]
    return documento          # el documento provisorio (Pro2025) se deja como está


def utc(hh: int, mm: int) -> datetime:
    """Hora de Lima -> instante UTC (los timestamps de WhatsApp vienen en UTC)."""
    return datetime(DIA.year, DIA.month, DIA.day, hh, mm, tzinfo=LIMA).astimezone(timezone.utc)


# --- El plantel del día -----------------------------------------------------
# (nombre, documento, confirmado, telefono, grupo, tienda, zona, supervisor,
#  planificado, hora_planificada, ingreso, salida, vueltas, pedidos, escenario)
PLANTEL = [
    ("FLORES MEDINA DANIEL", "70012341", True, "51987654321", "g-t-214", "T-214", "Lima Norte", "Rojas",
     42, "08:00", "07:56", "17:40", 3, 41, "ok"),
    ("CUSI TAPIA ROSA", "70123452", True, "51987654322", "g-t-214", "T-214", "Lima Norte", "Rojas",
     38, "08:00", "07:58", "17:12", 3, 38, "ok"),
    ("QUISPE CHAMBI JOSE", "70234563", True, "51987654323", "g-t-214", "T-214", "Lima Norte", "Rojas",
     45, "08:00", "08:22", "18:05", 3, 45, "tardanza"),
    ("BARBARAN PALACIOS ALVARO", "Pro2025", False, "51987654324", "g-t-214", "T-214", "Lima Norte", "Rojas",
     36, "08:00", "08:04", "16:58", 2, 33, "provisorio"),
    ("CHAVEZ ARIAS ROSA", "70456785", True, "51987654325", "g-t-214", "T-214", "Lima Norte", "Rojas",
     40, "08:00", None, None, None, None, "ausente"),
    ("SOTO CORDOVA LUIS", "70567896", True, "51987654326", "g-t-308", "T-308", "Lima Sur", "Quispe",
     44, "08:30", "08:26", None, None, None, "sin_interpretar"),
    ("MENDOZA CCAPA MARLENY", "70345674", True, "51987654327", "g-t-308", "T-308", "Lima Sur", "Quispe",
     40, "08:30", "08:29", "17:30", 3, 40, "duplicado"),
    ("VILCA RAMOS CESAR", "70678907", True, "51987654328", "g-t-308", "T-308", "Lima Sur", "Quispe",
     39, "08:30", "08:02", "17:20", 3, 180, "fuera_rango"),
    ("TTITO APAZA ELMER", "70789018", True, "51987654329", "g-t-308", "T-308", "Lima Sur", "Quispe",
     None, None, "09:10", "18:15", 2, 24, "no_programado"),
]

# El mensaje real que manda cada conductor (texto de WhatsApp, como llega).
MENSAJES = {
    "ok": "{n} pedidos, {v} vueltas. Salida {s}.",
    "tardanza": "Buenos días, ya ingresé. {v} vueltas, {n} pds. Salgo {s}",
    "provisorio": "cierro con {n} pedidos y {v} vueltas, salida {s}",
    "sin_interpretar": "ya terminé la ruta, mañana te paso el detalle",
    "duplicado": "Voy con {n} pedidos, {v} vueltas. Salida {s}",
    "fuera_rango": "listo {v} vueltas {n} pedidos salida {s}",
    "no_programado": "apoyo en T-308, {n} pedidos {v} vueltas, salida {s}",
}


def main() -> None:
    con = conectar()
    con.autocommit = False
    with con.cursor() as cur:
        # Empezar de cero para el cliente demo (idempotente).
        cur.execute("SELECT id FROM clientes WHERE nombre = %s", (CLIENTE,))
        fila = cur.fetchone()
        if fila:
            cid = fila[0]
            cur.execute(
                "DELETE FROM liquidaciones WHERE cliente_id=%s", (cid,))
            cur.execute(
                "DELETE FROM correcciones_admin WHERE cliente_id=%s", (cid,))
            cur.execute(
                "DELETE FROM dudas WHERE conductor_id IN (SELECT id FROM conductores WHERE cliente_id=%s)",
                (cid,))
            cur.execute(
                "DELETE FROM valores WHERE conductor_id IN (SELECT id FROM conductores WHERE cliente_id=%s)",
                (cid,))
            cur.execute(
                "DELETE FROM programado WHERE conductor_id IN (SELECT id FROM conductores WHERE cliente_id=%s)",
                (cid,))
            cur.execute(
                "DELETE FROM mensajes WHERE cliente_id=%s", (cid,))
            cur.execute("DELETE FROM conductores WHERE cliente_id=%s", (cid,))
            cur.execute("DELETE FROM grupos WHERE cliente_id=%s", (cid,))
            cur.execute("DELETE FROM chequeos WHERE dia=%s", (DIA,))
            cur.execute("DELETE FROM clientes WHERE id=%s", (cid,))

        cur.execute(
            "INSERT INTO clientes (nombre, tarifa_pedido, es_demo)"
            " VALUES (%s, 3.00, TRUE) RETURNING id",
            (CLIENTE,))
        cid = cur.fetchone()[0]
        # la tarifa vive en `tarifas_cliente` con vigencia: sin esta fila el día no liquida
        cur.execute(
            "INSERT INTO tarifas_cliente (cliente_id, tarifa, desde, creada_por)"
            " VALUES (%s, 3.00, DATE '2020-01-01', 'seed demo')"
            " ON CONFLICT (cliente_id, desde) DO NOTHING", (cid,))

        for grupo, tienda, zona, sup in [
            ("g-t-214", "T-214", "Lima Norte", "Rojas"),
            ("g-t-308", "T-308", "Lima Sur", "Quispe"),
        ]:
            cur.execute(
                "INSERT INTO grupos (grupo_id, cliente_id, etiqueta, codigo_tienda, zona, supervisor)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (f"{grupo}@g.us", cid, f"CadenaDemo {tienda}", tienda, zona, sup))

        for (nombre, doc, conf, tel, grupo, tienda, zona, sup, plan, hplan, ing,
             sal, vueltas, pedidos, caso) in PLANTEL:
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado,"
                " codigo_provisorio, telefono, grupo_id) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " RETURNING id",
                (cid, dni_demo(doc) if conf else None, nombre, conf,
                 None if conf else doc, tel, f"{grupo}@g.us"))
            conductor_id = cur.fetchone()[0]

            if plan is not None:
                cur.execute(
                    "INSERT INTO programado (cliente_id, conductor_id, dia, pedidos_programados,"
                    " hora_planificada) VALUES (%s,%s,%s,%s,%s)",
                    (cid, conductor_id, DIA, plan, hplan))

            if caso == "ausente":
                continue

            # --- mensajes crudos (lo que capturó el puente, en UTC) ---
            def mensaje(hh, texto, sufijo=""):
                cur.execute(
                    "INSERT INTO mensajes (id, cliente_id, grupo_id, de, de_nombre, hora, texto,"
                    " capturado_en) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (f"WA-{conductor_id}-{hh}{sufijo}", cid, f"{grupo}@g.us",
                     f"{tel}@s.whatsapp.net", nombre.title(),
                     utc(hh, 5 if sufijo == "" else 40), texto, utc(hh, 5)))
                return cur.fetchone()[0]

            ingreso_msg = None
            if ing:
                hh = int(ing.split(":")[0])
                ingreso_msg = mensaje(hh, f"ingresé a las {ing}", "i")

            texto = MENSAJES.get(caso, MENSAJES["ok"]).format(
                n=pedidos if pedidos is not None else 0,
                v=vueltas if vueltas is not None else 0,
                s=sal or "")
            if caso == "duplicado":
                mensaje(15, "Voy con 18 pedidos, 2 vueltas", "p")  # parcial: se descarta
                cierre_msg = mensaje(17, texto, "c")
            else:
                cierre_msg = mensaje(17, texto)

            # --- valores extraídos ---
            def valor(tipo, val, msg_id, estado="confirmado"):
                cur.execute(
                    "INSERT INTO valores (cliente_id, conductor_id, dia, tipo, valor, mensaje_id,"
                    " grupo_id, estado) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cid, conductor_id, DIA, tipo, str(val), msg_id, f"{grupo}@g.us", estado))

            if ing and ingreso_msg:
                valor("hora_ingreso", ing, ingreso_msg)
            if caso == "sin_interpretar":
                continue  # hay mensaje capturado y ningún valor: FALLA de completitud
            if vueltas is not None:
                valor("vuelta", vueltas, cierre_msg)
            if pedidos is not None:
                estado = "dudoso" if caso == "fuera_rango" else "confirmado"
                valor("pedidos", pedidos, cierre_msg, estado)
            if sal:
                valor("hora_salida", sal, cierre_msg)

        con.commit()
        print(f"demo listo: cliente_id={cid}, dia={DIA}, {len(PLANTEL)} conductores")

    con.close()


def borrar(cliente_id: int) -> None:
    """Borra la fixture completa, en orden de dependencias.

    La usan las pruebas al terminar: en la base de una operación real no puede quedar nada
    inventado. Se borra lo que creó el seed y nada más (se filtra por cliente_id).
    """
    con = conectar()
    con.autocommit = False
    with con.cursor() as cur:
        cur.execute("DELETE FROM dudas WHERE conductor_id IN"
                    " (SELECT id FROM conductores WHERE cliente_id=%s)", (cliente_id,))
        cur.execute("DELETE FROM chequeos WHERE dia = %s", (DIA,))
        cur.execute("DELETE FROM valores WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM liquidaciones WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM correcciones_admin WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM programado WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM jornadas WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM sesiones WHERE cuenta_id IN"
                    " (SELECT id FROM cuentas WHERE cliente_id=%s)", (cliente_id,))
        cur.execute("DELETE FROM cuentas WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM conductores WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM grupos WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM tarifas_cliente WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM mensajes WHERE cliente_id=%s", (cliente_id,))
        cur.execute("DELETE FROM clientes WHERE id=%s", (cliente_id,))
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
