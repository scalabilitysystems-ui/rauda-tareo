"""Panel de gestión de Rauda: el día, las excepciones y la liquidación.

Corre con uvicorn:

    cd ~/rauda && ./.venv/bin/uvicorn gestion.app:app --host 127.0.0.1 --port 8095

El cálculo vive en la base (ver `vistas_gestion.sql`). Acá solo se consulta y se dibuja.

**La puerta:** login propio con usuario y contraseña (tabla `cuentas`), sesión en una cookie
`HttpOnly`. No hay Basic Auth: el navegador no muestra ningún cartel, y cada persona entra con su
cuenta — así el cierre del día queda firmado por quien lo hizo y no por "rauda".
"""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, configuracion, conductor, db, exportar

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Rauda · control de cierres")
app.mount("/estatico", StaticFiles(directory=BASE / "estatico"), name="estatico")
app.mount("/conductor/app", StaticFiles(directory=BASE / "conductor_web"), name="conductor_app")
app.include_router(conductor.router)

# --- dos puertas, dos hostnames --------------------------------------------
# El panel de gestión y la app del conductor viven en la MISMA aplicación pero en dominios
# distintos. El dominio del conductor no expone el panel (ni /entrar, ni /dia, ni /usuarios) y el
# del panel no expone la puerta del conductor: así el que quiere romper algo no tiene a mano el
# otro edificio, y se puede poner una regla en Cloudflare por dominio sin afectar al otro.
HOST_PANEL = db.env("RAUDA_HOST_PANEL", "panel.example.com")
HOST_CONDUCTOR = db.env("RAUDA_HOST_CONDUCTOR", "conductores.example.com")

# Capa extra para el conductor: sin el código de entrada no se ve ni el formulario.
# No reemplaza a la contraseña — es un paso más para el que escanea el dominio.
CODIGO_ENTRADA = db.env("RAUDA_CODIGO_ENTRADA", "") or ""
PUERTA_COOKIE = "rauda_puerta"
RUTAS_CONDUCTOR = ("/conductor", "/api/conductor", "/c/", "/estatico", "/conductor/app")

# El conductor entra desde la calle con su propia cuenta, igual que la administración: lo que
# cambia es el rol y el edificio (la PWA vive bajo /conductor, sin el panel de gestión).
LIBRES = ("/conductor", "/api/conductor", "/c/", "/entrar", "/salir", "/estatico", "/favicon.ico")


def _cuenta_de(request: Request) -> dict | None:
    """La cuenta de la sesión, o None. Se apoya en el token de la cookie, nunca en el rol que
    diga el navegador."""
    return auth.por_token(request.cookies.get(auth.COOKIE))


@app.middleware("http")
async def puerta(request: Request, call_next):
    """Puerta de entrada del panel.

    Antes era Basic Auth: una sola clave compartida para todos, y el navegador mostraba su
    cartel gris pidiéndola. Ahora es una página de entrada propia y una cookie de sesión, así
    cada uno entra con su cuenta y el día queda firmado por quien lo cerró.
    """
    ruta = request.url.path
    host = (request.headers.get("host") or "").split(":")[0].lower()

    # --- separación por dominio (solo cuando el host es uno de los públicos) ---
    if host == HOST_CONDUCTOR:
        if not ruta.startswith(RUTAS_CONDUCTOR):
            # En el dominio del conductor no existe el panel. 404, no redirect: no se confirma
            # que haya algo del otro lado.
            return Response("No encontrado", status_code=404)
    elif host == HOST_PANEL:
        # ni la app del conductor ni su link de entrada existen en este dominio
        if ruta.startswith(("/conductor", "/api/conductor", "/c/")):
            return Response("No encontrado", status_code=404)

    # --- capa extra del conductor: el código de entrada ---
    if (host == HOST_CONDUCTOR and CODIGO_ENTRADA
            and ruta.split("?")[0] in ("/conductor", "/api/conductor/login",
                                       "/api/conductor/registrar")
            and request.cookies.get(PUERTA_COOKIE) != "1"
            and request.method != "OPTIONS"):
        # El login no se puede ni intentar sin haber entrado por el link de la operación.
        return Response(
            "Entra con el link que te dio la administración.", status_code=403,
            media_type="text/plain")

    if ruta.startswith(LIBRES):
        return await call_next(request)

    if os.environ.get("RAUDA_LOGIN_DEBUG"):
        print("DEBUG", request.method, ruta, "cookie:", bool(request.cookies.get(auth.COOKIE)),
              flush=True)

    cuenta = _cuenta_de(request)
    if cuenta is None:
        # Se guarda a dónde quería ir para volver ahí después de entrar.
        destino = ruta + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/entrar?ir={destino}", status_code=303)

    # Un conductor no entra al panel de gestión por ninguna ruta.
    if cuenta["rol"] == "conductor":
        return RedirectResponse("/conductor/hoy", status_code=303)

    request.state.cuenta = cuenta
    respuesta = await call_next(request)
    return respuesta


