"""
Revisión de vistas de INPI en marcas PRE-BOLETÍN (antes de que salgan en un boletín).

Toma los leads que todavía no tienen boletín (boletin IS NULL, es_lead = true)
y se presentaron hace --dias-minimos días o más, mira la Grilla Digital de cada
una y las clasifica:

  publicada          ya tiene "Hoja Publicacion": salió en un boletín; sigue el
                     flujo de oposiciones de siempre
  orden_publicacion  tiene "Orden de Publicación" (con o sin vista antes, ya resuelta):
                     sale en el próximo miércoles, no hay nada que ofrecer
  vista_pendiente    tiene "Vista de Marcas" y todavía no hay Orden de
                     Publicación ni contestación posterior: CANDIDATA. Se pide
                     además el expediente para ver notificación/vencimiento
  vista_resuelta     tuvo una vista y ya hay una contestación posterior, pero
                     todavía no la Orden de Publicación (puede llegar otra vista)
  en_examen          sin vista ni orden: INPI todavía la está examinando
  sin_datos          la Grilla vino vacía (bloqueo del WAF o sin filas)

Mide además cuánto tarda cada consulta, para dimensionar un proceso diario
(cuántas marcas entran en una corrida y cuánto dura).

Para las vistas pendientes además baja el PDF de la vista y lo lee (scripts/vistas_pdf.py):
tipo (de fondo o formal), artículo de la ley, marca anterior con la que se parece y plazo
que dice el texto (30 días corridos / 10 días hábiles). Con eso calcula un vencimiento
propio y usa el más cercano entre ese y el que marca INPI en el expediente.

Qué marcas se vuelven a revisar (sin --actas):
  - nunca las contactadas (contactado): ya se les ofreció ayuda, igual que en el seguimiento de oposiciones;
  - dejan de revisarse las que llegaron a Orden de Publicación o se publicaron, o cuyo titular ya
    tiene apoderado, y las que pasó el vencimiento de la vista hace más de --dias-despues-vencimiento días;
  - una marca sin vista se mira de nuevo a los --dias-entre-revisiones días (7) y una con vista sin
    contestar a los --dias-entre-revisiones-vista (3). El expediente se vuelve a leer en cada revisión de
    una vista pendiente porque la contestación no siempre aparece como fila en la Grilla.

Con --reinterpretar no consulta a INPI: vuelve a leer el texto ya guardado de cada vista con el
lector actual (para corregir lo guardado cuando mejora el lector).

Sin --guardar es solo lectura. Con --guardar escribe el resultado en las
columnas propias de la vista (tuvo_vista, vista_estado, vista_fecha...) -- NO
toca tuvo_oposicion ni los campos de la oposición: una vista de INPI antes de
publicar es otra cosa que una oposición de un tercero. Las marcas que ya llegaron
a "Orden de Publicación" o a la publicación no se vuelven a consultar.

Uso:
    DATABASE_URL=... python3 probar_vistas_preboletin.py [--dias-minimos 7]
        [--limite 600] [--delay 1.5] [--max-minutes 90] [--guardar]
        [--actas 4797016,4797046]   # revisa SOLO esas actas (y lee el PDF de la vista aunque ya esté resuelta)
"""

import argparse
import datetime
import os
import statistics
import sys
import time
import unicodedata

import psycopg2
import psycopg2.extras

import monitor_bloqueo
from oposiciones_expediente import clasificar_estado_oposicion, consultar_expediente
from validar_leads import _parsear_fecha_grilla, buscar_archivos_grilla, crear_sesion
from vistas_pdf import leer_vista, plazo_legible, problemas_de_lectura


def _norm(texto: str) -> str:
    """Mayúsculas y sin tildes, para comparar el Indice de la Grilla."""
    s = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").upper().strip()


def _fechas(archivos, contiene):
    """Fechas ISO de las filas cuyo Indice contiene ese texto (sin tildes)."""
    out = []
    for a in archivos:
        if contiene in _norm(a.get("Indice") or ""):
            f = _parsear_fecha_grilla(a.get("Fecha") or "")
            if f:
                out.append(f)
    return sorted(out)


