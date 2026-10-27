"""Da de alta una operación real: cliente, tienda, conductores y sus cuentas.

Para qué: el día que la administración pase los datos del piloto, cargarlos no puede ser "tocar la base a
mano". Un archivo con los datos y un comando. Si algo está mal, lo dice antes de escribir nada.

Formato del archivo (`operacion.json`):

    {
      "cliente": "Cliente de ejemplo",
      "tarifa_pedido": 3.00,
      "tienda": {"codigo": "TEMU-LIM-01", "direccion": "Av. ...", "zona": "Lima",
                 "supervisor": "Supervisor 1", "lat": -12.0464, "lon": -77.0428, "radio_m": 200},
      "conductores": [
        {"documento": "70012341", "nombre": "Nombre Apellido", "telefono": "51987654321"},
        {"documento": "70123452", "nombre": "Nombre Apellido",  "telefono": "51912345678"}
      ]
    }

Uso:
    cd ~/rauda && ./.venv/bin/python scripts/operacion.py operacion.json            # revisa
    cd ~/rauda && ./.venv/bin/python scripts/operacion.py operacion.json --aplicar  # carga

Qué deja listo:
  - El cliente, con su tarifa (que después se maneja con vigencia).
  - La tienda como grupo, con su coordenada y radio: es la geocerca del "ya llegué".
  - Cada conductor con su DNI, y su cuenta para entrar (el DNI es el usuario; él elige la clave).
  - Un día de `programado` NO se carga: eso lo dice la operación, no nosotros.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from gestion import auth, db  # noqa: E402


def revisar(datos: dict) -> list[str]:
    """Todo lo que impediría cargar la operación, dicho antes de escribir una fila."""
    problemas: list[str] = []

    if not datos.get("cliente"):
        problemas.append("Falta el nombre del cliente.")
    if not isinstance(datos.get("tarifa_pedido"), (int, float)):
        problemas.append("Falta `tarifa_pedido` (número, por ejemplo 3.00).")

    tienda = datos.get("tienda") or {}
    if not tienda.get("codigo"):
        problemas.append("La tienda necesita `codigo`: es lo que identifica la zona en el tareo.")
    if tienda.get("lat") is None or tienda.get("lon") is None:
        problemas.append("La tienda necesita `lat` y `lon`: sin coordenada no hay geocerca y "
                         "no se puede saber si el conductor llegó.")
    elif not (-18 <= float(tienda["lat"]) <= 0 and -82 <= float(tienda["lon"]) <= -68):
        problemas.append("Las coordenadas no parecen de Perú. Revisalas.")

    conductores = datos.get("conductores") or []
    if not conductores:
        problemas.append("No hay conductores: el piloto necesita al menos uno.")

    documentos = set()
    for i, c in enumerate(conductores, start=1):
        doc = str(c.get("documento") or "").strip()
        nombre = (c.get("nombre") or "").strip()
        if not nombre:
            problemas.append(f"Conductor {i}: falta el nombre.")
        if not doc.isdigit() or len(doc) != 8:
            problemas.append(f"Conductor {i} ({nombre or 'sin nombre'}): el DNI tiene que ser de "
                             f"8 números, y llegó {doc!r}. Sin DNI no puede entrar.")
        elif doc in documentos:
            problemas.append(f"Conductor {i}: el DNI {doc} está repetido.")
        documentos.add(doc)
        tel = str(c.get("telefono") or "")
        if tel and not (tel.isdigit() and len(tel) >= 9):
            problemas.append(f"Conductor {i}: el teléfono {tel!r} no parece un número válido "
                             f"(con código de país, sin espacios ni guiones).")
    return problemas


def cargar(datos: dict) -> dict:
    tienda = datos["tienda"]
    resultado = {"conductores": [], "cuentas": []}

    with db.transaccion() as con, con.cursor() as cur:
        # --- cliente (si ya existe, se reutiliza: no se duplica una operación por error) ---
        cur.execute("SELECT id FROM clientes WHERE nombre = %s", (datos["cliente"],))
        fila = cur.fetchone()
        if fila:
            cliente_id = fila["id"]
            cur.execute("UPDATE clientes SET tarifa_pedido = %s, es_demo = FALSE WHERE id = %s",
                        (datos["tarifa_pedido"], cliente_id))
        else:
            cur.execute(
                "INSERT INTO clientes (nombre, tarifa_pedido, es_demo) VALUES (%s,%s,FALSE)"
                " RETURNING id", (datos["cliente"], datos["tarifa_pedido"]))
            cliente_id = cur.fetchone()["id"]
        resultado["cliente_id"] = cliente_id

        # --- tienda: se guarda como grupo, que es donde vive la geocerca ---
        grupo_id = f"tienda:{tienda['codigo'].lower()}@rauda"
        cur.execute(
            "INSERT INTO grupos (grupo_id, cliente_id, etiqueta, codigo_tienda, zona,"
            " supervisor, lat, lon, radio_m)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (grupo_id) DO UPDATE SET etiqueta = EXCLUDED.etiqueta,"
            " codigo_tienda = EXCLUDED.codigo_tienda, zona = EXCLUDED.zona,"
            " supervisor = EXCLUDED.supervisor, lat = EXCLUDED.lat, lon = EXCLUDED.lon,"
            " radio_m = EXCLUDED.radio_m",
            (grupo_id, cliente_id, tienda.get("direccion") or tienda["codigo"],
             tienda["codigo"], tienda.get("zona"), tienda.get("supervisor"),
             tienda["lat"], tienda["lon"], tienda.get("radio_m", 200)))

        # --- conductores ---
        for c in datos["conductores"]:
            documento = str(c["documento"]).strip()
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado,"
                " telefono, grupo_id, activo)"
                " VALUES (%s,%s,%s,TRUE,%s,%s,TRUE)"
                " ON CONFLICT (documento) DO UPDATE SET nombre = EXCLUDED.nombre,"
                " cliente_id = EXCLUDED.cliente_id, grupo_id = EXCLUDED.grupo_id,"
                " telefono = COALESCE(EXCLUDED.telefono, conductores.telefono),"
                " documento_confirmado = TRUE, activo = TRUE"
                " RETURNING id, documento, nombre",
                (cliente_id, documento, c["nombre"].strip(), c.get("telefono"), grupo_id))
            conductor = cur.fetchone()
            resultado["conductores"].append(conductor)

            # cuenta: el DNI es el usuario; la clave la elige él la primera vez
            cur.execute("SELECT id FROM cuentas WHERE conductor_id = %s", (conductor["id"],))
            if cur.fetchone():
                cur.execute("UPDATE cuentas SET activa = TRUE WHERE conductor_id = %s",
                            (conductor["id"],))
            else:
                cur.execute("SELECT 1 FROM cuentas WHERE usuario = %s", (documento,))
                if cur.fetchone():
                    raise RuntimeError(
                        f"El usuario {documento} ya está tomado por otra cuenta. "
                        f"Revisá si ese DNI está cargado dos veces.")
                cur.execute(
                    "INSERT INTO cuentas (usuario, nombre, rol, cliente_id, conductor_id,"
                    " clave_salt, activa) VALUES (%s,%s,'conductor',%s,%s,%s,TRUE)",
                    (documento, conductor["nombre"], cliente_id, conductor["id"],
                     __import__("secrets").token_hex(16)))
                resultado["cuentas"].append(documento)

    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", help="JSON con la operación")
    parser.add_argument("--aplicar", action="store_true", help="cargar de verdad")
    args = parser.parse_args()

    datos = json.loads(Path(args.archivo).read_text())

    problemas = revisar(datos)
    if problemas:
        print("No se puede cargar todavía:\n")
        for p in problemas:
            print("  ·", p)
        return 1

    print(f"Cliente:  {datos['cliente']}  (S/ {datos['tarifa_pedido']:.2f} por pedido)")
    print(f"Tienda:   {datos['tienda']['codigo']} — radio {datos['tienda'].get('radio_m', 200)} m")
    print(f"Conductores ({len(datos['conductores'])}):")
    for c in datos["conductores"]:
        print(f"  {c['documento']}  {c['nombre']}")

    if not args.aplicar:
        print("\n(simulación) Todo se ve bien. Para cargarlo: --aplicar")
        return 0

    resultado = cargar(datos)
    print(f"\nCargado. Cliente {resultado['cliente_id']}, "
          f"{len(resultado['conductores'])} conductores.")
    if resultado["cuentas"]:
        print("Cuentas nuevas (entran con su DNI y eligen clave la primera vez):")
        for usuario in resultado["cuentas"]:
            print(f"  {usuario}")
    print("\nEntrada del conductor: https://conductores.example.com/conductor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
