"""
Pruebas del cuaderno de WhatsApp.

Cubre las 8 pruebas pedidas, tanto la librería reusable (`rauda/lib/captura_log.py`)
como el gancho real del plugin (`plugins/captura-mensajes/__init__.py`).

Correr:
    python3 /home/ubuntu/rauda/pruebas/test_captura.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
RAUDA = os.path.dirname(AQUI)
LIB = os.path.join(RAUDA, "lib")
PLUGIN_INIT = "/home/ubuntu/.hermes/profiles/asistente/plugins/captura-mensajes/__init__.py"

sys.path.insert(0, LIB)
import captura_log  # noqa: E402
from captura_log import Capturador  # noqa: E402


# --------------------------------------------------------------------------
# Helpers: eventos falsos con la MISMA forma que produce el gateway real
# (MessageEvent + SessionSource + raw_message del bridge de WhatsApp).
# --------------------------------------------------------------------------
def evento_whatsapp(
    *, chat_id, is_group=True, text="hola", message_id="MSG1",
    sender_id="5493411111111@s.whatsapp.net", sender_name="Juana",
    timestamp=1730000000, chat_name=None, raw_extra=None,
):
    src = types.SimpleNamespace(
        platform="whatsapp",
        chat_id=chat_id,
        chat_name=chat_name or ("Grupo " + chat_id),
        chat_type="group" if is_group else "dm",
        user_id=sender_id,
        user_name=sender_name,
        message_id=message_id,
    )
    raw = {
        "messageId": message_id, "chatId": chat_id, "senderId": sender_id,
        "senderName": sender_name, "isGroup": is_group, "body": text,
        "timestamp": timestamp,
    }
    if raw_extra:
        raw.update(raw_extra)
    return types.SimpleNamespace(
        text=text, source=src, message_id=message_id, raw_message=raw,
        message_type=types.SimpleNamespace(value="text"),
        timestamp=datetime(2024, 10, 26, 12, 0, 0), internal=False, metadata={},
    )


def leer_lineas(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [ln for ln in (l.rstrip("\n") for l in f) if ln]


def cargar_plugin(raiz):
    """Carga el plugin del disco con una raíz de datos temporal, como una carga nueva."""
    os.environ["RAUDA_CAPTURA_RAIZ"] = raiz
    os.environ["RAUDA_CAPTURA_LOG"] = os.path.join(raiz, "_captura.log")
    nombre = "captura_mensajes_test_%d" % abs(hash(raiz))
    spec = importlib.util.spec_from_file_location(
        nombre, PLUGIN_INIT, submodule_search_locations=[os.path.dirname(PLUGIN_INIT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


# ==========================================================================
# 1..6, 8: la librería reusable
# ==========================================================================
class TestLibreria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="captura_test_")

    def test_1_archivo_del_grupo_y_mensaje_bien_formado(self):
        cap = Capturador(raiz=self.tmp)
        ok = cap.capturar(
            chat_id="120363000000000000@g.us", chat_type="group", message_id="ABC123",
            sender_id="51900000123@s.whatsapp.net", sender_name="Juana",
            text="hola, ¿cómo va?", hora_epoch=1730000000, ahora="2024-10-26T12:00:05",
        )
        self.assertTrue(ok)
        ruta = os.path.join(self.tmp, "120363000000000000@g.us", "2024-10-26.jsonl")
        self.assertTrue(os.path.isfile(ruta), "no se creó el archivo del grupo/día")
        lineas = leer_lineas(ruta)
        self.assertEqual(len(lineas), 1)
        reg = json.loads(lineas[0])
        self.assertEqual(reg["id"], "ABC123")
        self.assertEqual(reg["grupo"], "120363000000000000@g.us")
        self.assertEqual(reg["de"], "51900000123@s.whatsapp.net")
        self.assertEqual(reg["de_nombre"], "Juana")
        self.assertEqual(reg["texto"], "hola, ¿cómo va?")
        self.assertEqual(reg["tipo_chat"], "group")
        self.assertEqual(reg["capturado_en"], "2024-10-26T12:00:05")
        self.assertIsNotNone(reg["hora"])          # hora del mensaje
        self.assertIsNotNone(reg["hora_epoch"])

    def test_2_mismo_mensaje_dos_veces_una_sola_linea(self):
        cap = Capturador(raiz=self.tmp)
        args = dict(chat_id="123@g.us", chat_type="group", message_id="DUP1",
                    sender_id="549@s.whatsapp.net", text="eco", hora_epoch=1730000000,
                    ahora="2024-10-26T12:00:00")
        self.assertTrue(cap.capturar(**args))
        self.assertFalse(cap.capturar(**args), "el duplicado no se detectó")
        ruta = os.path.join(self.tmp, "123@g.us", "2024-10-26.jsonl")
        self.assertEqual(len(leer_lineas(ruta)), 1)

    def test_2b_idempotencia_tras_reinicio(self):
        # Un proceso nuevo (otro Capturador) sobre la misma raíz debe deduplicar leyendo el disco.
        args = dict(chat_id="123@g.us", chat_type="group", message_id="DUR1",
                    sender_id="549@s.whatsapp.net", text="persistente", hora_epoch=1730000000,
                    ahora="2024-10-26T12:00:00")
        self.assertTrue(Capturador(raiz=self.tmp).capturar(**args))
        self.assertFalse(Capturador(raiz=self.tmp).capturar(**args),
                         "el reinicio volvió a escribir el mismo mensaje")
        ruta = os.path.join(self.tmp, "123@g.us", "2024-10-26.jsonl")
        self.assertEqual(len(leer_lineas(ruta)), 1)

    def test_3_dos_grupos_dos_carpetas(self):
        cap = Capturador(raiz=self.tmp)
        cap.capturar(chat_id="grupoA@g.us", chat_type="group", message_id="A1",
                     text="a", ahora="2024-10-26T10:00:00")
        cap.capturar(chat_id="grupoB@g.us", chat_type="group", message_id="B1",
                     text="b", ahora="2024-10-26T10:00:00")
        a = os.path.join(self.tmp, "grupoA@g.us", "2024-10-26.jsonl")
        b = os.path.join(self.tmp, "grupoB@g.us", "2024-10-26.jsonl")
        self.assertTrue(os.path.isfile(a) and os.path.isfile(b))
        self.assertEqual(json.loads(leer_lineas(a)[0])["texto"], "a")
        self.assertEqual(json.loads(leer_lineas(b)[0])["texto"], "b")

    def test_4_directo_no_contamina_grupos(self):
        cap = Capturador(raiz=self.tmp)
        ok = cap.capturar(chat_id="51900000123@s.whatsapp.net", chat_type="dm",
                          message_id="D1", text="privado", ahora="2024-10-26T10:00:00")
        self.assertTrue(ok)
        # Va a directos/, no a la raíz de grupos.
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "51900000123@s.whatsapp.net")),
                         "el directo contaminó la carpeta de grupos")
        esperado = os.path.join(self.tmp, "directos", "51900000123@s.whatsapp.net",
                                "2024-10-26.jsonl")
        self.assertTrue(os.path.isfile(esperado))
        self.assertEqual(json.loads(leer_lineas(esperado)[0])["tipo_chat"], "dm")

    def test_5_mensaje_deforme_no_rompe_ni_corrompe(self):
        cap = Capturador(raiz=self.tmp)
        # Sin id, sin texto, sin sender, y con campos raros. No debe levantar.
        cap.capturar(chat_id="raro@g.us", chat_type="group", message_id=None,
                     sender_id=None, text=None, hora_epoch=None, ahora="2024-10-26T10:00:00")
        # Con tipos inesperados tampoco.
        cap.capturar(chat_id="raro@g.us", chat_type=None, message_id=12345,
                     sender_id=99, text={"no": "soy texto"}, hora_epoch="no-es-hora",
                     ahora="2024-10-26T10:01:00")
        ruta = os.path.join(self.tmp, "raro@g.us", "2024-10-26.jsonl")
        lineas = leer_lineas(ruta)
        self.assertGreaterEqual(len(lineas), 1)
        for ln in lineas:  # todas las líneas quedan JSON válido
            json.loads(ln)

    def test_6_crea_el_directorio_si_no_existe(self):
        anidado = os.path.join(self.tmp, "no", "existe", "todavia")
        cap = Capturador(raiz=anidado)
        self.assertTrue(cap.capturar(chat_id="g@g.us", chat_type="group", message_id="X",
                                     text="x", ahora="2024-10-26T09:00:00"))
        self.assertTrue(os.path.isfile(os.path.join(anidado, "g@g.us", "2024-10-26.jsonl")))

    def test_8_fallo_de_escritura_no_propaga(self):
        cap = Capturador(raiz=self.tmp)
        original = os.fsync
        captura_log.os.fsync = lambda fd: (_ for _ in ()).throw(OSError("disco lleno"))
        try:
            # No debe salir la excepción; debe devolver False y anotarlo en su log.
            resultado = cap.capturar(chat_id="g@g.us", chat_type="group", message_id="F1",
                                     text="se cae el disco", ahora="2024-10-26T08:00:00")
        finally:
            captura_log.os.fsync = original
        self.assertFalse(resultado)
        self.assertTrue(os.path.isfile(cap.log_path), "no dejó rastro del error en su log")
        with open(cap.log_path, encoding="utf-8") as f:
            self.assertIn("fallo de captura", f.read())


# ==========================================================================
# 1, 7 y el contrato de no-interferencia: el GANCHO REAL del plugin
# ==========================================================================
class TestPluginHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="captura_plugin_")
        self.plugin = cargar_plugin(self.tmp)

    def tearDown(self):
        for k in ("RAUDA_CAPTURA_RAIZ", "RAUDA_CAPTURA_LOG"):
            os.environ.pop(k, None)

    def test_gancho_escribe_el_grupo(self):
        ev = evento_whatsapp(chat_id="1203@g.us", is_group=True, text="hola desde el gancho",
                             message_id="HOOK1")
        self.assertIsNone(self.plugin.capturar(event=ev, gateway=None, session_store=None))
        ruta = os.path.join(self.tmp, "1203@g.us", datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        self.assertTrue(os.path.isfile(ruta))
        reg = json.loads(leer_lineas(ruta)[0])
        self.assertEqual(reg["id"], "HOOK1")
        self.assertEqual(reg["texto"], "hola desde el gancho")
        self.assertEqual(reg["de"], "5493411111111@s.whatsapp.net")

    def test_7_devuelve_none_y_no_altera_el_mensaje(self):
        ev = evento_whatsapp(chat_id="1203@g.us", text="no me toques", message_id="N1")
        antes = (id(ev), ev.text, ev.message_id, dict(ev.raw_message), ev.source.chat_id,
                 ev.source.chat_type)
        ret = self.plugin.capturar(event=ev)
        despues = (id(ev), ev.text, ev.message_id, dict(ev.raw_message), ev.source.chat_id,
                   ev.source.chat_type)
        self.assertIsNone(ret, "el gancho no devolvió None")
        self.assertEqual(antes, despues, "el gancho alteró el evento")

    def test_directo_por_el_gancho_va_a_directos(self):
        ev = evento_whatsapp(chat_id="51900000123@s.whatsapp.net", is_group=False,
                             text="privado", message_id="DM1")
        self.assertIsNone(self.plugin.capturar(event=ev))
        esperado = os.path.join(self.tmp, "directos", "51900000123@s.whatsapp.net",
                                datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        self.assertTrue(os.path.isfile(esperado))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "51900000123@s.whatsapp.net")))

    def test_evento_interno_se_ignora(self):
        ev = evento_whatsapp(chat_id="1203@g.us")
        ev.internal = True
        self.assertIsNone(self.plugin.capturar(event=ev))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "1203@g.us")))

    def test_8b_fallo_dentro_del_gancho_no_propaga(self):
        class Rompe:
            raiz = self.tmp
            def capturar(self, **kw):
                raise RuntimeError("boom simulado")
        self.plugin._capturador = Rompe()
        ev = evento_whatsapp(chat_id="1203@g.us", message_id="BOOM")
        self.assertIsNone(self.plugin.capturar(event=ev))  # no levanta

    def test_evento_none_no_rompe(self):
        self.assertIsNone(self.plugin.capturar(event=None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