def clasificar(archivos):
    """(categoria, fecha_ingreso, fecha_vista) a partir de las filas de la Grilla."""
    ingreso = next(iter(_fechas([a for a in archivos if "SOLICITUD DE MARCA" in _norm(a.get("Referencia") or "")],
                                "RECIBO DE INGRESO")), None)
    if _fechas(archivos, "HOJA PUBLICACION"):
        return "publicada", ingreso, None
    vistas = _fechas(archivos, "VISTA")
    ordenes = _fechas(archivos, "ORDEN DE PUBLICACION")
    contestaciones = _fechas(archivos, "CONTEST")
    if vistas:
        ultima = vistas[-1]
        if any(o >= ultima for o in ordenes):
            return "orden_publicacion", ingreso, ultima  # la vista se resolvió y ya sale: no se vuelve a mirar
        resuelta = any(c >= ultima for c in contestaciones)
        return ("vista_resuelta" if resuelta else "vista_pendiente"), ingreso, ultima
    if ordenes:
        return "orden_publicacion", ingreso, None
    return "en_examen", ingreso, None


ESTADOS_VISTA_QUE_SIRVEN = ("sin_notificar", "notificada_en_plazo", "plazo_vencido")


def _estado_vista(est, hoy_iso, venc_texto=None):
    """Estado de la vista para guardar, a partir de clasificar_estado_oposicion.
    El vencimiento que cuenta es el más cercano entre el de INPI (expediente) y el que sale
    del texto de la vista (venc_texto). None si el expediente devolvió algo que no es una
    vista (no se guarda)."""
    e = est["estado"]
    if e == "con_apoderado":
        return "con_apoderado"
    if e == "vista_pendiente":
        venc = min((v for v in (est["vencimiento"], venc_texto) if v), default=None)
        if venc:
            return "plazo_vencido" if venc < hoy_iso else "notificada_en_plazo"
        return "notificada_en_plazo" if est["notificacion"] else "sin_notificar"
    if e == "sin_oposicion":
        return "resuelta"  # el expediente la muestra contestada
    return None


TIPOS_LEGIBLES = {"fondo": "de fondo", "formal": "formal (logotipos)", "fondo_y_formal": "de fondo y formal",
                  "convenio_paris": "por el Convenio de París", "clasificacion": "formal (productos y servicios)"}


def _fmt(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


def _detalle_vista(est, pdf):
    """Texto para la ficha: qué pide la vista y hasta cuándo, con las dos fechas."""
    partes = [f"Vista {TIPOS_LEGIBLES.get(pdf.get('tipo'), 'de INPI')}"]
    if pdf.get("articulos"):
        # los de la Ley 22.362 empiezan con «Art.»; el Convenio de París ya viene con su nombre
        partes[0] += ": " + ", ".join(a + (" de la Ley 22.362" if a.startswith("Art.") else "") for a in pdf["articulos"])
    if pdf.get("motivo"):
        partes.append(f"motivo: «{pdf['motivo']}»")
    if pdf.get("antecedentes"):
        partes.append("parecida a " + "; ".join(pdf["antecedentes"]))
    if est.get("notificacion"):
        partes.append(f"notificada el {_fmt(est['notificacion'])}")
    if pdf.get("vencimiento_texto"):
        marca = " (estimado, todavía sin notificación)" if pdf.get("vencimiento_estimado") else ""
        partes.append(f"plazo según el texto: {plazo_legible(pdf)}, hasta el {_fmt(pdf['vencimiento_texto'])}{marca}")
    elif pdf.get("sin_plazo_escrito"):
        partes.append("la vista no escribe el plazo")
    if est.get("vencimiento"):
        partes.append(f"INPI marca el {_fmt(est['vencimiento'])}")
    return " · ".join(partes)


def _texto_guardado(conn, acta):
    with conn.cursor() as cur:
        cur.execute("SELECT vista_texto FROM marcas WHERE acta = %s", (acta,))
        r = cur.fetchone()
    return (r[0] if r else None) or ""


def enviar_aviso_vistas(items):
    """Un solo mail con las vistas que el lector no supo interpretar (plantilla «vista_sin_entender»
    de la pestaña Mails; si no se puede leer esa configuración, valen RESEND_FROM / NOTIFICAR_A)."""
    if not items:
        return
    import requests
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
    import mails_plantillas
    asunto, cuerpo = mails_plantillas.armar_mail_vistas_sin_entender(items, monitor_bloqueo._link_corrida())
    try:
        import mails_core
        cfg = mails_core.preparar("vista_sin_entender")
        api_key, remitente, destinatarios, responder_a, env_key = (
            cfg["api_key"], cfg["remitente"], cfg["destinatarios"], cfg["responder_a"], cfg["env_key"])
    except Exception as e:  # noqa: BLE001 — el aviso no puede depender de la configuración
        print(f"::warning::No se pudo leer la configuración de mails ({e}); se usan los valores de respaldo")
        api_key, env_key = os.environ.get("RESEND_API_KEY"), "RESEND_API_KEY"
        remitente = os.environ.get("RESEND_FROM") or monitor_bloqueo.DEFAULT_FROM
        destinatarios = [d.strip() for d in (os.environ.get("NOTIFICAR_A") or monitor_bloqueo.DEFAULT_TO).split(",") if d.strip()]
        responder_a = None
    if not api_key:
        print(f"::warning::No hay {env_key}: no se pudo mandar el aviso de {len(items)} vistas sin entender")
        return
    payload = {"from": remitente, "to": destinatarios, "subject": asunto, "html": cuerpo}
    if responder_a:
        payload["reply_to"] = responder_a
    try:
        r = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=30)
        print(f"Aviso de {len(items)} vistas sin entender: HTTP {r.status_code}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::No se pudo mandar el aviso de vistas sin entender: {e}")


