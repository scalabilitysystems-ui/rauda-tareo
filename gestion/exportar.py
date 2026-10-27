"""
Reporte del período: el tareo resuelto y las métricas del negocio, en un solo archivo.

Qué resuelve: hoy el tareo se arma a mano cruzando planillas. Esto devuelve, para un rango de
fechas, una fila por conductor y día con todo lo que necesita la administración para liquidar
y para entender qué pasó en la calle — más una hoja de excepciones y una de números.

Tres hojas:
  1. Tareo        — una fila por conductor y día, con totales. Es lo que se usa para trabajar.
  2. Resumen      — las métricas del período (y por día), para decidir, no para cargar.
  3. Excepciones  — solo lo bloqueado o en duda, con el motivo, para que la operación lo resuelva.

Los números salen de las mismas vistas SQL que el panel: si acá dice 243, en la pantalla dice 243.
"""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import db

TINTA = "1F2933"
CLARO = "F7F8FA"
BORDE = Side(style="thin", color="D9DDE3")
CAJA = Border(left=BORDE, right=BORDE, top=BORDE, bottom=BORDE)

COLUMNAS_TAREO = [
    ("Fecha", 11, "dia"),
    ("DNI", 11, "documento"),
    ("Conductor", 30, "nombre"),
    ("Tienda", 9, "tienda"),
    ("Supervisor", 11, "supervisor"),
    ("Ingreso planificado", 10, "hora_planificada"),
    ("Hora de ingreso", 10, "hora_ingreso"),
    ("Hora de salida", 10, "hora_salida"),
    ("Vueltas", 8, "vueltas"),
    ("Pedidos", 8, "pedidos_regulares"),
    ("SAC", 6, "sac"),
    ("Total pedidos", 9, "pedidos"),
    ("Rechazados", 9, "rechazados"),
    ("Vueltas caídas", 9, "vueltas_caidas"),
    ("Tardanza (min)", 9, "tardanza_min"),
    ("Estado", 10, "estado"),
    ("Motivo", 46, "motivos"),
    ("Tocó", 7, "entregas_tocadas"),
    ("Origen", 9, "fuente_pedidos"),
    ("A pagar (S/)", 11, "a_pagar"),
]

ETIQUETA_ESTADO = {"ok": "A PAGAR", "duda": "PENDIENTE DE DATO", "falla": "PENDIENTE DE DATO",
                   "ausente": "NO TRABAJÓ"}
FILL_ESTADO = {
    "ok": None,
    "duda": PatternFill("solid", fgColor="FFF4DB"),
    "falla": PatternFill("solid", fgColor="FDE8E8"),
    "ausente": PatternFill("solid", fgColor="F1F2F4"),
}


# --- consultas --------------------------------------------------------------
def filas_tareo(cliente_id: int, desde: date, hasta: date) -> list[dict]:
    return db._consulta(
        "SELECT * FROM vista_tareo_dia"
        " WHERE cliente_id = %s AND dia BETWEEN %s AND %s"
        " ORDER BY dia, supervisor, nombre", (cliente_id, desde, hasta))


def metricas(cliente_id: int, desde: date, hasta: date) -> dict:
    fila = db._uno(
        "SELECT count(*)                                              AS filas,"
        "       count(DISTINCT dia)                                    AS dias,"
        "       count(DISTINCT conductor_id)                            AS conductores,"
        "       count(*) FILTER (WHERE pedidos_programados IS NOT NULL)  AS programados,"
        "       count(*) FILTER (WHERE estado = 'ok')                   AS a_pagar,"
        "       count(*) FILTER (WHERE estado = 'duda')                 AS pendientes_cierre,"
        "       count(*) FILTER (WHERE estado = 'falla')                AS pendientes_dato,"
        "       count(*) FILTER (WHERE estado = 'ausente')              AS no_trabajo,"
        "       coalesce(sum(pedidos) FILTER (WHERE estado = 'ok'), 0)  AS pedidos,"
        "       coalesce(sum(pedidos_regulares) FILTER (WHERE estado = 'ok'), 0) AS regulares,"
        "       coalesce(sum(sac) FILTER (WHERE estado = 'ok'), 0)      AS sac,"
        "       coalesce(sum(rechazados), 0)                            AS rechazados,"
        "       coalesce(sum(vueltas_caidas), 0)                        AS caidas,"
        "       coalesce(sum(vueltas) FILTER (WHERE estado = 'ok'), 0)  AS vueltas,"
        "       coalesce(sum(pedidos) FILTER (WHERE estado IN ('falla','duda')), 0) AS pendientes,"
        "       count(*) FILTER (WHERE tardanza_min > 15)               AS tardanzas,"
        "       coalesce(round(avg(tardanza_min) FILTER (WHERE tardanza_min > 15)), 0) AS tardanza_prom,"
        "       count(*) FILTER (WHERE hora_ingreso IS NOT NULL)        AS con_ingreso,"
        "       count(*) FILTER (WHERE tardanza_min IS NOT NULL AND tardanza_min <= 15) AS en_bloque,"
        "       coalesce(sum(entregas_tocadas), 0)                      AS tocadas,"
        "       count(*) FILTER (WHERE fuente_pedidos = 'panel')        AS desde_panel,"
        "       count(*) FILTER (WHERE fuente_pedidos = 'correccion')   AS corregidas"
        " FROM vista_tareo_dia WHERE cliente_id = %s AND dia BETWEEN %s AND %s",
        (cliente_id, desde, hasta)) or {}

    tarifa = db._uno("SELECT tarifa_pedido FROM clientes WHERE id = %s", (cliente_id,))
    tarifa = float(tarifa["tarifa_pedido"]) if tarifa else 0.0
    fila["tarifa"] = tarifa
    fila["plata"] = round(fila.get("pedidos", 0) * tarifa, 2)
    fila["sin_liquidar"] = round(fila.get("pendientes", 0) * tarifa, 2)
    fila["costo_por_fila"] = (round(fila["plata"] / fila["filas"], 2) if fila.get("filas") else 0)
    return fila


