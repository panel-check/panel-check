"""
API de la sección Calendario (sincronizada con Google Calendar).

Se arma con una fábrica (crear_router) para no importar app.py desde acá: app.py
le pasa el login y la conexión. La lógica está en calendario_core.py.

Rutas:
  GET    /api/calendario/estado               ¿está configurado? última sincronización, errores
  POST   /api/calendario/sincronizar          sincronizar ahora (con ?completa=true, relee todo)
  GET    /api/calendario/eventos?desde&hasta  eventos de un rango de fechas (AAAA-MM-DD)
  GET    /api/calendario/por-actas?actas=a,b  eventos que nombran esas actas (ficha del titular)
  GET    /api/calendario/actas?actas=a,b      de quién es cada acta (vista previa del formulario)
  GET    /api/calendario/buscar?q=texto       búsqueda en vivo de marcas por nombre, titular, cliente o acta
  POST   /api/calendario/agenda/enviar        manda ahora el mail AGENDA de mañana (el de las 20 hs); si no hay nada, no manda
  POST   /api/calendario/interpretar          «Agendar con IA»: texto pegado → propuesta para el formulario + preguntas (no crea nada)
  POST   /api/calendario/eventos              crear (en Google y en el panel); con modalidad «meet» genera el link;
                                              con avisar=true manda el mail a la persona y la copia al equipo
  PUT    /api/calendario/eventos/{id}         editar (con avisar=true, manda el aviso de cambio)
  DELETE /api/calendario/eventos/{id}         borrar (también en Google)
  GET    /api/calendario/seguimientos         recordatorios pendientes (atrasados incluidos) y los hechos hace poco
  POST   /api/calendario/eventos/{id}/hecho   marcar un recordatorio como hecho (o reabrirlo)
  POST   /api/calendario/disponibilidad/interpretar  texto como «próximo martes libre de 8 a 15» → ventanas (no guarda nada)
  POST   /api/calendario/disponibilidad       guardar ventanas de disponibilidad (con repetir_semanas = N, también las N semanas siguientes)
  GET    /api/calendario/disponibilidad?desde&hasta  días con disponibilidad: horarios libres, reuniones y resumen
  DELETE /api/calendario/disponibilidad/{id}  borrar una ventana
  GET    /api/calendario/dias-cerrados?desde&hasta  los días marcados «sin reuniones» (se pintan en rojo en el calendario)
  DELETE /api/calendario/disponibilidad/cierre/{id}  volver a habilitar un día marcado sin reuniones
  GET    /api/calendario/google/conectar      (admin) empieza la conexión con la cuenta de Google del estudio (OAuth)
  GET    /api/calendario/google/callback      (admin) vuelta de Google: muestra el token para cargar en Railway
"""

import datetime as _dt
import html as _html
import os
import re
import secrets
from typing import List, Optional
from urllib.parse import urlencode

import psycopg2.extras
import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

import calendario_core as cal
import ia_texto

# Un mes con semanas completas a los costados cabe de sobra en 70 días; el tope evita
# que un pedido a mano traiga años de eventos de una vez.
MAX_DIAS_RANGO = 120


class EventoEntrada(BaseModel):
    titulo: str
    fecha: str                       # AAAA-MM-DD
    hora: Optional[str] = None       # HH:MM; vacío = todo el día
    duracion_min: Optional[int] = 60
    todo_el_dia: Optional[bool] = False
    fecha_fin: Optional[str] = None  # solo para eventos de varios días (todo el día)
    descripcion: Optional[str] = None
    lugar: Optional[str] = None
    actas: Optional[List[str]] = None
    modalidad: Optional[str] = None      # «meet» | «llamada» | «presencial»
    avisar: Optional[bool] = False       # mandar el mail de aviso (a la persona, si hay mail, y la copia al equipo)
    email_aviso: Optional[str] = None    # a quién se le avisa (uno o varios separados por coma)
    tipo: Optional[str] = "evento"       # «evento» (reunión, llamada…) | «seguimiento» (recordatorio de todo el día, con otro color)


class HechoEntrada(BaseModel):
    hecho: Optional[bool] = True


class TextoDisponibilidad(BaseModel):
    texto: str


class VentanaEntrada(BaseModel):
    fecha: str      # AAAA-MM-DD
    desde: str      # HH:MM
    hasta: str      # HH:MM


class CierreEntrada(BaseModel):
    fecha: str                       # AAAA-MM-DD: día entero sin reuniones
    motivo: Optional[str] = ""