def reinterpretar(conn, hoy_iso, avisar=True):
    """Vuelve a interpretar, SIN consultar a INPI, el texto de las vistas ya guardadas
    (vista_texto) con el lector actual: corrige tipo, artículo, motivo, plazo y vencimiento
    propio cuando el lector mejora o cambian los feriados. Las fechas de INPI y la notificación no se tocan.
    Con avisar=False (lo usa feriados.py) no manda el mail de «vistas que no supo leer»."""
    from feriados import cargar_feriados
    from vistas_pdf import parsear_vista, vencimiento_desde_texto
    feriados = cargar_feriados(conn)
    iso = lambda d: d.isoformat() if d else None
    cambiadas = 0
    sin_entender = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT acta, denominacion, vista_texto, vista_fecha, vista_fecha_notificacion, vista_fecha_vencimiento,
                      vista_estado, vista_tipo, vista_articulos, vista_motivo, vista_vencimiento_calculado
               FROM marcas WHERE tuvo_vista IS TRUE AND COALESCE(vista_texto, '') <> '' ORDER BY acta"""
        )
        filas = cur.fetchall()
    print(f"Vistas guardadas con texto: {len(filas)}", flush=True)
    for f in filas:
        pdf = parsear_vista(f["vista_texto"])
        venc, estimado = vencimiento_desde_texto(pdf, iso(f["vista_fecha_notificacion"]), iso(f["vista_fecha"]), feriados)
        pdf.update({"vencimiento_texto": venc, "vencimiento_estimado": estimado})
        est = {"notificacion": iso(f["vista_fecha_notificacion"]), "vencimiento": iso(f["vista_fecha_vencimiento"])}
        estado = f["vista_estado"]
        if estado in ("sin_notificar", "notificada_en_plazo", "plazo_vencido"):
            estado = _estado_vista({"estado": "vista_pendiente", **est}, hoy_iso, venc)
        nuevo = (pdf["tipo"], ", ".join(pdf["articulos"]) or None, pdf["motivo"] or None)
        anterior = (f["vista_tipo"], f["vista_articulos"], f["vista_motivo"])
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE marcas SET vista_tipo = %s, vista_articulos = %s, vista_motivo = %s, vista_plazo = %s,
                          vista_antecedente = COALESCE(%s, vista_antecedente),
                          vista_vencimiento_calculado = %s, vista_estado = %s,
                          vista_sirve = %s, vista_detalle = %s
                   WHERE acta = %s""",
                (pdf["tipo"], ", ".join(pdf["articulos"]) or None, pdf["motivo"] or None,
                 plazo_legible(pdf) or None, "; ".join(pdf["antecedentes"]) or None, venc, estado,
                 estado in ESTADOS_VISTA_QUE_SIRVEN, _detalle_vista(est, pdf), f["acta"]),
            )
        conn.commit()
        problemas = problemas_de_lectura(pdf) if estado != "resuelta" else []
        if problemas:
            print(f"    sin entender ({'; '.join(problemas)}): {(f['vista_texto'] or '')[:400]}", flush=True)
            sin_entender.append({"acta": f["acta"], "marca": f["denominacion"], "tipo": pdf["tipo"],
                                 "problemas": problemas, "texto": f["vista_texto"]})
        if nuevo != anterior:
            cambiadas += 1
        print(f"  acta {f['acta']}: {pdf['tipo']} · {', '.join(pdf['articulos']) or 'sin artículo'}"
              f"{' · motivo ' + pdf['motivo'] if pdf['motivo'] else ''} · {plazo_legible(pdf) or 'sin plazo'} · vence {venc or '—'}"
              f"{'  (antes: ' + str(anterior[0]) + ')' if nuevo != anterior else ''}", flush=True)
    print(f"Reinterpretadas: {len(filas)}; con tipo, artículo o motivo distinto: {cambiadas}; sin entender: {len(sin_entender)}", flush=True)
    if avisar:
        enviar_aviso_vistas(sin_entender)


