"""
Informe diario del QA de cierres de reparto (Rauda).

Junta los cinco veredictos de `qa.py`, arma la lista de dudas ordenada por plata en juego
y escribe dos cosas: un JSON (`~/rauda/informes/<AAAA-MM-DD>.json`) para consumo de otros
programas y un `.md` legible para una persona. Nada de adjetivos: números y hechos.

Además de los cinco controles, calcula la métrica del día que publica el jefe de
operaciones: pedidos programados contra pedidos reales por conductor (lo que hoy comparan
a mano en la pestaña 'FALSO FLETE' del Excel).
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import qa

DIR_INFORMES = os.environ.get("RAUDA_DIR_INFORMES", "/home/ubuntu/rauda/informes")
TARIFA_POR_PEDIDO = qa.TARIFA_POR_PEDIDO


# --- Armado -----------------------------------------------------------------
def _dudas_de(resultados, dia):
    """Convierte la evidencia DUDA/FALLA de cada control en dudas del día."""
    dudas = []
    for r in resultados:
        for e in r.evidencia:
            if e.get("tipo") not in ("DUDA", "FALLA"):
                continue
            dudas.append({
                "dia": dia,
                "control": r.control,
                "severidad": e["tipo"],
                "motivo": e.get("motivo"),
                "conductor_id": e.get("conductor_id"),
                "valor_id": e.get("valor_id"),
                "mensaje_id": e.get("mensaje_id"),
                "plata_en_juego": round(float(e.get("plata_en_juego") or 0), 2),
                "estado": "abierta",
                "detalle": e,
            })
    dudas.sort(key=lambda d: d["plata_en_juego"], reverse=True)
    return dudas


def _falso_flete(valores, programado):
    """Programado contra real por conductor. No es un error que no coincidan: se muestra.

    Cuenta como "real" solo lo que tiene mensaje que lo respalde: un valor sin origen no es
    una entrega comprobada, no debería inflar el real.
    """
    reales = {}
    for v in valores or []:
        if v.get("tipo") != qa.TIPO_PEDIDOS or not v.get("mensaje_id"):
            continue
        clave = (v.get("conductor_id"), v.get("dia"))
        try:
            reales[clave] = reales.get(clave, 0.0) + float(v.get("valor"))
        except (TypeError, ValueError):
            continue
    filas = []
    for p in programado or []:
        clave = (p.get("conductor_id"), p.get("dia"))
        prog = p.get("pedidos_programados") or 0
        real = reales.get(clave, 0.0)
        filas.append({
            "conductor_id": p.get("conductor_id"),
            "dia": p.get("dia"),
            "pedidos_programados": prog,
            "pedidos_reales": real,
            "diferencia": round(prog - real, 2),
        })
    return filas


def _trazados(valores, cuaderno):
    """Cuántos valores apuntan a un mensaje que existe en el cuaderno."""
    ids = {m.get("id") for m in (cuaderno or []) if m.get("id")}
    return sum(1 for v in (valores or []) if v.get("mensaje_id") in ids)


def armar_informe(dia, resultados, valores, programado, cuaderno, dudas_resueltas=0):
    """Arma el informe completo a partir de los cinco resultados."""
    dudas = _dudas_de(resultados, dia)
    veredicto_global = qa.veredicto_peor([r.veredicto for r in resultados])

    metricas = {
        "dudas_abiertas": len(dudas),
        "dudas_resueltas": int(dudas_resueltas),
        "valores_trazados": _trazados(valores, cuaderno),
        "valores_totales": len(valores or []),
        "plata_en_duda": round(sum(d["plata_en_juego"] for d in dudas), 2),
        "falso_flete": _falso_flete(valores, programado),
    }

    return {
        "dia": str(dia),
        "veredicto_global": veredicto_global,
        "controles": [r.a_dict() for r in resultados],
        "dudas_abiertas": dudas,
        "metricas": metricas,
        "generado_en": datetime.now().isoformat(timespec="seconds"),
    }


# --- Render -----------------------------------------------------------------
def render_md(informe):
    """Texto legible del informe. Sin adjetivos: números y hechos."""
    lineas = []
    lineas.append("# Informe del día %s" % informe["dia"])
    lineas.append("")
    lineas.append("**Veredicto global: %s.**" % informe["veredicto_global"])
    lineas.append("")

    lineas.append("## Los cinco controles")
    lineas.append("")
    lineas.append("| Control | Veredicto | Resumen |")
    lineas.append("|---|---|---|")
    for c in informe["controles"]:
        lineas.append("| %s | %s | %s |" % (c["control"], c["veredicto"], c["resumen"]))
    lineas.append("")

    for c in informe["controles"]:
        if not c["evidencia"]:
            continue
        lineas.append("### %s — evidencia" % c["control"])
        lineas.append("")
        for e in c["evidencia"]:
            partes = ["- **%s**" % e.get("motivo", "").strip()]
            for k in ("mensaje_id", "fila", "columna", "valor_id", "conductor_id",
                      "tipo_valor", "valor_leido", "valor_esperado", "candidatos",
                      "veces", "hora", "mensajes", "plata_en_juego"):
                if e.get(k) not in (None, "", []):
                    partes.append("`%s=%s`" % (k, e.get(k)))
            lineas.append(" ".join(partes))
        lineas.append("")

    lineas.append("## Dudas abiertas (por plata en juego)")
    lineas.append("")
    if not informe["dudas_abiertas"]:
        lineas.append("Ninguna.")
    else:
        lineas.append("| # | Control | Severidad | Conductor | Motivo | Plata (S/) |")
        lineas.append("|---|---|---|---|---|---|")
        for i, d in enumerate(informe["dudas_abiertas"], 1):
            lineas.append("| %d | %s | %s | %s | %s | %.2f |" % (
                i, d["control"], d["severidad"], d.get("conductor_id") or "-",
                d.get("motivo") or "-", d["plata_en_juego"]))
    lineas.append("")

    m = informe["metricas"]
    lineas.append("## Métrica del día")
    lineas.append("")
    lineas.append("- Dudas abiertas: **%d**" % m["dudas_abiertas"])
    lineas.append("- Dudas resueltas: **%d**" % m["dudas_resueltas"])
    lineas.append("- Valores trazados (con mensaje en el cuaderno): **%d de %d**" % (
        m["valores_trazados"], m["valores_totales"]))
    lineas.append("- Plata en duda: **S/ %.2f**" % m["plata_en_duda"])
    lineas.append("")

    lineas.append("## Programado vs real por conductor (FALSO FLETE)")
    lineas.append("")
    if not m["falso_flete"]:
        lineas.append("Sin datos de programado.")
    else:
        lineas.append("| Conductor | Programado | Real | Diferencia |")
        lineas.append("|---|---|---|---|")
        for f in m["falso_flete"]:
            lineas.append("| %s | %s | %s | %s |" % (
                f["conductor_id"], f["pedidos_programados"],
                ("%g" % f["pedidos_reales"]), f["diferencia"]))
    lineas.append("")
    return "\n".join(lineas)


# --- Escritura --------------------------------------------------------------
def escribir_informe(informe, dir_salida=None):
    """Escribe el JSON y el .md del día. Devuelve (ruta_json, ruta_md)."""
    dir_salida = dir_salida or DIR_INFORMES
    os.makedirs(dir_salida, exist_ok=True)
    ruta_json = os.path.join(dir_salida, informe["dia"] + ".json")
    ruta_md = os.path.join(dir_salida, informe["dia"] + ".md")
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(informe, f, ensure_ascii=False, indent=2)
    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(render_md(informe))
    return ruta_json, ruta_md


def generar(dia, *, recibidos, cuaderno, valores, conductores, historial, programado,
            mensajes_por_conductor, tareo, dudas_resueltas=0, dir_salida=None):
    """Corre los cinco controles, arma el informe, lo escribe y lo devuelve."""
    resultados = qa.correr_controles(
        recibidos=recibidos, cuaderno=cuaderno, valores=valores, conductores=conductores,
        historial=historial, programado=programado,
        mensajes_por_conductor=mensajes_por_conductor, tareo=tareo,
    )
    informe = armar_informe(dia, resultados, valores, programado, cuaderno,
                            dudas_resueltas=dudas_resueltas)
    escribir_informe(informe, dir_salida)
    return informe
