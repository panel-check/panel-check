"""
Rutas de login / cuenta / usuarios del panel y las dependencias que usan
todas las demás rutas para exigir sesión. La lógica de claves y sesiones está
en auth.py.

  GET  /login                               pantalla de ingreso (pública)
  POST /api/login                           {usuario, clave, recordar}
  POST /api/logout                          cierra la sesión actual
  GET  /api/yo                              usuario logueado + lista de usuarios
  GET  /cuenta                              pantalla Mi cuenta (+ Usuarios si es admin)
  POST /api/cuenta/clave                    cambiar la clave propia
  GET  /api/cuenta/sesiones                 sesiones abiertas propias
  DELETE /api/cuenta/sesiones/{id}          cerrar una sesión propia
  POST /api/cuenta/sesiones/cerrar-otras    cerrar todas menos la actual
  GET  /api/usuarios                        (admin) lista de usuarios
  POST /api/usuarios                        (admin) crear usuario
  PUT  /api/usuarios/{usuario}              (admin) activar/desactivar, admin sí/no
  POST /api/usuarios/{usuario}/clave        (admin) resetear clave
  GET  /api/usuarios/ingresos               (admin) últimos intentos de ingreso
"""

import os
import re
import secrets
from typing import Optional
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth
import seguridad

STATIC = os.path.join(os.path.dirname(__file__), "static")
USUARIO_VALIDO = re.compile(r"^[A-Za-z0-9._-]{3,40}$")

# Rutas de la API que no piden sesión.
API_PUBLICAS = ("/api/login", "/api/publico/", "/api/version")
# Rutas a las que se puede entrar con "debe cambiar la clave" pendiente.
PERMITIDAS_CON_CAMBIO_PENDIENTE = ("/api/yo", "/api/logout", "/api/cuenta/")


class RedirigirA(Exception):
    def __init__(self, url: str):
        self.url = url


class Login(BaseModel):
    usuario: str
    clave: str
    recordar: bool = False


class CambioClave(BaseModel):
    actual: str
    nueva: str


class UsuarioNuevo(BaseModel):
    usuario: str
    clave: str
    es_admin: bool = False


class UsuarioCambios(BaseModel):
    activo: Optional[bool] = None
    es_admin: Optional[bool] = None


class ResetClave(BaseModel):
    clave: str