def guardar_resultado(conn, acta, categoria, datos_vista):
    """Escribe el resultado de la revisión. Solo columnas de vista: nunca toca
    tuvo_oposicion ni los campos de la oposición de un tercero."""
    with conn.cursor() as cur:
        if datos_vista:
            cur.execute(
                """
                UPDATE marcas SET
                    vista_grilla_estado = %s, vista_revisada_en = now(),
                    tuvo_vista = true,
                    vista_estado = %s, vista_sirve = %s, vista_detalle = %s,
                    vista_fecha = %s, vista_fecha_notificacion = %s, vista_fecha_vencimiento = %s,
                    vista_detectada_en = COALESCE(vista_detectada_en, now()),
                    -- lo que sale del PDF: si esta vez no se pudo leer, queda lo que ya había
                    vista_tipo = COALESCE(%s, vista_tipo), vista_articulos = COALESCE(%s, vista_articulos),
                    vista_motivo = %s,
                    vista_antecedente = COALESCE(%s, vista_antecedente), vista_plazo = COALESCE(%s, vista_plazo),
                    vista_vencimiento_calculado = COALESCE(%s, vista_vencimiento_calculado),
                    vista_texto = COALESCE(%s, vista_texto),
                    vista_pdf_leido_en = CASE WHEN %s THEN now() ELSE vista_pdf_leido_en END
                WHERE acta = %s
                """,
                (categoria, datos_vista["estado"], datos_vista["estado"] in ESTADOS_VISTA_QUE_SIRVEN,
                 datos_vista["detalle"], datos_vista["fecha"], datos_vista["notificacion"],
                 datos_vista["vencimiento"],
                 datos_vista.get("tipo"), datos_vista.get("articulos"), datos_vista.get("motivo") or None,
                 datos_vista.get("antecedente"),
                 datos_vista.get("plazo"), datos_vista.get("vencimiento_calculado"), datos_vista.get("texto"),
                 bool(datos_vista.get("tipo")), acta),
            )
        else:
            cur.execute(
                "UPDATE marcas SET vista_grilla_estado = %s, vista_revisada_en = now() WHERE acta = %s",
                (categoria, acta),
            )
            if categoria in ("publicada", "orden_publicacion"):
                # Para llegar a la publicación la vista tuvo que resolverse.
                cur.execute(
                    "UPDATE marcas SET vista_estado = 'resuelta', vista_sirve = false "
                    "WHERE acta = %s AND tuvo_vista IS TRUE AND vista_estado IS DISTINCT FROM 'resuelta'",
                    (acta,),
                )
    conn.commit()