class DisponibilidadEntrada(BaseModel):
    ventanas: List[VentanaEntrada] = []
    cierres: List[CierreEntrada] = []
    repetir_semanas: Optional[int] = 0   # además de la fecha, las N semanas siguientes


class TextoAgenda(BaseModel):
    texto: str
    omitir: Optional[List[str]] = None   # campos que la persona decidió no cargar (no se vuelven a preguntar)


def _fecha(valor: str, nombre: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{nombre} tiene que ser una fecha AAAA-MM-DD")


def _lista_actas(texto: str) -> list:
    return [a for a in re.split(r"[,\s]+", texto or "") if re.fullmatch(r"\d{4,9}", a)][:200]


def crear_router(verificar_login, conexion, verificar_admin=None) -> APIRouter:
    verificar_admin = verificar_admin or verificar_login
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _configurado_o_409():
        if not cal.configurado():
            raise HTTPException(
                status_code=409,
                detail="El calendario todavía no está conectado con Google (faltan GOOGLE_SERVICE_ACCOUNT_JSON y GOOGLE_CALENDAR_ID en Railway).",
            )

    def _como_http(e: cal.CalendarioError):
        # Los errores de Google (permisos, calendario no compartido, pedido rechazado) no son del usuario.
        # 424 y no 502: el proxy de Railway reemplaza el cuerpo de un 502 y la pantalla perdería el motivo.
        codigo = 424 if isinstance(e, cal.GoogleError) else 400
        return HTTPException(status_code=codigo, detail=str(e))

    def _redirect_uri(request: Request) -> str:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
        return f"{proto}://{host}/api/calendario/google/callback"

    @router.get("/api/calendario/estado")
    def estado(request: Request, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                try:
                    datos = cal.estado(cur)
                    datos["redirect_uri"] = _redirect_uri(request)
                    datos["ia_disponible"] = ia_texto.configurada()
                    return datos
                except Exception:
                    conn.rollback()
                    # La tabla puede no existir todavía si el panel recién arrancó: se informa igual.
                    return {"configurado": cal.configurado(), "calendario": cal.calendar_id(),
                            "cuenta_servicio": cal.cuenta_servicio(), "puede_meet": cal.puede_meet(),
                            "oauth_conectado": cal.oauth_completo(), "oauth_pendiente": cal.oauth_pendiente(),
                            "aviso_equipo": cal.mail_equipo(), "ultimo_ok_en": None,
                            "ultimo_error": None, "sondeo_segundos": cal.SONDEO_SEGUNDOS,
                            "redirect_uri": _redirect_uri(request),
                            "ia_disponible": ia_texto.configurada()}

    @router.post("/api/calendario/sincronizar")
    def sincronizar(completa: bool = False, _: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            return cal.sincronizar(conexion, completa=completa)
        except cal.CalendarioError as e:
            raise _como_http(e)

    @router.post("/api/calendario/agenda/enviar")
    def enviar_agenda(_: str = Depends(verificar_login)):
        """Manda ahora el mail AGENDA de mañana (el mismo de las 20 hs) a los destinatarios de «Agenda
        diaria» (pestaña Mails). Si mañana no hay ninguna reunión ni llamada, no manda nada. No toca el
        envío automático de las 20 hs."""
        _configurado_o_409()
        r = cal.enviar_agenda(conexion, manual=True)
        if r["estado"] == "error":
            raise HTTPException(status_code=424, detail=r["error"])
        return r

    @router.get("/api/calendario/eventos")
    def eventos(desde: str, hasta: str, _: str = Depends(verificar_login)):
        d0, d1 = _fecha(desde, "desde"), _fecha(hasta, "hasta")
        if d1 < d0:
            raise HTTPException(status_code=400, detail="«hasta» es anterior a «desde»")
        if (d1 - d0).days > MAX_DIAS_RANGO:
            raise HTTPException(status_code=400, detail=f"El rango es demasiado largo (máximo {MAX_DIAS_RANGO} días)")
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"eventos": cal.listar_eventos(cur, d0, d1)}

    @router.get("/api/calendario/por-actas")
    def por_actas(actas: str = Query(""), _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"eventos": cal.eventos_de_actas(cur, _lista_actas(actas))}

    @router.get("/api/calendario/actas")
    def actas_info(actas: str = Query(""), _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                info = cal.resolver_actas(cur, _lista_actas(actas))
        return {"actas": list(info.values())}

    @router.get("/api/calendario/buscar")
    def buscar(q: str = Query("", max_length=100), _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"resultados": cal.buscar_marcas(cur, q)}

    @router.post("/api/calendario/interpretar")
    def interpretar(datos: TextoAgenda, _: str = Depends(verificar_login)):
        """Agendar con IA: de un texto pegado a una propuesta para el formulario, con lo que falta
        preguntar. NO crea nada: la persona revisa y guarda el formulario."""
        texto = (datos.texto or "").strip()
        if len(texto) < 8:
            raise HTTPException(status_code=400, detail="Pegá o escribí un poco más de texto con los datos de la reunión.")
        if len(texto) > 4000:
            raise HTTPException(status_code=400, detail="El texto es demasiado largo (máximo 4000 caracteres).")
        if not ia_texto.configurada():
            # 424 (y no 502/503) para que ningún proxy reemplace el mensaje por su página de error.
            raise HTTPException(status_code=424, detail="La IA no está configurada en el servidor (falta IA_API_KEY).")
        hoy = _dt.datetime.now(cal.TZ).date()
        try:
            crudo = ia_texto.extraer_agenda(texto, hoy.isoformat(), cal.DIAS_SEMANA[hoy.weekday()])
        except ia_texto.ErrorIA as e:
            raise HTTPException(status_code=424, detail=str(e))
        with conexion() as conn:
            with rcur(conn) as cur:
                return cal.armar_propuesta(cur, crudo, texto, hoy, datos.omitir or [])

    def _evento_por_id(evento_id: int) -> dict:
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM calendario_eventos WHERE id = %s", (evento_id,))
                fila = cur.fetchone()
                if not fila:
                    raise HTTPException(status_code=404, detail="Ese evento no existe")
                return cal._con_vinculos(cur, [cal.evento_a_json(fila)])[0]

    def _avisar(evento_id: int, datos: EventoEntrada, accion: str, usuario: str):
        """El evento ya está en Google: un mail que falla no lo deshace, se informa aparte."""
        if not datos.avisar:
            return None
        try:
            return cal.avisar(conexion, evento_id, accion, usuario, datos.email_aviso or "")
        except Exception as e:
            return {"persona": None, "equipo": {"email": cal.mail_equipo() or "", "enviado": False, "error": str(e)[:300]}}

    def _validar_tipo(datos: EventoEntrada):
        if (datos.tipo or cal.TIPO_EVENTO) not in (cal.TIPO_EVENTO, cal.TIPO_SEGUIMIENTO):
            raise HTTPException(status_code=400, detail="El tipo tiene que ser «evento» o «seguimiento».")
        if datos.tipo == cal.TIPO_SEGUIMIENTO:
            datos.avisar = False   # un recordatorio es interno: no manda mails

    @router.post("/api/calendario/eventos")
    def crear(datos: EventoEntrada, usuario: str = Depends(verificar_login)):
        _configurado_o_409()
        _validar_tipo(datos)
        try:
            nuevo = cal.crear_evento(conexion, datos.dict(), usuario)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"evento": _evento_por_id(nuevo), "aviso": _avisar(nuevo, datos, "agendada", usuario)}

    @router.put("/api/calendario/eventos/{evento_id}")
    def editar(evento_id: int, datos: EventoEntrada, usuario: str = Depends(verificar_login)):
        _configurado_o_409()
        _validar_tipo(datos)
        try:
            cal.editar_evento(conexion, evento_id, datos.dict(), usuario)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"evento": _evento_por_id(evento_id), "aviso": _avisar(evento_id, datos, "modificada", usuario)}

    @router.post("/api/calendario/eventos/{evento_id}/hecho")
    def hecho(evento_id: int, datos: HechoEntrada, _: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            cal.marcar_hecho(conexion, evento_id, bool(datos.hecho if datos.hecho is not None else True))
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"evento": _evento_por_id(evento_id)}

    @router.get("/api/calendario/seguimientos")
    def seguimientos(_: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"hoy": _dt.datetime.now(cal.TZ).date().isoformat(), "seguimientos": cal.listar_seguimientos(cur)}

    # ── Disponibilidad horaria ───────────────────────────────────────

    @router.post("/api/calendario/disponibilidad/interpretar")
    def disponibilidad_interpretar(datos: TextoDisponibilidad, _: str = Depends(verificar_login)):
        texto = (datos.texto or "").strip()
        if not texto:
            raise HTTPException(status_code=400, detail="Escribí la disponibilidad, por ejemplo: próximo martes libre de 8 a 15.")
        if len(texto) > 1000:
            raise HTTPException(status_code=400, detail="El texto es demasiado largo (máximo 1000 caracteres).")
        return cal.interpretar_disponibilidad(texto, _dt.datetime.now(cal.TZ).date())

    @router.post("/api/calendario/disponibilidad")
    def disponibilidad_guardar(datos: DisponibilidadEntrada, usuario: str = Depends(verificar_login)):
        try:
            return cal.guardar_disponibilidad(conexion, [v.dict() for v in datos.ventanas], usuario, datos.repetir_semanas or 0,
                                           cierres=[c.dict() for c in datos.cierres])
        except cal.CalendarioError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/api/calendario/disponibilidad")
    def disponibilidad(desde: str = "", hasta: str = "", _: str = Depends(verificar_login)):
        hoy = _dt.datetime.now(cal.TZ).date()
        d0 = _fecha(desde, "desde") if desde else hoy
        d1 = _fecha(hasta, "hasta") if hasta else hoy + _dt.timedelta(days=56)
        if d1 < d0:
            raise HTTPException(status_code=400, detail="«hasta» es anterior a «desde»")
        if (d1 - d0).days > MAX_DIAS_RANGO:
            raise HTTPException(status_code=400, detail=f"El rango es demasiado largo (máximo {MAX_DIAS_RANGO} días)")
        with conexion() as conn:
            with rcur(conn) as cur:
                dias = cal.disponibilidad_del_rango(cur, d0, d1)
        return {"dias": dias, "duracion": cal.duracion_reunion(), "margen": cal.margen_reunion()}

    @router.get("/api/calendario/dias-cerrados")
    def dias_cerrados(desde: str = "", hasta: str = "", _: str = Depends(verificar_login)):
        hoy = _dt.datetime.now(cal.TZ).date()
        d0 = _fecha(desde, "desde") if desde else hoy
        d1 = _fecha(hasta, "hasta") if hasta else d0 + _dt.timedelta(days=60)
        if d1 < d0 or (d1 - d0).days > MAX_DIAS_RANGO:
            raise HTTPException(status_code=400, detail="El rango de fechas no es válido")
        with conexion() as conn:
            with rcur(conn) as cur:
                return {"dias": cal.listar_dias_cerrados(cur, d0, d1)}

    @router.delete("/api/calendario/disponibilidad/cierre/{cierre_id}")
    def disponibilidad_cierre_borrar(cierre_id: int, _: str = Depends(verificar_login)):
        cal.borrar_dia_cerrado(conexion, cierre_id)
        return {"ok": True}

    @router.delete("/api/calendario/disponibilidad/{ventana_id}")
    def disponibilidad_borrar(ventana_id: int, _: str = Depends(verificar_login)):
        cal.borrar_disponibilidad(conexion, ventana_id)
        return {"ok": True}

    @router.delete("/api/calendario/eventos/{evento_id}")
    def borrar(evento_id: int, _: str = Depends(verificar_login)):
        _configurado_o_409()
        try:
            cal.borrar_evento(conexion, evento_id)
        except cal.CalendarioError as e:
            raise _como_http(e)
        return {"ok": True}

    # ── Conexión con la cuenta de Google del estudio (para poder generar links de Meet) ──

    def _pagina(titulo: str, cuerpo_html: str, codigo: int = 200) -> HTMLResponse:
        pagina = (
            '<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{_html.escape(titulo)}</title>"
            '<link rel="stylesheet" href="/static/comun.css"><link rel="stylesheet" href="/static/calendario.css">'
            '</head><body><main class="cal-pagina" style="max-width:640px;margin:40px auto;padding:0 16px">'
            f"<h2>{_html.escape(titulo)}</h2>{cuerpo_html}"
            '<p><a href="/calendario">← Volver al calendario</a></p></main></body></html>'
        )
        return HTMLResponse(pagina, status_code=codigo, headers={"Cache-Control": "no-store"})

    @router.get("/api/calendario/google/conectar")
    def conectar(request: Request, _: str = Depends(verificar_admin)):
        cid, secreto, _token = cal._oauth_config()
        if not (cid and secreto):
            return _pagina("Falta el cliente OAuth", "<p>Cargá <code>GOOGLE_OAUTH_CLIENT_ID</code> y <code>GOOGLE_OAUTH_CLIENT_SECRET</code> "
                           "en Railway (servicio del panel) y volvé a probar. La guía está en la pantalla Calendario.</p>", 409)
        estado_oauth = secrets.token_urlsafe(24)
        params = {"client_id": cid, "redirect_uri": _redirect_uri(request), "response_type": "code",
                  "scope": cal.SCOPE, "access_type": "offline", "prompt": "consent", "state": estado_oauth}
        if "@" in (cal.calendar_id() or ""):
            params["login_hint"] = cal.calendar_id()
        resp = RedirectResponse(cal.AUTH_URI + "?" + urlencode(params), status_code=302)
        resp.set_cookie("cal_oauth_state", estado_oauth, max_age=600, httponly=True,
                        secure=(_redirect_uri(request).startswith("https")), samesite="lax", path="/api/calendario/google")
        return resp

    @router.get("/api/calendario/google/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = "", _: str = Depends(verificar_admin)):
        esperado = request.cookies.get("cal_oauth_state") or ""
        if error:
            return _pagina("Google no autorizó la conexión", f"<p>Google respondió: <code>{_html.escape(error)}</code>. Probá de nuevo desde la pantalla Calendario.</p>", 400)
        if not code or not esperado or not secrets.compare_digest(esperado, state or ""):
            return _pagina("La conexión venció", "<p>Volvé a empezar desde el botón «Conectar con Google» de la pantalla Calendario.</p>", 400)
        cid, secreto, _token = cal._oauth_config()
        if not (cid and secreto):
            return _pagina("Falta el cliente OAuth", "<p>Faltan <code>GOOGLE_OAUTH_CLIENT_ID</code> o <code>GOOGLE_OAUTH_CLIENT_SECRET</code>.</p>", 409)
        try:
            r = requests.post(cal.TOKEN_URI, data={"code": code, "client_id": cid, "client_secret": secreto,
                                                   "redirect_uri": _redirect_uri(request), "grant_type": "authorization_code"}, timeout=30)
            datos = r.json()
        except Exception as e:
            return _pagina("No se pudo hablar con Google", f"<p>{_html.escape(str(e))}</p>", 502)
        if r.status_code >= 400 or not datos.get("access_token"):
            detalle = datos.get("error_description") or datos.get("error") or r.text[:200]
            return _pagina("Google rechazó el código", f"<p>{_html.escape(str(detalle))}</p>", 400)
        refresco = datos.get("refresh_token")
        if not refresco:
            return _pagina("Google no devolvió el token permanente",
                           "<p>Pasa cuando esta cuenta ya había autorizado la app. Entrá a "
                           "<code>myaccount.google.com/permissions</code>, quitale el acceso a la app y volvé a tocar «Conectar con Google».</p>", 400)
        cuenta = ""
        try:
            c = requests.get(cal.API + "/calendars/primary", headers={"Authorization": f"Bearer {datos['access_token']}"}, timeout=30)
            cuenta = (c.json() or {}).get("id") or ""
        except Exception:
            pass
        aviso_cuenta = ""
        if cuenta and cal.calendar_id() and cuenta.lower() != cal.calendar_id().lower():
            aviso_cuenta = (f"<p><strong>Ojo:</strong> la cuenta que autorizaste es <code>{_html.escape(cuenta)}</code> y "
                            f"<code>GOOGLE_CALENDAR_ID</code> es <code>{_html.escape(cal.calendar_id())}</code>. Si el calendario del estudio "
                            "es el de la otra cuenta, volvé a conectar entrando con esa.</p>")
        cuerpo = (
            (f"<p>Cuenta conectada: <strong>{_html.escape(cuenta)}</strong>.</p>" if cuenta else "")
            + aviso_cuenta
            + "<p>Último paso: copiá este valor completo y cargalo en Railway, en el servicio del panel, como variable "
              "<code>GOOGLE_OAUTH_REFRESH_TOKEN</code>. Cuando Railway reinicie el panel, ya se pueden generar links de Meet.</p>"
              f'<p><code style="word-break:break-all;user-select:all;display:block;padding:10px;background:#f4f6f9;border-radius:6px">{_html.escape(refresco)}</code></p>'
              "<p>Es una clave: no la compartas ni la pegues en un chat o mail. Esta página no se guarda en ningún lado; "
              "si la cerrás sin copiarla, se vuelve a generar con «Conectar con Google».</p>"
        )
        resp = _pagina("Cuenta de Google conectada", cuerpo)
        resp.delete_cookie("cal_oauth_state", path="/api/calendario/google")
        return resp

    return router
