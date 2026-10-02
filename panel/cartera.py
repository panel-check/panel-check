"""
Cartera de clientes y vigilancia marcaria: tablas, datos iniciales y
cálculo de plazos. Sin FastAPI ni requests: lo usan el panel (app.py y
cartera_api.py) y también los scripts de GitHub Actions
(scripts/revisar_cartera.py, scripts/vigilancia.py,
scripts/notificar_cartera.py), que lo importan agregando panel/ al path.

Por qué acá y no duplicado en scripts/: Railway despliega solo panel/, así
que el panel no puede importar de scripts/; los scripts sí ven panel/ (el
workflow hace checkout del repo entero). Poniéndolo en panel/ hay una sola
copia de las tablas y de las reglas de plazos.

Modelo:
  clientes                 una fila por cliente (persona o empresa).
  cartera_marcas           una fila por acta de la cartera, siempre de UN
                           cliente. Es independiente de la tabla `marcas` (la
                           de leads): una marca vieja de un cliente puede no
                           haber pasado nunca por un boletín procesado.
  cartera_novedades        cambios detectados en los expedientes de la cartera
                           (estado, oposición, movimientos, titular...).
  cartera_matriculas       matrículas de agente del estudio: lo que se
                           presenta con esas matrículas se propone para sumar
                           a la cartera (pestaña "Por matrícula").
  cartera_descartes        actas propuestas por matrícula que se descartaron.
  cartera_avisos_plazo     qué plazos ya se avisaron por mail (para no repetir).
  solicitudes_escaneadas   actas que encontró el escaneo directo y NO son
                           leads (tienen agente). El escaneo antes las tiraba;
                           ahora se guardan acá (no en `marcas`, para no
                           mezclarlas con los leads) porque la vigilancia
                           necesita ver TODAS las solicitudes nuevas.
  vigilancia_alertas       parecidos detectados entre una marca nueva y una
                           marca de la cartera, y solicitudes nuevas de un
                           cliente presentadas con otro agente.
  vigilancia_comparadas    qué solicitudes nuevas ya se compararon (y con qué
                           nombre), para no recompararlas en cada corrida.
  vigilancia_clases_relacionadas / vigilancia_palabras_genericas /
  vigilancia_config        ajustes editables desde el panel.
"""

import calendar
import datetime as _dt

ESTADOS_FINALES = ("Concedida", "Denegada")

ORIGENES_CLIENTE = {
    "cartera": "Cartera previa",
    "sistema": "Vino del sistema de leads",
    "referido": "Referido",
    "formulario": "Completó el formulario web",
    "otro": "Otro",
}

ESTADOS_ALERTA = {
    "nueva": "Nueva",
    "monitorear": "Monitorear",
    "oponer": "Oponer",
    "opuesta": "Oposición presentada",
    "descartada": "Descartada",
}
ESTADOS_ALERTA_ABIERTOS = ("nueva", "monitorear", "oponer")

# Ajustes por defecto de la vigilancia (editables en Clientes → Ajustes).
CONFIG_DEFAULT = {
    "umbral": "75",          # puntaje mínimo para crear una alerta (0-100)
    "nivel_alta": "90",      # desde acá la alerta es "alta"
    "nivel_media": "82",     # desde acá "media"; debajo, "baja"
    "meses_universo": "18",  # contra qué antigüedad de solicitudes se compara
}

# Clases de Niza que suelen chocar entre sí (productos y su venta/servicio
# asociado). Punto de partida razonable, no una regla legal: se edita desde
# el panel. Se cargan una sola vez (ver vigilancia_config.semilla_clases);
# si después se borra un par, no vuelve a aparecer.
CLASES_RELACIONADAS_DEFAULT = [
    (3, 5), (3, 35), (3, 44), (5, 10), (5, 29), (5, 30), (5, 32), (5, 44),
    (6, 19), (6, 37), (7, 37), (9, 28), (9, 35), (9, 38), (9, 41), (9, 42),
    (11, 37), (12, 37), (12, 39), (14, 18), (14, 25), (14, 35), (16, 35),
    (16, 41), (18, 25), (18, 35), (19, 37), (20, 21), (20, 24), (20, 35),
    (24, 25), (25, 26), (25, 35), (28, 35), (28, 41), (29, 30), (29, 31),
    (29, 35), (29, 43), (30, 32), (30, 35), (30, 43), (31, 44), (32, 33),
    (32, 35), (32, 43), (33, 35), (33, 43), (35, 41), (39, 41), (41, 43),
    (41, 44), (42, 45),
]

