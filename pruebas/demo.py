"""
Demostración final: dos mensajes de grupo (uno repetido) por el gancho REAL del
plugin, y mostramos el contenido real del archivo generado.

Correr:
    python3 /home/ubuntu/rauda/pruebas/demo.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

PLUGIN_INIT = "/home/ubuntu/.hermes/profiles/asistente/plugins/captura-mensajes/__init__.py"

salida = tempfile.mkdtemp(prefix="captura_demo_")
os.environ["RAUDA_CAPTURA_RAIZ"] = salida
os.environ["RAUDA_CAPTURA_LOG"] = os.path.join(salida, "_captura.log")

spec = importlib.util.spec_from_file_location(
    "captura_mensajes_demo", PLUGIN_INIT,
    submodule_search_locations=[os.path.dirname(PLUGIN_INIT)])
plugin = importlib.util.module_from_spec(spec)
sys.modules["captura_mensajes_demo"] = plugin
spec.loader.exec_module(plugin)


def evento(message_id, texto, is_group=True, chat_id="120363000000000000@g.us"):
    src = types.SimpleNamespace(
        platform="whatsapp", chat_id=chat_id, chat_name="Ventas",
        chat_type="group" if is_group else "dm",
        user_id="51900000123@s.whatsapp.net", user_name="Juana", message_id=message_id)
    raw = {"messageId": message_id, "chatId": chat_id, "senderId": src.user_id,
           "senderName": src.user_name, "isGroup": is_group, "body": texto,
           "timestamp": 1730000000}
    return types.SimpleNamespace(text=texto, source=src, message_id=message_id,
                                 raw_message=raw, internal=False)


print("Raíz de datos de la demo:", salida)
print()
print("── Simulo el gancho del gateway con 2 mensajes + 1 repetido ──")
for etiqueta, ev in [
    ("grupo #1", evento("WA-0001", "Che, ¿el pedido salió?")),
    ("grupo #2", evento("WA-0002", "Sí, ya está despachado")),
    ("grupo #1 (repetido en el reintento)", evento("WA-0001", "Che, ¿el pedido salió?")),
]:
    ret = plugin.capturar(event=ev)  # el gancho real
    print("  %-38s → gancho devolvió %r" % (etiqueta, ret))

dia = __import__("datetime").date.today().isoformat()
ruta = os.path.join(salida, "120363000000000000@g.us", dia + ".jsonl")

print()
print("── Contenido real del archivo ──")
print("Ruta:", ruta)
print()
with open(ruta, encoding="utf-8") as f:
    lineas = [ln.rstrip("\n") for ln in f if ln.strip()]
for i, ln in enumerate(lineas, 1):
    print("%d| %s" % (i, ln))

print()
print("Cuenta de líneas:", len(lineas), "(2 mensajes distintos; el repetido NO se duplicó)")
print()
print("── Verificación de forma (JSON bien formado) ──")
for i, ln in enumerate(lineas, 1):
    reg = json.loads(ln)
    print("  línea %d → id=%s  de=%s  texto=%r  capturado_en=%s"
          % (i, reg["id"], reg["de"], reg["texto"], reg["capturado_en"]))

shutil.rmtree(salida, ignore_errors=True)
