"""
Guardar el PDF del análisis de marca en Google Drive.

Qué hace:
  - Sube el PDF (el mismo que baja «Exportar PDF») a una carpeta «Análisis de marca» del
    Drive de la cuenta de Google del estudio. La carpeta se crea sola la primera vez.
  - Si esa marca ya estaba subida, REEMPLAZA ese archivo (Drive suele conservar un tiempo
    las versiones anteriores), así que no se acumulan copias cada vez que se vuelve a exportar.

Cómo se conecta: usa la misma cuenta de Google del estudio que el Calendario (OAuth), con las
mismas tres variables de Railway: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET y
GOOGLE_OAUTH_REFRESH_TOKEN. La cuenta de servicio no sirve: no tiene espacio propio en Drive.

Permiso: solo `drive.file`, que deja ver y tocar únicamente los archivos y carpetas que creó
este panel; el resto del Drive del estudio queda fuera de su alcance. Por eso la carpeta y los
archivos se reconocen por una marca propia (appProperties) y no por el nombre: si alguien mueve
o renombra la carpeta dentro de Drive, el panel la sigue encontrando. (Si la mandan a la
papelera, se crea una nueva.)

Sin FastAPI: lo usan analisis_api.py y las pruebas.
"""

import logging
import threading

import calendario_core as cal

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
SUBIDA = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.file"
MIME_CARPETA = "application/vnd.google-apps.folder"
MIME_PDF = "application/pdf"
NOMBRE_CARPETA = "Análisis de marca"
MARCA_PROPIA = "analisis-marca"      # appProperties.panel de la carpeta y de cada archivo subido
CAMPOS = "id,name,webViewLink"

# Dos exportaciones a la vez no tienen que crear dos carpetas.
_candado = threading.Lock()
_candado_sesion = threading.Lock()
_sesion_cache = {"clave": None, "sesion": None}


class DriveError(Exception):
    """Error con un mensaje pensado para mostrarle a una persona."""

    def __init__(self, mensaje, status=None):
        super().__init__(mensaje)
        self.status = status


def configurado() -> bool:
    """Está conectada la cuenta de Google del estudio por OAuth (la única forma que sirve para Drive)."""
    return cal.oauth_completo()


MSG_RECONECTAR = ("Entrá a Calendario → «Conectar con Google», aceptá los permisos (Calendar y Drive) y cargá el token nuevo "
                  "en GOOGLE_OAUTH_REFRESH_TOKEN en Railway.")


def _sesion():
    cid, secreto, token = cal._oauth_config()
    if not (cid and secreto and token):
        raise DriveError("Falta conectar la cuenta de Google del estudio (GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET y "
                         "GOOGLE_OAUTH_REFRESH_TOKEN en Railway). " + MSG_RECONECTAR)
    clave = (cid, token)
    with _candado_sesion:
        if _sesion_cache["clave"] != clave:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.credentials import Credentials

            creds = Credentials(None, refresh_token=token, token_uri=cal.TOKEN_URI, client_id=cid,
                                client_secret=secreto, scopes=[SCOPE])
            _sesion_cache["sesion"] = AuthorizedSession(creds)
            _sesion_cache["clave"] = clave
        return _sesion_cache["sesion"]


def _detalle(cuerpo) -> tuple:
    """(mensaje, motivo) que Google puso en el error, si los hay."""
    try:
        e = cuerpo.get("error") or {}
        if isinstance(e, str):
            return e, ""
        motivo = ((e.get("errors") or [{}])[0] or {}).get("reason") or e.get("status") or ""
        return e.get("message") or "", str(motivo)
    except Exception:  # noqa: BLE001
        return "", ""


def _mensaje_google(status, cuerpo) -> str:
    detalle, motivo = _detalle(cuerpo)
    motivo_bajo = (motivo + " " + detalle).lower()
    if status == 401 or "insufficientpermissions" in motivo_bajo or "insufficient authentication scopes" in motivo_bajo:
        return "Google no dejó guardar en Drive con la conexión actual (le falta el permiso de Drive o ya no vale). " + MSG_RECONECTAR
    if status == 403 and ("accessnotconfigured" in motivo_bajo or "service_disabled" in motivo_bajo or "has not been used" in motivo_bajo):
        return ("La API de Google Drive no está activada en el proyecto de Google Cloud del panel: en «APIs y servicios → Biblioteca» "
                "buscá «Google Drive API» y tocá «Habilitar». Después probá de nuevo.")
    if status == 403 and ("storagequotaexceeded" in motivo_bajo or "quota" in motivo_bajo):
        return "El Drive del estudio no tiene espacio libre."
    return f"Google respondió {status}. {detalle}".strip()


