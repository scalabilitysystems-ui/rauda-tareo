"""
Pruebas del panel del conductor: PIN, jornada, toques y modo sin señal.

Lo que verifican, en criollo:
  - el PIN correcto entra y el incorrecto no, y a los 10 intentos frena;
  - "ya llegué" abre el día una sola vez, aunque el celular reintente;
  - la geocerca dice si marcó cerca o lejos de la tienda;
  - un toque repetido con la misma clave se guarda una vez (así funciona la cola offline);
  - un toque sin haber abierto el día no entra.

Corren contra `rauda_dev` con el cliente DEMO. Después limpian lo que crearon.

    ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_demo_gestion.py
    ~/rauda/.venv/bin/python ~/rauda/pruebas/seed_conductor.py
    ~/rauda/.venv/bin/python ~/rauda/pruebas/test_conductor.py -v
"""

from __future__ import annotations

import hashlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from fastapi.testclient import TestClient  # noqa: E402

from gestion import auth, db  # noqa: E402
from gestion.app import app  # noqa: E402

CLAVE_DEMO = "ClaveDePrueba9"


def _hash_clave(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), sal.encode(), auth.ITERACIONES).hex()


class Base(unittest.TestCase):
    """Las pruebas corren sobre un cliente propio y descartable.

    Antes usaban los conductores del cliente DEMO y lo ensuciaban (y peor: borraban su día).
    Así, el demo queda intacto y las pruebas se pueden correr en cualquier momento.
    """

    @classmethod
    def setUpClass(cls):
        cls.cli = TestClient(app)
        # UNA sola conexión y UNA transacción para armar todo: `db` abre una conexión por
        # sentencia, y la de `auth.crear_cuenta` puede terminar en otra sesión de Postgres que
        # todavía no ve el cliente recién insertado (la FK la rechaza). En una transacción sola
        # el orden no depende de la suerte.
        with db.conexion() as con, con.cursor() as cur:
            base = cur.execute("SELECT id FROM clientes WHERE nombre = %s",
                               ("Pruebas (temporal)",)) and cur.fetchall()
            for fila in base:
                antiguo = fila["id"]
                for tabla in ("jornadas", "valores", "programado", "liquidaciones",
                              "correcciones_admin", "cuentas", "conductores", "grupos"):
                    cur.execute(f"DELETE FROM {tabla} WHERE cliente_id=%s", (antiguo,))
                cur.execute("DELETE FROM clientes WHERE id=%s", (antiguo,))
            cur.execute(
                "INSERT INTO clientes (nombre, tarifa_pedido) VALUES (%s, 3.00) RETURNING id",
                ("Pruebas (temporal)",))
            cls.cliente_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO grupos (grupo_id, cliente_id, etiqueta, codigo_tienda, zona,"
                " supervisor, lat, lon, radio_m)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("g-pruebas@g.us", cls.cliente_id, "Tienda de prueba", "TST-1", "Prueba",
                 "Prueba", -11.9900, -77.0600, 150))

            cls.c1, cls.c2 = None, None
            for i, (documento, telefono) in enumerate(
                    [("90000001", "51900000001"), ("90000002", "51900000002")], start=1):
                cur.execute(
                    "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado,"
                    " telefono, grupo_id)"
                    " VALUES (%s,%s,%s,TRUE,%s,%s)"
                    " RETURNING id, documento, cliente_id, nombre",
                    (cls.cliente_id, documento, f"PRUEBA CONDUCTOR {i}", telefono,
                     "g-pruebas@g.us"))
                fila = cur.fetchone()

                # La cuenta nace sin clave y se activa con el código: es el camino real.
                # Se escribe acá (no con `auth.crear_cuenta`) para que todo quede en la misma
                # transacción; la lógica de `auth` se prueba aparte, en su propio archivo.
                sal = f"test-{i}-c"
                cur.execute(
                    "INSERT INTO cuentas (usuario, nombre, rol, cliente_id, conductor_id,"
                    " clave_salt, clave_hash, clave_actualizada_en)"
                    " VALUES (%s,%s,'conductor',%s,%s,%s,%s,now()) RETURNING id",
                    (fila["documento"], fila["nombre"], cls.cliente_id, fila["id"], sal,
                     _hash_clave(CLAVE_DEMO, sal)))
                fila["usuario"] = fila["documento"]
                if i == 1:
                    cls.c1 = fila
                else:
                    cls.c2 = fila

    @classmethod
    def tearDownClass(cls):
        with db.conexion() as con, con.cursor() as cur:
            cur.execute("DELETE FROM jornadas WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM valores WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM programado WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM liquidaciones WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM correcciones_admin WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM cuentas WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM conductores WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM grupos WHERE cliente_id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM clientes WHERE id=%s", (cls.cliente_id,))
            cur.execute("DELETE FROM intentos_login")

    def _limpiar(self):
        with db.conexion() as con, con.cursor() as cur:
            for c in (self.c1, self.c2):
                cur.execute("DELETE FROM jornadas WHERE conductor_id=%s", (c["id"],))
                cur.execute(
                    "DELETE FROM valores WHERE origen='panel' AND conductor_id=%s"
                    " AND dia=(now() AT TIME ZONE 'America/Lima')::date", (c["id"],))
            cur.execute("DELETE FROM intentos_login")

    def setUp(self):
        """Cada prueba arranca con el día limpio: la llegada es idempotente y una jornada
        anterior contestaría por la siguiente."""
        self._limpiar()

    def tearDown(self):
        self._limpiar()

    def entrar(self, conductor):
        r = self.cli.post("/api/conductor/login",
                          json={"documento": conductor["documento"], "clave": CLAVE_DEMO})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": f"Bearer {r.json()['token']}"}

    @staticmethod
    def clave() -> str:
        return str(uuid.uuid4())

    def abrir(self, conductor):
        """Entra y abre el día del conductor. Lo usan las pruebas de cierre y de mapa."""
        h = self.entrar(conductor)
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 20, "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        return h