def instalar(app, conexion):
    """Registra rutas, middleware y manejador de redirecciones. Devuelve un
    objeto con las dependencias para el resto de la app."""

    # ── Dependencias ────────────────────────────────────────────────────
    def _sesion(request: Request) -> Optional[dict]:
        if not hasattr(request.state, "sesion"):
            request.state.sesion = auth.sesion_de_request(request, conexion)
        return request.state.sesion

    def verificar_login(request: Request) -> str:
        """Para la API: 401 sin sesión; 403 si tiene que cambiar la clave."""
        s = _sesion(request)
        if not s:
            raise auth.no_autorizado()
        if s["debe_cambiar_clave"] and not request.url.path.startswith(PERMITIDAS_CON_CAMBIO_PENDIENTE):
            raise HTTPException(status_code=403, detail="Tenés que cambiar tu clave antes de seguir (Mi cuenta)")
        return s["usuario"]

    def verificar_admin(request: Request) -> str:
        usuario = verificar_login(request)
        if not _sesion(request)["es_admin"]:
            raise HTTPException(status_code=403, detail="Solo para administradores")
        return usuario

    def verificar_pagina(request: Request) -> str:
        """Para las pantallas HTML: sin sesión -> a /login (volviendo después
        a la misma página)."""
        s = _sesion(request)
        if not s:
            destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise RedirigirA(f"/login?next={quote(destino, safe='')}")
        if s["debe_cambiar_clave"] and request.url.path != "/cuenta":
            raise RedirigirA("/cuenta?cambiar=1")
        return s["usuario"]

    def usuarios_activos() -> list:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT usuario FROM usuarios_panel WHERE activo ORDER BY usuario")
                return [f[0] for f in cur.fetchall()]

    def usuario_existe(usuario: str) -> bool:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM usuarios_panel WHERE usuario = %s", (usuario,))
                return cur.fetchone() is not None

    @app.exception_handler(RedirigirA)
    async def _redirigir(request: Request, exc: RedirigirA):
        return RedirectResponse(exc.url, status_code=303)

    # ── Middleware: CSRF + cabeceras de seguridad ───────────────────────
    @app.middleware("http")
    async def _seguridad(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.origen_valido(request):
            return JSONResponse({"detail": "Pedido rechazado (origen distinto)"}, status_code=403)
        # Las páginas HTML de /static (Ayuda, Crons, Mails, Clientes...) solo con
        # sesión: antes se podían abrir directo sin iniciar sesión. Los .js y .css
        # siguen abiertos (los necesita la pantalla de ingreso).
        if seguridad.es_html_privado(request.url.path) and not await run_in_threadpool(_sesion, request):
            return RedirectResponse("/login", status_code=303)
        respuesta = await call_next(request)
        h = respuesta.headers
        h.setdefault("Content-Security-Policy", seguridad.CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")  # SAMEORIGIN: el panel de comentarios (menu.js) se muestra en un iframe propio
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/publico/"):
            h.setdefault("Cache-Control", "no-store")
        return respuesta

    router = APIRouter()

    # ── Login / logout ──────────────────────────────────────────────────
    @router.get("/login", include_in_schema=False)
    def pagina_login(request: Request):
        if _sesion(request):
            return RedirectResponse(_destino_seguro(request.query_params.get("next")), status_code=303)
        r = FileResponse(os.path.join(STATIC, "login.html"))
        r.headers["Cache-Control"] = "no-cache"
        r.headers["X-Robots-Tag"] = "noindex"
        return r

    @router.post("/api/login")
    def login(body: Login, request: Request):
        ip = auth.ip_cliente(request)
        with conexion() as conn:
            with conn.cursor() as cur:
                fila, error = auth.intentar_login(cur, body.usuario, body.clave, ip)
                if error:
                    conn.commit()  # el intento fallido queda registrado
                    codigo = 429 if "intentos" in error else 401
                    return JSONResponse({"detail": error}, status_code=codigo)
                token, max_age = auth.crear_sesion(cur, fila[0], body.recordar, request)
            conn.commit()
        r = JSONResponse({"ok": True, "usuario": fila[0], "debe_cambiar_clave": fila[4]})
        auth.poner_cookie(r, request, token, max_age)
        return r

    @router.post("/api/logout")
    def logout(request: Request):
        s = _sesion(request)
        if s:
            with conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sesiones_panel WHERE token_hash = %s", (s["token_hash"],))
                conn.commit()
            auth.olvidar_cache(s["usuario"])
        r = JSONResponse({"ok": True})
        auth.borrar_cookie(r)
        return r

    @router.get("/logout", include_in_schema=False)
    def logout_link(request: Request):
        """Link directo (por si falla el JS): cierra y vuelve al login."""
        logout(request)
        r = RedirectResponse("/login", status_code=303)
        auth.borrar_cookie(r)
        return r

    @router.get("/api/yo")
    def quien_soy(request: Request, usuario: str = Depends(verificar_login)):
        s = _sesion(request)
        return {"usuario": usuario, "es_admin": s["es_admin"], "debe_cambiar_clave": s["debe_cambiar_clave"],
                "usuarios": usuarios_activos()}

    # ── Mi cuenta ───────────────────────────────────────────────────────
    @router.get("/cuenta", include_in_schema=False)
    def pagina_cuenta(_: str = Depends(verificar_pagina)):
        return FileResponse(os.path.join(STATIC, "cuenta.html"))

    @router.post("/api/cuenta/clave")
    def cambiar_clave(body: CambioClave, request: Request, usuario: str = Depends(verificar_login)):
        problema = auth.problema_clave(body.nueva, usuario)
        if problema:
            raise HTTPException(status_code=400, detail=problema)
        if body.nueva == body.actual:
            raise HTTPException(status_code=400, detail="La clave nueva tiene que ser distinta de la actual")
        s = _sesion(request)
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT clave_hash FROM usuarios_panel WHERE usuario = %s", (usuario,))
                fila = cur.fetchone()
                if not fila or not auth.verificar_clave(body.actual, fila[0]):
                    raise HTTPException(status_code=400, detail="La clave actual no es correcta")
                cur.execute(
                    """UPDATE usuarios_panel SET clave_hash = %s, debe_cambiar_clave = FALSE,
                       clave_cambiada_en = now() WHERE usuario = %s""",
                    (auth.hashear_clave(body.nueva), usuario),
                )
                # Por seguridad se cierran las demás sesiones (si alguien tenía
                # la clave vieja y estaba adentro, queda afuera).
                cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s AND token_hash <> %s",
                            (usuario, s["token_hash"]))
                cerradas = cur.rowcount
            conn.commit()
        auth.olvidar_cache(usuario)
        return {"ok": True, "sesiones_cerradas": cerradas}

    @router.get("/api/cuenta/sesiones")
    def mis_sesiones(request: Request, usuario: str = Depends(verificar_login)):
        actual = _sesion(request)["token_hash"]
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT token_hash, creada_en, ultima_actividad, expira_en, recordar, ip, dispositivo
                       FROM sesiones_panel WHERE usuario = %s AND expira_en > now()
                       ORDER BY ultima_actividad DESC""",
                    (usuario,),
                )
                filas = cur.fetchall()
        return [
            {
                # id corto (primeros 16 del hash): alcanza para identificarla y
                # no sirve para entrar con ella.
                "id": f["token_hash"][:16],
                "actual": f["token_hash"] == actual,
                "creada_en": f["creada_en"], "ultima_actividad": f["ultima_actividad"],
                "expira_en": f["expira_en"], "recordar": f["recordar"], "ip": f["ip"],
                "dispositivo": auth.describir_dispositivo(f["dispositivo"]),
            }
            for f in filas
        ]

    @router.delete("/api/cuenta/sesiones/{sid}")
    def cerrar_sesion(sid: str, request: Request, usuario: str = Depends(verificar_login)):
        if not re.fullmatch(r"[0-9a-f]{16}", sid):
            raise HTTPException(status_code=400, detail="Sesión inválida")
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s AND left(token_hash, 16) = %s",
                            (usuario, sid))
                n = cur.rowcount
            conn.commit()
        auth.olvidar_cache(usuario)
        return {"ok": True, "cerradas": n}

    @router.post("/api/cuenta/sesiones/cerrar-otras")
    def cerrar_otras(request: Request, usuario: str = Depends(verificar_login)):
        actual = _sesion(request)["token_hash"]
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s AND token_hash <> %s", (usuario, actual))
                n = cur.rowcount
            conn.commit()
        auth.olvidar_cache(usuario)
        return {"ok": True, "cerradas": n}

    # ── Usuarios (solo admin) ───────────────────────────────────────────
    @router.get("/api/usuarios")
    def listar_usuarios(_: str = Depends(verificar_admin)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT u.usuario, u.es_admin, u.activo, u.debe_cambiar_clave, u.creado_en, u.creado_por,
                              u.clave_cambiada_en, u.ultimo_ingreso,
                              (SELECT COUNT(*) FROM sesiones_panel s WHERE s.usuario = u.usuario
                                 AND s.expira_en > now()) AS sesiones
                       FROM usuarios_panel u ORDER BY u.activo DESC, u.usuario"""
                )
                return cur.fetchall()

    @router.post("/api/usuarios")
    def crear_usuario(body: UsuarioNuevo, admin: str = Depends(verificar_admin)):
        usuario = body.usuario.strip()
        if not USUARIO_VALIDO.match(usuario):
            raise HTTPException(status_code=400,
                                detail="Usuario inválido: de 3 a 40 caracteres, solo letras, números, punto, guion o guion bajo")
        problema = auth.problema_clave(body.clave, usuario)
        if problema:
            raise HTTPException(status_code=400, detail=problema)
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM usuarios_panel WHERE lower(usuario) = lower(%s)", (usuario,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre")
                cur.execute(
                    """INSERT INTO usuarios_panel (usuario, clave_hash, es_admin, debe_cambiar_clave, creado_por, clave_cambiada_en)
                       VALUES (%s, %s, %s, TRUE, %s, now())""",
                    (usuario, auth.hashear_clave(body.clave), body.es_admin, admin),
                )
            conn.commit()
        return {"ok": True}

    def _quedaria_sin_admin(cur, usuario: str) -> bool:
        cur.execute("SELECT COUNT(*) FROM usuarios_panel WHERE es_admin AND activo AND usuario <> %s", (usuario,))
        return cur.fetchone()[0] == 0

    @router.put("/api/usuarios/{usuario}")
    def cambiar_usuario(usuario: str, body: UsuarioCambios, admin: str = Depends(verificar_admin)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT es_admin, activo FROM usuarios_panel WHERE usuario = %s", (usuario,))
                fila = cur.fetchone()
                if not fila:
                    raise HTTPException(status_code=404, detail="No existe ese usuario")
                quita_admin = (body.activo is False) or (body.es_admin is False)
                if quita_admin and fila[0] and fila[1] and _quedaria_sin_admin(cur, usuario):
                    raise HTTPException(status_code=400, detail="Tiene que quedar al menos un administrador activo")
                if body.activo is False and usuario == admin:
                    raise HTTPException(status_code=400, detail="No podés desactivar tu propio usuario")
                if body.activo is not None:
                    cur.execute("UPDATE usuarios_panel SET activo = %s WHERE usuario = %s", (body.activo, usuario))
                    if not body.activo:
                        cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s", (usuario,))
                if body.es_admin is not None:
                    cur.execute("UPDATE usuarios_panel SET es_admin = %s WHERE usuario = %s", (body.es_admin, usuario))
            conn.commit()
        auth.olvidar_cache(usuario)
        return {"ok": True}

    @router.post("/api/usuarios/{usuario}/clave")
    def resetear_clave(usuario: str, body: ResetClave, admin: str = Depends(verificar_admin)):
        problema = auth.problema_clave(body.clave, usuario)
        if problema:
            raise HTTPException(status_code=400, detail=problema)
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE usuarios_panel SET clave_hash = %s, debe_cambiar_clave = %s, clave_cambiada_en = now()
                       WHERE usuario = %s""",
                    (auth.hashear_clave(body.clave), usuario != admin, usuario),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="No existe ese usuario")
                cur.execute("DELETE FROM sesiones_panel WHERE usuario = %s", (usuario,))
            conn.commit()
        auth.olvidar_cache(usuario)
        return {"ok": True}

    @router.get("/api/usuarios/clave-sugerida")
    def clave_sugerida(_: str = Depends(verificar_admin)):
        """Clave temporal aleatoria para dar de alta / resetear."""
        alfabeto = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return {"clave": "-".join("".join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(4))}

    @router.get("/api/usuarios/ingresos")
    def ultimos_ingresos(_: str = Depends(verificar_admin)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT usuario, ip, exito, motivo, cuando FROM intentos_login
                       ORDER BY cuando DESC LIMIT 100"""
                )
                return cur.fetchall()

    app.include_router(router)

    class Deps:
        pass

    d = Deps()
    d.verificar_login = verificar_login
    d.verificar_admin = verificar_admin
    d.verificar_pagina = verificar_pagina
    d.usuarios_activos = usuarios_activos
    d.usuario_existe = usuario_existe
    return d


def _destino_seguro(nxt: Optional[str]) -> str:
    """Solo rutas internas (evita usar el login para redirigir a otro sitio)."""
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return "/"
