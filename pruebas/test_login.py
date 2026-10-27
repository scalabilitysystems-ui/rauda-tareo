"""Pruebas del login: cuentas, contraseñas, sesiones y auditoría.

Lo que se prueba acá no es "el formulario anda": es que **no se pueda entrar de más**. Cada
prueba corresponde a una forma concreta de entrar sin permiso, o a una promesa que el sistema
tiene que cumplir (la contraseña no se puede leer, el código no se reusa, el conductor no ve el
panel de gestión).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from fastapi.testclient import TestClient  # noqa: E402

from gestion import auth, db  # noqa: E402
from gestion.app import app  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = TestClient(app)
        with db.conexion() as con, con.cursor() as cur:
            cur.execute("DELETE FROM cuentas WHERE usuario LIKE %s", ("t.%",))
            cur.execute(
                "INSERT INTO clientes (nombre, tarifa_pedido) VALUES (%s, 3.00) RETURNING id",
                ("Pruebas login (temporal)",))
            cls.cliente_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado)"
                " VALUES (%s,'90000100','PRUEBA LOGIN',TRUE) RETURNING id", (cls.cliente_id,))
            cls.conductor_id = cur.fetchone()["id"]
            cls.dni = "90000100"

    @classmethod
    def tearDownClass(cls):
        with db.conexion() as con, con.cursor() as cur:
            cur.execute("DELETE FROM cuentas WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM conductores WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM clientes WHERE id=%s", (cls.cliente_id,))

    def admin(self, usuario: str, rol: str = "operaciones", clave: str = "ClaveLarga123"):
        """Cuenta de administración con su contraseña puesta de entrada (sin códigos)."""
        auth.crear_cuenta(usuario, usuario.upper(), rol, cliente_id=self.cliente_id,
                          clave_inicial=clave)
        return auth.por_usuario(usuario)

    def setUp(self):
        with db.conexion() as con, con.cursor() as cur:
            cur.execute("DELETE FROM cuentas WHERE cliente_id=%s", (self.cliente_id,))
            cur.execute("DELETE FROM intentos_login")


class TestClaves(Base):
    def test_la_clave_no_se_puede_leer(self):
        """Se guarda el hash, nunca la contraseña: ni la administración puede verla."""
        self.admin("t.uno", clave="MiClaveLarga1")
        fila = auth.por_usuario("t.uno")
        self.assertNotIn("MiClaveLarga1", str(fila))
        self.assertIsNotNone(fila["clave_hash"])
        self.assertNotEqual(fila["clave_hash"], "MiClaveLarga1")

    def test_clave_corta_no_sirve(self):
        with self.assertRaises(ValueError) as error:
            self.admin("t.dos", clave="abc1")
        self.assertIn("8", str(error.exception))

    def test_la_clave_no_puede_ser_el_usuario(self):
        with self.assertRaises(ValueError):
            self.admin("t.tres", clave="t.tres")

    def test_quien_no_tiene_clave_no_entra(self):
        """La cuenta existe pero sin contraseña: no hay forma de entrar todavía."""
        auth.crear_cuenta("t.seis", "T Seis", "operaciones", cliente_id=self.cliente_id)
        with self.assertRaises(ValueError):
            auth.entrar("t.seis", "cualquierClave1")

    def test_el_conductor_elige_su_clave_con_el_dni(self):
        """Primera vez del conductor: DNI + contraseña nueva. Sin códigos."""
        auth.crear_cuenta(self.dni, "PRUEBA LOGIN", "conductor", cliente_id=self.cliente_id,
                          conductor_id=self.conductor_id)
        cuenta = auth.reclamar(self.dni, "MiClavePropia1")
        self.assertEqual(cuenta["usuario"], self.dni)
        token, _ = auth.entrar(self.dni, "MiClavePropia1")
        self.assertTrue(token)

    def test_un_dni_ya_reclamado_no_se_vuelve_a_reclamar(self):
        """Si alguien más prueba el mismo DNI después, no puede pisar la clave."""
        auth.crear_cuenta(self.dni, "PRUEBA LOGIN", "conductor", cliente_id=self.cliente_id,
                          conductor_id=self.conductor_id)
        auth.reclamar(self.dni, "MiClavePropia1")
        with self.assertRaises(ValueError) as error:
            auth.reclamar(self.dni, "OtraClaveAqui1")
        self.assertIn("ya tiene contraseña", str(error.exception))

    def test_un_dni_que_no_esta_dado_de_alta_no_reclama_nada(self):
        with self.assertRaises(ValueError) as error:
            auth.reclamar("12345678", "ClaveInventada1")
        self.assertIn("no está cargado", str(error.exception))

    def test_reiniciar_deja_la_cuenta_en_cero(self):
        """Olvidó la clave (o alguien le reclamó el DNI): operaciones la reinicia y la vieja muere."""
        auth.crear_cuenta(self.dni, "PRUEBA LOGIN", "conductor", cliente_id=self.cliente_id,
                          conductor_id=self.conductor_id)
        auth.reclamar(self.dni, "LaViejaClave1")
        auth.reiniciar_clave(self.dni)
        with self.assertRaises(ValueError):
            auth.entrar(self.dni, "LaViejaClave1")
        auth.reclamar(self.dni, "LaNuevaClave1")
        token, _ = auth.entrar(self.dni, "LaNuevaClave1")
        self.assertTrue(token)


class TestSesiones(Base):
    def test_salir_invalida_el_token(self):
        self.admin("t.ocho")
        token, _ = auth.entrar("t.ocho", "ClaveLarga123")
        self.assertTrue(auth.por_token(token))
        auth.salir(token)
        self.assertIsNone(auth.por_token(token))

    def test_token_inventado_no_sirve(self):
        self.assertIsNone(auth.por_token("inventado-" + "x" * 40))

    def test_cuenta_desactivada_no_entra(self):
        self.admin("t.nueve")
        db._ejecutar("UPDATE cuentas SET activa = FALSE WHERE usuario = %s", ("t.nueve",))
        with self.assertRaises(ValueError):
            auth.entrar("t.nueve", "ClaveLarga123")


class TestPermisos(Base):
    def _cuenta(self, usuario: str, rol: str):
        cuenta = auth.crear_cuenta(usuario, usuario.upper(), rol, cliente_id=self.cliente_id,
                                   conductor_id=self.conductor_id if rol == "conductor" else None,
                                   clave_inicial="ClaveLarga123")
        return auth.por_usuario(cuenta["usuario"])

    def test_operaciones_cierra_pero_no_administra_cuentas(self):
        cuenta = self._cuenta("t.op", "operaciones")
        self.assertTrue(auth.puede(cuenta, "cerrar"))
        self.assertFalse(auth.puede(cuenta, "usuarios"))

    def test_admin_hace_todo(self):
        cuenta = self._cuenta("t.adm", "admin")
        self.assertTrue(auth.puede(cuenta, "usuarios"))
        self.assertTrue(auth.puede(cuenta, "cerrar"))

    def test_un_conductor_solo_ve_su_dia(self):
        cuenta = self._cuenta("t.con", "conductor")
        self.assertTrue(auth.puede(cuenta, "mi_dia"))
        self.assertFalse(auth.puede(cuenta, "dia"))
        self.assertFalse(auth.puede(cuenta, "cerrar"))


class TestPanel(Base):
    def test_sin_sesion_redirige_a_entrar(self):
        """Ya no aparece el cartel gris del navegador: es una página propia."""
        r = self.cli.get("/dia", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/entrar"))
        self.assertNotIn("www-authenticate", {k.lower() for k in r.headers})

    def test_la_pantalla_de_entrada_se_ve(self):
        r = self.cli.get("/entrar")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Entrar", r.text)
        self.assertIn("Es mi primera vez", r.text)

    def test_con_sesion_entra_al_dia(self):
        self.admin("t.panel")
        token, _ = auth.entrar("t.panel", "ClaveLarga123")
        r = self.cli.get("/dia", cookies={auth.COOKIE: token}, follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Rauda", r.text)

    def test_entrar_por_el_formulario_deja_la_cookie(self):
        self.admin("t.form")
        r = self.cli.post("/entrar", data={"usuario": "t.form", "clave": "ClaveLarga123",
                                           "ir": "/dia"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn(auth.COOKIE, r.cookies)

    def test_no_redirige_a_un_sitio_ajeno(self):
        """`ir=https://otro.sitio` sería un redirect abierto: se cae a /dia."""
        self.assertEqual(destino("/entrar?ir=https://malo.example"), "/dia")
        self.assertEqual(destino("/entrar?ir=//malo.example"), "/dia")
        self.assertEqual(destino("/entrar?ir=/liquidacion"), "/liquidacion")

    def test_clave_mala_no_entra_y_no_dice_por_que(self):
        self.admin("t.mala")
        r = self.cli.post("/entrar", data={"usuario": "t.mala", "clave": "otraCosa123",
                                           "ir": "/dia"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("incorrectos", r.text)
        self.assertNotIn("no existe", r.text)


def destino(url: str) -> str:
    """Reproduce la regla de `_destino_seguro` a partir de una URL de entrada."""
    from urllib.parse import parse_qs, urlparse

    from gestion.app import _destino_seguro

    return _destino_seguro(parse_qs(urlparse(url).query).get("ir", [None])[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