def por_dia(cliente_id: int, desde: date, hasta: date) -> list[dict]:
    return db._consulta(
        "SELECT dia, count(*) AS filas,"
        "       count(*) FILTER (WHERE estado = 'ausente') AS ausentes,"
        "       count(*) FILTER (WHERE estado IN ('falla','duda')) AS pendientes,"
        "       coalesce(sum(pedidos) FILTER (WHERE estado = 'ok'), 0) AS pedidos,"
        "       coalesce(sum(sac) FILTER (WHERE estado = 'ok'), 0) AS sac,"
        "       count(*) FILTER (WHERE tardanza_min > 15) AS tardanzas"
        " FROM vista_tareo_dia WHERE cliente_id = %s AND dia BETWEEN %s AND %s"
        " GROUP BY dia ORDER BY dia", (cliente_id, desde, hasta))


def motivos_rechazo(cliente_id: int, desde: date, hasta: date) -> list[dict]:
    return db._consulta(
        "SELECT m.motivo, count(*) AS cuantas"
        " FROM marcas m JOIN jornadas j ON j.id = m.jornada_id"
        " WHERE j.cliente_id = %s AND j.dia BETWEEN %s AND %s AND m.tipo = 'rechazado'"
        " GROUP BY m.motivo ORDER BY cuantas DESC", (cliente_id, desde, hasta))


# --- armado del archivo -----------------------------------------------------
def _titulo(hoja, texto: str, fila: int, columnas: int) -> None:
    hoja.cell(row=fila, column=1, value=texto).font = Font(bold=True, size=13, color=TINTA)
    hoja.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=columnas)


def _encabezado(hoja, fila: int, columnas: list[tuple]) -> None:
    for i, (titulo, ancho, _) in enumerate(columnas, start=1):
        celda = hoja.cell(row=fila, column=i, value=titulo)
        celda.font = Font(bold=True, color="FFFFFF", size=10)
        celda.fill = PatternFill("solid", fgColor=TINTA)
        celda.alignment = Alignment(vertical="center", wrap_text=True)
        celda.border = CAJA
        hoja.column_dimensions[get_column_letter(i)].width = ancho
    hoja.row_dimensions[fila].height = 30


def _valor(fila: dict, clave: str):
    valor = fila.get(clave)
    if clave in ("hora_planificada", "hora_ingreso", "hora_salida"):
        return (str(valor)[:5] if valor else "")
    if clave == "estado":
        return ETIQUETA_ESTADO.get(valor, valor)
    if clave == "origen":
        return {"panel": "Panel", "mensaje": "WhatsApp", "correccion": "Corregido"}.get(valor, "")
    if clave == "tienda":
        return fila.get("tienda") or fila.get("codigo_tienda") or ""
    if clave == "documento":
        return valor or "—"
    return "" if valor is None else valor


