"""
Lectura del cuaderno de WhatsApp capturado en disco.

El cuaderno (`~/rauda/datos/<grupo>/<AAAA-MM-DD>.jsonl`) es la fuente de verdad de la
trazabilidad: para poder decir que un valor "se leyó de verdad", el mensaje del que salió
tiene que estar acá. Este módulo solo lee; no escribe ni interpreta.
"""

from __future__ import annotations

import json
import os

RAIZ_POR_DEFECTO = os.environ.get("RAUDA_CAPTURA_RAIZ", "/home/ubuntu/rauda/datos")


def leer_archivo(ruta: str):
    """Lee un JSONL y devuelve la lista de mensajes. Una línea corrupta no invalida el resto."""
    mensajes = []
    if not os.path.isfile(ruta):
        return mensajes
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                mensajes.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
    return mensajes


def leer_grupo_dia(grupo_id: str, dia: str, raiz: str | None = None):
    """Mensajes de un grupo en un día."""
    raiz = raiz or RAIZ_POR_DEFECTO
    return leer_archivo(os.path.join(raiz, grupo_id, str(dia) + ".jsonl"))


def leer_dia(dia: str, raiz: str | None = None):
    """{grupo_id: [mensajes]} de todos los grupos para un día. Saltea `directos/`."""
    raiz = raiz or RAIZ_POR_DEFECTO
    salida = {}
    if not os.path.isdir(raiz):
        return salida
    for nombre in sorted(os.listdir(raiz)):
        carpeta = os.path.join(raiz, nombre)
        if not os.path.isdir(carpeta) or nombre == "directos":
            continue
        msgs = leer_grupo_dia(nombre, dia, raiz)
        if msgs:
            salida[nombre] = msgs
    return salida


def mensajes_planos(dia: str, raiz: str | None = None):
    """Todos los mensajes del día, sin importar el grupo."""
    return [m for grupo in leer_dia(dia, raiz).values() for m in grupo]
