"""
Controles de QA del circuito de cierres de reparto (Rauda).

Qué vigila
----------
El circuito tiene cinco pasos y cada uno puede romperse en silencio:

  1. el mensaje llega a WhatsApp y se captura en disco;
  2. se le extraen valores (pedidos, vuelta, hora de ingreso, hora de salida);
  3. cada valor se asocia a un conductor (documento/DNI);
  4. los valores se escriben en el tareo, en la celda y el día que corresponde;
  5. el tareo liquida los pagos (tarifa por pedido configurable por cliente).

Un documento mal asignado le paga el sueldo a otra persona: por eso la identificación es
un control, no un detalle de implementación.

Regla dura del cliente
----------------------
El sistema NUNCA completa una celda con un dato que no leyó. Ni un 0, ni el valor del día
anterior, ni un promedio. Si no lo leyó, la celda queda vacía y eso es una duda. El control
FIDELIDAD es el que vuelve demostrable esa regla: cada valor escrito tiene que apuntar al
mensaje del que salió, y ese mensaje tiene que estar en el cuaderno capturado.

Cómo se usa
-----------
Los controles trabajan sobre estructuras simples (listas de diccionarios), no sobre la
base: así se prueban plantando cada error sin tocar Postgres. El esquema de la base
(esquema.sql) tiene las mismas columnas, y `lib/cuaderno.py` carga el cuaderno real de
disco. Ningún control escribe ni corrige: devuelven un veredicto y su evidencia.

Cada control devuelve un Resultado con veredicto (OK / DUDA / FALLA), un resumen con
números y una evidencia concreta (ids de mensaje, filas, valores esperados vs leídos).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# --- Parámetros del negocio y del sentido común ----------------------------
TARIFA_POR_PEDIDO = 3.00        # tarifa de ejemplo por pedido (dimensiona la plata en juego; la real sale de la base)
TOLERANCIA_NUMEROS = 2.0        # cuánto se puede ir un número del histórico del propio conductor
VUELTAS_MAX = 15                # arriba de esto, una jornada de reparto no es creíble
JORNADA_MAX_MIN = 20 * 60       # 20 horas: más que esto es DUDA
PISO_PLATA = TARIFA_POR_PEDIDO  # cuando no se puede dimensionar, no es cero

TIPO_PEDIDOS = "pedidos"
TIPO_VUELTA = "vuelta"
TIPO_HORA_INGRESO = "hora_ingreso"
TIPO_HORA_SALIDA = "hora_salida"
TIPOS_NUMERICOS = (TIPO_PEDIDOS, TIPO_VUELTA)
TIPOS_HORA = (TIPO_HORA_INGRESO, TIPO_HORA_SALIDA)

_ORDEN = {"OK": 0, "DUDA": 1, "FALLA": 2}
_RE_DNI = re.compile(r"^\d{8}$")   # un DNI peruano: exactamente 8 dígitos


# --- Piezas comunes ---------------------------------------------------------
def veredicto_peor(veredictos):
    """El veredicto más grave de una lista: FALLA > DUDA > OK. Lista vacía = OK."""
    limpios = [v for v in veredictos if v in _ORDEN]
    return max(limpios, key=lambda v: _ORDEN[v]) if limpios else "OK"


def es_documento_valido(documento) -> bool:
    """Un DNI peruano: 8 dígitos, nada más.

    Todo lo demás es un código provisorio y NO se puede liquidar: 'Pro2025',
    'Pro123$$', 'Pro$0102', un '123456' de seis dígitos, un nombre, vacío.
    """
    return bool(_RE_DNI.match(str(documento or "").strip()))


@dataclass
class Resultado:
    """Lo que devuelve cada control: veredicto, resumen con números y evidencia."""

    control: str
    veredicto: str
    resumen: str
    evidencia: list = field(default_factory=list)
    metricas: dict = field(default_factory=dict)

    def a_dict(self):
        return {
            "control": self.control,
            "veredicto": self.veredicto,
            "resumen": self.resumen,
            "evidencia": self.evidencia,
            "metricas": self.metricas,
        }


def _plata_de_valor(valor) -> float:
    """Cuánta plata representa un valor, para poder ordenar las dudas."""
    if valor and str(valor.get("tipo")) == TIPO_PEDIDOS:
        try:
            return round(abs(float(valor.get("valor"))) * TARIFA_POR_PEDIDO, 2)
        except (TypeError, ValueError):
            return PISO_PLATA
    return PISO_PLATA


def _clave_conductor_dia(valor):
    return (valor.get("conductor_id"), valor.get("dia"))


# --- Control 1: CAPTURA -----------------------------------------------------
def control_captura(recibidos, cuaderno):
    """No se perdió ni se duplicó nada.

    Compara lo que el puente recibió (`recibidos`) contra lo que quedó en el cuaderno
    (`cuaderno`). El `recibidos` es un parámetro a propósito: el control se prueba sin el
    puente real.

    - Un id repetido en el cuaderno = duplicado -> FALLA.
    - Un id que el puente recibió y no está en el cuaderno = hueco -> FALLA.
    - Un id que está en el cuaderno y el puente no reportó = sobrante -> DUDA (no es
      pérdida ni duplicado, pero hay que mirarlo).
    """
    recibidos = list(recibidos or [])
    cuaderno = list(cuaderno or [])

    conteo = Counter(m.get("id") for m in cuaderno if m.get("id"))
    ids_recibidos = {m.get("id") for m in recibidos if m.get("id")}
    ids_cuaderno = set(conteo)

    evidencia = []
    for mid, veces in sorted(conteo.items()):
        if veces > 1:
            evidencia.append({
                "tipo": "FALLA",
                "motivo": "mensaje duplicado en el cuaderno",
                "mensaje_id": mid,
                "veces": veces,
                "plata_en_juego": 0.0,
            })

    huecos = sorted(ids_recibidos - ids_cuaderno)
    for mid in huecos:
        hora = next((m.get("hora") for m in recibidos if m.get("id") == mid), None)
        evidencia.append({
            "tipo": "FALLA",
            "motivo": "el puente recibió un mensaje que no quedó en el cuaderno (hueco)",
            "mensaje_id": mid,
            "hora": hora,
            "plata_en_juego": 0.0,
        })

    sobrantes = sorted(ids_cuaderno - ids_recibidos)
    for mid in sobrantes:
        evidencia.append({
            "tipo": "DUDA",
            "motivo": "hay un mensaje en el cuaderno que el puente no reportó",
            "mensaje_id": mid,
            "plata_en_juego": 0.0,
        })

    veredicto = veredicto_peor([e["tipo"] for e in evidencia])
    duplicados = sum(1 for n in conteo.values() if n > 1)
    resumen = ("%d recibidos por el puente, %d en el cuaderno, %d duplicados, "
               "%d huecos, %d sobrantes" % (
                   len(recibidos), len(cuaderno), duplicados, len(huecos), len(sobrantes)))
    return Resultado(
        "captura", veredicto, resumen, evidencia,
        {"recibidos": len(recibidos), "cuaderno": len(cuaderno),
         "duplicados": duplicados, "huecos": len(huecos), "sobrantes": len(sobrantes)},
    )


# --- Control 2: IDENTIFICACION ----------------------------------------------
def control_identificacion(valores, conductores):
    """Todo valor tiene documento, y el documento está confirmado.

    - Sin documento -> FALLA (no se sabe a quién pagarle).
    - Documento provisorio o inválido -> FALLA (no se puede liquidar).
    - Dos candidatos posibles -> DUDA (no se asigna a ciegas: el dato termina en una
      liquidación a una persona).
    """
    conductores = conductores or {}
    evidencia = []

    for v in valores or []:
        candidatos = v.get("candidatos") or []
        cid = v.get("conductor_id") or (candidatos[0] if len(candidatos) == 1 else None)
        reg = conductores.get(cid, {}) if cid else {}

        documento = v.get("documento")
        if documento in (None, ""):
            documento = reg.get("documento")
        confirmado = v.get("documento_confirmado")
        if confirmado is None:
            confirmado = reg.get("documento_confirmado", False)

        plata = _plata_de_valor(v)
        base = {
            "conductor_id": cid,
            "valor_id": v.get("id"),
            "mensaje_id": v.get("mensaje_id"),
            "documento": documento,
        }

        if len(candidatos) > 1:
            evidencia.append(dict(base, tipo="DUDA",
                motivo="dos candidatos posibles para el mismo valor; no se asigna a ciegas",
                candidatos=list(candidatos), plata_en_juego=plata))
            continue
        if not documento:
            evidencia.append(dict(base, tipo="FALLA",
                motivo="valor sin documento: no se sabe a quién pagarle",
                plata_en_juego=plata))
            continue
        if not confirmado or not es_documento_valido(documento):
            evidencia.append(dict(base, tipo="FALLA",
                motivo="documento provisorio o inválido: no se puede liquidar",
                plata_en_juego=plata))
            continue

    veredicto = veredicto_peor([e["tipo"] for e in evidencia])
    resumen = "%d valores, %d sin resolver o sin confirmar" % (
        len(valores or []), len(evidencia))
    return Resultado(
        "identificacion", veredicto, resumen, evidencia,
        {"valores": len(valores or []), "problemas": len(evidencia)},
    )


# --- Control 3: NUMEROS -----------------------------------------------------
def _minutos(hora):
    """'08:00', '08:00:00' o un ISO -> minutos desde medianoche. None si no se entiende."""
    if hora is None:
        return None
    s = str(hora).strip()
    if "T" in s:
        s = s.split("T", 1)[1]
    partes = s.split(":")
    try:
        hh = int(partes[0])
        mm = int(partes[1]) if len(partes) > 1 else 0
        return hh * 60 + mm
    except (ValueError, IndexError):
        return None


def _pares_horarios(valores):
    """Agrupa hora_ingreso / hora_salida por (conductor, día) para poder compararlas."""
    grupos = {}
    for v in valores or []:
        if v.get("tipo") in TIPOS_HORA:
            grupos.setdefault(_clave_conductor_dia(v), {})[v.get("tipo")] = v
    return grupos


def control_numeros(valores, historial):
    """Cada valor es razonable contra el historial del propio conductor.

    - pedidos / vueltas fuera del rango histórico -> DUDA (con el esperado al lado del leído).
    - vueltas fuera del rango sensato -> DUDA.
    - salida antes que la entrada, o jornada de más de 20 horas -> DUDA.
    Nunca corrige solo: marca y muestra el esperado y el leído.
    """
    historial = historial or {}
    evidencia = []

    for v in valores or []:
        tipo = v.get("tipo")
        if tipo not in TIPOS_NUMERICOS:
            continue
        try:
            num = float(v.get("valor"))
        except (TypeError, ValueError):
            continue
        cid = v.get("conductor_id")
        prom = (historial.get(cid) or {}).get(tipo)
        plata = round(abs(num) * TARIFA_POR_PEDIDO, 2) if tipo == TIPO_PEDIDOS else \
            round(abs(num - (prom or 0)) * TARIFA_POR_PEDIDO, 2)
        base = {
            "conductor_id": cid,
            "valor_id": v.get("id"),
            "mensaje_id": v.get("mensaje_id"),
            "tipo_valor": tipo,
            "valor_leido": num,
        }
        if prom:
            try:
                ratio = num / float(prom)
            except (TypeError, ValueError, ZeroDivisionError):
                ratio = None
            if ratio is not None and (ratio > TOLERANCIA_NUMEROS
                                      or ratio < 1.0 / TOLERANCIA_NUMEROS):
                evidencia.append(dict(base, tipo="DUDA",
                    motivo="el número se va del histórico del propio conductor",
                    valor_esperado=round(float(prom), 2), ratio=round(ratio, 2),
                    plata_en_juego=plata))
                continue
        if tipo == TIPO_VUELTA and not (1 <= num <= VUELTAS_MAX):
            evidencia.append(dict(base, tipo="DUDA",
                motivo="vueltas fuera del rango sensato",
                valor_esperado="1..%d" % VUELTAS_MAX, plata_en_juego=plata))

    # Horas: coherencia entre ingreso y salida del mismo conductor el mismo día.
    for (cid, dia), par in sorted(_pares_horarios(valores).items(),
                                  key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        ing = par.get(TIPO_HORA_INGRESO)
        sal = par.get(TIPO_HORA_SALIDA)
        if not ing or not sal:
            continue
        mi, ms = _minutos(ing.get("valor")), _minutos(sal.get("valor"))
        if mi is None or ms is None:
            continue
        base = {
            "conductor_id": cid,
            "dia": dia,
            "valor_leido": "%s -> %s" % (ing.get("valor"), sal.get("valor")),
            "mensaje_id": sal.get("mensaje_id"),
            "plata_en_juego": PISO_PLATA,
        }
        if ms <= mi:
            evidencia.append(dict(base, tipo="DUDA",
                motivo="la hora de salida no puede ser anterior (ni igual) a la de ingreso",
                valor_esperado="salida > ingreso"))
            continue
        if (ms - mi) > JORNADA_MAX_MIN:
            evidencia.append(dict(base, tipo="DUDA",
                motivo="la jornada pasa las 20 horas: revisar el dato",
                valor_esperado="jornada <= 20 h",
                minutos=ms - mi))

    veredicto = veredicto_peor([e["tipo"] for e in evidencia])
    resumen = "%d valores numéricos y de hora revisados, %d fuera de rango" % (
        len(valores or []), len(evidencia))
    return Resultado(
        "numeros", veredicto, resumen, evidencia,
        {"valores_revisados": len(valores or []), "fuera_de_rango": len(evidencia)},
    )


# --- Control 4: COMPLETITUD -------------------------------------------------
def control_completitud(valores, programado, mensajes_por_conductor, huecos=None):
    """Quién tenía que trabajar y no tiene valores, y quién mandó mensaje y no se interpretó.

    - Conductor programado sin ningún valor -> NO es duda: por regla del cliente, el que no
      reportó es porque no fue a trabajar. Queda como información contable, nunca en la cola.
    - Conductor del que SÍ hay mensajes pero sin valores extraídos -> FALLA (el mensaje se
      capturó y no se interpretó: plata que no se factura).
    - Si hubo un hueco de captura -> DUDA sobre el día: un hueco se disfraza de 'no fue a
      trabajar', así que mientras no se explique, la lista de ausentes no es confiable.

    `mensajes_por_conductor` va con claves (conductor_id, dia) -> [ids de mensaje].
    `huecos`: los que detectó el control de captura, con 'desde' y 'hasta'.
    """
    evidencia = []
    idx_val = {}
    for v in valores or []:
        idx_val.setdefault(_clave_conductor_dia(v), []).append(v)

    for p in programado or []:
        clave = (p.get("conductor_id"), p.get("dia"))
        if not idx_val.get(clave):
            prog = p.get("pedidos_programados") or 0
            evidencia.append({
                # No es duda: el que no reportó es porque no fue a trabajar. Va como
                # información para que la cola del día no se llene de falsas alarmas.
                "tipo": "INFO",
                "motivo": "programado sin ningún valor: por regla del cliente, no reportar es no haber ido",
                "conductor_id": p.get("conductor_id"),
                "dia": p.get("dia"),
                "pedidos_programados": prog,
                "plata_en_juego": round(float(prog) * TARIFA_POR_PEDIDO, 2),
            })

    for clave, msgs in (mensajes_por_conductor or {}).items():
        if not msgs:
            continue
        cid, dia = clave if isinstance(clave, tuple) else (clave, None)
        valores_de = idx_val.get((cid, dia)) or []
        if not valores_de:
            evidencia.append({
                "tipo": "FALLA",
                "motivo": "hay mensajes capturados y no se extrajo ningún valor (plata que no se factura)",
                "conductor_id": cid,
                "dia": dia,
                "mensajes": list(msgs),
                "plata_en_juego": PISO_PLATA,
            })
            continue
        # Un cierre que se capturó y del que NO salieron pedidos es plata que no se factura:
        # el ingreso por GPS puede haber quedado, y eso hace que el día parezca completo.
        if not any(v.get("tipo") == TIPO_PEDIDOS for v in valores_de):
            evidencia.append({
                "tipo": "FALLA",
                "motivo": "hay mensajes capturados y no se extrajeron pedidos (plata que no se factura)",
                "conductor_id": cid,
                "dia": dia,
                "mensajes": list(msgs),
                "plata_en_juego": PISO_PLATA,
            })

    # Un hueco de captura se disfraza de ausencia: si el socket estuvo caído una hora, los que
    # reportaron en esa hora quedan igual que los que no fueron. Por eso el día queda en duda
    # hasta que el hueco se explique.
    for h in huecos or []:
        evidencia.append({
            "tipo": "DUDA",
            "motivo": "hubo un hueco de captura: no se puede distinguir quién no fue de quién no llegó a reportar",
            "desde": h.get("desde"),
            "hasta": h.get("hasta"),
            "plata_en_juego": 0.0,
        })

    veredicto = veredicto_peor([e["tipo"] for e in evidencia])
    resumen = "%d programados, %d con reporte, %d mensaje(s) sin interpretar" % (
        len(programado or []),
        sum(1 for p in (programado or []) if idx_val.get((p.get("conductor_id"), p.get("dia")))),
        sum(1 for e in evidencia if e["tipo"] == "FALLA"),
    )
    return Resultado(
        "completitud", veredicto, resumen, evidencia,
        {"programados": len(programado or []), "problemas": len(evidencia)},
    )


# --- Control 5: FIDELIDAD ---------------------------------------------------
def control_fidelidad(valores, tareo, mensajes_cuaderno):
    """Ningún valor escrito sin mensaje de origen. El control que hace demostrable la regla dura.

    Para cada fila del `tareo` tiene que existir el valor que la respalda, ese valor tiene
    que tener `mensaje_id`, y ese mensaje tiene que estar en el cuaderno capturado.
    Cualquier eslabón faltante = FALLA grave.
    """
    idx_val = {v.get("id"): v for v in (valores or [])}
    ids_cuaderno = {m.get("id") for m in (mensajes_cuaderno or []) if m.get("id")}
    evidencia = []

    for fila in tareo or []:
        vid = fila.get("valor_id")
        v = idx_val.get(vid)
        base = {
            "fila": fila.get("fila"),
            "columna": fila.get("columna"),
            "celda": fila.get("celda"),
            "valor_id": vid,
            "conductor_id": fila.get("conductor_id"),
        }
        if v is None:
            evidencia.append(dict(base, tipo="FALLA", mensaje_id=None,
                motivo="el tareo tiene un valor escrito que no sale de ningún valor registrado",
                plata_en_juego=PISO_PLATA))
            continue
        mid = v.get("mensaje_id")
        if not mid:
            evidencia.append(dict(base, tipo="FALLA", mensaje_id=None,
                motivo="el valor escrito en el tareo no tiene mensaje que lo respalde (regla dura)",
                plata_en_juego=_plata_de_valor(v)))
            continue
        if mid not in ids_cuaderno:
            evidencia.append(dict(base, tipo="FALLA", mensaje_id=mid,
                motivo="el mensaje que respalda el valor no está en el cuaderno capturado",
                plata_en_juego=_plata_de_valor(v)))
            continue

    veredicto = veredicto_peor([e["tipo"] for e in evidencia])
    respaldadas = len(tareo or []) - len(evidencia)
    resumen = "%d filas de tareo, %d respaldadas por un mensaje del cuaderno, %d sin respaldo" % (
        len(tareo or []), respaldadas, len(evidencia))
    return Resultado(
        "fidelidad", veredicto, resumen, evidencia,
        {"filas_tareo": len(tareo or []), "sin_respaldo": len(evidencia)},
    )


# --- Los cinco, en orden ---------------------------------------------------
def correr_controles(*, recibidos, cuaderno, valores, conductores, historial,
                     programado, mensajes_por_conductor, tareo):
    """Corre los cinco controles y devuelve la lista de Resultado, en orden.

    La captura va primero porque su resultado alimenta a la completitud: un hueco de captura
    se disfraza de ausencia, así que el día no se puede dar por completo sin explicarlo.
    """
    captura = control_captura(recibidos, cuaderno)
    huecos = [{"desde": e.get("hora"), "hasta": e.get("hora")}
              for e in captura.evidencia if "hueco" in (e.get("motivo") or "")]
    return [
        captura,
        control_identificacion(valores, conductores),
        control_numeros(valores, historial),
        control_completitud(valores, programado, mensajes_por_conductor, huecos=huecos),
        control_fidelidad(valores, tareo, cuaderno),
    ]


__all__ = [
    "Resultado", "control_captura", "control_identificacion", "control_numeros",
    "control_completitud", "control_fidelidad", "correr_controles",
    "veredicto_peor", "es_documento_valido", "TARIFA_POR_PEDIDO",
]
