"""
Alarma de bloqueo MASIVO del WAF de INPI.

Se engancha solo a toda sesión creada con validar_leads.crear_sesion() (que
usan todos los procesos que consultan INPI) y cuenta cada respuesta: OK o
bloqueada ("Web Page Blocked" / "Attack ID" / HTTP 403). Al terminar el
proceso, si el patrón es de bloqueo masivo -- no un bloqueo suelto, que
es normal y lo levantan los reintentos --:
  * manda un mail por Resend (mismas variables que notificar_oposiciones.py:
    RESEND_API_KEY, RESEND_FROM, NOTIFICAR_A), y
  * deja la corrida en ROJO en GitHub Actions (así además llega el mail
    estándar de GitHub de "workflow failed").

Criterios de "masivo" (cualquiera alcanza):
  A) BLOQUEOS_SEGUIDOS o más consultas seguidas bloqueadas
  B) al menos MIN_BLOQUEOS_SIN_OK bloqueos y NINGUNA respuesta OK en la corrida
  C) al menos MIN_BLOQUEOS_PORCENTAJE bloqueos y >= PORCENTAJE de las consultas
"""

import atexit
import os
import sys

import requests

BLOQUEOS_SEGUIDOS = 5
MIN_BLOQUEOS_SIN_OK = 3
MIN_BLOQUEOS_PORCENTAJE = 10
PORCENTAJE = 0.25

DEFAULT_FROM = "Avisos Panel <avisos@quieroregistrarmimarca.com.ar>"
DEFAULT_TO = "marcas@komunikacion.com.ar"

_estado = {"ok": 0, "bloqueadas": 0, "seguidas": 0, "max_seguidas": 0, "instalado": False}


def es_bloqueo(r: requests.Response) -> bool:
    if r.status_code == 403:
        return True
    ctype = r.headers.get("Content-Type", "").lower()
    if "pdf" in ctype or "octet-stream" in ctype:
        return False
    try:
        texto = r.text
    except Exception:
        return False
    return "Web Page Blocked" in texto or "Attack ID" in texto


def _hook(r: requests.Response, *args, **kwargs):
    if "inpi.gob.ar" not in (r.url or ""):
        return r
    if es_bloqueo(r):
        _estado["bloqueadas"] += 1
        _estado["seguidas"] += 1
        _estado["max_seguidas"] = max(_estado["max_seguidas"], _estado["seguidas"])
    else:
        _estado["ok"] += 1
        _estado["seguidas"] = 0
    return r


def instalar(s: requests.Session) -> None:
    s.hooks.setdefault("response", []).append(_hook)
    if not _estado["instalado"]:
        _estado["instalado"] = True
        atexit.register(_al_terminar)


def motivo_masivo() -> str | None:
    ok, bl, seg = _estado["ok"], _estado["bloqueadas"], _estado["max_seguidas"]
    total = ok + bl
    if seg >= BLOQUEOS_SEGUIDOS:
        return f"{seg} consultas seguidas bloqueadas"
    if bl >= MIN_BLOQUEOS_SIN_OK and ok == 0:
        return f"{bl} consultas bloqueadas y ninguna respondió bien"
    if bl >= MIN_BLOQUEOS_PORCENTAJE and total and bl / total >= PORCENTAJE:
        return f"{bl} de {total} consultas bloqueadas ({bl / total:.0%})"
    return None


def _link_corrida() -> str:
    srv, repo, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{run}" if srv and repo and run else ""


def _enviar_mail(proceso: str, motivo: str) -> None:
    # Cuenta, remitente y destinatarios: los de la pestaña Mails del panel
    # (si la base no responde, quedan los de respaldo: RESEND_FROM / NOTIFICAR_A).
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
    try:
        import mails_core
        import mails_plantillas
        cfg = mails_core.preparar("bloqueo")
        api_key, remitente, destinatarios, responder_a = cfg["api_key"], cfg["remitente"], cfg["destinatarios"], cfg["responder_a"]
        env_key = cfg["env_key"]
        asunto, cuerpo = mails_plantillas.armar_mail_bloqueo(proceso, motivo, _estado["ok"], _estado["bloqueadas"], _link_corrida())
    except Exception as e:  # noqa: BLE001 — la alarma no puede depender de la configuración
        print(f"::warning::No se pudo leer la configuración de mails ({e}); se usan los valores de respaldo")
        api_key, env_key = os.environ.get("RESEND_API_KEY"), "RESEND_API_KEY"
        remitente = os.environ.get("RESEND_FROM") or DEFAULT_FROM
        destinatarios = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or DEFAULT_TO).split(",") if d.strip()]
        responder_a = None
        asunto = f"⚠️ INPI está bloqueando consultas ({proceso})"
        cuerpo = f"<p><b>INPI está bloqueando las consultas del sistema.</b></p><p>Proceso: <b>{proceso}</b><br>Detalle: {motivo}</p>"
    if not api_key:
        print(f"::warning::No hay {env_key} -- no se pudo mandar el mail de alarma de bloqueo")
        return
    payload = {"from": remitente, "to": destinatarios, "subject": asunto, "html": cuerpo}
    if responder_a:
        payload["reply_to"] = responder_a
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        print(f"Mail de alarma de bloqueo: HTTP {r.status_code}")
    except Exception as e:
        print(f"::warning::No se pudo mandar el mail de alarma de bloqueo: {e}")


def enviar_prueba() -> None:
    """Manda el mismo mail de alarma con datos de ejemplo (no toca INPI)."""
    _estado.update(ok=0, bloqueadas=7, seguidas=7, max_seguidas=7)
    _enviar_mail("PRUEBA - Escanear actas nuevas", "PRUEBA: 7 consultas seguidas bloqueadas")


def _al_terminar() -> None:
    ok, bl = _estado["ok"], _estado["bloqueadas"]
    if bl:
        print(f"Monitor INPI: {ok} consultas OK, {bl} bloqueadas (máx. {_estado['max_seguidas']} seguidas)")
    motivo = motivo_masivo()
    if not motivo:
        return
    proceso = os.environ.get("GITHUB_WORKFLOW") or os.path.basename(sys.argv[0])
    print(f"::error::BLOQUEO MASIVO DE INPI en '{proceso}': {motivo}")
    _enviar_mail(proceso, motivo)
    sys.stdout.flush()
    os._exit(3)  # corrida en rojo aunque el script haya terminado "bien"


if __name__ == "__main__" and "--prueba" in sys.argv:
    enviar_prueba()