plantillas = Jinja2Templates(directory=str(BASE / "plantillas"))
plantillas.env.filters["soles"] = lambda v: "S/ " + f"{float(v or 0):,.2f}"
plantillas.env.filters["hora"] = lambda v: str(v)[:5] if v else ""
plantillas.env.filters["hora_local"] = db.hora_local

# Cómo se llama cada veredicto en pantalla. `falla` y `duda` son lo mismo para quien mira:
# hay un pedido que todavía no se puede pagar, y el motivo dice cuál es el trabajo pendiente.
ESTADOS = {
    "ok": ("A pagar", "ok"),
    "duda": ("Pendiente de dato", "duda"),
    "falla": ("Pendiente de dato", "falla"),
    "ausente": ("No trabajó", "ausente"),
}

CAMPOS = {
    "hora_ingreso": "Hora de ingreso",
    "hora_salida": "Hora de salida",
    "vueltas": "Vueltas",
    "pedidos": "Pedidos",
    "observacion": "Observación",
    "codigo_tienda": "Código de tienda",
    "supervisor": "Supervisor",
}


def _fecha(texto: str | None, defecto: date) -> date:
    if not texto:
        return defecto
    try:
        return datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except ValueError:
        return defecto


def _contexto(cliente_id: int | None, fecha: str | None, cuenta: dict | None = None) -> dict:
    """Todo lo que la plantilla necesita. El cliente activo sale de la URL, pero **solo si la
    cuenta tiene alcance sobre él**: el rol `operaciones` está atado a un cliente."""
    clientes = db.clientes()
    if cuenta and cuenta["rol"] != "admin":
        # Una cuenta que no es de administración SIEMPRE está atada a un cliente. Si no lo está,
        # no ve todos los clientes: no ve ninguno y se le dice. Mostrar todo "porque falta el
        # dato" es la forma más silenciosa de filtrar datos de otra operación.
        if not cuenta.get("cliente_id"):
            raise HTTPException(403, "Tu cuenta no tiene un cliente asignado. Avisa a la "
                                     "administración.")
        propios = [c for c in clientes if c["id"] == cuenta["cliente_id"]]
        if not propios:
            raise HTTPException(403, "Tu cuenta apunta a un cliente que no está activo.")
        clientes = propios
    if not clientes:
        raise RuntimeError("no hay clientes cargados")
    cliente = next((c for c in clientes if c["id"] == cliente_id), clientes[0])

    dias = db.dias_con_datos(cliente["id"])
    dia = _fecha(fecha, dias[0] if dias else date.today())

    return {
        "clientes": clientes,
        "cliente": cliente,
        "tarifa": float(cliente["tarifa_pedido"]),
        "dia": dia,
        "dias": dias,
        "d": db.cargar_dia(cliente["id"], dia),
        "estados": ESTADOS,
        "campos": CAMPOS,
        "cierre": db.cierre(cliente["id"], dia),
        "es_demo": "DEMO" in cliente["nombre"].upper(),
        "cuenta": cuenta,
        "menu": auth.PERMISOS.get((cuenta or {}).get("rol", ""), frozenset()),
    }


@app.get("/", response_class=HTMLResponse)
def raiz(request: Request, cliente: int | None = None, fecha: str | None = None):
    return plantillas.TemplateResponse(
        request, "dia.html", {**_contexto(cliente, fecha, request.state.cuenta), "vista": "dia"})


@app.get("/dia", response_class=HTMLResponse)
def ver_dia(request: Request, cliente: int | None = None, fecha: str | None = None):
    return plantillas.TemplateResponse(
        request, "dia.html", {**_contexto(cliente, fecha, request.state.cuenta), "vista": "dia"})


@app.get("/fila", response_class=HTMLResponse)
def ver_fila(request: Request, conductor: int, cliente: int | None = None,
             fecha: str | None = None):
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    fila = next((f for f in ctx["d"]["filas"] if f["conductor_id"] == conductor), None)
    if fila is None:
        return RedirectResponse(f"/dia?fecha={ctx['dia']}", status_code=303)
    return plantillas.TemplateResponse(
        request, "fila.html",
        {**ctx, "vista": "dia", "fila": fila,
         "mensajes": db.mensajes_de(ctx["d"]["filas"], conductor)})


# --- acceso del conductor ---------------------------------------------------
# Acá vivía `POST /clave`, que inventaba un PIN de 4 números y lo mostraba en pantalla. Se fue
# con el login nuevo: el conductor entra con su DNI y elige su propia contraseña, y si la
# olvida, operaciones la reinicia desde /usuarios. La ruta quedaba muerta y devolvía 500.