class TestEntrada(Base):
    def test_dni_y_clave_abren_sesion(self):
        r = self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": CLAVE_DEMO})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"])

    def test_clave_incorrecta_no_entra(self):
        r = self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": "otraClave123"})
        self.assertEqual(r.status_code, 401)

    def test_dni_inexistente_no_entra(self):
        r = self.cli.post("/api/conductor/login",
                          json={"documento": "99999999", "clave": CLAVE_DEMO})
        self.assertEqual(r.status_code, 401)

    def test_el_mensaje_no_dice_si_el_usuario_existe(self):
        """Decir "ese usuario no existe" le regala al atacante la mitad del trabajo."""
        a = self.cli.post("/api/conductor/login",
                          json={"documento": "99999999", "clave": CLAVE_DEMO})
        b = self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": "otraClave123"})
        self.assertEqual(a.json()["detail"], b.json()["detail"])

    def test_un_conductor_no_entra_al_panel_de_gestion(self):
        """Su cuenta es de conductor: el panel de gestión no lo deja pasar."""
        r = self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": CLAVE_DEMO})
        token = r.json()["token"]
        panel = self.cli.get("/dia", cookies={auth.COOKIE: token}, follow_redirects=False)
        self.assertEqual(panel.status_code, 303)
        self.assertIn("/conductor", panel.headers["location"])

    def test_primera_vez_elige_su_clave_con_el_dni(self):
        """El camino real: entra con su DNI y elige la contraseña. Sin códigos que repartir."""
        with db.conexion() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO conductores (cliente_id, documento, nombre, documento_confirmado,"
                " grupo_id) VALUES (%s,%s,%s,TRUE,%s) RETURNING id",
                (self.cliente_id, "90000099", "PRUEBA NUEVA", "g-pruebas@g.us"))
            nuevo = cur.fetchone()["id"]
        cuenta = auth.crear_cuenta("90000099", "PRUEBA NUEVA", "conductor",
                                   cliente_id=self.cliente_id, conductor_id=nuevo)
        r = self.cli.post("/api/conductor/registrar",
                          json={"documento": "90000099", "clave": "MiClavePropia1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["token"])
        # ya tiene clave: el DNI solo no vuelve a reclamar la cuenta
        otra = self.cli.post("/api/conductor/registrar",
                             json={"documento": "90000099", "clave": "OtraClave22"})
        self.assertEqual(otra.status_code, 400)

    def test_un_dni_sin_alta_no_puede_reclamar(self):
        r = self.cli.post("/api/conductor/registrar",
                          json={"documento": "11111111", "clave": "ClaveInventada1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no está cargado", r.json()["detail"])

    def test_sin_token_no_ve_el_dia(self):
        self.assertEqual(self.cli.get("/api/conductor/hoy").status_code, 401)
        self.assertEqual(
            self.cli.get("/api/conductor/hoy", headers={"Authorization": "Bearer nope"}).status_code,
            401)

    def test_a_los_diez_intentos_frena(self):
        """Diez claves mal puestas y la cuenta queda frenada un rato: sin esto, una clave
        corta se adivina por fuerza bruta en minutos."""
        for _ in range(10):
            self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": "malMalMal1"})
        r = self.cli.post("/api/conductor/login",
                          json={"documento": self.c1["documento"], "clave": "malMalMal1"})
        self.assertEqual(r.status_code, 429)


class TestJornada(Base):
    def test_llegada_abre_el_dia_una_sola_vez(self):
        h = self.entrar(self.c1)
        cuerpo = {"pedidos": 20, "lat": -11.9932, "lon": -77.0614, "precision": 12,
                  "clave_cliente": self.clave()}
        primera = self.cli.post("/api/conductor/llegada", json=cuerpo, headers=h)
        segunda = self.cli.post("/api/conductor/llegada", json=cuerpo, headers=h)

        self.assertEqual(primera.status_code, 200)
        self.assertFalse(primera.json()["repetida"])
        self.assertTrue(segunda.json()["repetida"])
        self.assertEqual(primera.json()["pedidos_cargados"], 20)

        with db.conexion() as con, con.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM jornadas WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date",
                        (self.c1["id"],))
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_la_geocerca_dice_cerca(self):
        h = self.entrar(self.c1)
        # la tienda de prueba está en -11.9900, -77.0600: marcamos a ~20 metros
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 5, "lat": -11.9899, "lon": -77.0601, "precision": 10,
                                "clave_cliente": self.clave()}, headers=h)
        jornada = r.json()["jornada"]
        self.assertEqual(jornada["estado_ubicacion"], "cerca")
        self.assertLess(jornada["distancia_tienda_m"], 150)

    def test_la_geocerca_dice_lejos(self):
        h = self.entrar(self.c2)
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 5, "lat": -12.5000, "lon": -77.0000, "precision": 10,
                                "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.json()["jornada"]["estado_ubicacion"], "lejos")

    def test_sin_ubicacion_queda_la_hora(self):
        h = self.entrar(self.c2)
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 5, "clave_cliente": self.clave()}, headers=h)
        jornada = r.json()["jornada"]
        self.assertEqual(jornada["estado_ubicacion"], "sin_ubicacion")
        self.assertIsNotNone(jornada["abierta_en"])

    def test_reloj_corrido_queda_marcado(self):
        h = self.entrar(self.c2)
        hace_una_hora = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = self.cli.post("/api/conductor/llegada",
                          json={"pedidos": 5, "hora_telefono": hace_una_hora,
                                "clave_cliente": self.clave()}, headers=h)
        self.assertTrue(r.json()["jornada"]["reloj_sospechoso"])

    def test_carga_por_vuelta(self):
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 20, "clave_cliente": self.clave()}, headers=h)
        r = self.cli.post("/api/conductor/carga",
                          json={"pedidos": 15, "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.json()["pedidos_cargados"], 35)
        self.assertEqual([c["vuelta"] for c in r.json()["cargas"]], [1, 2])


class TestToques(Base):
    def test_un_toque_repetido_es_un_solo_toque(self):
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 20, "clave_cliente": self.clave()}, headers=h)
        clave = self.clave()
        for _ in range(3):
            r = self.cli.post("/api/conductor/marca",
                              json={"tipo": "entregado", "lat": -11.994, "lon": -77.062,
                                    "clave_cliente": clave}, headers=h)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["toques"]["entregados"], 1)

    def test_tres_toques_distintos_cuentan_tres(self):
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 20, "clave_cliente": self.clave()}, headers=h)
        for _ in range(3):
            self.cli.post("/api/conductor/marca",
                          json={"tipo": "entregado", "clave_cliente": self.clave()}, headers=h)
        r = self.cli.get("/api/conductor/hoy", headers=h)
        self.assertEqual(r.json()["toques"]["entregados"], 3)

    def test_rechazado_y_vuelta_caida_se_cuentan_aparte(self):
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 10, "clave_cliente": self.clave()}, headers=h)
        self.cli.post("/api/conductor/marca",
                      json={"tipo": "rechazado", "clave_cliente": self.clave()}, headers=h)
        r = self.cli.post("/api/conductor/marca",
                          json={"tipo": "vuelta_caida", "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.json()["toques"], {"entregados": 0, "rechazados": 1, "vueltas_caidas": 1})

    def test_tipo_inventado_no_entra(self):
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 10, "clave_cliente": self.clave()}, headers=h)
        r = self.cli.post("/api/conductor/marca",
                          json={"tipo": "entregadísimo", "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.status_code, 422)

    def test_toque_sin_dia_abierto_no_entra(self):
        h = self.entrar(self.c2)
        with db.conexion() as con, con.cursor() as cur:
            cur.execute("DELETE FROM jornadas WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date",
                        (self.c2["id"],))
        r = self.cli.post("/api/conductor/marca",
                          json={"tipo": "entregado", "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.status_code, 409)


class TestCierre(Base):
    def test_el_cierre_escribe_los_valores_del_tareo(self):
        h = self.abrir(self.c1)
        r = self.cli.post("/api/conductor/cerrar",
                          json={"vueltas": 3, "pedidos": 41, "sac": 2, "rechazados": 1,
                                "clave_cliente": self.clave()}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cerrada"])

        with db.conexion() as con, con.cursor() as cur:
            cur.execute(
                "SELECT tipo, valor, origen, mensaje_id FROM valores"
                " WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date ORDER BY tipo", (self.c1["id"],))
            filas = {f["tipo"]: f for f in cur.fetchall()}
        self.assertEqual(filas["pedidos"]["valor"], "41")
        self.assertEqual(filas["vuelta"]["valor"], "3")
        self.assertEqual(filas["sac"]["valor"], "2")
        self.assertEqual(filas["rechazados"]["valor"], "1")
        self.assertEqual(filas["pedidos"]["origen"], "panel")
        self.assertIsNone(filas["pedidos"]["mensaje_id"])
        self.assertIn("hora_salida", filas)

    def test_el_sac_suma_al_total_que_se_liquida(self):
        h = self.abrir(self.c1)
        self.cli.post("/api/conductor/cerrar",
                      json={"vueltas": 2, "pedidos": 40, "sac": 5, "clave_cliente": self.clave()},
                      headers=h)
        fila = db._uno("SELECT pedidos, pedidos_regulares, sac FROM vista_tareo_dia"
                       " WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date", (self.c1["id"],))
        self.assertEqual(fila["pedidos_regulares"], 40)
        self.assertEqual(fila["sac"], 5)
        self.assertEqual(fila["pedidos"], 45, "el SAC suma al total, por regla del cliente")

    def test_volver_a_cerrar_corrige(self):
        h = self.abrir(self.c1)
        self.cli.post("/api/conductor/cerrar",
                      json={"vueltas": 2, "pedidos": 40, "clave_cliente": self.clave()}, headers=h)
        self.cli.post("/api/conductor/cerrar",
                      json={"vueltas": 2, "pedidos": 44, "clave_cliente": self.clave()}, headers=h)
        fila = db._uno("SELECT pedidos FROM vista_tareo_dia"
                       " WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date", (self.c1["id"],))
        self.assertEqual(fila["pedidos"], 44)

    def test_cerrar_sin_dia_abierto_no_entra(self):
        h = self.entrar(self.c2)
        r = self.cli.post("/api/conductor/cerrar",
                          json={"vueltas": 1, "pedidos": 10, "clave_cliente": self.clave()},
                          headers=h)
        self.assertEqual(r.status_code, 409)

    def test_no_se_puede_cerrar_si_la_administracion_ya_cerro_el_dia(self):
        h = self.abrir(self.c1)
        with db.conexion() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO liquidaciones (cliente_id, dia, conductor_id, pedidos_liquidados,"
                " tarifa, bruto, total, estado) VALUES (%s, (now() AT TIME ZONE 'America/Lima')::date, %s, 0, 3, 0, 0,"
                " 'calculada') ON CONFLICT (dia, conductor_id) DO NOTHING",
                (self.c1["cliente_id"], self.c1["id"]))
        try:
            r = self.cli.post("/api/conductor/cerrar",
                              json={"vueltas": 1, "pedidos": 10, "clave_cliente": self.clave()},
                              headers=h)
            self.assertEqual(r.status_code, 409)
            self.assertIn("administración", r.json()["detail"])
        finally:
            with db.conexion() as con, con.cursor() as cur:
                cur.execute("DELETE FROM liquidaciones WHERE dia=(now() AT TIME ZONE 'America/Lima')::date AND conductor_id=%s",
                            (self.c1["id"],))

    def test_los_toques_no_cambian_el_numero_que_se_paga(self):
        h = self.abrir(self.c1)
        for _ in range(5):
            self.cli.post("/api/conductor/marca",
                          json={"tipo": "entregado", "clave_cliente": self.clave()}, headers=h)
        self.cli.post("/api/conductor/cerrar",
                      json={"vueltas": 1, "pedidos": 12, "clave_cliente": self.clave()}, headers=h)
        fila = db._uno("SELECT pedidos, entregas_tocadas FROM vista_tareo_dia"
                       " WHERE conductor_id=%s AND dia=(now() AT TIME ZONE 'America/Lima')::date", (self.c1["id"],))
        self.assertEqual(fila["pedidos"], 12, "manda el cierre, no los toques")
        self.assertEqual(fila["entregas_tocadas"], 5, "los toques quedan para el control")


class TestMapa(Base):
    def test_el_mapa_devuelve_el_ultimo_punto_y_el_recorrido(self):
        # se abre el día con ubicación a propósito: el mapa dibuja lo que el conductor manda
        h = self.entrar(self.c1)
        self.cli.post("/api/conductor/llegada",
                      json={"pedidos": 20, "lat": -11.9932, "lon": -77.0614, "precision": 12,
                            "clave_cliente": self.clave()}, headers=h)
        for i in range(3):
            self.cli.post("/api/conductor/marca",
                          json={"tipo": "entregado", "lat": -11.9940 - i * 0.001,
                                "lon": -77.0620 - i * 0.001, "clave_cliente": self.clave()},
                          headers=h)
        mapa = db.mapa_dia(self.c1["cliente_id"], __import__("datetime").date.today())
        fila = next(c for c in mapa["conductores"] if c["conductor_id"] == self.c1["id"])

        self.assertEqual(fila["n_puntos"], 4, "llegada + 3 entregas con ubicación")
        self.assertEqual(fila["entregas_tocadas"], 3)
        self.assertEqual(len(fila["puntos"]), 4)
        self.assertEqual(len(fila["lats"]), 4, "el recorrido une todos los puntos")
        # el último punto es el de la última entrega, no el de la llegada
        self.assertAlmostEqual(float(fila["ultima_lat"]), -11.9960, places=3)

    def test_lo_que_se_manda_al_mapa_es_serializable(self):
        """Si esto falla, la página del mapa no renderiza (y el error se ve mal)."""
        import json
        mapa = db.mapa_dia(self.c1["cliente_id"], __import__("datetime").date.today())
        json.dumps(mapa["conductores"])   # tiene que poder irse como JSON al navegador


if __name__ == "__main__":
    unittest.main(verbosity=2)
