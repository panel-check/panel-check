"""
Seguimientos (/seguimientos): cada trabajo puntual que contrató un cliente.

Un trabajo es cliente + marca + servicio (registro, oposición, vista...). No necesita
número de acta: una marca que todavía no se presentó (por ejemplo TIMBEROS o MAPIAPPS de
Leonardo, cliente 32) ya es un trabajo en etapa «Seña cobrada». Cuando se presenta se le
carga el acta y el expediente se arma solo con lo que el panel ya lee del INPI para la
cartera (cartera_marcas + cartera_novedades). Si no tiene acta pero el cliente tiene en
la cartera una marca con el mismo nombre, se toma esa (acta «vinculada sola»).

Plazos: se carga solo la fecha de NOTIFICACIÓN y el vencimiento se calcula:
  - Oposición + negociación: 3 meses corridos.
  - Contestación de vista: 30 días corridos.
  - Registro, oposición administrativa y vigilancia: sin vencimiento.

Sin FastAPI ni base: lo usan trabajos_api.py y scripts/test_trabajos.py.
"""

import calendar
import datetime as _dt
import re
import unicodedata

# Procesos y plantillas de mails (Notion, página «Smarties + KOM»).
NOTION_PROCESOS = "https://www.notion.so/f6a567ff9f074f22b8dccde2283af8c5"

# Orden de las columnas del tablero: primero las que tienen vencimiento (vista, oposición).
SERVICIOS = [
    {"id": "vista", "nombre": "Contestación de vista", "corto": "Vista", "color": "#7c3aed",
     "plazo": ("dias", 30),
     "etapas": ["Seña cobrada", "Análisis", "Escrito presentado", "Resuelta"]},
    {"id": "oposicion", "nombre": "Oposición + negociación", "corto": "Oposición", "color": "#ea580c",
     "plazo": ("meses", 3),
     "etapas": ["Seña cobrada", "Análisis", "Contacto con el oponente", "Negociando", "Acuerdo o retiro"]},
    {"id": "oposicion_adm", "nombre": "Oposición administrativa", "corto": "Opo. administrativa", "color": "#dc2626",
     "plazo": None,
     "etapas": ["Seña cobrada", "Escrito presentado", "Esperando resolución", "Resuelta"]},
    {"id": "registro", "nombre": "Registro de marca", "corto": "Registro", "color": "#2563eb",
     "plazo": None,
     "etapas": ["Seña cobrada", "Formulario y poder", "Presentada", "Publicada", "Concedida"]},
    {"id": "vigilancia", "nombre": "Vigilancia marcaria", "corto": "Vigilancia", "color": "#16a34a",
     "plazo": None,
     "etapas": ["Contratada", "Activa"]},
]
POR_ID = {s["id"]: s for s in SERVICIOS}
ESTADOS = ("activo", "cerrado")


def servicio(sid):
    return POR_ID.get(sid)


def servicios_publicos():
    """Lo que necesita la pantalla (sin tuplas)."""
    return [{"id": s["id"], "nombre": s["nombre"], "corto": s["corto"], "color": s["color"],
             "etapas": s["etapas"], "con_vencimiento": s["plazo"] is not None,
             "plazo_texto": _plazo_texto(s["plazo"])} for s in SERVICIOS]


def _plazo_texto(p):
    if not p:
        return None
    n = p[1]
    return f"{n} meses corridos" if p[0] == "meses" else f"{n} días corridos"


def hoy_ar():
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-3))).date()


def a_fecha(v):
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def sumar_meses(d, meses):
    """Mismo día del mes; si no existe (31/11), el último día del mes."""
    m = d.month - 1 + meses
    anio, mes = d.year + m // 12, m % 12 + 1
    return d.replace(year=anio, month=mes, day=min(d.day, calendar.monthrange(anio, mes)[1]))


def vencimiento(sid, notificacion):
    """Fecha en que vence el plazo del trabajo, o None si el servicio no tiene o falta la notificación."""
    s, n = servicio(sid), a_fecha(notificacion)
    if not s or not s["plazo"] or not n:
        return None
    unidad, cant = s["plazo"]
    return sumar_meses(n, cant) if unidad == "meses" else n + _dt.timedelta(days=cant)


def alerta(sid, notificacion, estado="activo", hoy=None):
    """{nivel, texto, dias}. Niveles: vencido, rojo (≤3 días), amarillo (≤7), verde,
    falta (servicio con plazo sin notificación cargada), ninguno (sin vencimiento), cerrado."""
    hoy = hoy or hoy_ar()
    s = servicio(sid)
    if estado == "cerrado":
        return {"nivel": "cerrado", "texto": "Cerrado", "dias": None}
    if not s or not s["plazo"]:
        return {"nivel": "ninguno", "texto": "Sin vencimiento", "dias": None}
    v = vencimiento(sid, notificacion)
    if not v:
        return {"nivel": "falta", "texto": "Cargar notificación", "dias": None}
    d = (v - hoy).days
    if d < 0:
        return {"nivel": "vencido", "texto": f"Venció hace {-d} día{'s' if d != -1 else ''}", "dias": d}
    texto = "Vence hoy" if d == 0 else ("Vence mañana" if d == 1 else f"Vence en {d} días")
    nivel = "rojo" if d <= 3 else ("amarillo" if d <= 7 else "verde")
    return {"nivel": nivel, "texto": texto, "dias": d}