# --- el panel del conductor (PWA) -------------------------------------------
@app.get("/conductor", response_class=HTMLResponse)
def conductor_login(request: Request):
    return plantillas.TemplateResponse(request, "conductor_login.html", {})


@app.get("/conductor/hoy", response_class=HTMLResponse)
def conductor_hoy(request: Request):
    return plantillas.TemplateResponse(request, "conductor_hoy.html", {})


@app.get("/conductor/sw.js")
def conductor_sw():
    return FileResponse(BASE / "conductor_web" / "sw.js",
                        media_type="application/javascript")


@app.get("/conductor/manifest.webmanifest")
def conductor_manifest():
    return FileResponse(BASE / "conductor_web" / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/liquidacion", response_class=HTMLResponse)
def ver_liquidacion(request: Request, cliente: int | None = None, fecha: str | None = None):
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    liq = db.liquidacion_dia(ctx["cliente"]["id"], ctx["dia"])
    return plantillas.TemplateResponse(
        request, "liquidacion.html", {**ctx, "vista": "liquidacion", "liq": liq})


@app.get("/reporte", response_class=HTMLResponse)
def ver_reporte(request: Request, cliente: int | None = None, desde: str | None = None,
                hasta: str | None = None):
    """El reporte del período: el tareo resuelto y los números del negocio, listos para usar."""
    ctx = _contexto(cliente, hasta, request.state.cuenta)
    hasta_d = ctx["dia"]
    desde_d = _fecha(desde, hasta_d - timedelta(days=6))
    if desde_d > hasta_d:
        desde_d, hasta_d = hasta_d, desde_d
    return plantillas.TemplateResponse(
        request, "reporte.html",
        {**ctx, "vista": "reporte", "desde": desde_d, "hasta": hasta_d,
         "m": exportar.metricas(ctx["cliente"]["id"], desde_d, hasta_d),
         "por_dia": exportar.por_dia(ctx["cliente"]["id"], desde_d, hasta_d),
         "motivos": exportar.motivos_rechazo(ctx["cliente"]["id"], desde_d, hasta_d),
         "nombre_motivo": exportar.NOMBRE_MOTIVO})


@app.get("/reporte.xlsx")
def descargar_reporte(request: Request, cliente: int | None = None,
                      desde: str | None = None, hasta: str | None = None):
    ctx = _contexto(cliente, hasta, request.state.cuenta)
    hasta_d = ctx["dia"]
    desde_d = _fecha(desde, hasta_d - timedelta(days=6))
    if desde_d > hasta_d:
        desde_d, hasta_d = hasta_d, desde_d
    contenido = exportar.construir(ctx["cliente"], desde_d, hasta_d)
    nombre = f"tareo-{ctx['cliente']['id']}-{desde_d}-a-{hasta_d}.xlsx"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/revision", response_class=HTMLResponse)
def revision(request: Request, cliente: int | None = None, fecha: str | None = None):
    """Índice para revisar todo de un solo link: el día, la ficha, la liquidación, el mapa y el
    panel del conductor."""
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    dias = db.dias_con_datos(ctx["cliente"]["id"])
    fecha_demo = dias[-1] if dias else ctx["dia"]   # el día más viejo: el de la demo de WhatsApp
    return plantillas.TemplateResponse(
        request, "revision.html", {**ctx, "vista": "revision", "fecha_demo": fecha_demo})


@app.get("/mapa", response_class=HTMLResponse)
def ver_mapa(request: Request, cliente: int | None = None, fecha: str | None = None):
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    mapa = db.mapa_dia(ctx["cliente"]["id"], ctx["dia"])
    return plantillas.TemplateResponse(
        request, "mapa.html", {**ctx, "vista": "mapa", "mapa": mapa})


@app.post("/corregir")
def corregir(
    request: Request,
    conductor: int = Form(...),
    campo: str = Form(...),
    valor: str = Form(""),
    motivo: str = Form(""),
    cliente: int | None = Form(None),
    fecha: str | None = Form(None),
):
    """Una corrección lleva el nombre de quien la hizo, tomado de la sesión: antes venía en un
    campo del formulario, así que cualquiera podía firmar como otro."""
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    autor = request.state.cuenta["nombre"]

    # El conductor tiene que ser de ESTE cliente. Sin esto, con dos operaciones en la misma base
    # una corrección de un cliente reescribe el tareo y el sueldo del otro (y la firma es de
    # quien la hizo, así que además parece legítima).
    dueno = db._uno("SELECT cliente_id FROM conductores WHERE id = %s", (conductor,))
    if not dueno or dueno["cliente_id"] != ctx["cliente"]["id"]:
        raise HTTPException(403, "Ese conductor no pertenece al cliente seleccionado.")

    if campo in CAMPOS:
        with db.conexion() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO correcciones_admin"
                " (cliente_id, dia, conductor_id, campo, valor, autor, motivo)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (dia, conductor_id, campo) DO UPDATE SET valor=EXCLUDED.valor,"
                " autor=EXCLUDED.autor, motivo=EXCLUDED.motivo, creado_en=now()",
                (ctx["cliente"]["id"], ctx["dia"], conductor, campo, valor.strip() or None,
                 autor, motivo.strip() or None))
            con.commit()
        auth.auditar(request.state.cuenta, "correccion", ctx["cliente"]["id"], ctx["dia"],
                     {"conductor": conductor, "campo": campo, "valor": valor.strip()})
    return RedirectResponse(
        f"/fila?conductor={conductor}&cliente={ctx['cliente']['id']}&fecha={ctx['dia']}",
        status_code=303)


