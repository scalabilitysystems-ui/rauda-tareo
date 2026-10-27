"""
Captura duradera de mensajes entrantes a disco (JSONL, un archivo por grupo por día).

Por qué existe
--------------
El puente de WhatsApp guarda lo que llega SOLO en memoria (`const messageQueue = []`).
Si el proceso se reinicia, todo lo recibido y no consumido se pierde, y WhatsApp no
tiene forma de devolvernos el historial. Sin un registro en disco no se puede
reconstruir, auditar ni reprocesar nada.

Esta librería es la parte reusable (sin dependencias de Hermes): escribe cada
mensaje entrante apenas llega, en `<raiz>/<chat_id>/<AAAA-MM-DD>.jsonl`, de forma
**idempotente** (el mismo mensaje dos veces queda una sola línea) y **duradera**
(write + flush + fsync en el momento, no queda en un buffer).

Reglas que no se rompen:
  - `capturar()` NUNCA propaga una excepción hacia afuera. Si falla la escritura,
    se anota en su propio log y el mensaje sigue viajando igual.
  - No clasifica, no interpreta, no parsea: solo captura lo que el gancho provee.

Uso:
    from captura_log import Capturador
    cap = Capturador()                      # raíz por defecto
    cap.capturar(                            # devuelve True si escribió, False si duplicado/fuera
        chat_id="1203...@g.us",
        chat_type="group",
        message_id="ABC123",
        sender_id="549...@s.whatsapp.net",
        sender_name="Juana",
        text="hola",
        hora_epoch=1730000000.0,
    )
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime

# Raíz de datos por defecto del sistema de este cliente.
DEFAULT_RAIZ = "/home/ubuntu/rauda/datos"
# Subcarpeta para los chats directos (no contaminan la carpeta de grupos).
SUBCARPETA_DIRECTOS = "directos"
# Tipos de chat que consideramos "grupo" (lo demás va a directos/).
TIPOS_GRUPO = ("group", "channel")

_SAFE = re.compile(r"[^A-Za-z0-9@._-]")


def raiz_por_defecto() -> str:
    """La raíz de datos, configurable por variable de entorno."""
    return os.environ.get("RAUDA_CAPTURA_RAIZ", DEFAULT_RAIZ)


def _sanitizar(chat_id: str) -> str:
    """Un id de chat seguro como nombre de carpeta, sin perder el id."""
    nombre = _SAFE.sub("_", str(chat_id or "")).strip(" ._")
    return nombre or "sin_id"


def _a_epoch(valor):
    """Convierte el timestamp del mensaje (segundos unix) a float, o None."""
    if valor is None:
        return None
    if isinstance(valor, dict):  # por si algún día llega un Long {low, high}
        valor = valor.get("low", valor.get("seconds"))
    try:
        return float(valor)
    except (TypeError, ValueError):
        try:
            return float(str(valor))
        except (TypeError, ValueError, ValueError):  # noqa: Dup — defensivo
            return None


def _iso_de_epoch(epoch):
    """ISO legible en hora local, o None."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class Capturador:
    """Escritor idempotente y duradero de mensajes entrantes.

    Un proceso = un Capturador (el plugin crea uno solo). Es thread-safe.
    """

    def __init__(self, raiz: str | None = None, log_path: str | None = None):
        self.raiz = raiz or raiz_por_defecto()
        self.log_path = log_path or os.environ.get(
            "RAUDA_CAPTURA_LOG", os.path.join(self.raiz, "_captura.log")
        )
        self._lock = threading.Lock()
        self._vistos: set[str] = set()     # claves de dedupe ya escritas/persistidas
        self._cargados: set[str] = set()   # archivos cuyo contenido ya se cargó a _vistos

    # ------------------------------------------------------------------ log
    def _log(self, mensaje: str) -> None:
        """Log propio del plugin: a un archivo, nunca a stdout, nunca a una excepción."""
        try:
            linea = "%s\t%s\n" % (datetime.now().isoformat(timespec="seconds"), mensaje)
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(linea)
                f.flush()
        except Exception:
            pass  # el log no puede romper la captura

    # -------------------------------------------------------------- helpers
    def _carpeta(self, chat_id: str, chat_type: str | None) -> str:
        es_grupo = (str(chat_type or "").lower() in TIPOS_GRUPO) or str(chat_id or "").endswith("@g.us")
        base = self.raiz if es_grupo else os.path.join(self.raiz, SUBCARPETA_DIRECTOS)
        return os.path.join(base, _sanitizar(chat_id))

    @staticmethod
    def _clave(message_id, chat_id, sender_id, texto, hora_iso) -> str:
        """Clave de idempotencia: el id del mensaje si viene; si no, un hash estable."""
        if message_id not in (None, "", "None"):
            return "id:" + str(message_id)
        crudo = "|".join(str(x) for x in (chat_id, sender_id, texto, hora_iso))
        return "sha:" + hashlib.sha1(crudo.encode("utf-8", "replace")).hexdigest()

    def _cargar_si_hace_falta(self, ruta: str) -> None:
        """Carga una sola vez los ids ya presentes en el archivo (dedupe post-reinicio)."""
        if ruta in self._cargados:
            return
        self._cargados.add(ruta)
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        reg = json.loads(linea)
                    except Exception:
                        continue  # una línea corrupta no invalida el resto
                    clave = self._clave(
                        reg.get("id"), reg.get("grupo") or reg.get("chat_id"),
                        reg.get("de"), reg.get("texto"), reg.get("hora"),
                    )
                    self._vistos.add(clave)
        except FileNotFoundError:
            pass
        except Exception as e:
            self._log("no pude cargar el archivo %s: %r" % (ruta, e))

    @staticmethod
    def _escribir_linea(ruta: str, linea: str) -> None:
        """Append + flush + fsync: el mensaje queda en disco antes de seguir."""
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(linea)
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------- capturar
    def capturar(
        self,
        *,
        chat_id,
        chat_type=None,
        message_id=None,
        sender_id=None,
        sender_name=None,
        text=None,
        hora_epoch=None,
        hora_iso=None,
        message_type=None,
        ahora=None,
    ) -> bool:
        """Escribe el mensaje. Devuelve True si escribió, False si era duplicado/vacío.

        NUNCA levanta: cualquier fallo se registra en el log propio.
        """
        try:
            chat_id = str(chat_id or "")
            if not chat_id:
                self._log("mensaje sin chat_id: lo salteo")
                return False

            if ahora is None:
                ahora = datetime.now().isoformat(timespec="seconds")
            dia = str(ahora)[:10]

            if not hora_iso:
                hora_iso = _iso_de_epoch(hora_epoch) or ahora

            clave = self._clave(message_id, chat_id, sender_id, text, hora_iso)

            carpeta = self._carpeta(chat_id, chat_type)
            ruta = os.path.join(carpeta, str(dia) + ".jsonl")

            with self._lock:
                self._cargar_si_hace_falta(ruta)
                if clave in self._vistos:
                    return False
                # Marcar ANTES de escribir: si la escritura falla igual no reintentamos en loop.
                self._vistos.add(clave)

                registro = {
                    "id": None if message_id in (None, "") else str(message_id),
                    "hora": hora_iso,
                    "hora_epoch": hora_epoch,
                    "de": None if sender_id in (None, "") else str(sender_id),
                    "de_nombre": sender_name if sender_name is not None else None,
                    "grupo": chat_id,
                    "tipo_chat": "group" if str(chat_type or "").lower() in TIPOS_GRUPO
                                 or chat_id.endswith("@g.us") else "dm",
                    "tipo_mensaje": message_type,
                    "texto": "" if text is None else str(text),
                    "capturado_en": ahora,
                }
                linea = json.dumps(registro, ensure_ascii=False, default=str) + "\n"

                os.makedirs(carpeta, exist_ok=True)
                self._escribir_linea(ruta, linea)
            return True

        except Exception as e:  # el mundo se puede caer: igual no rompemos el pipeline
            self._log("fallo de captura (%s): %r" % (chat_id, e))
            return False
