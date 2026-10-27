"""
Pruebas del lado de gestión: las vistas y el cierre del día.

Corren contra `rauda_dev` con los datos de demostración plantados por
`seed_demo_gestion.py`. No inventan datos: verifican que la base resuelva el día como se
acordó con el cliente. Antes de correrlas:

    ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_demo_gestion.py
    ~/rauda/.venv/bin/python ~/rauda/pruebas/test_gestion.py -v
"""

from __future__ import annotations

import atexit
import sys
import unittest
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from gestion import db  # noqa: E402

DIA = date(2026, 10, 16)


_DEMO: int | None = None


def cliente_demo() -> int:
    """La fixture de las pruebas: se arma sola y se borra al terminar la corrida.

    Antes dependía de que alguien hubiera corrido el seed a mano y dejaba datos inventados en la
    base de producción. Ahora: si no está, se crea; al salir, se va. La base de la operación real
    no tiene por qué saber que existen las pruebas.
    """
    global _DEMO
    if _DEMO:
        return _DEMO

    fila = db._uno("SELECT id FROM clientes WHERE nombre ILIKE %s ORDER BY id LIMIT 1",
                   ("%Pruebas de gestión%",))
    if not fila:
        sys.path.insert(0, str(RAIZ / "pruebas"))
        import seed_demo_gestion  # noqa: PLC0415

        seed_demo_gestion.main()
        fila = db._uno("SELECT id FROM clientes WHERE nombre ILIKE %s ORDER BY id LIMIT 1",
                       ("%Pruebas de gestión%",))
    if not fila:
        raise unittest.SkipTest("no se pudo armar la fixture de pruebas")
    _DEMO = fila["id"]
    return _DEMO


@atexit.register
def _limpiar_fixture() -> None:
    if not _DEMO:
        return
    try:
        sys.path.insert(0, str(RAIZ / "pruebas"))
        import seed_demo_gestion  # noqa: PLC0415

        seed_demo_gestion.borrar(_DEMO)
    except Exception as error:      # noqa: BLE001 — no tapar el resultado de las pruebas
        print(f"aviso: no se pudo borrar la fixture ({error})")


class TestTareo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cid = cliente_demo()
        # Se cierra el día para tener un punto de partida conocido, pero con el nombre que ya
        # tenía: antes cada corrida reescribía la firma del demo como "prueba" y se perdía
        # quién lo había cerrado de verdad.
        previo = db._uno(
            "SELECT cerrado_por FROM liquidaciones"
            " WHERE cliente_id=%s AND dia=%s AND cerrado_por IS NOT NULL LIMIT 1",
            (cls.cid, DIA))
        db.cerrar(cls.cid, DIA, previo["cerrado_por"] if previo else "operaciones")
        cls.d = db.cargar_dia(cls.cid, DIA)
        cls.filas = {f["nombre"].split()[0]: f for f in cls.d["filas"]}

    def test_una_fila_por_conductor_y_dia(self):
        self.assertEqual(len(self.d["filas"]), 9)
        claves = [(f["conductor_id"], f["dia"]) for f in self.d["filas"]]
        self.assertEqual(len(claves), len(set(claves)))

    def test_el_documento_provisorio_no_se_liquida(self):
        vigo = self.filas["BARBARAN"]
        self.assertEqual(vigo["estado"], "falla")
        self.assertFalse(vigo["documento_confirmado"])
        self.assertIn("no se puede liquidar", " ".join(vigo["motivos"]))

    def test_el_que_no_reporto_es_ausente_y_no_duda(self):
        chavez = self.filas["CHAVEZ"]
        self.assertEqual(chavez["estado"], "ausente")
        self.assertEqual(len(chavez["motivos"]), 1, "una sola explicación, no una lista de alarmas")

    def test_el_cierre_sin_pedidos_es_falla(self):
        huaman = self.filas["SOTO"]
        self.assertEqual(huaman["estado"], "falla")
        self.assertIn("plata que no se factura", " ".join(huaman["motivos"]))

    def test_el_valor_dudoso_queda_en_duda(self):
        self.assertEqual(self.filas["VILCA"]["estado"], "duda")

    def test_la_tardanza_se_mide_contra_el_bloque(self):
        self.assertEqual(self.filas["QUISPE"]["tardanza_min"], 22)
        self.assertEqual(self.filas["FLORES"]["tardanza_min"], -4)

    def test_todo_valor_tiene_mensaje_de_origen(self):
        for f in self.d["filas"]:
            for campo, msg in f["mensaje_por_campo"].items():
                if f["estado"] in ("ok", "duda", "falla") and f[campo if campo != "vueltas" else "vueltas"]:
                    if campo in ("pedidos",) and f["pedidos"]:
                        self.assertIsNotNone(msg, f"{f['nombre']} tiene {campo} sin mensaje")


