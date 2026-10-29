"""
Cola de mails a prospectos (29/10/2026).

Problema: la cuenta de prospectos de Resend está en el plan free (100 mails por día UTC, se reinicia a las
21:00 hora Argentina). Cuando se llega al tope, Resend contesta 429 «daily_quota_exceeded» y el mail no sale.

Qué hace: cuando un mail a un prospecto choca con el cupo, en vez de dar error queda guardado en la tabla
`mails_cola` (con el HTML, el texto y los adjuntos tal como iban a salir). Un hilo del panel revisa la cola
cada minuto y, desde las 21:02 (2 minutos después del reinicio del cupo), los manda en orden de llegada.
Si vuelve a chocar con el cupo, el resto espera al reinicio siguiente. No se pasa a la otra cuenta de Resend:
la cuenta interna se reserva para los avisos al equipo, para no mezclar reputaciones.

Qué pasa en cada caso al salir de la cola:
  - Mail mandado a mano desde la ficha (mails_envios): la fila ya existe desde que se encoló (así cuenta
    como «ya se le mandó» y no se repite); al salir se le carga el id de Resend y la hora real de envío.
  - Confirmación de la consulta de la web (verificaciones_marca) y poder del formulario
    (formularios_respuestas): se marca la hora de envío y se borra el aviso de «en cola».
Antes de mandar se vuelve a mirar la lista de bajas: si el mail se dio de baja mientras esperaba, se cancela.
Errores que no son de cupo: se reintenta a los 15 minutos, hasta 3 veces; después queda como «error».

Sin FastAPI: lo usan el panel (mails_api.py, verificador_api.py, formularios_api.py, app.py) y las pruebas.
"""

import base64
import datetime as _dt
import json
import threading
import time

import psycopg2.extras

import mails_core as mc

MINUTOS_DESPUES_DEL_REINICIO = 2
REINTENTO_MINUTOS = 15
MAX_INTENTOS = 3
PAUSA_ENTRE_ENVIOS = 0.6     # segundos (Resend limita los pedidos por segundo)
PASADA_SEGUNDOS = 60

# Qué se actualiza cuando sale un mail encolado. Lista cerrada: nunca se arma un UPDATE con un nombre de
# tabla que venga de la base.
_AL_ENVIAR = {
    "mails_envios": "UPDATE mails_envios SET resend_id = %(rid)s, enviado_en = now() WHERE id = %(id)s",
    "verificaciones_marca": "UPDATE verificaciones_marca SET confirmacion_enviada_en = now(), confirmacion_error = NULL "
                            "WHERE id = %(id)s",
    "formularios_respuestas": "UPDATE formularios_respuestas SET poder_enviado_en = now(), poder_error = NULL "
                              "WHERE id = %(id)s",
}
_AL_FALLAR = {
    "verificaciones_marca": "UPDATE verificaciones_marca SET confirmacion_error = %(err)s WHERE id = %(id)s",
    "formularios_respuestas": "UPDATE formularios_respuestas SET poder_error = %(err)s WHERE id = %(id)s",
}

AVISO_EN_COLA = "En cola: se agotó el cupo diario de Resend; sale después de las 21:00."


def crear_tablas(cur):
    """Idempotente. Va después de mails_core.crear_tablas (usa mails_envios)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mails_cola (
            id             SERIAL PRIMARY KEY,
            cuenta         TEXT NOT NULL,
            remitente      TEXT NOT NULL,
            responder_a    TEXT,
            para           JSONB NOT NULL,
            asunto         TEXT NOT NULL,
            html           TEXT NOT NULL,
            texto          TEXT,
            adjuntos       JSONB,
            clave_mail     TEXT,
            acta           TEXT,
            origen         TEXT,
            encolado_por   TEXT,
            al_enviar      JSONB,
            estado         TEXT NOT NULL DEFAULT 'pendiente',
            intentos       INT NOT NULL DEFAULT 0,
            ultimo_error   TEXT,
            motivo         TEXT,
            intentar_desde TIMESTAMPTZ NOT NULL,
            encolado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
            enviado_en     TIMESTAMPTZ,
            resend_id      TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mails_cola_pendientes ON mails_cola(estado, intentar_desde, id)")
    cur.execute("ALTER TABLE mails_envios ADD COLUMN IF NOT EXISTS cola_id INT")


def proximo_reinicio(ahora: _dt.datetime = None) -> _dt.datetime:
    """Próxima medianoche UTC (21:00 hora Argentina) + unos minutos de margen."""
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    manana = (ahora.astimezone(_dt.timezone.utc) + _dt.timedelta(days=1)).date()
    return _dt.datetime(manana.year, manana.month, manana.day, tzinfo=_dt.timezone.utc) + \
        _dt.timedelta(minutes=MINUTOS_DESPUES_DEL_REINICIO)


def encolar(cur, *, cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos=None, clave_mail=None,
            acta=None, origen=None, encolado_por=None, al_enviar=None, motivo="daily_quota_exceeded") -> dict:
    """Guarda el mail en la cola. `al_enviar` = {"tabla": <una de _AL_ENVIAR>, "id": n}. No hace commit."""
    if al_enviar and al_enviar.get("tabla") not in _AL_ENVIAR:
        raise ValueError(f"al_enviar desconocido: {al_enviar}")
    adj = [{"filename": a["filename"], "content": base64.b64encode(a["content"]).decode("ascii")} for a in (adjuntos or [])]
    desde = proximo_reinicio()
    cur.execute(
        """INSERT INTO mails_cola (cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos, clave_mail, acta,
                                   origen, encolado_por, al_enviar, motivo, intentar_desde)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (cuenta, remitente, responder_a or None, psycopg2.extras.Json(list(para)), asunto, html, texto,
         psycopg2.extras.Json(adj) if adj else None, clave_mail, acta, origen, encolado_por,
         psycopg2.extras.Json(al_enviar) if al_enviar else None, motivo, desde))
    fila = cur.fetchone()
    return {"cola_id": fila["id"] if isinstance(fila, dict) else fila[0], "sale_desde": desde.isoformat()}