def _percentil(valores, p):
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias-minimos", type=int, default=7, help="solo marcas presentadas hace N días o más")
    ap.add_argument("--limite", type=int, default=600, help="tope de marcas a consultar (las más viejas primero)")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre marcas (freno de mano)")
    ap.add_argument("--max-minutes", type=float, default=90, help="corta solo pasados estos minutos")
    ap.add_argument("--guardar", action="store_true", help="escribe el resultado en las columnas de vista de la marca (sin esto, solo lectura)")
    ap.add_argument("--dias-entre-revisiones", type=int, default=7, help="días hasta volver a mirar una marca sin vista")
    ap.add_argument("--dias-entre-revisiones-vista", type=int, default=3, help="días hasta volver a mirar una marca con vista sin contestar")
    ap.add_argument("--dias-despues-vencimiento", type=int, default=7, help="días después del vencimiento de la vista en que se deja de revisar")
    ap.add_argument("--reinterpretar", action="store_true",
                    help="vuelve a interpretar el texto ya guardado de las vistas con el lector actual y sale (no consulta a INPI)")
    ap.add_argument("--actas", default="", help="revisar SOLO estas actas (separadas por coma), sin los filtros de antigüedad y estado")
    args = ap.parse_args()
    actas_pedidas = [a.strip() for a in args.actas.split(",") if a.strip()]

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    if args.reinterpretar:
        reinterpretar(conn, datetime.date.today().isoformat())
        conn.close()
        return
    from feriados import cargar_feriados
    feriados = cargar_feriados(conn)
    print(f"Feriados cargados para contar días hábiles: {len(feriados)}", flush=True)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'marcas' AND column_name = 'vista_motivo'")
        if not cur.fetchone()[0]:
            sys.exit("Faltan las columnas de vista en la tabla marcas: se crean solas cuando el panel arranca con el último deploy. Reintentar en unos minutos.")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if actas_pedidas:
            cur.execute(
                """
                SELECT acta, COALESCE(NULLIF(denominacion, ''), denominacion_inpi) AS denominacion, titular,
                       COALESCE(fecha_presentacion::date, CURRENT_DATE) AS presentada
                FROM marcas WHERE acta = ANY(%s)
                """,
                (actas_pedidas,),
            )
            todas = sorted(cur.fetchall(), key=lambda r: actas_pedidas.index(r["acta"]))
            faltan = set(actas_pedidas) - {r["acta"] for r in todas}
            if faltan:
                print(f"AVISO: estas actas no están en la base: {', '.join(sorted(faltan))}", flush=True)
        else:
            cur.execute(
                """
                SELECT acta, COALESCE(NULLIF(denominacion, ''), denominacion_inpi) AS denominacion, titular, fecha_presentacion::date AS presentada
                FROM marcas
                WHERE es_lead = true AND boletin IS NULL
                  AND contactado IS NOT TRUE
                  AND fecha_presentacion IS NOT NULL
                  AND fecha_presentacion::date <= CURRENT_DATE - %s
                  AND COALESCE(vista_grilla_estado, '') NOT IN ('orden_publicacion', 'publicada', 'vista_con_apoderado')
                  AND COALESCE(vista_estado, '') <> 'con_apoderado'
                  -- pasó el vencimiento de la vista hace más de N días: ya no hay nada que ofrecer
                  AND NOT COALESCE(tuvo_vista IS TRUE AND vista_sirve IS TRUE
                           AND LEAST(vista_vencimiento_calculado, vista_fecha_vencimiento) < CURRENT_DATE - %s, false)
                  -- no volver a mirar antes de tiempo
                  AND (vista_revisada_en IS NULL
                       OR vista_revisada_en < now() - make_interval(days => CASE WHEN tuvo_vista IS TRUE AND vista_sirve IS TRUE THEN %s ELSE %s END))
                ORDER BY vista_revisada_en ASC NULLS FIRST, fecha_presentacion ASC
                """,
                (args.dias_minimos, args.dias_despues_vencimiento, args.dias_entre_revisiones_vista, args.dias_entre_revisiones),
            )
            todas = cur.fetchall()
    if not args.guardar:
        conn.close()
    marcas = todas[: args.limite]
    print(f"Leads pre-boletín con {args.dias_minimos}+ días: {len(todas)}. A consultar en esta prueba: {len(marcas)}.", flush=True)

    hoy_iso = datetime.date.today().isoformat()
    s = crear_sesion()
    inicio = time.time()
    duraciones, duraciones_exp, duraciones_pdf = [], [], []
    conteo, candidatas = {}, []
    sin_entender = []  # vistas que el lector no supo interpretar (se avisa por mail al final)
    consultadas = 0
    corte = None

    for n, m in enumerate(marcas, 1):
        if (time.time() - inicio) / 60 >= args.max_minutes:
            corte = "se agotó el tiempo de la prueba"
            break
        if monitor_bloqueo.debe_cortar():
            corte = "INPI bloqueó consultas seguidas"
            break
        t0 = time.time()
        try:
            archivos = buscar_archivos_grilla(s, m["acta"])
        except Exception as e:  # noqa: BLE001
            print(f"  [{n}/{len(marcas)}] acta {m['acta']}: error {e}", flush=True)
            archivos = []
        dt_grilla = time.time() - t0
        duraciones.append(dt_grilla)
        consultadas += 1

        if not archivos:
            categoria, ingreso, vista = "sin_datos", None, None
        else:
            categoria, ingreso, vista = clasificar(archivos)

        extra = ""
        datos_vista = None  # lo que se guarda en las columnas de vista (None = nada que guardar)
        guardable = categoria != "sin_datos"
        leer_pdf = categoria == "vista_pendiente" or (actas_pedidas and categoria == "vista_resuelta")
        if leer_pdf:
            t1 = time.time()
            try:
                exp = consultar_expediente(s, m["acta"])
                if exp.get("bloqueado"):
                    categoria, guardable = "vista_sin_expediente", False
                    extra = " | expediente: bloqueado por el WAF, se reintenta"
                else:
                    est = clasificar_estado_oposicion(exp, archivos, None)
                    duraciones_exp.append(time.time() - t1)
                    time.sleep(args.delay)
                    # PDF de la vista: tipo, artículo, antecedente y plazo que dice el texto
                    pdf = {}
                    if est["estado"] in ("vista_pendiente", "con_apoderado") or actas_pedidas:
                        t2 = time.time()
                        try:
                            pdf = leer_vista(s, archivos, ingreso, est["notificacion"], feriados)
                        except Exception as e:  # noqa: BLE001
                            extra += f" | pdf: error {e}"
                        duraciones_pdf.append(time.time() - t2)
                        time.sleep(args.delay)
                    estado_v = _estado_vista(est, hoy_iso, pdf.get("vencimiento_texto"))
                    if estado_v is None:
                        categoria, guardable = "vista_sin_expediente", False
                    else:
                        extra = (f" | expediente: {estado_v}, notif {est['notificacion'] or '—'}, vence {est['vencimiento'] or '—'}"
                                 + extra)
                        if pdf:
                            extra += (f" | PDF: {pdf['tipo']}, {', '.join(pdf['articulos']) or 'sin artículo'}"
                                      f"{', parecida a ' + '; '.join(pdf['antecedentes']) if pdf['antecedentes'] else ''}"
                                      f", plazo {plazo_legible(pdf) or 'no encontrado'}, vence según texto {pdf.get('vencimiento_texto') or '—'}")
                        elif est["estado"] in ("vista_pendiente", "con_apoderado"):
                            extra += " | PDF: no se pudo leer"
                        if est["estado"] in ("vista_pendiente", "con_apoderado") or actas_pedidas:
                            problemas = problemas_de_lectura(pdf or None)
                            # el mismo texto ya avisado en una corrida anterior no se repite
                            if problemas and args.guardar and pdf.get("texto") and _texto_guardado(conn, m["acta"]) == pdf["texto"]:
                                problemas = []
                            if problemas:
                                sin_entender.append({"acta": m["acta"], "marca": m["denominacion"], "tipo": pdf.get("tipo"),
                                                     "problemas": problemas, "texto": pdf.get("texto")})
                                extra += " | LECTOR: " + "; ".join(problemas)
                        datos_vista = {
                            "estado": estado_v, "fecha": vista, "notificacion": est["notificacion"],
                            "vencimiento": est["vencimiento"],
                            "detalle": _detalle_vista(est, pdf) if pdf else est["detalle"],
                            "tipo": pdf.get("tipo"),
                            "articulos": ", ".join(pdf["articulos"]) if pdf.get("articulos") else None,
                            "motivo": pdf.get("motivo") or None,
                            "antecedente": "; ".join(pdf["antecedentes"]) if pdf.get("antecedentes") else None,
                            "plazo": plazo_legible(pdf) if pdf.get("plazos") else None,
                            "vencimiento_calculado": pdf.get("vencimiento_texto"),
                            "texto": pdf.get("texto"),
                        }
                        if estado_v == "con_apoderado":
                            categoria = "vista_con_apoderado"
                        elif estado_v == "resuelta":
                            categoria = "vista_resuelta"
                        if actas_pedidas and pdf.get("texto"):
                            print(f"      texto de la vista: {pdf['texto'][:500]}", flush=True)
            except Exception as e:  # noqa: BLE001
                categoria, guardable = "vista_sin_expediente", False
                extra = f" | expediente: error {e}"
            if categoria == "vista_pendiente":
                candidatas.append((m, ingreso, vista, extra))
        elif categoria == "vista_resuelta":
            datos_vista = {"estado": "resuelta", "fecha": vista, "notificacion": None, "vencimiento": None,
                           "detalle": "La vista ya se resolvió: hay una Orden de Publicación o una contestación posterior."}

        if args.guardar and guardable:
            try:
                guardar_resultado(conn, m["acta"], categoria, datos_vista)
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                extra += f" | NO se pudo guardar: {e}"

        conteo[categoria] = conteo.get(categoria, 0) + 1
        dias = (datetime.date.today() - m["presentada"]).days
        print(f"  [{n}/{len(marcas)}] acta {m['acta']} ({dias} d): {categoria} [{dt_grilla:.1f} s]{extra}", flush=True)
        time.sleep(args.delay)

    total_s = time.time() - inicio
    lineas = [
        f"Leads pre-boletín con {args.dias_minimos}+ días: {len(todas)} (consultadas: {consultadas})",
        f"Tiempo total de la prueba: {total_s / 60:.1f} min ({total_s / max(consultadas, 1):.1f} s por marca, incluida la espera de {args.delay} s)",
    ]
    if duraciones:
        lineas += [
            f"Grilla (2 consultas a INPI): promedio {statistics.mean(duraciones):.1f} s, mediana {statistics.median(duraciones):.1f} s, "
            f"p95 {_percentil(duraciones, 0.95):.1f} s, máximo {max(duraciones):.1f} s",
            f"Proyección de 600 marcas por noche: ≈ {600 * total_s / max(consultadas, 1) / 60:.0f} min",
        ]
    if duraciones_exp:
        lineas.append(f"Expediente (solo marcas con vista): {len(duraciones_exp)} consultas, promedio {statistics.mean(duraciones_exp):.1f} s")
    if duraciones_pdf:
        lineas.append(f"PDF de la vista (descarga + lectura): {len(duraciones_pdf)} consultas, promedio {statistics.mean(duraciones_pdf):.1f} s")
    lineas.append(f"Consultas OK / bloqueadas por el WAF: {monitor_bloqueo._estado['ok']} / {monitor_bloqueo._estado['bloqueadas']}")
    if corte:
        lineas.append(f"AVISO: la prueba se cortó antes de terminar: {corte}")

    if args.guardar:
        enviar_aviso_vistas(sin_entender)
        conn.close()
        lineas.append("Resultado guardado en el panel (columnas de vista de cada marca)")
    print("\n=== Resultado de la prueba ===")
    print("\n".join(lineas))
    print("\nPor categoría:")
    for k, v in sorted(conteo.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"\nCandidatas con vista pendiente: {len(candidatas)}")
    for m, ingreso, vista, extra in candidatas:
        print(f"  acta {m['acta']} · {m['denominacion'] or '—'} · {m['titular'] or '—'} · ingreso {ingreso or '—'} · vista {vista or '—'}{extra}")

    resumen_gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_gh:
        with open(resumen_gh, "a", encoding="utf-8") as f:
            f.write("## Prueba de vistas en pre-boletín (solo lectura)\n\n")
            f.write("\n".join(f"- {l}" for l in lineas) + "\n\n")
            f.write("| Categoría | Marcas |\n|---|---|\n")
            for k, v in sorted(conteo.items(), key=lambda kv: -kv[1]):
                f.write(f"| {k} | {v} |\n")
            if candidatas:
                f.write("\n### Candidatas con vista pendiente\n\n| Acta | Marca | Titular | Ingreso | Vista | Expediente |\n|---|---|---|---|---|---|\n")
                for m, ingreso, vista, extra in candidatas:
                    f.write(f"| {m['acta']} | {m['denominacion'] or '—'} | {m['titular'] or '—'} | {ingreso or '—'} | {vista or '—'} | {extra.replace(' | expediente: ', '')} |\n")


if __name__ == "__main__":
    main()