def _enviar(metodo, url, **kw):
    """Pedido a la API de Google Drive; devuelve la respuesta (ya verificada)."""
    sesion = _sesion()
    try:
        r = sesion.request(metodo, url, timeout=120, **kw)
    except DriveError:
        raise
    except Exception as e:  # noqa: BLE001 — red caída, token vencido o revocado, etc.
        texto = str(e)
        if "invalid_scope" in texto:
            raise DriveError("La conexión con Google se hizo antes de que el panel usara Drive y no incluye ese permiso. " + MSG_RECONECTAR)
        if "invalid_grant" in texto or "invalid_client" in texto or "unauthorized_client" in texto:
            raise DriveError("Google ya no acepta la conexión con la cuenta del estudio (el permiso se revocó, venció o cambió el cliente OAuth). "
                             + MSG_RECONECTAR)
        raise DriveError(f"No se pudo hablar con Google Drive: {e}")
    if r.status_code >= 400:
        try:
            cuerpo = r.json()
        except Exception:  # noqa: BLE001
            cuerpo = {}
        raise DriveError(_mensaje_google(r.status_code, cuerpo), status=r.status_code)
    return r


def _pedir(metodo, url, **kw):
    r = _enviar(metodo, url, **kw)
    return r.json() if r.content else None


def _q(valor: str) -> str:
    """Un texto dentro de una consulta de Drive (entre comillas simples)."""
    return "'" + str(valor).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _propiedad(clave: str, valor: str) -> str:
    return f"appProperties has {{ key={_q(clave)} and value={_q(valor)} }}"


def _buscar(q: str) -> list:
    r = _pedir("GET", API + "/files", params={"q": q, "fields": f"files({CAMPOS})", "pageSize": 10,
                                               "spaces": "drive", "orderBy": "createdTime"})
    return (r or {}).get("files") or []


def asegurar_carpeta() -> str:
    """ID de la carpeta «Análisis de marca» del panel (la crea si no existe)."""
    with _candado:
        halladas = _buscar(f"mimeType = {_q(MIME_CARPETA)} and trashed = false and {_propiedad('panel', MARCA_PROPIA)}")
        if halladas:
            return halladas[0]["id"]
        creada = _pedir("POST", API + "/files", params={"fields": "id"},
                        json={"name": NOMBRE_CARPETA, "mimeType": MIME_CARPETA,
                              "appProperties": {"panel": MARCA_PROPIA}})
        return creada["id"]


def subir_pdf(contenido: bytes, nombre: str, clave: tuple = None) -> dict:
    """Sube el PDF a la carpeta del panel.

    `clave` = (tipo, valor), por ejemplo ("acta", "4782038"): identifica el archivo para que una
    nueva exportación lo reemplace en vez de duplicarlo. Sin clave, siempre se crea uno nuevo.
    Devuelve {id, nombre, url, reemplazo, carpeta}."""
    if not contenido:
        raise DriveError("El PDF salió vacío.")
    carpeta = asegurar_carpeta()
    previo = None
    props = {"panel": MARCA_PROPIA}
    if clave:
        props[clave[0]] = str(clave[1])
        # Se busca en todo el Drive del panel (no solo en la carpeta): si lo movieron, se actualiza igual.
        halladas = _buscar(f"mimeType = {_q(MIME_PDF)} and trashed = false and {_propiedad('panel', MARCA_PROPIA)} "
                           f"and {_propiedad(clave[0], str(clave[1]))}")
        previo = halladas[0] if halladas else None
    meta = {"name": nombre, "mimeType": MIME_PDF, "appProperties": props}
    if previo:
        metodo, url = "PATCH", f"{SUBIDA}/files/{previo['id']}"
    else:
        metodo, url = "POST", f"{SUBIDA}/files"
        meta["parents"] = [carpeta]
    # Subida «resumable»: sirve para cualquier tamaño (primero se avisa qué se va a subir, después se manda el archivo).
    inicio = _enviar(metodo, url, params={"uploadType": "resumable", "fields": CAMPOS}, json=meta,
                     headers={"X-Upload-Content-Type": MIME_PDF, "X-Upload-Content-Length": str(len(contenido))})
    destino = inicio.headers.get("Location")
    if not destino:
        raise DriveError("Google no devolvió dónde subir el archivo.")
    r = _enviar("PUT", destino, data=contenido, headers={"Content-Type": MIME_PDF})
    datos = r.json() if r.content else {}
    log.info("PDF guardado en Drive (%s)", "reemplazo" if previo else "nuevo")
    return {"id": datos.get("id") or (previo or {}).get("id"), "nombre": datos.get("name") or nombre,
            "url": datos.get("webViewLink"), "reemplazo": bool(previo), "carpeta": NOMBRE_CARPETA}