def enviar_o_encolar(conexion, *, cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos=None,
                     clave_mail=None, acta=None, origen=None, encolado_por=None, al_enviar=None) -> dict:
    """Manda el mail; si Resend dice que se agotó el cupo, lo deja en la cola.
    Devuelve {"en_cola": False, "id": <resend>} o {"en_cola": True, "cola_id": n, "sale_desde": iso}.
    Los demás errores se levantan igual que mc.enviar (ValueError)."""
    try:
        return {"en_cola": False, "id": mc.enviar(cuenta, remitente, responder_a, para, asunto, html, texto, adjuntos)}
    except mc.CupoAgotado as e:
        with conexion() as conn:
            with conn.cursor() as cur:
                r = encolar(cur, cuenta=cuenta, remitente=remitente, responder_a=responder_a, para=para, asunto=asunto,
                            html=html, texto=texto, adjuntos=adjuntos, clave_mail=clave_mail, acta=acta, origen=origen,
                            encolado_por=encolado_por, al_enviar=al_enviar, motivo=e.motivo)
            conn.commit()
        print(f"[cola] mail «{asunto[:60]}» encolado (#{r['cola_id']}): {e.motivo}")
        return {"en_cola": True, **r}


def _correr_al_enviar(cur, al_enviar, resend_id):
    if al_enviar and al_enviar.get("tabla") in _AL_ENVIAR:
        cur.execute(_AL_ENVIAR[al_enviar["tabla"]], {"rid": resend_id, "id": al_enviar.get("id")})


def _correr_al_fallar(cur, al_enviar, error):
    if al_enviar and al_enviar.get("tabla") in _AL_FALLAR:
        cur.execute(_AL_FALLAR[al_enviar["tabla"]], {"err": (error or "")[:500], "id": al_enviar.get("id")})


def procesar(conexion, enviar=None, pausa: float = PAUSA_ENTRE_ENVIOS, ahora=None) -> dict:
    """Manda los mails pendientes cuya hora ya llegó, de a uno y en orden. Devuelve un resumen.
    Cada fila se toma con FOR UPDATE SKIP LOCKED: dos instancias del panel no mandan el mismo mail."""
    enviar = enviar or mc.enviar
    res = {"enviados": 0, "cancelados": 0, "errores": 0, "esperando_cupo": 0}
    cuentas_sin_cupo = set()
    while True:
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                filtro = "AND cuenta <> ALL(%s)" if cuentas_sin_cupo else ""
                params = [ahora or _dt.datetime.now(_dt.timezone.utc)] + ([list(cuentas_sin_cupo)] if cuentas_sin_cupo else [])
                cur.execute(f"""SELECT * FROM mails_cola WHERE estado = 'pendiente' AND intentar_desde <= %s {filtro}
                                ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""", params)
                f = cur.fetchone()
                if not f:
                    conn.commit()
                    return res
                para = list(f["para"] or [])
                if any(mc.esta_de_baja(cur, p) for p in para):
                    _cancelar_fila(cur, f, "Cancelado: el destinatario se dio de baja mientras esperaba en la cola.")
                    conn.commit()
                    res["cancelados"] += 1
                    continue
                adj = [{"filename": a["filename"], "content": base64.b64decode(a["content"])} for a in (f["adjuntos"] or [])]
                try:
                    rid = enviar(f["cuenta"], f["remitente"], f["responder_a"] or "", para, f["asunto"], f["html"],
                                 f["texto"] or "", adj or None)
                except mc.CupoAgotado as e:
                    # Sigue sin cupo: todo lo pendiente de esa cuenta espera al próximo reinicio.
                    cur.execute("UPDATE mails_cola SET intentar_desde = %s, motivo = %s "
                                "WHERE estado = 'pendiente' AND cuenta = %s",
                                (proximo_reinicio(ahora), e.motivo, f["cuenta"]))
                    conn.commit()
                    cuentas_sin_cupo.add(f["cuenta"])
                    res["esperando_cupo"] += 1
                    continue
                except Exception as e:  # noqa: BLE001
                    intentos = f["intentos"] + 1
                    final = intentos >= MAX_INTENTOS
                    cur.execute("UPDATE mails_cola SET intentos = %s, ultimo_error = %s, estado = %s, "
                                "intentar_desde = now() + %s * interval '1 minute' WHERE id = %s",
                                (intentos, str(e)[:500], "error" if final else "pendiente", REINTENTO_MINUTOS, f["id"]))
                    if final:
                        _correr_al_fallar(cur, f["al_enviar"], f"No salió de la cola: {e}")
                    conn.commit()
                    res["errores"] += 1
                    continue
                cur.execute("UPDATE mails_cola SET estado = 'enviado', enviado_en = now(), resend_id = %s, ultimo_error = NULL "
                            "WHERE id = %s", (rid, f["id"]))
                _correr_al_enviar(cur, f["al_enviar"], rid)
                conn.commit()
                res["enviados"] += 1
        if pausa:
            time.sleep(pausa)


