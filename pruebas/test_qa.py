"""
Pruebas del QA de control de cierres de reparto (Rauda).

La idea no es probar que el QA "anda": es PLANTAR cada error y demostrar que el control
lo atrapa. Cada prueba mete un defecto concreto en un día por lo demás correcto y verifica
que el veredicto y la evidencia sean los que corresponden. Al final hay un día entero
correcto que tiene que dar los cinco OK, para que el QA no sea un detector de todo.

Sin dependencias externas: solo unittest.

Correr:
    python3 /home/ubuntu/rauda/pruebas/test_qa.py -v
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAUDA = os.path.dirname(AQUI)
LIB = os.path.join(RAUDA, "lib")
sys.path.insert(0, LIB)

import cuaderno  # noqa: E402
import informe  # noqa: E402
import qa  # noqa: E402


# ---------------------------------------------------------------------------
# Un día completo y correcto. Cada prueba copia esto y le planta un solo error.
# Datos inventados: ningún documento ni nombre es de una persona real.
# ---------------------------------------------------------------------------
DIA = "2026-10-05"
G_A = "grupo-norte@g.us"
G_B = "grupo-sur@g.us"


def dia_ok():
    cuaderno_dia = [
        {"id": "WA-1", "hora": "2026-10-05T13:00:00", "de": "51...@s.whatsapp.net",
         "de_nombre": "Ana", "grupo": G_A, "texto": "Vuelta 3, 40 pedidos, 08:00 a 17:00"},
        {"id": "WA-2", "hora": "2026-10-05T13:05:00", "de": "51...@s.whatsapp.net",
         "de_nombre": "Beto", "grupo": G_A, "texto": "2 vueltas, 30 pedidos, 08:30 a 16:00"},
        {"id": "WA-3", "hora": "2026-10-05T13:10:00", "de": "51...@s.whatsapp.net",
         "de_nombre": "Carla", "grupo": G_B, "texto": "2 vueltas, 25 pedidos, 09:00 a 16:30"},
    ]
    recibidos = [{"id": m["id"], "hora": m["hora"]} for m in cuaderno_dia]

    conductores = {
        "C1": {"documento": "12345678", "documento_confirmado": True, "nombre": "Ana"},
        "C2": {"documento": "23456789", "documento_confirmado": True, "nombre": "Beto"},
        "C3": {"documento": "34567890", "documento_confirmado": True, "nombre": "Carla"},
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
        val("V7", "C3", "pedidos", 25, "WA-3"),
        val("V8", "C3", "vuelta", 2, "WA-3"),
    ]

    historial = {
        "C1": {"vuelta": 3.0, "pedidos": 42.0},
        "C2": {"vuelta": 2.0, "pedidos": 30.0},
        "C3": {"vuelta": 2.5, "pedidos": 26.0},
    }

    programado = [
        {"conductor_id": "C1", "dia": DIA, "pedidos_programados": 40},
        {"conductor_id": "C2", "dia": DIA, "pedidos_programados": 30},
        {"conductor_id": "C3", "dia": DIA, "pedidos_programados": 25},
    ]

    mensajes_por_conductor = {
        ("C1", DIA): ["WA-1"],
        ("C2", DIA): ["WA-2"],
        ("C3", DIA): ["WA-3"],
    }

    tareo = [
        {"fila": 10, "columna": "PEDIDOS", "valor_id": "V1", "conductor_id": "C1"},
        {"fila": 10, "columna": "VUELTAS", "valor_id": "V2", "conductor_id": "C1"},
        {"fila": 10, "columna": "INGRESO", "valor_id": "V3", "conductor_id": "C1"},
        {"fila": 10, "columna": "SALIDA", "valor_id": "V4", "conductor_id": "C1"},
        {"fila": 11, "columna": "PEDIDOS", "valor_id": "V5", "conductor_id": "C2"},
        {"fila": 11, "columna": "VUELTAS", "valor_id": "V6", "conductor_id": "C2"},
        {"fila": 12, "columna": "PEDIDOS", "valor_id": "V7", "conductor_id": "C3"},
        {"fila": 12, "columna": "VUELTAS", "valor_id": "V8", "conductor_id": "C3"},
    ]

    return {
        "recibidos": recibidos, "cuaderno": cuaderno_dia, "valores": valores,
        "conductores": conductores, "historial": historial, "programado": programado,
        "mensajes_por_conductor": mensajes_por_conductor, "tareo": tareo,
    }


def correr(datos):
    return qa.correr_controles(**datos)


def por_control(resultados):
    return {r.control: r for r in resultados}


# ===========================================================================
# 1. CAPTURA
# ===========================================================================
class TestCaptura(unittest.TestCase):
    def test_mensaje_duplicado_en_el_cuaderno_es_falla(self):
        d = dia_ok()
        d["cuaderno"] = d["cuaderno"] + [dict(d["cuaderno"][0])]  # WA-1 otra vez
        r = qa.control_captura(d["recibidos"], d["cuaderno"])
        self.assertEqual(r.veredicto, "FALLA")
        self.assertTrue(any(e.get("mensaje_id") == "WA-1" and e.get("veces") == 2
                            for e in r.evidencia))
        self.assertEqual(r.metricas["duplicados"], 1)

    def test_hueco_temporal_en_la_captura_es_falla(self):
        # El puente recibió WA-9 con su hora, y no quedó en el cuaderno: se perdió.
        d = dia_ok()
        d["recibidos"] = d["recibidos"] + [{"id": "WA-9", "hora": "2026-10-05T14:00:00"}]
        r = qa.control_captura(d["recibidos"], d["cuaderno"])
        self.assertEqual(r.veredicto, "FALLA")
        self.assertTrue(any(e.get("mensaje_id") == "WA-9" for e in r.evidencia))
        self.assertEqual(r.metricas["huecos"], 1)


# ===========================================================================
# 2. IDENTIFICACION
# ===========================================================================
class TestIdentificacion(unittest.TestCase):
    def test_documento_provisorio_es_falla_no_duda(self):
        d = dia_ok()
        d["conductores"]["C4"] = {"documento": "Pro2025",
                                  "documento_confirmado": False, "nombre": "Nuevo"}
        d["valores"].append({"id": "V9", "conductor_id": "C4", "dia": DIA,
                             "tipo": "pedidos", "valor": 10, "mensaje_id": "WA-1"})
        r = qa.control_identificacion(d["valores"], d["conductores"])
        self.assertEqual(r.veredicto, "FALLA")   # no se puede liquidar
        self.assertTrue(any(e.get("valor_id") == "V9" and e["tipo"] == "FALLA"
                            for e in r.evidencia))

    def test_otros_codigos_provisorios_tambien_son_falla(self):
        for codigo in ("123456", "Pro123$$", "Pro$0102"):
            with self.subTest(codigo=codigo):
                conductores = {"C9": {"documento": codigo,
                                      "documento_confirmado": True, "nombre": "X"}}
                valores = [{"id": "V", "conductor_id": "C9", "tipo": "pedidos",
                            "valor": 5, "mensaje_id": "WA-1"}]
                r = qa.control_identificacion(valores, conductores)
                self.assertEqual(r.veredicto, "FALLA",
                                 "%s no se rechazó como provisorio" % codigo)

    def test_valor_sin_documento_es_falla(self):
        d = dia_ok()
        d["valores"].append({"id": "V9", "conductor_id": None, "dia": DIA,
                             "tipo": "pedidos", "valor": 10, "mensaje_id": "WA-1"})
        r = qa.control_identificacion(d["valores"], d["conductores"])
        self.assertEqual(r.veredicto, "FALLA")
        self.assertTrue(any(e.get("valor_id") == "V9" for e in r.evidencia))

    def test_dos_candidatos_es_duda(self):
        d = dia_ok()
        d["valores"].append({"id": "V9", "conductor_id": None, "candidatos": ["C1", "C2"],
                             "dia": DIA, "tipo": "pedidos", "valor": 10,
                             "mensaje_id": "WA-3"})
        r = qa.control_identificacion(d["valores"], d["conductores"])
        self.assertEqual(r.veredicto, "DUDA")
        e = next(e for e in r.evidencia if e.get("valor_id") == "V9")
        self.assertEqual(e["tipo"], "DUDA")
        self.assertEqual(e["candidatos"], ["C1", "C2"])


# ===========================================================================
# 3. NUMEROS
# ===========================================================================
class TestNumeros(unittest.TestCase):
    def test_25_vueltas_contra_historico_de_8_es_duda(self):
        valores = [{"id": "V", "conductor_id": "C1", "dia": DIA, "tipo": "vuelta",
                    "valor": 25, "mensaje_id": "WA-1"}]
        historial = {"C1": {"vuelta": 8.0}}
        r = qa.control_numeros(valores, historial)
        self.assertEqual(r.veredicto, "DUDA")
        e = r.evidencia[0]
        self.assertEqual(e["valor_esperado"], 8.0)   # el esperado al lado del leído
        self.assertEqual(e["valor_leido"], 25.0)
        self.assertEqual(e["tipo"], "DUDA")          # DUDA, no FALLA: no se corrige solo

    def test_salida_antes_que_la_entrada_es_duda(self):
        valores = [
            {"id": "V1", "conductor_id": "C1", "dia": DIA, "tipo": "hora_ingreso",
             "valor": "08:00", "mensaje_id": "WA-1"},
            {"id": "V2", "conductor_id": "C1", "dia": DIA, "tipo": "hora_salida",
             "valor": "07:00", "mensaje_id": "WA-1"},
        ]
        r = qa.control_numeros(valores, {})
        self.assertEqual(r.veredicto, "DUDA")
        self.assertTrue(any("salida" in (e.get("motivo") or "") for e in r.evidencia))

    def test_jornada_de_20_horas_es_duda(self):
        valores = [
            {"id": "V1", "conductor_id": "C1", "dia": DIA, "tipo": "hora_ingreso",
             "valor": "03:00", "mensaje_id": "WA-1"},
            {"id": "V2", "conductor_id": "C1", "dia": DIA, "tipo": "hora_salida",
             "valor": "23:30", "mensaje_id": "WA-1"},
        ]
        r = qa.control_numeros(valores, {})
        self.assertEqual(r.veredicto, "DUDA")


# ===========================================================================
# 4. COMPLETITUD
# ===========================================================================
class TestCompletitud(unittest.TestCase):
    def test_programado_sin_valores_no_es_duda(self):
        # Regla del cliente: el que no reportó es porque no fue a trabajar. Va como
        # información, nunca en la cola: si no, la cola se llena de falsas alarmas.
        d = dia_ok()
        d["programado"].append({"conductor_id": "C9", "dia": DIA,
                                "pedidos_programados": 20})
        r = qa.control_completitud(d["valores"], d["programado"],
                                   d["mensajes_por_conductor"])
        self.assertEqual(r.veredicto, "OK")
        info = [e for e in r.evidencia if e.get("conductor_id") == "C9"]
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["tipo"], "INFO")
        # Y no entra en la lista de dudas del informe.
        self.assertEqual(informe._dudas_de([r], DIA), [])

    def test_hueco_de_captura_deja_el_dia_en_duda(self):
        # Un hueco de captura se disfraza de ausencia: mientras no se explique, la lista de
        # 'no reportó' no es confiable.
        d = dia_ok()
        r = qa.control_completitud(d["valores"], d["programado"],
                                   d["mensajes_por_conductor"],
                                   huecos=[{"desde": "12:00", "hasta": "13:00"}])
        self.assertEqual(r.veredicto, "DUDA")
        self.assertTrue(any(e["tipo"] == "DUDA" and "hueco" in e["motivo"]
                            for e in r.evidencia))

    def test_el_hueco_de_captura_baja_la_completitud_solo(self):
        # La cadena real: un mensaje que el puente recibió y no quedó en el cuaderno tiene
        # que marcar FALLA en captura Y dejar la completitud en duda, sin que nadie lo ate
        # a mano.
        d = dia_ok()
        recibidos = d["cuaderno"] + [{"id": "WA-PERDIDO", "hora": "12:30",
                                      "grupo_id": G_A, "texto": "cierre 12:30"}]
        res = qa.correr_controles(recibidos=recibidos, cuaderno=d["cuaderno"],
                                  valores=d["valores"], conductores=d["conductores"],
                                  historial=d["historial"], programado=d["programado"],
                                  mensajes_por_conductor=d["mensajes_por_conductor"],
                                  tareo=d["tareo"])
        por_control = {r.control: r for r in res}
        self.assertEqual(por_control["captura"].veredicto, "FALLA")
        self.assertEqual(por_control["completitud"].veredicto, "DUDA")

    def test_mensaje_capturado_sin_valores_es_falla(self):
        d = dia_ok()
        # Llegó el mensaje de C4 (WA-8) y no se extrajo ningún valor: plata sin facturar.
        d["mensajes_por_conductor"][("C4", DIA)] = ["WA-8"]
        r = qa.control_completitud(d["valores"], d["programado"],
                                   d["mensajes_por_conductor"])
        self.assertEqual(r.veredicto, "FALLA")
        self.assertTrue(any(e.get("conductor_id") == "C4" and e["tipo"] == "FALLA"
                            for e in r.evidencia))


# ===========================================================================
# 5. FIDELIDAD  (la regla dura)
# ===========================================================================
class TestFidelidad(unittest.TestCase):
    def test_valor_escrito_sin_mensaje_es_falla_grave(self):
        d = dia_ok()
        # Un valor que no salió de ningún mensaje y, sin embargo, terminó en el tareo.
        d["valores"].append({"id": "V9", "conductor_id": "C1", "dia": DIA,
                             "tipo": "pedidos", "valor": 50, "mensaje_id": None})
        d["tareo"].append({"fila": 13, "columna": "PEDIDOS", "valor_id": "V9",
                           "conductor_id": "C1"})
        r = qa.control_fidelidad(d["valores"], d["tareo"], d["cuaderno"])
        self.assertEqual(r.veredicto, "FALLA")
        self.assertTrue(any(e.get("valor_id") == "V9" for e in r.evidencia))

    def test_valor_que_apunta_a_mensaje_fuera_del_cuaderno_es_falla(self):
        d = dia_ok()
        d["valores"].append({"id": "V9", "conductor_id": "C1", "dia": DIA,
                             "tipo": "pedidos", "valor": 50, "mensaje_id": "WA-999"})
        d["tareo"].append({"fila": 13, "columna": "PEDIDOS", "valor_id": "V9",
                           "conductor_id": "C1"})
        r = qa.control_fidelidad(d["valores"], d["tareo"], d["cuaderno"])
        self.assertEqual(r.veredicto, "FALLA")


# ===========================================================================
# El día correcto: los cinco OK
# ===========================================================================
class TestDiaCorrecto(unittest.TestCase):
    def test_un_dia_entero_correcto_da_los_cinco_ok(self):
        resultados = por_control(correr(dia_ok()))
        self.assertEqual(set(resultados), {"captura", "identificacion", "numeros",
                                           "completitud", "fidelidad"})
        for control, r in resultados.items():
            with self.subTest(control=control):
                self.assertEqual(r.veredicto, "OK",
                                 "%s dio %s: %s" % (control, r.veredicto, r.evidencia))
                self.assertEqual(r.evidencia, [])


# ===========================================================================
# El informe del día
# ===========================================================================
class TestInforme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="informe_test_")

    def _dia_con_problemas(self):
        d = dia_ok()
        # IDENTIFICACION: valor de 60 pedidos sin documento -> plata 180.
        d["valores"].append({"id": "V9", "conductor_id": None, "dia": DIA,
                             "tipo": "pedidos", "valor": 60, "mensaje_id": "WA-1"})
        # NUMEROS: 25 vueltas contra un histórico de 2 -> duda de número (plata 69).
        d["valores"].append({"id": "V10", "conductor_id": "C2", "dia": DIA,
                             "tipo": "vuelta", "valor": 25, "mensaje_id": "WA-2"})
        # COMPLETITUD: programado sin valores (DUDA) y mensaje sin interpretar (FALLA).
        d["programado"].append({"conductor_id": "C9", "dia": DIA,
                                "pedidos_programados": 20})
        d["mensajes_por_conductor"][("C4", DIA)] = ["WA-8"]
        # FIDELIDAD: valor escrito sin respaldo (plata 150).
        d["valores"].append({"id": "V11", "conductor_id": "C1", "dia": DIA,
                             "tipo": "pedidos", "valor": 50, "mensaje_id": None})
        d["tareo"].append({"fila": 13, "columna": "PEDIDOS", "valor_id": "V11",
                           "conductor_id": "C1"})
        return d

    def test_informe_tiene_los_cinco_controles_y_la_metrica(self):
        d = self._dia_con_problemas()
        inf = informe.generar(DIA, dudas_resueltas=2, dir_salida=self.tmp, **d)

        self.assertEqual(len(inf["controles"]), 5)
        self.assertEqual({c["control"] for c in inf["controles"]},
                         {"captura", "identificacion", "numeros", "completitud",
                          "fidelidad"})
        self.assertIn(inf["veredicto_global"], ("DUDA", "FALLA"))

        m = inf["metricas"]
        for clave in ("dudas_abiertas", "dudas_resueltas", "valores_trazados",
                      "valores_totales", "falso_flete"):
            self.assertIn(clave, m)
        self.assertEqual(m["dudas_resueltas"], 2)
        self.assertGreater(m["dudas_abiertas"], 0)
        self.assertEqual(m["valores_totales"], 11)     # V1..V11
        self.assertEqual(m["valores_trazados"], 10)    # V11 sin mensaje

    def test_dudas_ordenadas_por_plata_y_sin_documento_primero(self):
        d = self._dia_con_problemas()
        inf = informe.generar(DIA, dir_salida=self.tmp, **d)
        dudas = inf["dudas_abiertas"]
        self.assertGreater(len(dudas), 1)

        platas = [x["plata_en_juego"] for x in dudas]
        self.assertEqual(platas, sorted(platas, reverse=True),
                         "las dudas no están ordenadas por plata en juego: %s" % platas)

        idx_sin_doc = next(i for i, x in enumerate(dudas)
                           if x["control"] == "identificacion")
        idx_numero = next(i for i, x in enumerate(dudas) if x["control"] == "numeros")
        self.assertLess(idx_sin_doc, idx_numero,
                        "un valor sin documento tiene que pesar más que una duda de número")

    def test_informe_escribe_json_y_md(self):
        d = self._dia_con_problemas()
        inf = informe.generar(DIA, dir_salida=self.tmp, **d)
        ruta_json = os.path.join(self.tmp, DIA + ".json")
        ruta_md = os.path.join(self.tmp, DIA + ".md")
        self.assertTrue(os.path.isfile(ruta_json))
        self.assertTrue(os.path.isfile(ruta_md))
        with open(ruta_json, encoding="utf-8") as f:
            releido = json.load(f)
        self.assertEqual(releido["dia"], DIA)
        self.assertEqual(len(releido["controles"]), 5)
        with open(ruta_md, encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("Veredicto global", texto)
        self.assertIn("Métrica del día", texto)
        self.assertIn("FALSO FLETE", texto)


# ===========================================================================
# Integración con el cuaderno real de disco
# ===========================================================================
class TestCuadernoEnDisco(unittest.TestCase):
    def test_lee_el_jsonl_por_grupo_y_dia(self):
        raiz = tempfile.mkdtemp(prefix="cuaderno_test_")
        carpeta = os.path.join(raiz, G_A)
        os.makedirs(carpeta)
        with open(os.path.join(carpeta, DIA + ".jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "WA-1", "grupo": G_A, "texto": "hola"}) + "\n")
            f.write("{ linea corrupta\n")   # una línea rota no invalida la anterior
            f.write(json.dumps({"id": "WA-2", "grupo": G_A, "texto": "chau"}) + "\n")
        msgs = cuaderno.leer_grupo_dia(G_A, DIA, raiz)
        self.assertEqual([m["id"] for m in msgs], ["WA-1", "WA-2"])
        self.assertEqual(cuaderno.leer_dia(DIA, raiz), {G_A: msgs})


if __name__ == "__main__":
    unittest.main(verbosity=2)