@app.post("/cerrar")
def cerrar_dia(
    request: Request,
    cliente: int | None = Form(None),
    fecha: str | None = Form(None),
):
    """Cierra el día. Queda firmado con la cuenta de la sesión — no con un nombre que viaja en
    un campo del formulario."""
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    autor = request.state.cuenta["nombre"]
    db.cerrar(ctx["cliente"]["id"], ctx["dia"], autor)
    auth.auditar(request.state.cuenta, "cierre", ctx["cliente"]["id"], ctx["dia"])
    return RedirectResponse(
        f"/liquidacion?cliente={ctx['cliente']['id']}&fecha={ctx['dia']}", status_code=303)


# --- entrada, activación y cuentas ------------------------------------------
def _fijar_cookie(respuesta, token: str) -> Response:
    """Cookie `HttpOnly`: el JavaScript de la página no la puede leer, así una inyección en la
    pantalla no se lleva la sesión. `secure` porque el dominio es https."""
    dias = auth.DIAS_SESION_CONDUCTOR
    respuesta.set_cookie(auth.COOKIE, token, max_age=dias * 86400, httponly=True,
                         secure=True, samesite="lax", path="/")
    return respuesta


def _destino_seguro(ir: str | None) -> str:
    """Solo se vuelve a una ruta propia: una dirección externa en `ir=` sería un redirect
    abierto (sirve para robar la sesión ajena)."""
    # `\` también se normaliza a `/` en los navegadores: "/\evil.com" es un redirect externo.
    if not ir or not ir.startswith("/") or ir.startswith("//") or "\\" in ir:
        return "/dia"
    return ir


@app.get("/c/{codigo}")
def puerta_del_conductor(codigo: str, request: Request):
    """Link de entrada del conductor: canjea el código por una cookie y lo manda a su app.

    El link se le da una vez (`/c/RAUDA-XXXX`); la app queda abierta en ese celular. Sin ese paso,
    el formulario de login ni siquiera responde en el dominio del conductor.
    """
    if not CODIGO_ENTRADA or codigo.strip().upper() != CODIGO_ENTRADA.upper():
        return Response("Ese link no es válido. Pídele uno nuevo a la administración.",
                        status_code=404, media_type="text/plain")
    respuesta = RedirectResponse("/conductor", status_code=303)
    respuesta.set_cookie(PUERTA_COOKIE, "1", max_age=365 * 86400, httponly=True,
                         secure=True, samesite="lax", path="/")
    return respuesta


@app.get("/entrar", response_class=HTMLResponse)
def entrar(request: Request, ir: str | None = None, usuario: str | None = None):
    return plantillas.TemplateResponse(
        request, "entrar.html",
        {"ir": _destino_seguro(ir), "usuario": usuario or "", "error": None, "modo": "entrar"})


@app.post("/entrar", response_class=HTMLResponse)
def entrar_enviar(request: Request, usuario: str = Form(...), clave: str = Form(...),
                  ir: str = Form("/dia")):
    ip = request.client.host if request.client else None
    huella = request.headers.get("user-agent", "")[:120]
    try:
        token, cuenta = auth.entrar(usuario, clave, ip=ip, huella=huella)
    except PermissionError as error:
        return plantillas.TemplateResponse(
            request, "entrar.html",
            {"ir": _destino_seguro(ir), "usuario": usuario, "error": str(error),
             "modo": "entrar"}, status_code=429)
    except ValueError:
        return plantillas.TemplateResponse(
            request, "entrar.html",
            {"ir": _destino_seguro(ir), "usuario": usuario,
             "error": "Usuario o contraseña incorrectos.", "modo": "entrar"},
            status_code=401)

    auth.auditar(cuenta, "ingreso")
    if cuenta["rol"] == "conductor":
        return _fijar_cookie(RedirectResponse("/conductor/hoy", status_code=303), token)
    return _fijar_cookie(
        RedirectResponse(_destino_seguro(ir), status_code=303), token)


