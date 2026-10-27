"""Pruebas de las dos puertas.

El panel de gestión y la app del conductor están en hostnames distintos. Lo que se prueba acá:

  1. En el dominio del conductor **no existe el panel** (ni /entrar, ni /dia, ni /usuarios).
  2. En el dominio del panel **no existe la puerta del conductor** (ni el formulario ni el login).
  3. El login del conductor **no responde sin el link de entrada** (código de operación).
  4. Con el link, todo funciona igual que antes (la capa no rompe el trabajo).

El host se manda en la cabecera `Host`, que es lo que recibe Cloudflare.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from fastapi.testclient import TestClient  # noqa: E402

from gestion import app as modulo  # noqa: E402
from gestion import db  # noqa: E402
from gestion.app import app  # noqa: E402

CONDUCTOR = modulo.HOST_CONDUCTOR
PANEL = modulo.HOST_PANEL
CODIGO = modulo.CODIGO_ENTRADA


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = TestClient(app)

    def pedir(self, ruta: str, host: str, **kwargs):
        return self.cli.get(ruta, headers={"Host": host}, follow_redirects=False, **kwargs)

    def enviar(self, ruta: str, host: str, **kwargs):
        return self.cli.post(ruta, headers={"Host": host}, follow_redirects=False, **kwargs)


class TestDominioDelConductor(Base):
    def test_no_existe_el_panel(self):
        for ruta in ("/entrar", "/dia", "/liquidacion", "/usuarios", "/configuracion",
                     "/reporte", "/mapa"):
            self.assertEqual(self.pedir(ruta, CONDUCTOR).status_code, 404, ruta)

    def test_no_se_puede_entrar_al_panel_desde_ahi(self):
        r = self.enviar("/entrar", CONDUCTOR, data={"usuario": "admin", "clave": "x"})
        self.assertEqual(r.status_code, 404)

    def test_la_app_del_conductor_responde(self):
        r = self.pedir("/conductor", CONDUCTOR, cookies={modulo.PUERTA_COOKIE: "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Es mi primera vez", r.text)


class TestDominioDelPanel(Base):
    def test_no_existe_la_puerta_del_conductor(self):
        for ruta in ("/conductor", "/api/conductor/hoy", "/api/conductor/login"):
            self.assertEqual(self.pedir(ruta, PANEL).status_code, 404, ruta)

    def test_el_login_del_conductor_no_responde(self):
        r = self.enviar("/api/conductor/login", PANEL,
                        json={"documento": "70012341", "clave": "x"})
        self.assertEqual(r.status_code, 404)

    def test_el_panel_sigue_pidiendo_sesion(self):
        r = self.pedir("/dia", PANEL)
        self.assertEqual(r.status_code, 303)
        self.assertIn("/entrar", r.headers["location"])


class TestCapaDelCodigo(Base):
    def test_sin_el_link_el_login_no_responde(self):
        """Sin la cookie de entrada, ni el formulario ni la API de login contestan."""
        self.assertEqual(self.pedir("/conductor", CONDUCTOR).status_code, 403)
        r = self.enviar("/api/conductor/login", CONDUCTOR,
                        json={"documento": "70012341", "clave": "cualquiera1"})
        self.assertEqual(r.status_code, 403)

    def test_un_codigo_inventado_no_sirve(self):
        self.assertEqual(self.pedir("/c/NO-EXISTE", CONDUCTOR).status_code, 404)

    def test_el_link_correcto_abre_la_puerta(self):
        if not CODIGO:
            self.skipTest("no hay código de entrada configurado")
        r = self.pedir(f"/c/{CODIGO}", CONDUCTOR)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/conductor")
        self.assertIn(modulo.PUERTA_COOKIE, r.cookies)
        # con la cookie, el formulario se ve
        r = self.pedir("/conductor", CONDUCTOR, cookies={modulo.PUERTA_COOKIE: "1"})
        self.assertEqual(r.status_code, 200)

    def test_con_la_puerta_abierta_el_login_llega_a_validar(self):
        """No tiene que quedar bloqueado en la capa: con cookie, el login evalúa credenciales."""
        if not CODIGO:
            self.skipTest("no hay código de entrada configurado")
        r = self.enviar("/api/conductor/login", CONDUCTOR,
                        json={"documento": "00000000", "clave": "claveInventada1"},
                        cookies={modulo.PUERTA_COOKIE: "1"})
        self.assertEqual(r.status_code, 401)   # llegó a autenticar (credenciales malas)

    def test_el_link_no_vale_en_el_dominio_del_panel(self):
        self.assertEqual(self.pedir(f"/c/{CODIGO or 'X'}", PANEL).status_code, 404)


class TestHostsDesconocidos(Base):
    def test_localhost_sigue_funcionando_igual(self):
        """En desarrollo y en las pruebas no hay dominios: no se rompe nada."""
        r = self.cli.get("/entrar", follow_redirects=False)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