# Palabras que no distinguen una marca de otra (artículos, formas
# societarias, términos comerciales genéricos). Se sacan antes de comparar,
# salvo que la marca quede vacía (entonces se usa completa).
PALABRAS_GENERICAS_DEFAULT = [
    "EL", "LA", "LOS", "LAS", "LO", "DE", "DEL", "Y", "E", "EN", "AL", "UN", "UNA",
    "MI", "TU", "SU", "THE", "OF", "AND", "A", "BY",
    "SA", "SRL", "SAS", "SAU", "SH", "SOCIEDAD", "ANONIMA", "LTDA", "INC", "LLC", "CO",
    "CIA", "CORP", "COMPANY",
    "GROUP", "GRUPO", "ARGENTINA", "ARG", "STORE", "SHOP", "TIENDA", "ONLINE",
    "OFICIAL", "OFFICIAL", "BRAND", "MARCA", "INTERNACIONAL", "INTERNATIONAL",
]


def crear_tablas(cur):
    """Idempotente: se puede llamar en cada arranque del panel y en cada
    script. No toca la tabla `marcas` (no compite por su lock)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id                     SERIAL PRIMARY KEY,
            nombre                 TEXT NOT NULL,
            cuit                   TEXT,
            email                  TEXT,
            telefono               TEXT,
            contacto               TEXT,
            notas                  TEXT,
            origen                 TEXT NOT NULL DEFAULT 'cartera',
            clave_crm              TEXT,
            convertido_en          TIMESTAMPTZ,
            vigilancia_contratada  BOOLEAN NOT NULL DEFAULT false,
            vigilancia_desde       DATE,
            activo                 BOOLEAN NOT NULL DEFAULT true,
            alta_en                TIMESTAMPTZ NOT NULL DEFAULT now(),
            alta_por               TEXT,
            modificado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
            modificado_por         TEXT
        )
        """
    )
    # Quién nos refirió al cliente (agencia de marketing, diseñador, etc.): se ve como tag.
    cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS referido TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clientes_cuit ON clientes(cuit)")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_clientes_clave_crm ON clientes(clave_crm) "
        "WHERE clave_crm IS NOT NULL"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartera_marcas (
            acta                     TEXT PRIMARY KEY,
            cliente_id               INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
            denominacion             TEXT,
            tipo                     TEXT,
            clase                    INTEGER,
            titular                  TEXT,
            cuit                     TEXT,
            fecha_presentacion       DATE,
            fecha_publicacion        DATE,
            agente                   TEXT,
            matricula_agente         TEXT,
            caracter                 TEXT,
            estado_tramite           TEXT,
            fecha_concesion          DATE,
            numero_disposicion       TEXT,
            fecha_vencimiento_marca  DATE,
            tuvo_oposicion           BOOLEAN,
            detalle_oposicion        TEXT,
            movimientos              INTEGER,
            grilla_claves            JSONB,
            ultimo_movimiento        TEXT,
            ultimo_movimiento_fecha  DATE,
            vigilar                  BOOLEAN NOT NULL DEFAULT true,
            vigilar_todas_clases     BOOLEAN NOT NULL DEFAULT false,
            terminos_vigilancia      TEXT,
            vigilancia_firma         TEXT,
            notas                    TEXT,
            origen_carga             TEXT,
            consultado_en            TIMESTAMPTZ,
            error_consulta           TEXT,
            alta_en                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            alta_por                 TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cartera_marcas_cliente ON cartera_marcas(cliente_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartera_novedades (
            id             BIGSERIAL PRIMARY KEY,
            acta           TEXT NOT NULL,
            cliente_id     INTEGER,
            tipo           TEXT NOT NULL,
            texto          TEXT NOT NULL,
            detectado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
            notificado_en  TIMESTAMPTZ
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cartera_novedades_acta ON cartera_novedades(acta, detectado_en DESC)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_cartera_novedades_cliente ON cartera_novedades(cliente_id, detectado_en DESC)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartera_matriculas (
            matricula  TEXT PRIMARY KEY,
            nombre     TEXT,
            alta_en    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartera_descartes (
            acta            TEXT PRIMARY KEY,
            descartado_por  TEXT,
            descartado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartera_avisos_plazo (
            acta        TEXT NOT NULL,
            tipo        TEXT NOT NULL,
            fecha       DATE NOT NULL,
            umbral      INTEGER NOT NULL,
            avisado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (acta, tipo, fecha, umbral)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS solicitudes_escaneadas (
            acta                TEXT PRIMARY KEY,
            denominacion        TEXT,
            tipo                TEXT,
            clase               INTEGER,
            titular             TEXT,
            cuit                TEXT,
            fecha_presentacion  DATE,
            agente              TEXT,
            matricula_agente    TEXT,
            caracter            TEXT,
            es_lead             BOOLEAN,
            creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_solicitudes_escaneadas_cuit ON solicitudes_escaneadas(cuit)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vigilancia_alertas (
            id                        BIGSERIAL PRIMARY KEY,
            tipo                      TEXT NOT NULL DEFAULT 'similitud',
            acta_cliente              TEXT,
            cliente_id                INTEGER,
            acta_nueva                TEXT NOT NULL,
            denominacion_nueva        TEXT,
            clase_nueva               INTEGER,
            tipo_nuevo                TEXT,
            titular_nuevo             TEXT,
            cuit_nuevo                TEXT,
            agente_nuevo              TEXT,
            fecha_presentacion_nueva  DATE,
            fuente_nueva              TEXT,
            puntaje                   INTEGER,
            nivel                     TEXT,
            motivos                   TEXT,
            estado                    TEXT NOT NULL DEFAULT 'nueva',
            nota                      TEXT,
            decidido_por              TEXT,
            decidido_en               TIMESTAMPTZ,
            creada_en                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            notificada_en             TIMESTAMPTZ
        )
        """
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_vigilancia_alertas_unica "
        "ON vigilancia_alertas(tipo, COALESCE(acta_cliente, ''), acta_nueva)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vigilancia_alertas_estado ON vigilancia_alertas(estado, creada_en DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vigilancia_alertas_cliente ON vigilancia_alertas(cliente_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vigilancia_comparadas (
            acta          TEXT PRIMARY KEY,
            firma         TEXT,
            comparada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vigilancia_clases_relacionadas (
            clase_a  INTEGER NOT NULL,
            clase_b  INTEGER NOT NULL,
            PRIMARY KEY (clase_a, clase_b),
            CONSTRAINT vigilancia_clases_orden CHECK (clase_a < clase_b)
        )
        """
    )
    cur.execute("CREATE TABLE IF NOT EXISTS vigilancia_palabras_genericas (palabra TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE IF NOT EXISTS vigilancia_config (clave TEXT PRIMARY KEY, valor TEXT)")

    # Datos iniciales: una sola vez (si alguien borra un par o una palabra
    # desde el panel, no tiene que volver a aparecer en el próximo arranque).
    cur.execute("SELECT 1 FROM vigilancia_config WHERE clave = 'semilla_aplicada'")
    if not cur.fetchone():
        for a, b in CLASES_RELACIONADAS_DEFAULT:
            cur.execute(
                "INSERT INTO vigilancia_clases_relacionadas (clase_a, clase_b) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (min(a, b), max(a, b)),
            )
        for p in PALABRAS_GENERICAS_DEFAULT:
            cur.execute(
                "INSERT INTO vigilancia_palabras_genericas (palabra) VALUES (%s) ON CONFLICT DO NOTHING", (p,)
            )
        for k, v in CONFIG_DEFAULT.items():
            cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))
        cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES ('semilla_aplicada', '1')")


def leer_config(cur) -> dict:
    cur.execute("SELECT clave, valor FROM vigilancia_config")
    filas = cur.fetchall()
    conf = dict(CONFIG_DEFAULT)
    for f in filas:
        k, v = (f["clave"], f["valor"]) if isinstance(f, dict) else f
        conf[k] = v
    return {
        "umbral": int(conf["umbral"]),
        "nivel_alta": int(conf["nivel_alta"]),
        "nivel_media": int(conf["nivel_media"]),
        "meses_universo": int(conf["meses_universo"]),
    }


# ── Plazos ────────────────────────────────────────────────────────────────

def hoy_ar():
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()


def sumar_anios(d, anios: int):
    """Art. 6 CCyC: mismo día del mes; si no existe (29/02), el último día."""
    anio = d.year + anios
    return d.replace(year=anio, day=min(d.day, calendar.monthrange(anio, d.month)[1]))


def urgencia(dias: int) -> str:
    if dias < 0:
        return "vencido"
    if dias <= 7:
        return "urgente"
    if dias <= 60:
        return "proximo"
    return "lejano"


def _a_fecha(v):
    if v is None or isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.datetime):
        return v.date()
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def nombre_marca(r) -> str:
    nombre = (r.get("denominacion") or "").strip()
    if nombre:
        return nombre
    return "(figurativa, sin texto)" if (r.get("tipo") or "").strip().upper() == "F" else "(nombre pendiente)"


def plazos_marca_cartera(r, hoy=None) -> list:
    """Plazos orientativos de una marca propia de la cartera (mismo criterio
    que el CRM, ver _plazos_de_marcas en app.py): cierre del plazo de
    oposición de terceros (30 días corridos desde la publicación), oposición
    o vista recibida, ventana de la DJ de uso de medio término (año siguiente
    al 5.º aniversario de la concesión) y vencimiento del registro (tal como
    lo informa INPI). La fecha exacta se confirma siempre en el expediente."""
    hoy = hoy or hoy_ar()
    plazos = []

    def agregar(tipo, titulo, fecha, detalle="", urg=None):
        dias = (fecha - hoy).days
        plazos.append({
            "acta": r["acta"], "marca": nombre_marca(r), "clase": r.get("clase"),
            "cliente_id": r.get("cliente_id"), "cliente": r.get("cliente_nombre"),
            "tipo": tipo, "titulo": titulo, "detalle": detalle,
            "fecha": fecha, "dias": dias, "urgencia": urg or urgencia(dias),
        })

    estado = r.get("estado_tramite")
    pub = _a_fecha(r.get("fecha_publicacion"))
    if pub and estado not in ESTADOS_FINALES:
        cierre = pub + _dt.timedelta(days=30)
        if (hoy - cierre).days <= 30:
            abierto = hoy <= cierre
            agregar("oposicion_terceros",
                    "Cierra el plazo para que terceros se opongan" if abierto else "Cerró el plazo de oposición de terceros",
                    cierre, f"Publicada el {pub.strftime('%d/%m/%Y')} · 30 días corridos")
    if r.get("tuvo_oposicion") and estado not in ESTADOS_FINALES:
        detalle = (r.get("detalle_oposicion") or "").strip()
        agregar("oposicion_recibida", "Recibió una oposición / vista", hoy,
                "El plazo para responder corre desde la notificación: confirmar en el expediente"
                + (f" · {detalle}" if detalle else ""), urg="urgente")
    conc = _a_fecha(r.get("fecha_concesion"))
    if conc and estado == "Concedida":
        desde = sumar_anios(conc, 5)
        hasta = sumar_anios(conc, 6)
        if hoy < desde:
            agregar("ddjj", "Se abre la DJ de uso de medio término", desde,
                    f"Ventana hasta el {hasta.strftime('%d/%m/%Y')} (concedida el {conc.strftime('%d/%m/%Y')})")
        elif hoy <= hasta:
            agregar("ddjj", "Cierra la DJ de uso de medio término", hasta,
                    f"Ventana abierta desde el {desde.strftime('%d/%m/%Y')}")
    venc = _a_fecha(r.get("fecha_vencimiento_marca"))
    if venc and (hoy - venc).days <= 180:
        agregar("renovacion", "Vence el registro (renovación)" if venc >= hoy else "Venció el registro",
                venc, "Fecha de vencimiento informada por INPI")
    return plazos


def plazo_oposicion_alerta(a, hoy=None):
    """Para una alerta de vigilancia: hasta cuándo se puede presentar una
    oposición a la marca nueva (30 días corridos desde su publicación).
    None si todavía no se publicó (detectada por el escaneo, pre-boletín) o
    si el plazo cerró hace más de 30 días."""
    hoy = hoy or hoy_ar()
    pub = _a_fecha(a.get("publicacion_nueva"))
    if not pub:
        return None
    cierre = pub + _dt.timedelta(days=30)
    dias = (cierre - hoy).days
    if dias < -30:
        return None
    return {
        "acta": a.get("acta_cliente"), "marca": a.get("denominacion_cliente") or "",
        "clase": a.get("clase_cliente"), "cliente_id": a.get("cliente_id"), "cliente": a.get("cliente_nombre"),
        "alerta_id": a.get("id"), "tipo": "oposicion_vigilancia",
        "titulo": ("Vence el plazo para oponerse a " if dias >= 0 else "Venció el plazo para oponerse a ")
                  + f"«{a.get('denominacion_nueva') or 'marca sin nombre'}» (acta {a.get('acta_nueva')}, clase {a.get('clase_nueva')})",
        "detalle": f"Publicada el {pub.strftime('%d/%m/%Y')} · alerta {ESTADOS_ALERTA.get(a.get('estado'), a.get('estado'))}",
        "fecha": cierre, "dias": dias, "urgencia": urgencia(dias),
    }


# ── Alta y actualización de marcas de la cartera desde un expediente ───────

def _json(v):
    import json
    return json.dumps(v) if v is not None else None


def _vacio_a_none(v):
    return None if v in ("", None) else v


CAMPOS_EXPEDIENTE = (
    "denominacion", "tipo", "clase", "titular", "cuit", "fecha_presentacion", "fecha_publicacion",
    "agente", "matricula_agente", "caracter", "estado_tramite", "fecha_concesion", "numero_disposicion",
    "fecha_vencimiento_marca", "tuvo_oposicion", "detalle_oposicion", "movimientos",
    "ultimo_movimiento", "ultimo_movimiento_fecha",
)


def _valores_expediente(info: dict) -> dict:
    """Lo que se guarda de una consulta a INPI, con los vacíos como NULL (un
    vacío nunca pisa un dato que ya teníamos: ver COALESCE en los UPDATE)."""
    v = {k: _vacio_a_none(info.get(k)) for k in CAMPOS_EXPEDIENTE}
    v["grilla_claves"] = _json(info.get("grilla_claves"))
    return v


def alta_marca_cartera(cur, cliente_id: int, acta: str, info: dict, usuario: str, origen_carga: str = "acta"):
    """Inserta una marca en la cartera de un cliente con lo que devolvió
    inpi_lead.consultar_expediente(). Si la consulta no terminó bien (bloqueo,
    error de conexión) la marca se guarda igual, marcada como pendiente de
    consultar (consultado_en NULL): scripts/revisar_cartera.py la completa
    solo. Si el acta ya estaba en la cartera de ESTE cliente, completa los
    datos sin pisar nada con vacíos; si estaba en otro cliente no toca nada
    (el que llama decide si la mueve)."""
    ok = info.get("estado_consulta") == "ok"
    v = _valores_expediente(info) if ok else {k: None for k in CAMPOS_EXPEDIENTE + ("grilla_claves",)}
    ahora = _dt.datetime.now(_dt.timezone.utc) if ok else None
    error = None if ok else (info.get("error") or "todavía sin consultar")
    cols = list(CAMPOS_EXPEDIENTE) + ["grilla_claves"]
    placeholders = ", ".join("%s::jsonb" if c == "grilla_claves" else "%s" for c in cols)
    sets = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, cartera_marcas.{c})" for c in cols)
    cur.execute(
        f"""
        INSERT INTO cartera_marcas (acta, cliente_id, {", ".join(cols)}, origen_carga, consultado_en, error_consulta, alta_por)
        VALUES (%s, %s, {placeholders}, %s, %s, %s, %s)
        ON CONFLICT (acta) DO UPDATE SET {sets},
            consultado_en = COALESCE(EXCLUDED.consultado_en, cartera_marcas.consultado_en),
            error_consulta = EXCLUDED.error_consulta
        WHERE cartera_marcas.cliente_id = EXCLUDED.cliente_id
        """,
        [acta, cliente_id] + [v[c] for c in cols] + [origen_carga, ahora, error, usuario],
    )


def alta_marca_desde_fila(cur, cliente_id: int, fila: dict, usuario: str, origen_carga: str):
    """Suma a la cartera una marca que ya tenemos cargada (tabla `marcas` o
    `solicitudes_escaneadas`), sin consultar INPI. Queda con consultado_en NULL:
    el seguimiento la completa (agente, movimientos, estado) en su próxima
    corrida."""
    cur.execute(
        """
        INSERT INTO cartera_marcas (acta, cliente_id, denominacion, tipo, clase, titular, cuit,
            fecha_presentacion, fecha_publicacion, matricula_agente, caracter, estado_tramite,
            fecha_concesion, numero_disposicion, fecha_vencimiento_marca, tuvo_oposicion,
            detalle_oposicion, origen_carga, alta_por)
        VALUES (%(acta)s, %(cliente_id)s, %(denominacion)s, %(tipo)s, %(clase)s, %(titular)s, %(cuit)s,
            %(fecha_presentacion)s, %(fecha_publicacion)s, %(matricula_agente)s, %(caracter)s, %(estado_tramite)s,
            %(fecha_concesion)s, %(numero_disposicion)s, %(fecha_vencimiento_marca)s, %(tuvo_oposicion)s,
            %(detalle_oposicion)s, %(origen_carga)s, %(alta_por)s)
        ON CONFLICT (acta) DO NOTHING
        """,
        {
            "acta": fila["acta"], "cliente_id": cliente_id,
            "denominacion": _vacio_a_none((fila.get("denominacion_inpi") or fila.get("denominacion") or "").strip()),
            "tipo": _vacio_a_none(fila.get("tipo")), "clase": fila.get("clase"),
            "titular": _vacio_a_none(fila.get("titular")), "cuit": _vacio_a_none(fila.get("cuit")),
            "fecha_presentacion": _a_fecha(fila.get("fecha_presentacion")),
            "fecha_publicacion": _a_fecha(fila.get("fecha_publicacion")),
            "matricula_agente": _vacio_a_none((fila.get("matricula_agente") or "").strip()),
            "caracter": _vacio_a_none(fila.get("caracter")),
            "estado_tramite": fila.get("estado_tramite"), "fecha_concesion": _a_fecha(fila.get("fecha_concesion")),
            "numero_disposicion": fila.get("numero_disposicion"),
            "fecha_vencimiento_marca": _a_fecha(fila.get("fecha_vencimiento_marca")),
            "tuvo_oposicion": fila.get("tuvo_oposicion"), "detalle_oposicion": _vacio_a_none(fila.get("detalle_oposicion")),
            "origen_carga": origen_carga, "alta_por": usuario,
        },
    )
    return cur.rowcount == 1


def _fmt(d):
    d = _a_fecha(d)
    return d.strftime("%d/%m/%Y") if d else "?"


def aplicar_expediente(cur, acta: str, info: dict) -> list:
    """Actualiza una marca de la cartera con una consulta nueva a INPI y deja
    registradas las novedades (cambio de estado, oposición o vista, publicación,
    movimientos nuevos del expediente, cambio de titular). Devuelve la lista de
    (tipo, texto) de las novedades creadas. Nunca pisa un dato con un vacío.

    Si la consulta no salió bien, solo guarda el motivo (la marca se vuelve a
    intentar en la próxima corrida). La primera consulta completa de una marca
    (consultado_en NULL) no genera novedades de movimientos: es la foto
    inicial, no un cambio."""
    cur.execute("SELECT * FROM cartera_marcas WHERE acta = %s FOR UPDATE", (acta,))
    actual = cur.fetchone()
    if actual is None:
        return []
    if not isinstance(actual, dict):
        actual = dict(zip([d[0] for d in cur.description], actual))

    if info.get("estado_consulta") != "ok":
        cur.execute("UPDATE cartera_marcas SET error_consulta = %s WHERE acta = %s",
                    ((info.get("error") or "no se pudo consultar")[:300], acta))
        return []

    v = _valores_expediente(info)
    primera = actual.get("consultado_en") is None
    novedades = []

    est_nuevo, est_viejo = v["estado_tramite"], actual.get("estado_tramite")
    if est_nuevo and est_nuevo != est_viejo:
        texto = f"Estado del trámite: {est_viejo or 'en trámite'} → {est_nuevo}"
        if est_nuevo == "Concedida" and v["fecha_concesion"]:
            texto += f" (concedida el {_fmt(v['fecha_concesion'])}"
            if v["fecha_vencimiento_marca"]:
                texto += f", vence el {_fmt(v['fecha_vencimiento_marca'])}"
            texto += ")"
        if not primera:  # la primera consulta es la foto inicial, no un cambio
            novedades.append(("estado", texto))
    if v["tuvo_oposicion"] and not actual.get("tuvo_oposicion"):
        novedades.append(("oposicion", "Se detectó una oposición o vista en el expediente"
                          + (f": {v['detalle_oposicion']}" if v["detalle_oposicion"] else "")))
    if v["fecha_publicacion"] and not actual.get("fecha_publicacion") and not primera:
        pub = _a_fecha(v["fecha_publicacion"])
        novedades.append(("publicacion", f"Se publicó en el boletín el {_fmt(pub)}. Los terceros pueden oponerse "
                                         f"hasta el {_fmt(pub + _dt.timedelta(days=30))}"))
    if v["titular"] and actual.get("titular") and v["titular"].strip().upper() != actual["titular"].strip().upper():
        novedades.append(("titular", f"Cambió el titular en INPI: «{actual['titular']}» → «{v['titular']}»"))

    nuevos_claves = info.get("grilla_claves")
    if nuevos_claves is not None and not primera and actual.get("grilla_claves") is not None:
        pendientes = list(actual["grilla_claves"])
        nuevos = []
        for c in nuevos_claves:
            if c in pendientes:
                pendientes.remove(c)
            else:
                nuevos.append(c)
        for c in nuevos[:8]:
            fecha, _, resto = c.partition("|")
            indice, _, ref = resto.partition("|")
            novedades.append(("movimiento", f"Nuevo movimiento en el expediente: {(_fmt(fecha) + ' · ') if fecha else ''}"
                                            f"{indice}{(' — ' + ref) if ref else ''}"))

    cols = list(CAMPOS_EXPEDIENTE) + ["grilla_claves"]
    sets = ", ".join(f"{c} = COALESCE(%s{'::jsonb' if c == 'grilla_claves' else ''}, {c})" for c in cols)
    cur.execute(
        f"UPDATE cartera_marcas SET {sets}, consultado_en = now(), error_consulta = NULL WHERE acta = %s",
        [v[c] for c in cols] + [acta],
    )
    for tipo, texto in novedades:
        cur.execute(
            "INSERT INTO cartera_novedades (acta, cliente_id, tipo, texto) VALUES (%s, %s, %s, %s)",
            (acta, actual["cliente_id"], tipo, texto),
        )
    return novedades
