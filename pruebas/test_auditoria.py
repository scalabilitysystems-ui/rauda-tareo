"""Pruebas de regresión de la auditoría del 19/10.

Cada prueba de acá corresponde a un hallazgo concreto del informe. La idea: si alguien vuelve a
introducir el error, la prueba falla con el nombre del hallazgo, no con un 500 random.

Ninguna es teórica: las tres primeras se reprodujeron contra el sistema antes de arreglarlas.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from fastapi.testclient import TestClient  # noqa: E402

from gestion import auth, db  # noqa: E402
from gestion.app import _destino_seguro, app  # noqa: E402

CLAVE = "ClaveAuditoria1"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = TestClient(app)
        with db.conexion() as con, con.cursor() as cur:
            # dos clientes: la corrección cruzada solo se puede probar con dos
            cls.clientes = {}
            for nombre in ("Auditoría A", "Auditoría B"):
                cur.execute(
                    "INSERT INTO clientes (nombre, tarifa_pedido) VALUES (%s, 3.00) RETURNING id",
                    (nombre,))
                cls.clientes[nombre] = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO grupos (grupo_id, cliente_id, etiqueta, codigo_tienda, zona,"
                " supervisor, lat, lon, radio_m) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("g-aud@g.us", cls.clientes["Auditoría A"], "Tienda A", "AUD-1", "Lima", "operaciones",
                 -12.09, -77.00, 200))

            # conductor del cliente A, con tienda
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado,"
                " grupo_id) VALUES (%s,%s,%s,TRUE,%s) RETURNING id",
                (cls.clientes["Auditoría A"], "91000001", "CONDUCTOR A", "g-aud@g.us"))
            cls.conductor_a = cur.fetchone()["id"]

            # conductor del cliente B, SIN tienda (el caso del día perdido en silencio)
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado)"
                " VALUES (%s,%s,%s,TRUE) RETURNING id",
                (cls.clientes["Auditoría B"], "91000002", "CONDUCTOR SIN TIENDA"))
            cls.conductor_sin_tienda = cur.fetchone()["id"]

        # cuentas
        cls.cuenta_op = uuid.uuid4().hex[:6]
        auth.crear_cuenta(f"aud.op.{cls.cuenta_op}", "Operaciones A", "operaciones",
                          cliente_id=cls.clientes["Auditoría A"], clave_inicial=CLAVE)
        auth.crear_cuenta("91000002", "CONDUCTOR SIN TIENDA", "conductor",
                          cliente_id=cls.clientes["Auditoría B"],
                          conductor_id=cls.conductor_sin_tienda, clave_inicial=CLAVE)

    @classmethod
    def tearDownClass(cls):
        with db.conexion() as con, con.cursor() as cur:
            for cid in cls.clientes.values():
                for tabla in ("jornadas", "valores", "programado", "liquidaciones",
                              "correcciones_admin", "cuentas", "conductores", "grupos"):
                    cur.execute(f"DELETE FROM {tabla} WHERE cliente_id=%s", (cid,))
                cur.execute("DELETE FROM clientes WHERE id=%s", (cid,))
            # las cuentas que crea esta prueba (aud.*) también se van: si no, quedan como
            # puertas de administración vivas en producción después de cada corrida
            cur.execute("DELETE FROM sesiones WHERE cuenta_id IN"
                        " (SELECT id FROM cuentas WHERE usuario LIKE %s)", ("aud.%",))
            cur.execute("DELETE FROM cuentas WHERE usuario LIKE %s", ("aud.%",))
            cur.execute("DELETE FROM intentos_login WHERE usuario LIKE %s", ("91000%",))

    def sesion_panel(self) -> dict:
        token, _ = auth.entrar(f"aud.op.{self.cuenta_op}", CLAVE, ip="127.0.0.1")
        return {auth.COOKIE: token}


class TestCorreccionCruzada(Base):
    """CRÍTICO 1: una corrección de un cliente reescribía el tareo de otro."""

    def test_una_cuenta_sin_cliente_no_ve_todos_los_clientes(self):
        """Si a la cuenta le falta el cliente, no ve todo: no ve nada (y avisa)."""
        usuario = "aud.sin." + uuid.uuid4().hex[:6]
        auth.crear_cuenta(usuario, "Sin Cliente", "operaciones", cliente_id=None,
                          clave_inicial=CLAVE)
        token, _ = auth.entrar(usuario, CLAVE, ip="127.0.0.1")
        r = self.cli.get("/dia", cookies={auth.COOKIE: token}, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)

    def test_no_puede_corregir_un_conductor_de_otro_cliente(self):
        usuario = "aud.adm." + uuid.uuid4().hex[:6]
        auth.crear_cuenta(usuario, "Admin Auditoría", "admin", cliente_id=None,
                          clave_inicial=CLAVE)
        token, _ = auth.entrar(usuario, CLAVE, ip="127.0.0.1")
        r = self.cli.post(
            "/corregir",
            data={"conductor": self.conductor_a, "campo": "pedidos", "valor": "777",
                  "motivo": "prueba", "cliente": self.clientes["Auditoría B"],
                  "fecha": "2026-10-16"},
            cookies={auth.COOKIE: token}, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        escrito = db._uno(
            "SELECT count(*) AS n FROM correcciones_admin WHERE conductor_id = %s AND valor = %s",
            (self.conductor_a, "777"))
        self.assertEqual(escrito["n"], 0, "la corrección cruzada se escribió igual")

    def test_si_puede_corregir_su_propio_conductor(self):
        """La prueba positiva: la validación no puede bloquear el trabajo legítimo."""
        r = self.cli.post(
            "/corregir",
            data={"conductor": self.conductor_a, "campo": "pedidos", "valor": "40",
                  "motivo": "prueba", "cliente": self.clientes["Auditoría A"],
                  "fecha": "2026-10-16"},
            cookies=self.sesion_panel(), follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)


class TestSinTienda(Base):
    """CRÍTICO 2: sin tienda el día se cerraba y no se guardaba nada, sin error."""

    def test_abrir_el_dia_sin_tienda_avisa(self):
        token, _ = auth.entrar("91000002", CLAVE, ip="127.0.0.1")
        h = {"Authorization": f"Bearer {token}"}
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 20, "clave_cliente": str(uuid.uuid4())}, headers=h)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("tienda", r.json()["detail"].lower())

    def test_y_no_deja_una_jornada_a_medias(self):
        jornadas = db._uno("SELECT count(*) AS n FROM jornadas WHERE conductor_id = %s",
                           (self.conductor_sin_tienda,))
        self.assertEqual(jornadas["n"], 0)


class TestExcelDelPeriodo(Base):
    """ALTO: /reporte.xlsx devolvía 500 porque la función no recibía `request`."""

    def test_el_excel_se_descarga(self):
        r = self.cli.get(f"/reporte.xlsx?cliente={self.clientes['Auditoría A']}"
                         "&hasta=2026-10-16", cookies=self.sesion_panel())
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r.headers["content-type"])
        self.assertGreater(len(r.content), 2000)


class TestRutasViejas(Base):
    """MEDIO: `/clave` (PIN de 4 números) quedaba con un botón que daba 500."""

    def test_la_ruta_del_pin_ya_no_existe(self):
        r = self.cli.post("/clave", data={"conductor": self.conductor_a},
                          cookies=self.sesion_panel(), follow_redirects=False)
        self.assertIn(r.status_code, (404, 405))


class TestRedireccion(Base):
    """MEDIO: `ir=\\evil.com` era un redirect abierto (los navegadores normalizan la barra)."""

    def test_no_se_va_a_otro_sitio(self):
        for intento in ("https://malo.example", "//malo.example", "/\\malo.example",
                        "\\\\malo.example", ""):
            self.assertEqual(_destino_seguro(intento), "/dia", f"se escapó con {intento!r}")

    def test_si_vuelve_a_una_ruta_propia(self):
        self.assertEqual(_destino_seguro("/liquidacion?cliente=3"), "/liquidacion?cliente=3")


class TestFrenoDeIntentos(Base):
    """MEDIO: el freno por usuario dejaba afuera a la administración si alguien probaba su clave."""

    def test_el_freno_no_bloquea_a_la_cuenta_desde_otra_ip(self):
        for _ in range(12):
            auth._anotar_intento("aud.op." + self.cuenta_op, False, "10.9.9.9")
        # el atacante queda frenado en SU ip...
        self.assertTrue(auth.demasiados_intentos("aud.op." + self.cuenta_op, "10.9.9.9"))
        # ...y la dueña entra igual desde la suya
        token, _ = auth.entrar(f"aud.op.{self.cuenta_op}", CLAVE, ip="127.0.0.1")
        self.assertTrue(token)

    def test_sin_ip_se_cuenta_por_usuario(self):
        for _ in range(25):
            auth._anotar_intento("aud.op." + self.cuenta_op, False, None)
        self.assertTrue(auth.demasiados_intentos("aud.op." + self.cuenta_op))


class TestCuentaDemo(Base):
    """MEDIO: la cuenta `demo` era una puerta de administración con clave conocida."""

    def test_la_cuenta_de_demo_no_puede_entrar(self):
        cuenta = auth.por_usuario("demo")
        if cuenta is None:
            self.skipTest("la cuenta demo ya no existe")
        self.assertFalse(cuenta["activa"], "la cuenta demo sigue activa en producción")


if __name__ == "__main__":
    unittest.main(verbosity=2)