@app.get("/entrar/activar", response_class=HTMLResponse)
def activar(request: Request, usuario: str | None = None):
    """Primer ingreso: la persona tiene un código de un solo uso y crea su propia clave."""
    return plantillas.TemplateResponse(
        request, "entrar.html",
        {"ir": "/dia", "usuario": usuario or "", "error": None, "modo": "activar"})


@app.post("/entrar/activar", response_class=HTMLResponse)
def activar_enviar(request: Request, usuario: str = Form(...), codigo: str = Form(...),
                   clave: str = Form(...), clave2: str = Form(...)):
    contexto = {"ir": "/dia", "usuario": usuario, "modo": "activar"}
    if clave != clave2:
        return plantillas.TemplateResponse(
            request, "entrar.html",
            {**contexto, "error": "Las dos contraseñas no coinciden."}, status_code=400)
    try:
        cuenta = auth.activar(usuario, codigo, clave)
    except ValueError as error:
        return plantillas.TemplateResponse(
            request, "entrar.html", {**contexto, "error": str(error)}, status_code=400)

    # Recién creada la clave, se lo deja adentro: no tiene sentido pedirle que la escriba otra vez.
    ip = request.client.host if request.client else None
    token, cuenta = auth.entrar(cuenta["usuario"], clave, ip=ip,
                                huella=request.headers.get("user-agent", "")[:120])
    destino = "/conductor/hoy" if cuenta["rol"] == "conductor" else "/dia"
    return _fijar_cookie(RedirectResponse(destino, status_code=303), token)


@app.post("/salir")
def salir(request: Request):
    auth.salir(request.cookies.get(auth.COOKIE))
    respuesta = RedirectResponse("/entrar", status_code=303)
    respuesta.delete_cookie(auth.COOKIE, path="/")
    return respuesta


@app.get("/mi-clave", response_class=HTMLResponse)
def mi_clave(request: Request):
    return plantillas.TemplateResponse(
        request, "mi_clave.html",
        {**_contexto(None, None, request.state.cuenta), "vista": "clave", "error": None,
         "listo": False})


@app.post("/mi-clave", response_class=HTMLResponse)
def mi_clave_enviar(request: Request, actual: str = Form(""), nueva: str = Form(...),
                    nueva2: str = Form(...)):
    cuenta = request.state.cuenta
    contexto = {**_contexto(None, None, cuenta), "vista": "clave", "error": None,
                "listo": False}
    if nueva != nueva2:
        return plantillas.TemplateResponse(
            request, "mi_clave.html", {**contexto, "error": "Las dos no coinciden."},
            status_code=400)
    try:
        auth.cambiar_clave(cuenta, actual, nueva)
    except ValueError as error:
        return plantillas.TemplateResponse(
            request, "mi_clave.html", {**contexto, "error": str(error)}, status_code=400)
    return plantillas.TemplateResponse(
        request, "mi_clave.html", {**contexto, "listo": True})


# El código de activación viaja en memoria hasta que se muestra: así no queda en el historial
# del navegador ni en un log de proxy como parte de una URL.
_CODIGOS_PENDIENTES: dict[str, dict] = {}