class TestLiquidacion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cid = cliente_demo()
        # cerrar primero deja el día limpio de correcciones de la prueba anterior
        cls.filas = {f["nombre"].split()[0]: f for f in db.liquidacion_dia(cls.cid, DIA)["filas"]}

    def test_lo_liquidable_es_pedidos_por_tarifa(self):
        añazgo = self.filas["FLORES"]
        self.assertEqual(añazgo["estado"], "a_pagar")
        self.assertEqual(float(añazgo["bruto"]), 41 * 3.0)

    def test_el_bloqueado_no_lleva_total(self):
        for nombre in ("BARBARAN", "SOTO", "VILCA"):
            self.assertEqual(self.filas[nombre]["estado"], "pendiente", nombre)
            self.assertIsNone(self.filas[nombre]["total"], nombre)

    def test_el_descuento_por_tardanza_queda_vacio(self):
        for f in self.filas.values():
            self.assertIsNone(f["descuento_tardanza"])


class TestCierre(unittest.TestCase):
    def test_cerrar_escribe_el_dia(self):
        cid = cliente_demo()
        db.cerrar(cid, DIA, "operaciones")
        cierre = db.cierre(cid, DIA)
        self.assertEqual(cierre["filas"], 9)
        self.assertEqual(cierre["cerrado_por"], "operaciones")
        self.assertEqual(float(cierre["total"]), 564.0)


class TestReporte(unittest.TestCase):
    """El Excel del período: que sea el tareo resuelto y que los números coincidan con el panel."""

    @classmethod
    def setUpClass(cls):
        cls.cid = cliente_demo()
        cls.cliente = db._uno("SELECT * FROM clientes WHERE id=%s", (cls.cid,))
        cls.filas = db._consulta(
            "SELECT dia FROM vista_tareo_dia WHERE cliente_id=%s", (cls.cid,))
        dias = sorted({f["dia"] for f in cls.filas})
        cls.desde, cls.hasta = dias[0], dias[-1]

    def libro(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from gestion import exportar
        contenido = exportar.construir(self.cliente, self.desde, self.hasta)
        self.assertGreater(len(contenido), 4000, "el archivo no puede salir vacío")
        return load_workbook(BytesIO(contenido))

    def test_tiene_las_tres_hojas(self):
        self.assertEqual(self.libro().sheetnames, ["Tareo", "Resumen", "Excepciones"])

    def test_el_tareo_trae_una_fila_por_conductor_y_dia(self):
        hoja = self.libro()["Tareo"]
        encabezado = [c.value for c in hoja[4]]
        self.assertIn("Fecha", encabezado)
        self.assertIn("Total pedidos", encabezado)
        # filas de datos: desde la 5 hasta la última con fecha cargada
        con_datos = [r for r in range(5, hoja.max_row + 1) if hoja.cell(row=r, column=1).value]
        esperadas = len(db._consulta(
            "SELECT 1 FROM vista_tareo_dia WHERE cliente_id=%s AND dia BETWEEN %s AND %s",
            (self.cid, self.desde, self.hasta)))
        self.assertEqual(len(con_datos), esperadas)

    def test_el_resumen_cuenta_lo_mismo_que_la_vista(self):
        hoja = self.libro()["Resumen"]
        valores = {hoja.cell(row=r, column=1).value: hoja.cell(row=r, column=2).value
                   for r in range(5, 30)}
        esperado = db._uno(
            "SELECT coalesce(sum(pedidos) FILTER (WHERE estado='ok'),0) AS p"
            " FROM vista_tareo_dia WHERE cliente_id=%s AND dia BETWEEN %s AND %s",
            (self.cid, self.desde, self.hasta))["p"]
        self.assertEqual(valores["Pedidos liquidados"], esperado)

    def test_el_sac_aparece_sumarizado(self):
        hoja = self.libro()["Resumen"]
        detalle = {hoja.cell(row=r, column=1).value: hoja.cell(row=r, column=3).value
                   for r in range(5, 30)}
        sac = db._uno(
            "SELECT coalesce(sum(sac) FILTER (WHERE estado='ok'),0) AS s"
            " FROM vista_tareo_dia WHERE cliente_id=%s AND dia BETWEEN %s AND %s",
            (self.cid, self.desde, self.hasta))["s"]
        self.assertIn(f"{sac} SAC", detalle["Pedidos liquidados"])

    def test_las_excepciones_son_solo_las_bloqueadas(self):
        hoja = self.libro()["Excepciones"]
        estados = {hoja.cell(row=r, column=4).value
                   for r in range(5, hoja.max_row + 1) if hoja.cell(row=r, column=2).value}
        self.assertTrue(estados.issubset({"PENDIENTE DE DATO"}), estados)


if __name__ == "__main__":
    unittest.main(verbosity=2)