def clave_orden(t):
    """Orden dentro de cada columna: 1) los que tienen vencimiento, del más cercano al más
    lejano (los vencidos quedan arriba de todo); 2) los de servicios con plazo a los que les
    falta cargar la notificación; 3) los que no tienen vencimiento, lo contratado más nuevo
    arriba. Los cerrados siempre al final."""
    v = a_fecha(t.get("vencimiento"))
    c = a_fecha(t.get("contratacion")) or _dt.date(1900, 1, 1)
    cerrado = t.get("estado") == "cerrado"
    if v:
        return (1 if cerrado else 0, 0, v.toordinal(), 0)
    nivel = (t.get("alerta") or {}).get("nivel")
    return (1 if cerrado else 0, 1 if nivel == "falta" else 2, 0, -c.toordinal())


def ordenar(trabajos):
    return sorted(trabajos, key=clave_orden)


def normalizar(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().upper()


def marca_limpia(marca):
    """«TIMBEROS (BBATACAZO)» → «TIMBEROS» (lo de los paréntesis en la planilla es el titular)."""
    return re.sub(r"\s*\(.*?\)\s*", " ", marca or "").strip().upper()


def completar(t, hoy=None):
    """Agrega a una fila de trabajos los campos calculados que muestra la pantalla."""
    s = servicio(t.get("servicio")) or {"nombre": t.get("servicio"), "etapas": [], "plazo": None}
    etapas = s["etapas"]
    etapa = max(0, min(int(t.get("etapa") or 0), len(etapas)))
    t["servicio_nombre"] = s["nombre"]
    t["etapas_total"] = len(etapas)
    t["etapa"] = etapa
    t["etapa_nombre"] = etapas[etapa - 1] if etapa else "Esperando seña"
    t["proxima_etapa"] = etapas[etapa] if etapa < len(etapas) else None
    t["vencimiento"] = vencimiento(t.get("servicio"), t.get("notificacion"))
    t["alerta"] = alerta(t.get("servicio"), t.get("notificacion"), t.get("estado") or "activo", hoy)
    return t


# ── Tablas ────────────────────────────────────────────────────────────────

TABLAS = [
    """CREATE TABLE IF NOT EXISTS trabajos (
        id             SERIAL PRIMARY KEY,
        cliente_id     INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
        marca          TEXT NOT NULL,
        servicio       TEXT NOT NULL,
        acta           TEXT,
        etapa          INTEGER NOT NULL DEFAULT 0,
        estado         TEXT NOT NULL DEFAULT 'activo',
        contratacion   DATE,
        notificacion   DATE,
        carpeta_drive  TEXT,
        comprobante    TEXT,
        notas          TEXT,
        responsable    TEXT,
        clave_carga    TEXT UNIQUE,
        alta_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
        alta_por       TEXT,
        modificado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
        modificado_por TEXT,
        cerrado_en     TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trabajos_cliente ON trabajos(cliente_id)",
    "CREATE INDEX IF NOT EXISTS idx_trabajos_estado ON trabajos(estado)",
    """CREATE TABLE IF NOT EXISTS trabajos_bitacora (
        id          BIGSERIAL PRIMARY KEY,
        trabajo_id  INTEGER NOT NULL REFERENCES trabajos(id) ON DELETE CASCADE,
        fecha       DATE NOT NULL,
        texto       TEXT NOT NULL,
        usuario     TEXT,
        creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trabajos_bitacora ON trabajos_bitacora(trabajo_id, fecha DESC)",
]

# Carga inicial (18/11/2026): los trabajos que estaban en la hoja CLIENTES de la «Planilla
# Pagos». El cliente sale del link «Ficha de cliente» de cada fila (panel/clientes?cliente=N).
# Se inserta una sola vez (clave_carga) y solo si el cliente existe; después se edita en el panel.
CARGA_INICIAL = [
    # clave, cliente_id, marca, servicio, etapa, contratación, notificación, acta, drive, comprobante, notas
    ("planilla:BARRIOKEY", 31, "BARRIOKEY", "oposicion", 4, "2026-10-16", "2026-11-04", None,
     "https://docs.google.com/document/d/1R-lTO0TKtyMR6wYeknUizLytm11fatrq_dkymo1YYKA/edit?usp=drive_link",
     "https://mail.google.com/mail/u/2?ui=2&ik=568bf27d9b&attid=0.1&permmsgid=msg-f:1879219394474072262&th=1a1454152ea6d8c6&view=fimg&fur=ip&sz=s0-l75-ft&disp=emb&realattid=ii_1a14540c8f1dfc58af31",
     None),
    ("planilla:TIMBEROS", 32, "TIMBEROS", "registro", 1, "2026-10-16", None, None,
     "https://drive.google.com/drive/folders/1vU79aAWe23KJYF_JLWx3ycjolD6kB-dC?usp=drive_link",
     "https://mail.google.com/mail/u/2/popout?ver=1rxqa29am4kag", "Titular: BBATACAZO. Saldo cobrado el 10/11."),
    ("planilla:CRITERIO LABORAL", 34, "CRITERIO LABORAL", "registro", 1, "2026-10-21", None, None,
     "https://drive.google.com/drive/folders/1-6kQRGkdJoWHbemEK8gB6Jb6Vi-w5MIW?usp=drive_link", None,
     "El poder no se firmó todavía: seguramente el titular sea una SRL."),
    ("planilla:MATEACIDO", 37, "MATEACIDO", "vista", 1, "2026-11-01", "2026-10-29", None,
     "https://drive.google.com/drive/u/0/folders/1hT2xl_q_JGFo6plXvvRm7LR7mY8E2-BE",
     "https://mail.google.com/mail/u/2/popout?ver=1rxqa29am4kag", None),
    ("planilla:CANVA PADEL", 38, "CANVA PADEL", "oposicion", 4, "2026-11-01", "2026-10-14", "4775277",
     "https://drive.google.com/drive/u/0/folders/1X0S_CJ8FuXg8YeDlSqx5ezbwPpK5Ialp",
     "https://web.whatsapp.com/02d525ec-1894-42b8-aaec-2431d42f1115",
     "Titular: ANTHAL SPORT SRL. Propuesta: coexistencia usando siempre CANVA PADEL completo y logo propio."),
    ("planilla:CINE CATASTROFE", 41, "CINE CATASTROFE", "vista", 1, "2026-11-06", "2026-11-05", None,
     "https://drive.google.com/drive/folders/1oGowdPUWjDPKBEkDJmCf6U46zgqoKjkq?usp=drive_link",
     "https://web.whatsapp.com/ef11cf27-1897-4849-aace-a004ed463586", None),
    ("planilla:MAPIAPPS", 32, "MAPIAPPS", "registro", 1, "2026-11-10", None, None,
     "https://drive.google.com/drive/u/0/folders/1Blv-J3dgnKJL5J_uqoJbrelFljdbS4cn",
     "https://mail.google.com/mail/u/2/popout?ver=fqtyz4bow8hr", None),
    ("planilla:MAS PROPIEDADES", 42, "MAS PROPIEDADES", "oposicion", 0, "2026-11-17", "2026-11-04", None,
     "https://drive.google.com/drive/folders/1pIkqHj12K7YAjFs0RzG3tfGLCgfVAdm1?usp=drive_link", None,
     "Pendiente de que pague la seña."),
    ("planilla:AKRO LAB", 39, "AKRO LAB", "registro", 1, "2026-11-17", None, None,
     "https://drive.google.com/drive/folders/1galeT4iiWhfSGNPNB6LMSmlc60UDRXUx?usp=drive_link", None,
     "Pendiente de que nos mande el formulario."),
    ("planilla:VISION SOBERANA", 43, "VISION SOBERANA", "vista", 1, "2026-11-18", "2026-11-04", None,
     "https://drive.google.com/drive/folders/1vn7H_9rssnswAZiXXfkmvGa_Fe2nyg8K?usp=drive_link", None, None),
]


def crear_tablas(cur):
    for sql in TABLAS:
        cur.execute(sql)
    for (clave, cid, marca, sid, etapa, contr, notif, acta, drive, comp, notas) in CARGA_INICIAL:
        cur.execute(
            """INSERT INTO trabajos (clave_carga, cliente_id, marca, servicio, etapa, contratacion, notificacion,
                                     acta, carpeta_drive, comprobante, notas, alta_por, modificado_por)
               SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'planilla', 'planilla'
               WHERE EXISTS (SELECT 1 FROM clientes WHERE id = %s)
               ON CONFLICT (clave_carga) DO NOTHING""",
            (clave, cid, marca, sid, etapa, contr, notif, acta, drive, comp, notas, cid),
        )


# ── Detección automática del acta ─────────────────────────────────────────
# Una marca que todavía no tiene acta (registro recién presentado) se busca:
#   1. en la base, cada vez que se abre el tablero (como mucho cada 10 minutos): el escaneo diario de
#      actas nuevas (18 hs) guarda TODAS las solicitudes en `solicitudes_escaneadas`, también las que
#      presentamos nosotros con agente (que no son leads y por eso no van a `marcas`), más `marcas`
#      (boletines) y la cartera del cliente;
#   2. en vivo en el INPI, con el botón «Buscar en el INPI» de la ficha.
# Se asigna sola solo si es segura: mismo nombre (sin tildes ni signos), presentada desde 15 días
# antes de la contratación, y presentada con una matrícula del estudio (Clientes → Ajustes →
# matrículas) o a nombre del cliente (mismo CUIT o su nombre dentro del titular). Si hay dudas, la
# ficha muestra las candidatas para elegir con un clic.

DIAS_ANTES_CONTRATACION = 15


def comparable(texto) -> str:
    """Mayúsculas, sin tildes y solo letras, números y espacios («Cine  Catástrofe!» → «CINE CATASTROFE»)."""
    sin = "".join(c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", sin.upper()).split())


def patron_ilike(marca) -> str:
    """Patrón para prefiltrar en SQL sin depender de tildes: cada vocal pasa a «_» (una letra cualquiera)."""
    return "%" + re.sub(r"[AEIOU]", "_", comparable(marca)).replace(" ", "%") + "%"


def _nombre_en_titular(nombre_cliente, titular) -> bool:
    """El nombre del cliente aparece en el titular: todas sus palabras de 3 letras o más, y al menos 2."""
    palabras = [p for p in comparable(nombre_cliente).split() if len(p) >= 3]
    tit = f" {comparable(titular)} "
    return len(palabras) >= 2 and all(f" {p} " in tit for p in palabras)


def evaluar_candidata(trabajo, cand, cliente, matriculas) -> dict | None:
    """Devuelve la candidata con `segura` y `motivos`, o None si no es la misma marca o es muy vieja.
    cand: acta, denominacion, titular, cuit, fecha (iso o date), matricula, fuente."""
    if not cand.get("acta") or comparable(cand.get("denominacion")) != comparable(trabajo.get("marca")):
        return None
    contr, fecha = a_fecha(trabajo.get("contratacion")), a_fecha(cand.get("fecha"))
    if contr and fecha and fecha < contr - _dt.timedelta(days=DIAS_ANTES_CONTRATACION):
        return None
    motivos = []
    mat = re.sub(r"\D", "", str(cand.get("matricula") or ""))
    if mat and mat in {re.sub(r"\D", "", str(m)) for m in (matriculas or [])}:
        motivos.append(f"presentada con nuestra matrícula ({mat})")
    ccuit = re.sub(r"\D", "", str((cliente or {}).get("cuit") or ""))
    if ccuit and ccuit == re.sub(r"\D", "", str(cand.get("cuit") or "")):
        motivos.append("mismo CUIT que el cliente")
    elif _nombre_en_titular((cliente or {}).get("nombre"), cand.get("titular")):
        motivos.append("el titular es el cliente")
    if cand.get("fuente") == "cartera":
        motivos.append("está en la cartera del cliente")
    return {**cand, "fecha": fecha, "segura": bool(motivos) and (fecha is not None or cand.get("fuente") == "cartera"),
            "motivos": motivos}


def elegir_acta(trabajo, candidatas, cliente, matriculas):
    """(elegida o None, todas las evaluadas). Si hay varias seguras (la misma marca en varias clases)
    se elige la de número más bajo y las demás se nombran en la bitácora."""
    vistas, evaluadas = set(), []
    for c in candidatas:
        e = evaluar_candidata(trabajo, c, cliente, matriculas)
        if e and e["acta"] not in vistas:
            vistas.add(e["acta"]); evaluadas.append(e)
    evaluadas.sort(key=lambda e: (not e["segura"], int(e["acta"]) if str(e["acta"]).isdigit() else 0))
    seguras = [e for e in evaluadas if e["segura"]]
    return (seguras[0] if seguras else None), evaluadas


def texto_deteccion(elegida, evaluadas, origen) -> str:
    partes = [f"Acta {elegida['acta']} detectada sola ({origen})"]
    det = []
    if elegida.get("fecha"):
        det.append(f"presentada el {elegida['fecha'].strftime('%d/%m/%Y')}")
    if elegida.get("clase"):
        det.append(f"clase {elegida['clase']}")
    if elegida.get("titular"):
        det.append(f"titular {elegida['titular']}")
    if elegida.get("motivos"):
        det.append("; ".join(elegida["motivos"]))
    texto = partes[0] + (": " + ", ".join(det) if det else "") + "."
    otras = [e["acta"] for e in evaluadas if e["segura"] and e["acta"] != elegida["acta"]]
    if otras:
        texto += f" También hay con el mismo nombre: actas {', '.join(otras)} (otra clase o tipo)."
    return texto