@app.get("/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, creado: str | None = None, codigo: str | None = None):
    """Administración de cuentas: quién puede entrar y quién hizo qué."""
    cuenta = request.state.cuenta
    if not auth.puede(cuenta, "usuarios"):
        return RedirectResponse("/dia", status_code=303)
    return plantillas.TemplateResponse(
        request, "usuarios.html",
        {**_contexto(None, None, cuenta), "vista": "usuarios",
         "cuentas": auth.cuentas(), "creado": creado, "error": None,
         "codigo": _CODIGOS_PENDIENTES.pop(codigo, None) if codigo else None,
         "auditoria": db._consulta(
             "SELECT a.cuando, a.usuario, a.accion, a.dia, a.detalle"
             " FROM auditoria a ORDER BY a.cuando DESC LIMIT 25")})


@app.post("/usuarios", response_class=HTMLResponse)
def usuarios_crear(request: Request, usuario: str = Form(...), nombre: str = Form(...),
                   rol: str = Form("operaciones"), conductor: int | None = Form(None),
                   cliente: int | None = Form(None)):
    cuenta = request.state.cuenta
    if not auth.puede(cuenta, "usuarios"):
        return RedirectResponse("/dia", status_code=303)
    try:
        if rol == "conductor":
            # El conductor no se crea desde acá: su cuenta nace con su DNI en
            # Configuración → Conductores. Inventarla acá dejaría una cuenta sin persona.
            raise ValueError("Las cuentas de conductor se crean en Configuración → Conductores.")
        if rol not in ("admin", "operaciones"):
            raise ValueError("Rol inválido.")
        nueva = auth.crear_cuenta(usuario, nombre, rol, cliente_id=cliente,
                                  conductor_id=None)
    except Exception as error:      # noqa: BLE001 — el mensaje va tal cual a la pantalla
        return plantillas.TemplateResponse(
            request, "usuarios.html",
            {**_contexto(None, None, cuenta), "vista": "usuarios", "cuentas": auth.cuentas(),
             "creado": None, "error": str(getattr(error, "args", ["no se pudo crear"])[0]),
             "auditoria": []}, status_code=400)
    auth.auditar(cuenta, "cuenta", detalle={"creada": nueva["usuario"], "rol": rol})
    llave = secrets.token_urlsafe(8)
    _CODIGOS_PENDIENTES[llave] = nueva
    return RedirectResponse(f"/usuarios?codigo={llave}", status_code=303)


@app.post("/usuarios/reiniciar")
def usuarios_reiniciar(request: Request, usuario: str = Form(...)):
    """Deja la cuenta del conductor en cero: vuelve a elegir su clave con su DNI.

    Sirve para el que olvidó la contraseña y para el caso feo: alguien reclamó un DNI que no
    era suyo. La clave vieja deja de servir en el acto.
    """
    if not auth.puede(request.state.cuenta, "usuarios"):
        return RedirectResponse("/dia", status_code=303)
    try:
        tocado = auth.reiniciar_clave(usuario)
    except ValueError:
        return RedirectResponse("/usuarios", status_code=303)
    auth.auditar(request.state.cuenta, "clave", detalle={"reinicio": tocado})
    return RedirectResponse(f"/usuarios?creado={tocado}", status_code=303)


@app.post("/usuarios/rol")
def usuarios_rol(request: Request, usuario: str = Form(...), rol: str = Form(...)):
    """Cambia entre administración y operaciones.

    Un conductor no se "convierte" en administración: su cuenta está atada a una persona y a su
    DNI. Si hace falta que alguien de la calle pase a la oficina, se crea una cuenta de gestión.
    """
    cuenta = request.state.cuenta
    if not auth.puede(cuenta, "usuarios"):
        return RedirectResponse("/dia", status_code=303)
    if rol not in ("admin", "operaciones"):
        return RedirectResponse("/usuarios", status_code=303)
    if usuario == cuenta["usuario"]:
        # cambiarse el rol a sí mismo es cómo alguien se deja afuera sin querer
        return RedirectResponse("/usuarios", status_code=303)
    destino = auth.por_usuario(usuario)
    if not destino or destino["rol"] == "conductor":
        return RedirectResponse("/usuarios", status_code=303)
    db._ejecutar("UPDATE cuentas SET rol = %s WHERE id = %s", (rol, destino["id"]))
    auth.auditar(cuenta, "cuenta", detalle={"rol_de": usuario, "rol_nuevo": rol})
    return RedirectResponse("/usuarios", status_code=303)


@app.post("/usuarios/codigo")
def usuarios_codigo(request: Request, usuario: str = Form(...)):
    """Código nuevo para quien olvidó su clave. Se muestra una sola vez."""
    if not auth.puede(request.state.cuenta, "usuarios"):
        return RedirectResponse("/dia", status_code=303)
    try:
        datos = auth.nuevo_codigo(usuario)
    except ValueError:
        return RedirectResponse("/usuarios", status_code=303)
    auth.auditar(request.state.cuenta, "clave", detalle={"codigo_nuevo": datos["usuario"]})
    llave = secrets.token_urlsafe(8)
    _CODIGOS_PENDIENTES[llave] = datos
    return RedirectResponse(f"/usuarios?codigo={llave}", status_code=303)


# --- configuración (lo que ellos cargan sin nosotros) ------------------------
@app.get("/configuracion", response_class=HTMLResponse)
def configuracion_panel(request: Request, cliente: int | None = None, aviso: str | None = None,
                        error: str | None = None):
    """Clientes, tiendas, conductores y tarifa: lo que antes pedían por teléfono."""
    cuenta = request.state.cuenta
    if not auth.puede(cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)

    operacion = configuracion.operacion(cliente)
    ctx = _contexto(None, None, cuenta)
    return plantillas.TemplateResponse(
        request, "configuracion.html",
        {**ctx, "vista": "configuracion", "operacion": operacion,
         "todos_clientes": configuracion.clientes(),
         "tarifas": configuracion.tarifas(operacion["id"]) if operacion else [],
         "tiendas": configuracion.tiendas(operacion["id"]) if operacion else [],
         "conductores": configuracion.conductores(operacion["id"]) if operacion else [],
         "hoy": configuracion.hoy_lima().isoformat(),
         "aviso": aviso, "error": error})


def _volver_configuracion(aviso: str | None = None, error: str | None = None,
                          cliente: int | None = None):
    from urllib.parse import quote

    partes = []
    if cliente:
        partes.append("cliente=" + str(cliente))
    if aviso:
        partes.append("aviso=" + quote(aviso))
    if error:
        partes.append("error=" + quote(error))
    destino = "/configuracion" + ("?" + "&".join(partes) if partes else "")
    return RedirectResponse(destino, status_code=303)


@app.post("/configuracion/tarifa")
def configuracion_tarifa(request: Request, tarifa: str = Form(...), desde: str = Form(...),
                         cliente: int | None = Form(None)):
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    operacion = configuracion.operacion(cliente)
    try:
        valor = float(tarifa.replace(",", "."))
        fecha = _fecha(desde, configuracion.hoy_lima())
        configuracion.guardar_tarifa(operacion["id"], valor, fecha, request.state.cuenta["nombre"])
    except (ValueError, TypeError) as error:
        return _volver_configuracion(error=str(error))
    auth.auditar(request.state.cuenta, "tarifa", operacion["id"], fecha,
                 {"tarifa": valor, "desde": str(fecha)})
    return _volver_configuracion(aviso=f"Tarifa de S/ {valor:.2f} cargada desde el {fecha}.",
                                 cliente=operacion["id"])


@app.post("/configuracion/tolerancia")
def configuracion_tolerancia(request: Request, minutos: str = Form(...),
                             cliente: int | None = Form(None)):
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    operacion = configuracion.operacion(cliente)
    try:
        configuracion.guardar_tolerancia(operacion["id"], int(minutos))
    except (ValueError, TypeError) as error:
        return _volver_configuracion(error=str(error), cliente=operacion["id"])
    auth.auditar(request.state.cuenta, "tolerancia", operacion["id"], None,
                 {"minutos": int(minutos)})
    return _volver_configuracion(
        aviso=f"Tolerancia de tardanza: {minutos} minutos.", cliente=operacion["id"])


@app.post("/configuracion/tienda")
def configuracion_tienda(request: Request, codigo: str = Form(...), direccion: str = Form(""),
                         zona: str = Form(""), supervisor: str = Form(""),
                         lat: str = Form(""), lon: str = Form(""), radio_m: str = Form("200"),
                         anterior: str = Form(""), cliente: int | None = Form(None)):
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    operacion = configuracion.operacion(cliente)
    try:
        configuracion.guardar_tienda(
            operacion["id"], codigo, direccion, zona, supervisor,
            float(lat) if lat else None, float(lon) if lon else None,
            int(radio_m) if radio_m else None, anterior or None)
    except (ValueError, TypeError) as error:
        return _volver_configuracion(error=str(error), cliente=operacion["id"])
    auth.auditar(request.state.cuenta, "tienda", operacion["id"], None,
                 {"codigo": codigo.strip().upper()})
    return _volver_configuracion(aviso=f"Tienda {codigo.strip().upper()} guardada.",
                                 cliente=operacion["id"])


@app.post("/configuracion/conductor")
def configuracion_conductor(request: Request, documento: str = Form(...), nombre: str = Form(...),
                            telefono: str = Form(""), grupo_id: str = Form(""),
                            conductor: int | None = Form(None),
                            cliente: int | None = Form(None)):
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    operacion = configuracion.operacion(cliente)
    try:
        nuevo = configuracion.guardar_conductor(
            operacion["id"], documento, nombre, telefono, grupo_id or None, conductor)
    except ValueError as error:
        return _volver_configuracion(error=str(error), cliente=operacion["id"])
    auth.auditar(request.state.cuenta, "conductor", operacion["id"], None,
                 {"conductor": nuevo, "evento": "edicion" if conductor else "alta"})
    return _volver_configuracion(
        aviso=f"Conductor guardado. Entra en /conductor con el DNI {documento}.",
        cliente=operacion["id"])


@app.post("/configuracion/cliente")
def configuracion_cliente(request: Request, nombre: str = Form(...), tarifa: str = Form(""),
                          cliente: int | None = Form(None), activo: str = Form("1")):
    """Alta y edición de clientes: una operación nueva no puede depender de nosotros."""
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    try:
        nuevo = configuracion.guardar_cliente(
            nombre, float(tarifa.replace(",", ".")) if tarifa.strip() else None,
            cliente, activo == "1", request.state.cuenta["nombre"])
    except (ValueError, TypeError) as error:
        return _volver_configuracion(error=str(error), cliente=cliente)
    auth.auditar(request.state.cuenta, "cliente", nuevo, None,
                 {"nombre": nombre, "evento": "edicion" if cliente else "alta"})
    return _volver_configuracion(aviso=f"Cliente {nombre} guardado.", cliente=nuevo)


@app.post("/configuracion/conductor/mover")
def configuracion_mover(request: Request, conductor: int = Form(...), destino: int = Form(...),
                        grupo_id: str = Form(""), cliente: int | None = Form(None)):
    """Pasar un conductor a otro cliente (y a una tienda de ese cliente)."""
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    try:
        configuracion.mover_conductor(conductor, destino, grupo_id or None)
    except ValueError as error:
        return _volver_configuracion(error=str(error), cliente=cliente)
    auth.auditar(request.state.cuenta, "conductor", destino, None,
                 {"conductor": conductor, "evento": "cambio_de_cliente"})
    return _volver_configuracion(aviso="Conductor asignado al cliente elegido.", cliente=destino)


@app.post("/configuracion/conductor/clave")
def configuracion_conductor_clave(request: Request, conductor: int = Form(...),
                                  cliente: int | None = Form(None)):
    """Reinicia la contraseña del conductor: vuelve a elegirla con su DNI.

    Vive en Configuración y no en Usuarios porque es una tarea de operación (operaciones la hace), no de
    administración de cuentas.
    """
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    fila = db._uno("SELECT username FROM (SELECT usuario AS username FROM cuentas"
                   " WHERE conductor_id = %s) q", (conductor,))
    if not fila:
        return _volver_configuracion(
            aviso="Ese conductor todavía no entró: no hay contraseña que reiniciar.", cliente=cliente)
    auth.reiniciar_clave(fila["username"])
    auth.auditar(request.state.cuenta, "clave", cliente, None,
                 {"conductor": conductor, "evento": "reinicio"})
    return _volver_configuracion(
        aviso="Contraseña reiniciada: el conductor entra con su DNI y elige una nueva.", cliente=cliente)


@app.post("/configuracion/conductor/activo")
def configuracion_conductor_activo(request: Request, conductor: int = Form(...),
                                   activo: str = Form("1"), cliente: int | None = Form(None)):
    if not auth.puede(request.state.cuenta, "configuracion"):
        return RedirectResponse("/dia", status_code=303)
    configuracion.activar_conductor(conductor, activo == "1")
    auth.auditar(request.state.cuenta, "conductor", None, None,
                 {"conductor": conductor, "activo": activo == "1"})
    return _volver_configuracion(
        aviso="Conductor activado." if activo == "1" else "Conductor desactivado: no puede entrar.",
        cliente=cliente)


@app.get("/planificacion", response_class=HTMLResponse)
def planificacion(request: Request, cliente: int | None = None, fecha: str | None = None,
                  aviso: str | None = None, error: str | None = None):
    """El plantel del día: quién trabaja. Es lo que permite decir "no trabajó"."""
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    dia = ctx["dia"]
    plantel = configuracion.plantel(ctx["cliente"]["id"], dia)
    en_plantel = {p["conductor_id"] for p in plantel}
    disponibles = [c for c in configuracion.conductores(ctx["cliente"]["id"])
                   if c["activo"] and c["id"] not in en_plantel]
    return plantillas.TemplateResponse(
        request, "planificacion.html",
        {**ctx, "vista": "planificacion", "plantel": plantel, "disponibles": disponibles,
         "aviso": aviso, "error": error})


def _volver_planificacion(cliente: int, dia: date, aviso: str | None = None,
                          error: str | None = None):
    from urllib.parse import quote

    url = f"/planificacion?cliente={cliente}&fecha={dia}"
    if aviso:
        url += "&aviso=" + quote(aviso)
    if error:
        url += "&error=" + quote(error)
    return RedirectResponse(url, status_code=303)


@app.post("/planificacion")
def planificacion_agregar(request: Request, cliente: int = Form(...), fecha: str = Form(...),
                          conductor: list[int] = Form(...), pedidos: str = Form("")):
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    try:
        cuantos = configuracion.programar(
            ctx["cliente"]["id"], ctx["dia"], conductor,
            int(pedidos) if pedidos.strip() else None)
    except (ValueError, TypeError) as error:
        return _volver_planificacion(ctx["cliente"]["id"], ctx["dia"], error=str(error))
    auth.auditar(request.state.cuenta, "planificacion", ctx["cliente"]["id"], ctx["dia"],
                 {"conductores": conductor})
    return _volver_planificacion(ctx["cliente"]["id"], ctx["dia"],
                                 aviso=f"{cuantos} conductor(es) agregados al día.")


@app.post("/planificacion/quitar")
def planificacion_quitar(request: Request, conductor: int = Form(...), cliente: int = Form(...),
                         fecha: str = Form(...)):
    ctx = _contexto(cliente, fecha, request.state.cuenta)
    configuracion.quitar_del_dia(conductor, ctx["dia"])
    return _volver_planificacion(ctx["cliente"]["id"], ctx["dia"], aviso="Quitado del día.")