def construir(cliente: dict, desde: date, hasta: date) -> bytes:
    cid = cliente["id"]
    filas = filas_tareo(cid, desde, hasta)
    m = metricas(cid, desde, hasta)
    dias = por_dia(cid, desde, hasta)
    motivos = motivos_rechazo(cid, desde, hasta)

    wb = Workbook()

    # ---- 1. Tareo ----------------------------------------------------------
    hoja = wb.active
    hoja.title = "Tareo"
    _titulo(hoja, f"Tareo · {cliente['nombre']}", 1, len(COLUMNAS_TAREO))
    hoja.cell(row=2, column=1,
              value=f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
                    f" · {len(filas)} filas · tarifa S/ {m['tarifa']:.2f} por pedido").font = \
        Font(size=10, color="4B5563")
    _encabezado(hoja, 4, COLUMNAS_TAREO)

    fila_excel = 5
    total_pagar = 0.0
    for f in filas:
        # lo que se le paga a esa persona por ese día: pedidos (regulares + SAC) × tarifa.
        # Si la fila está bloqueada o no trabajó, no hay número: no se inventa un cero.
        if f["estado"] == "ok" and f.get("pedidos") is not None:
            a_pagar = round(f["pedidos"] * m["tarifa"], 2)
            total_pagar += a_pagar
        else:
            a_pagar = None

        for i, (_, _, clave) in enumerate(COLUMNAS_TAREO, start=1):
            valor = a_pagar if clave == "a_pagar" else _valor(f, clave)
            celda = hoja.cell(row=fila_excel, column=i, value=valor)
            celda.border = CAJA
            celda.font = Font(size=10)
            if clave in ("pedidos", "pedidos_regulares", "sac", "rechazados", "vueltas",
                         "vueltas_caidas", "tardanza_min", "entregas_tocadas", "a_pagar"):
                celda.alignment = Alignment(horizontal="right")
            if clave == "a_pagar":
                celda.number_format = "#,##0.00"
                celda.font = Font(size=10, bold=True)
            if clave == "dia":
                celda.number_format = "DD/MM/YYYY"
        relleno = FILL_ESTADO.get(f["estado"])
        if relleno:
            for i in range(1, len(COLUMNAS_TAREO) + 1):
                hoja.cell(row=fila_excel, column=i).fill = relleno
        fila_excel += 1

    # totales del período al pie: lo que se liquida y lo que queda retenido
    hoja.cell(row=fila_excel + 1, column=3, value="TOTAL DEL PERÍODO").font = Font(bold=True)
    totales = {"pedidos": m.get("pedidos", 0), "rechazados": m.get("rechazados", 0),
               "tocadas": m.get("tocadas", 0), "a_pagar": round(total_pagar, 2)}
    for columna, (_, _, clave) in enumerate(COLUMNAS_TAREO, start=1):
        if clave not in totales:
            continue
        celda = hoja.cell(row=fila_excel + 1, column=columna, value=totales[clave])
        celda.font = Font(bold=True)
        celda.alignment = Alignment(horizontal="right")
        if clave == "a_pagar":
            celda.number_format = "#,##0.00"

    hoja.freeze_panes = "C5"
    hoja.auto_filter.ref = f"A4:{get_column_letter(len(COLUMNAS_TAREO))}{fila_excel - 1}"

    # ---- 2. Resumen --------------------------------------------------------
    res = wb.create_sheet("Resumen")
    _titulo(res, "Números del período", 1, 4)
    res.cell(row=2, column=1,
             value=f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}").font = \
        Font(size=10, color="4B5563")

    con_ingreso = m.get("con_ingreso") or 0
    en_bloque = m.get("en_bloque") or 0
    filas_total = m.get("filas") or 0
    desde_panel = m.get("desde_panel") or 0
    correcciones = m.get("corregidas") or 0

    indicadores = [
        ("Días con operación", m.get("dias", 0), "días del rango con al menos una fila"),
        ("Conductores", m.get("conductores", 0), "distintos en el período"),
        ("Registros (conductor × día)", filas_total, "una fila por conductor y día"),
        ("Pedidos liquidados", m.get("pedidos", 0), f"{m.get('regulares', 0)} regulares + {m.get('sac', 0)} SAC"),
        ("S/ a pagar por pedidos", m["plata"], f"tarifa S/ {m['tarifa']:.2f}"),
        ("S/ sin liquidar", m["sin_liquidar"],
         f"{m.get('pendientes', 0)} pedidos en filas pendientes de un dato"),
        ("S/ por conductor-día", m["costo_por_fila"], "costo medio de la operación"),
        ("Vueltas", m.get("vueltas", 0), "total declarado"),
        ("Rechazados", m.get("rechazados", 0), "pedidos que volvieron"),
        ("Vueltas caídas", m.get("caidas", 0), "vueltas que no se completaron"),
        ("No trabajó", m.get("no_trabajo", 0),
         f"{_pct(m.get('no_trabajo', 0), filas_total)} de los registros · no reportó"),
        ("Pendiente de dato", (m.get("pendientes_dato", 0) or 0) + (m.get("pendientes_cierre", 0) or 0),
         f"{m.get('pendientes_dato', 0)} por documento o dato · "
         f"{m.get('pendientes_cierre', 0)} sin cerrar el día"),
        ("Ingresos dentro del bloque", en_bloque,
         f"{_pct(en_bloque, con_ingreso)} de los que marcaron ingreso"),
        ("Tardanzas (> 15 min)", m.get("tardanzas", 0),
         f"promedio {m.get('tardanza_prom', 0)} min"),
        ("Entregas tocadas", m.get("tocadas", 0),
         f"{_pct(m.get('tocadas', 0), m.get('pedidos', 0))} de los pedidos liquidados"),
        ("Cargado desde el panel", desde_panel,
         f"{_pct(desde_panel, filas_total)} de los registros"),
        ("Corregidos a mano", correcciones, f"{_pct(correcciones, filas_total)} de los registros"),
    ]

    _encabezado(res, 4, [("Indicador", 30, ""), ("Valor", 12, ""), ("Qué significa", 52, "")])
    fila_excel = 5
    for nombre, valor, detalle in indicadores:
        res.cell(row=fila_excel, column=1, value=nombre).font = Font(bold=True, size=10)
        celda_valor = res.cell(row=fila_excel, column=2, value=valor)
        celda_valor.font = Font(size=11, bold=True)
        celda_valor.alignment = Alignment(horizontal="right")
        if isinstance(valor, float):
            celda_valor.number_format = "#,##0.00"
        res.cell(row=fila_excel, column=3, value=detalle).font = Font(size=9, color="6B7280")
        for i in range(1, 4):
            res.cell(row=fila_excel, column=i).border = CAJA
        fila_excel += 1

    # detalle por día
    fila_excel += 1
    res.cell(row=fila_excel, column=1, value="Día por día").font = Font(bold=True, size=11)
    fila_excel += 1
    _encabezado(res, fila_excel, [("Fecha", 12, ""), ("Filas", 8, ""), ("Pedidos", 10, ""),
                                  ("SAC", 7, ""), ("Pendientes", 11, ""), ("No trabajó", 11, ""),
                                  ("Tardanzas", 10, "")])
    fila_excel += 1
    for d in dias:
        valores = [d["dia"], d["filas"], d["pedidos"], d["sac"], d["pendientes"],
                   d["ausentes"], d["tardanzas"]]
        for i, valor in enumerate(valores, start=1):
            celda = res.cell(row=fila_excel, column=i, value=valor)
            celda.border = CAJA
            celda.font = Font(size=10)
            if i == 1:
                celda.number_format = "DD/MM/YYYY"
            else:
                celda.alignment = Alignment(horizontal="right")
        fila_excel += 1

    # motivo de los rechazos
    if motivos:
        fila_excel += 1
        res.cell(row=fila_excel, column=1, value="Por qué no se entregó").font = Font(bold=True, size=11)
        fila_excel += 1
        for mtr in motivos:
            res.cell(row=fila_excel, column=1,
                     value=NOMBRE_MOTIVO.get(mtr["motivo"], "Sin motivo")).font = Font(size=10)
            celda = res.cell(row=fila_excel, column=2, value=mtr["cuantas"])
            celda.alignment = Alignment(horizontal="right")
            celda.font = Font(size=10, bold=True)
            fila_excel += 1

    # ---- 3. Excepciones ----------------------------------------------------
    exc = wb.create_sheet("Excepciones")
    bloqueadas = [f for f in filas if f["estado"] in ("falla", "duda")]  # pendientes de dato
    _titulo(exc, "Pendiente de un dato", 1, 6)
    exc.cell(row=2, column=1,
             value=f"{len(bloqueadas)} filas pendientes de un dato en el período").font = \
        Font(size=10, color="4B5563")
    _encabezado(exc, 4, [("Fecha", 12, ""), ("Conductor", 30, ""), ("DNI", 12, ""),
                         ("Estado", 12, ""), ("Motivo", 60, ""), ("Pedidos", 9, "")])
    fila_excel = 5
    for f in bloqueadas:
        valores = [f["dia"], f["nombre"], f["documento"] or "—",
                   ETIQUETA_ESTADO.get(f["estado"], f["estado"]), f["motivos"], f["pedidos"]]
        for i, valor in enumerate(valores, start=1):
            celda = exc.cell(row=fila_excel, column=i, value=valor)
            celda.border = CAJA
            celda.font = Font(size=10)
            if i == 1:
                celda.number_format = "DD/MM/YYYY"
            if i == 5:
                celda.alignment = Alignment(wrap_text=True, vertical="top")
        fila_excel += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


NOMBRE_MOTIVO = {
    "no_habia_quien_reciba": "No había quién reciba",
    "direccion_erronea": "Dirección errónea",
    "rechazado_por_cliente": "Lo rechazó el cliente",
    "fuera_de_horario": "Fuera de horario",
    "otro": "Otro / sin motivo",
    None: "Sin motivo",
}


def _pct(parte, total) -> str:
    try:
        return f"{round(100 * float(parte) / float(total))}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"
