"""Crea las cuentas de entrada.

Dos formas de entrar, a propósito:

  - **Administración** (la administración, operaciones): usuario y contraseña. La contraseña inicial se genera
    acá y se muestra una sola vez; conviene cambiarla en el primer ingreso.
  - **Conductores**: su DNI y la contraseña que eligen ellos la primera vez. No hay código que
    generar ni repartir: la identidad es el DNI que ya está en la base.

Lo único que hay que entregar por WhatsApp es, para los conductores, una línea: "entra con tu
DNI". Y para la administración, su usuario y su contraseña inicial.

Uso:
    cd ~/rauda && ./.venv/bin/python scripts/cuentas.py             # ver qué haría
    cd ~/rauda && ./.venv/bin/python scripts/cuentas.py --aplicar   # hacerlo
"""

from __future__ import annotations

import argparse
import secrets
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from gestion import auth, db  # noqa: E402

# Quién entra al panel de gestión. El usuario va en minúsculas, sin espacios.
GESTION = [
    {"usuario": "admin", "nombre": "Administración", "rol": "admin"},
    {"usuario": "operaciones", "nombre": "Operaciones", "rol": "operaciones"},
]


def _sin_tildes(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in plano if not unicodedata.combining(c))


def usuario_de(nombre: str, usados: set[str]) -> str:
    """`MARCO QUISPE` → `operaciones.quispe`; si ya existe, `operaciones.quispe2`.

    Se arma del nombre porque es lo único que el conductor tiene grabado: no tiene correo.
    """
    partes = [p for p in _sin_tildes(nombre).lower().split() if p.isalpha()][:2]
    base = ".".join(partes) or "conductor"
    base = base[:40]
    if base not in usados:
        return base
    for i in range(2, 60):
        candidato = f"{base[:37]}{i}"
        if candidato not in usados:
            return candidato
    raise RuntimeError(f"no se pudo armar un usuario para {nombre}")


def clave_inicial(nombre: str) -> str:
    """Una contraseña provisoria legible: `Rauda-4821-operaciones`.

    Se dicta por teléfono sin equivocarse y no es un desastre si se filtra, porque la cambia
    en el primer ingreso.
    """
    return f"Rauda-{secrets.randbelow(9000) + 1000}-{nombre.split()[0].lower()}"


def main(aplicar: bool) -> int:
    usados = {c["usuario"] for c in auth.cuentas()}
    plan: list[dict] = []

    for persona in GESTION:
        if persona["usuario"] in usados:
            continue
        cliente = db._uno("SELECT id FROM clientes ORDER BY id LIMIT 1")
        plan.append({**persona, "cliente_id": cliente["id"], "nuevo": True})

    # Un conductor solo puede entrar si tiene DNI cargado: su DNI es su usuario.
    conductores = db._consulta(
        "SELECT c.id, c.nombre, c.cliente_id, c.documento FROM conductores c"
        " WHERE c.activo AND c.documento ~ '^[0-9]{8}$'"
        "   AND NOT EXISTS (SELECT 1 FROM cuentas q WHERE q.conductor_id = c.id)"
        " ORDER BY c.cliente_id, c.nombre")
    for c in conductores:
        plan.append({"usuario": c["documento"], "nombre": c["nombre"], "rol": "conductor",
                     "cliente_id": c["cliente_id"], "conductor_id": c["id"], "nuevo": True})

    sin_dni = db._consulta(
        "SELECT c.nombre FROM conductores c WHERE c.activo"
        " AND (c.documento IS NULL OR c.documento !~ '^[0-9]{8}$')")
    for c in sin_dni:
        print(f"  (sin DNI, no puede entrar todavía: {c['nombre']})")

    if not plan:
        print("No hay cuentas para crear: ya están todas.")
        return 0

    print(f"{'usuario':<24} {'rol':<12} nombre")
    print("-" * 70)
    for p in plan:
        print(f"{p['usuario']:<24} {p['rol']:<12} {p['nombre']}")
    print(f"\n{len(plan)} cuenta(s).")

    if not aplicar:
        print("\n(simulación) Para crearlas de verdad: --aplicar")
        return 0

    print("\nCONTRASEÑAS INICIALES DE LA ADMINISTRACIÓN (se muestran una sola vez)\n")
    for p in plan:
        try:
            if p["rol"] == "conductor":
                auth.crear_cuenta(p["usuario"], p["nombre"], "conductor",
                                  cliente_id=p["cliente_id"], conductor_id=p["conductor_id"])
                print(f"  {p['usuario']:<12} {p['nombre']:<26} entra con su DNI y elige clave")
            else:
                clave = clave_inicial(p["nombre"])
                auth.crear_cuenta(p["usuario"], p["nombre"], p["rol"],
                                  cliente_id=p["cliente_id"], clave_inicial=clave)
                print(f"  {p['usuario']:<12} {p['nombre']:<26} {clave}")
        except Exception as error:      # noqa: BLE001
            print(f"  {p['usuario']:<12} ERROR: {error}")

    print("\nPanel:      https://panel.example.com/entrar   (usuario y contraseña)"
          "\nConductores: https://conductores.example.com/conductor (DNI, y «Es mi primera vez»)")
    return 0


def clave_para(usuario: str) -> int:
    """Le pone una contraseña provisoria a una cuenta de administración y la muestra."""
    cuenta = auth.por_usuario(usuario)
    if not cuenta:
        print(f"No existe la cuenta {usuario}.")
        return 1
    if cuenta["rol"] == "conductor":
        print("Los conductores no llevan contraseña provisoria: reiniciala desde /usuarios.")
        return 1
    clave = clave_inicial(cuenta["nombre"])
    with db.transaccion() as con, con.cursor() as cur:
        cur.execute("UPDATE cuentas SET clave_hash = %s, clave_actualizada_en = now()"
                    " WHERE id = %s", (auth._hash(clave, cuenta["clave_salt"]), cuenta["id"]))
    db._ejecutar("UPDATE sesiones SET revocada_en = now()"
                 " WHERE cuenta_id = %s AND revocada_en IS NULL", (cuenta["id"],))
    print(f"{cuenta['usuario']:<12} {cuenta['nombre']:<20} {clave}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true", help="crear las cuentas de verdad")
    parser.add_argument("--clave", metavar="USUARIO",
                        help="poner una contraseña provisoria a una cuenta de administración")
    args = parser.parse_args()
    if args.clave:
        sys.exit(clave_para(args.clave))
    sys.exit(main(args.aplicar))
