"""
Prepara el día del conductor para probar el panel (cliente DEMO).

Qué hace:
  - le pone a cada conductor del plantel demo un PIN (1234, es demo y así se ve en pantalla)
  - carga las coordenadas de las dos tiendas del demo, para que la geocerca tenga contra qué
    comparar
  - si se le pide con `--jornada`, abre el día de un conductor y le planta un par de toques
    para que el panel de gestión tenga algo que mostrar

Uso:
    ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_conductor.py
    ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_conductor.py --jornada
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from gestion import db  # noqa: E402

PIN_DEMO = "1234"

# Coordenadas de referencia del DEMO (Lima). Las de verdad se cargan por tienda.
TIENDAS = {
    "T-214": (-11.9931, -77.0612),   # Lima Norte
    "T-308": (-12.1841, -76.9876),   # Lima Sur
}


def hashear(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def main() -> None:
    con_jornada = "--jornada" in sys.argv

    with db.conexion() as con, con.cursor() as cur:
        cur.execute("SELECT id, nombre FROM clientes WHERE nombre ILIKE '%DEMO%'")
        cliente = cur.fetchone()
        if not cliente:
            raise SystemExit("no hay cliente DEMO: correr seed_demo_gestion.py primero")
        cid = cliente["id"]

        # --- PIN del plantel (demo: el mismo para todos) ---
        for tienda, (lat, lon) in TIENDAS.items():
            cur.execute(
                "UPDATE grupos SET lat=%s, lon=%s, radio_m=150"
                " WHERE cliente_id=%s AND codigo_tienda=%s", (lat, lon, cid, tienda))

        cur.execute("SELECT id, nombre FROM conductores WHERE cliente_id=%s", (cid,))
        conductores = cur.fetchall()
        for c in conductores:
            salt = f"demo-{c['id']}"
            cur.execute(
                "UPDATE conductores SET pin_hash=%s, pin_salt=%s, pin_actualizado_en=now()"
                " WHERE id=%s", (hashear(PIN_DEMO, salt), salt, c["id"]))

        if con_jornada:
            cur.execute(
                "SELECT id, nombre FROM conductores WHERE cliente_id=%s AND documento_confirmado"
                " ORDER BY id LIMIT 1", (cid,))
            quien = cur.fetchone()
            cur.execute("DELETE FROM jornadas WHERE conductor_id=%s", (quien["id"],))
            cur.execute(
                "INSERT INTO jornadas (cliente_id, conductor_id, dia, lat, lon, precision_m,"
                " distancia_tienda_m, estado_ubicacion)"
                " SELECT %s, %s, (now() AT TIME ZONE 'America/Lima')::date, -11.9932, -77.0614, 12,"
                "        distancia_m(-11.9932, -77.0614, lat, lon),"
                "        CASE WHEN distancia_m(-11.9932, -77.0614, lat, lon) <= radio_m"
                "             THEN 'cerca' ELSE 'lejos' END"
                " FROM grupos WHERE cliente_id=%s AND codigo_tienda='T-214'"
                " RETURNING id", (cid, quien["id"], cid))
            jornada = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO cargas (jornada_id, vuelta, pedidos) VALUES (%s, 1, 20)", (jornada,))
            for i in range(8):
                cur.execute(
                    "INSERT INTO marcas (jornada_id, tipo, lat, lon, precision_m, clave_cliente)"
                    " VALUES (%s, 'entregado', %s, %s, 14, %s)",
                    (jornada, -11.9931 - i * 0.0012, -77.0612 - i * 0.0008, f"demo-{i}"))
            print(f"jornada demo abierta para {quien['nombre']} (id {quien['id']})")

        con.commit()
        print(f"PIN {PIN_DEMO} puesto a {len(conductores)} conductores del cliente {cid}")
        print("tiendas:", ", ".join(f"{t} {v}" for t, v in TIENDAS.items()))


if __name__ == "__main__":
    main()
