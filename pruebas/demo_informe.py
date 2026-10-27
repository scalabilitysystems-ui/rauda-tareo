"""
Genera un informe de ejemplo con datos INVENTADOS (ningún DNI ni nombre es real) y lo
deja escrito en ~/rauda/informes/. Sirve para mostrar el formato real del informe.

Correr:
    python3 /home/ubuntu/rauda/pruebas/demo_informe.py
"""

from __future__ import annotations

import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAUDA = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(RAUDA, "lib"))

import informe  # noqa: E402

DIA = "2026-10-05"
G_A = "120363000000000001@g.us"
G_B = "120363000000000002@g.us"

# --- Cuaderno capturado (mensajes crudos, con la forma real del JSONL) ------
cuaderno = [
    {"id": "WA-1", "hora": "2026-10-05T13:00:00", "de": "51...@s.whatsapp.net",
     "de_nombre": "Ana", "grupo": G_A, "texto": "Vuelta 3, 40 pds, 08:00 a 17:00"},
    {"id": "WA-2", "hora": "2026-10-05T13:05:00", "de": "51...@s.whatsapp.net",
     "de_nombre": "Beto", "grupo": G_A, "texto": "2 vueltas, 30 pds, 08:30 a 16:00"},
    {"id": "WA-3", "hora": "2026-10-05T13:10:00", "de": "51...@s.whatsapp.net",
     "de_nombre": "Carla", "grupo": G_B, "texto": "1 vuelta, 18 pds, 09:00 a 15:00"},
    {"id": "WA-4", "hora": "2026-10-05T13:12:00", "de": "51...@s.whatsapp.net",
     "de_nombre": "Dora", "grupo": G_B, "texto": "Pro2025: 22 pds, 09:10 a 15:40"},
]
recibidos = [{"id": m["id"], "hora": m["hora"]} for m in cuaderno]

conductores = {
    "C1": {"documento": "70123456", "documento_confirmado": True, "nombre": "Ana Uno"},
    "C2": {"documento": "71234567", "documento_confirmado": True, "nombre": "Beto Dos"},
    "C3": {"documento": "72345678", "documento_confirmado": True, "nombre": "Carla Tres"},
    "C4": {"documento": "Pro2025", "documento_confirmado": False, "nombre": "Dora Cuatro"},
}


def val(vid, cid, tipo, valor, mid, dia=DIA):
    return {"id": vid, "conductor_id": cid, "dia": dia, "tipo": tipo,
            "valor": valor, "mensaje_id": mid}


valores = [
    val("V1", "C1", "pedidos", 40, "WA-1"),
    val("V2", "C1", "vuelta", 3, "WA-1"),
    val("V3", "C1", "hora_ingreso", "08:00", "WA-1"),
    val("V4", "C1", "hora_salida", "17:00", "WA-1"),
    val("V5", "C2", "pedidos", 30, "WA-2"),
    val("V6", "C2", "vuelta", 2, "WA-2"),
    val("V7", "C3", "pedidos", 18, "WA-3"),
    val("V8", "C3", "vuelta", 1, "WA-3"),
    # C4 (Dora) tiene documento provisorio: sus valores NO se pueden liquidar.
    val("V9", "C4", "pedidos", 22, "WA-4"),
    val("V10", "C4", "vuelta", 1, "WA-4"),
    # Un valor sin mensaje que lo respalde: no se puede probar que se leyó.
    {"id": "V11", "conductor_id": "C3", "dia": DIA, "tipo": "pedidos",
     "valor": 33, "mensaje_id": None},
]

historial = {
    "C1": {"vuelta": 3.0, "pedidos": 42.0},
    "C2": {"vuelta": 2.0, "pedidos": 31.0},
    "C3": {"vuelta": 1.0, "pedidos": 19.0},
}

programado = [
    {"conductor_id": "C1", "dia": DIA, "pedidos_programados": 40},
    {"conductor_id": "C2", "dia": DIA, "pedidos_programados": 30},
    {"conductor_id": "C3", "dia": DIA, "pedidos_programados": 20},
    # C5 programado y no reportó: DUDA de completitud.
    {"conductor_id": "C5", "dia": DIA, "pedidos_programados": 25},
]

mensajes_por_conductor = {
    ("C1", DIA): ["WA-1"],
    ("C2", DIA): ["WA-2"],
    ("C3", DIA): ["WA-3"],
    ("C4", DIA): ["WA-4"],
    # C6 mandó mensaje y no se le extrajo nada: plata que no se factura.
    ("C6", DIA): ["WA-6"],
}

tareo = [
    {"fila": 10, "columna": "PEDIDOS", "valor_id": "V1", "conductor_id": "C1"},
    {"fila": 10, "columna": "VUELTAS", "valor_id": "V2", "conductor_id": "C1"},
    {"fila": 11, "columna": "PEDIDOS", "valor_id": "V5", "conductor_id": "C2"},
    {"fila": 12, "columna": "PEDIDOS", "valor_id": "V7", "conductor_id": "C3"},
    # Una celda que se escribió SIN un valor que la respalde: la regla dura se rompió.
    {"fila": 12, "columna": "EXTRA", "valor_id": "V11", "conductor_id": "C3"},
]

informe.generar(
    DIA,
    recibidos=recibidos, cuaderno=cuaderno, valores=valores, conductores=conductores,
    historial=historial, programado=programado,
    mensajes_por_conductor=mensajes_por_conductor, tareo=tareo,
    dudas_resueltas=1,
)

ruta_md = os.path.join(RAUDA, "informes", DIA + ".md")
ruta_json = os.path.join(RAUDA, "informes", DIA + ".json")
print("Informe escrito en:")
print("  ", ruta_json)
print("  ", ruta_md)
print()
print("=" * 72)
with open(ruta_md, encoding="utf-8") as f:
    print(f.read())
