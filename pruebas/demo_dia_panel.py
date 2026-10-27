"""
Deja el día de HOY armado con datos del panel, pasando por la API real.

Sirve para mostrar el sistema: dos conductores que cerraron, uno en la calle (abrió y marcó
pero no cerró) y uno que no abrió. Nada de esto es inventado a mano en la base: se manda por
los mismos endpoints que usa el celular, así lo que se ve es lo que el sistema produce.

Uso:
    ~/rauda/.venv/bin/python ~/rauda/pruebas/demo_dia_panel.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent

BASE = "http://127.0.0.1:8095"
PIN = "1234"

# (documento, pedidos que carga, toques, cierre o None si sigue en la calle)
GUION = [
    ("70012341", 20, 5, {"vueltas": 3, "pedidos": 41, "sac": 2, "rechazados": 1}),
    ("70123452", 18, 18, {"vueltas": 3, "pedidos": 38}),
    ("70234563", 22, 3, None),
]

# Lo programado del día: sin esto el tareo no tiene contra qué comparar y todas las filas
# dicen "reportó sin estar programado" (en la vida real esto lo carga Rauda cada día).
PROGRAMADO = {
    "70012341": (42, "08:00"),
    "70123452": (40, "08:00"),
    "70234563": (45, "08:00"),
    "70345674": (40, "08:30"),   # no abre: tiene que quedar como "no reportó"
    "Pro2025":  (36, "08:00"),   # documento provisorio: no se liquida
}


def sembrar_programado() -> None:
    """Los bloques se calculan con la hora actual (menos unos minutos), así la tardanza
    del demo es creíble y no aparece como 'ingresó 500 minutos tarde'."""
    from datetime import datetime, timedelta, timezone

    sys.path.insert(0, str(RAIZ))
    from gestion import db

    lima = timezone(timedelta(hours=-5))
    ahora = datetime.now(lima)
    bloques = {
        "70012341": (42, (ahora - timedelta(minutes=4)).strftime("%H:%M")),
        "70123452": (40, (ahora - timedelta(minutes=3)).strftime("%H:%M")),
        "70234563": (45, (ahora - timedelta(minutes=2)).strftime("%H:%M")),
        "70345674": (40, ahora.strftime("%H:%M")),
        "pro2025":  (36, ahora.strftime("%H:%M")),
    }

    with db.conexion() as con, con.cursor() as cur:
        for documento, (pedidos, hora) in bloques.items():
            cur.execute(
                "INSERT INTO programado (cliente_id, conductor_id, dia, pedidos_programados,"
                " hora_planificada)"
                " SELECT c.cliente_id, c.id, (now() AT TIME ZONE 'America/Lima')::date, %s, %s::time"
                " FROM conductores c"
                " LEFT JOIN conductores p ON p.id = c.id"
                " WHERE upper(coalesce(c.documento, c.codigo_provisorio, '')) = %s"
                " ON CONFLICT (conductor_id, dia) DO UPDATE"
                " SET pedidos_programados = EXCLUDED.pedidos_programados,"
                "     hora_planificada = EXCLUDED.hora_planificada",
                (pedidos, hora, documento.upper()))


def limpiar_dia() -> None:
    """Deja el día de hoy limpio para esos conductores: se puede correr varias veces."""
    sys.path.insert(0, str(RAIZ))
    from gestion import db

    documentos = [d for d, _, _, _ in GUION]
    with db.conexion() as con, con.cursor() as cur:
        cur.execute(
            "DELETE FROM jornadas WHERE dia = (now() AT TIME ZONE 'America/Lima')::date AND conductor_id IN"
            " (SELECT id FROM conductores WHERE documento = ANY(%s))", (documentos,))
        cur.execute(
            "DELETE FROM valores WHERE origen='panel' AND dia = (now() AT TIME ZONE 'America/Lima')::date AND conductor_id IN"
            " (SELECT id FROM conductores WHERE documento = ANY(%s))", (documentos,))


def main() -> None:
    limpiar_dia()
    sembrar_programado()
    with httpx.Client(base_url=BASE, timeout=20) as cli:
        for documento, cargados, toques, cierre in GUION:
            r = cli.post("/api/conductor/login", json={"documento": documento, "pin": PIN})
            r.raise_for_status()
            h = {"Authorization": f"Bearer {r.json()['token']}"}
            nombre = r.json()["nombre"]

            r = cli.post("/api/conductor/llegada",
                         json={"pedidos": cargados, "lat": -11.9932, "lon": -77.0614,
                               "precision": 12}, headers=h)
            if r.status_code != 200:
                print(f"{nombre}: no se pudo abrir el día ({r.status_code}) {r.text[:120]}")
                continue

            for i in range(toques):
                cli.post("/api/conductor/marca", json={"tipo": "entregado",
                         "lat": -11.9932 - i * 0.001, "lon": -77.0614 - i * 0.001},
                         headers=h)

            if cierre:
                r = cli.post("/api/conductor/cerrar", json=cierre, headers=h)
                print(f"{nombre}: cerró {cierre.get('pedidos')} pedidos"
                      f"{' + ' + str(cierre['sac']) + ' SAC' if cierre.get('sac') else ''}"
                      f" en {cierre['vueltas']} vueltas ({r.status_code})")
            else:
                print(f"{nombre}: sigue en la calle ({toques} toques, sin cerrar)")


if __name__ == "__main__":
    main()