def _cancelar_fila(cur, f, motivo):
    cur.execute("UPDATE mails_cola SET estado = 'cancelado', ultimo_error = %s WHERE id = %s", (motivo, f["id"]))
    al = f.get("al_enviar") or {}
    if al.get("tabla") == "mails_envios":
        # Se borra el registro del envío: si no, la ficha diría «ya se le mandó» un mail que nunca salió.
        cur.execute("DELETE FROM mails_envios WHERE id = %s AND resend_id IS NULL", (al.get("id"),))
    else:
        _correr_al_fallar(cur, al, motivo)


def cancelar(cur, cola_id: int, usuario: str = None) -> bool:
    cur.execute("SELECT * FROM mails_cola WHERE id = %s AND estado IN ('pendiente', 'error') FOR UPDATE", (cola_id,))
    f = cur.fetchone()
    if not f:
        return False
    if not isinstance(f, dict):
        f = dict(zip([d[0] for d in cur.description], f))
    _cancelar_fila(cur, f, f"Cancelado a mano{' por ' + usuario if usuario else ''}.")
    return True


def reintentar(cur, cola_id: int) -> bool:
    """Vuelve a poner un mail con error (o en espera) para que salga en la próxima pasada."""
    cur.execute("UPDATE mails_cola SET estado = 'pendiente', intentos = 0, intentar_desde = now() "
                "WHERE id = %s AND estado IN ('pendiente', 'error')", (cola_id,))
    return cur.rowcount == 1


def listar(cur) -> list:
    """Pendientes y con error, más lo que salió o se canceló en las últimas 48 h."""
    cur.execute(
        """SELECT c.id, c.cuenta, c.para, c.asunto, c.clave_mail, c.acta, c.origen, c.encolado_por, c.estado, c.intentos,
                  c.ultimo_error, c.motivo, c.intentar_desde, c.encolado_en, c.enviado_en,
                  COALESCE(NULLIF(m.denominacion_inpi, ''), m.denominacion) AS marca
             FROM mails_cola c LEFT JOIN marcas m ON m.acta = c.acta
            WHERE c.estado IN ('pendiente', 'error') OR c.encolado_en > now() - interval '48 hours'
            ORDER BY (c.estado IN ('pendiente', 'error')) DESC, c.id DESC LIMIT 300""")
    filas = cur.fetchall()
    for f in filas:
        for k in ("intentar_desde", "encolado_en", "enviado_en"):
            f[k] = f[k].isoformat() if f.get(k) else None
        f["plantilla"] = mc.CATALOGO.get(f["clave_mail"] or "", {}).get("nombre", f["clave_mail"])
        f["para"] = ", ".join(f["para"] or [])
    return filas


def iniciar(conexion):
    """Hilo que revisa la cola cada minuto (corre dentro del panel, siempre prendido en Railway)."""
    def bucle():
        time.sleep(30)
        while True:
            try:
                r = procesar(conexion)
                if any(r.values()):
                    print(f"[cola] {json.dumps(r)}")
            except Exception as e:  # noqa: BLE001
                print(f"[cola] falló la pasada de la cola de mails: {e}")
            time.sleep(PASADA_SEGUNDOS)

    hilo = threading.Thread(target=bucle, name="cola-mails", daemon=True)
    hilo.start()
    return hilo
