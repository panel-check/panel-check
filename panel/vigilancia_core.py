"""
Vigilancia marcaria: compara las solicitudes de marca que van apareciendo
(boletines + escaneo directo de actas) contra las marcas de la cartera de
clientes, y deja alertas para revisar.

Lo usan scripts/vigilancia.py (todos los días, en GitHub Actions) y el panel
(probar un nombre, marcas parecidas). Solo habla con la base: no consulta
INPI.

Qué genera:
  * alerta "similitud": una solicitud nueva se parece a una marca de un
    cliente (ver similitud.py para el puntaje).
  * alerta "otro_agente": un cliente (por CUIT) presentó una marca nueva que
    NO está a cargo del estudio -- con otro agente o sin agente. Señal de
    retención / oportunidad.
  * suma automática por matrícula: lo que se presenta con una matrícula del
    estudio y el CUIT ya es de un cliente, entra solo a su cartera.

Reglas para no llenar el panel de ruido:
  * Solo se compara contra solicitudes cuyo plazo de oposición sigue abierto
    (todavía sin publicar, o publicadas hace menos de ~33 días). Una marca
    publicada hace un año ya no se puede oponer.
  * Se ignoran las solicitudes del propio cliente (mismo CUIT), las del
    estudio (matrícula propia) y las denegadas.
  * Cada par (marca del cliente, solicitud nueva) genera UNA alerta; lo que
    alguien ya decidió (monitorear, oponer, descartar) no se pisa.
"""

import datetime as _dt
import re

import similitud as sim
import cartera

VENTANA_OPOSICION_DIAS = 30
GRACIA_DIAS = 3


def ventana_abierta(publicacion, hoy) -> bool:
    pub = cartera._a_fecha(publicacion)
    return pub is None or hoy <= pub + _dt.timedelta(days=VENTANA_OPOSICION_DIAS + GRACIA_DIAS)


def _dict_rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else r for r in cur.fetchall()]


def cargar_ajustes(cur) -> dict:
    cur.execute("SELECT palabra FROM vigilancia_palabras_genericas")
    genericas = {(r["palabra"] if isinstance(r, dict) else r[0]).upper() for r in cur.fetchall()}
    cur.execute("SELECT clase_a, clase_b FROM vigilancia_clases_relacionadas")
    pares = {(r["clase_a"], r["clase_b"]) if isinstance(r, dict) else (r[0], r[1]) for r in cur.fetchall()}
    cur.execute("SELECT matricula FROM cartera_matriculas")
    matriculas = {(r["matricula"] if isinstance(r, dict) else r[0]).strip() for r in cur.fetchall()}
    return {"genericas": frozenset(genericas), "pares": pares, "matriculas": matriculas,
            "config": cartera.leer_config(cur)}


# Todas las solicitudes que conocemos (boletines + escaneo directo). Una misma
# acta puede estar en las dos tablas: se queda la de `marcas`.
SQL_UNIVERSO = """
    SELECT m.acta,
           COALESCE(NULLIF(trim(m.denominacion_inpi), ''), NULLIF(trim(m.denominacion), '')) AS nombre,
           m.clase, m.tipo, m.titular, m.cuit,
           NULLIF(trim(COALESCE(m.matricula_agente, '')), '') AS matricula_agente, m.caracter,
           m.fecha_presentacion::date AS fecha_presentacion,
           CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END AS fecha_publicacion,
           m.boletin, m.estado_tramite, COALESCE(m.fuente, 'boletin') AS fuente
    FROM marcas m
    LEFT JOIN boletines b ON b.numero = m.boletin
    {donde_marcas}
    UNION ALL
    SELECT s.acta, NULLIF(trim(s.denominacion), ''), s.clase, s.tipo, s.titular, s.cuit,
           NULLIF(trim(COALESCE(s.matricula_agente, '')), ''), s.caracter,
           s.fecha_presentacion, NULL::date, NULL, NULL, 'escaneo_directo'
    FROM solicitudes_escaneadas s
    WHERE NOT EXISTS (SELECT 1 FROM marcas m WHERE m.acta = s.acta)
    {y_escaneadas}
"""


def _sql_universo(donde_marcas="", y_escaneadas=""):
    return SQL_UNIVERSO.format(donde_marcas=donde_marcas, y_escaneadas=y_escaneadas)


def _cartera_en_memoria(cur, ajustes):
    """Marcas de la cartera a vigilar (de clientes activos) con sus nombres
    a comparar (denominación + términos extra) y un índice de claves."""
    cur.execute(
        """
        SELECT cm.acta, cm.cliente_id, cm.denominacion, cm.terminos_vigilancia, cm.clase, cm.tipo,
               cm.vigilar_todas_clases, cm.vigilancia_firma, cm.cuit, c.nombre AS cliente_nombre, c.cuit AS cliente_cuit
        FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
        WHERE cm.vigilar AND c.activo
        """
    )
    marcas = _dict_rows(cur)
    gen = ajustes["genericas"]
    indice = {}
    for m in marcas:
        nombres = [n.strip() for n in [m["denominacion"] or ""] + re.split(r"[,;\n]", m["terminos_vigilancia"] or "") if n.strip()]
        m["nombres"] = nombres
        m["firma"] = firma_cartera(m)
        claves = set()
        for n in nombres:
            claves |= sim.claves_indice(n, gen)
        for k in claves:
            indice.setdefault(k, []).append(m)
    return marcas, indice


def firma_cartera(m) -> str:
    return "|".join([sim.normalizar(m.get("denominacion") or ""), (m.get("terminos_vigilancia") or "").strip().upper(),
                     str(m.get("clase") or ""), "T" if m.get("vigilar_todas_clases") else "C"])


def _mejor_contra(nombre_nuevo, clase_nueva, m, ajustes):
    mejor = None
    for n in m["nombres"]:
        r = sim.puntaje_marcas(n, m.get("clase"), nombre_nuevo, clase_nueva, ajustes["genericas"],
                               ajustes["pares"], m.get("vigilar_todas_clases"))
        if r and (mejor is None or r["puntaje"] > mejor["puntaje"]):
            mejor = r
    return mejor


def _candidatas(indice, nombre, gen):
    vistos, out = set(), []
    for k in sim.claves_indice(nombre, gen):
        for m in indice.get(k, ()):
            if m["acta"] not in vistos:
                vistos.add(m["acta"])
                out.append(m)
    return out


def _cuits_clientes(cur):
    cur.execute(
        """
        SELECT cuit, cliente_id FROM (
            SELECT regexp_replace(cuit, '\\D', '', 'g') AS cuit, id AS cliente_id FROM clientes WHERE cuit IS NOT NULL AND activo
            UNION
            SELECT regexp_replace(cm.cuit, '\\D', '', 'g'), cm.cliente_id FROM cartera_marcas cm
            JOIN clientes c ON c.id = cm.cliente_id WHERE cm.cuit IS NOT NULL AND c.activo
        ) x WHERE cuit <> ''
        """
    )
    mapa = {}
    for r in cur.fetchall():
        c, cid = (r["cuit"], r["cliente_id"]) if isinstance(r, dict) else r
        mapa.setdefault(c, set()).add(cid)
    return mapa


def _solo_digitos(v):
    return re.sub(r"\D", "", v or "")


def _insertar_alerta(cur, a: dict) -> bool:
    cur.execute(
        """
        INSERT INTO vigilancia_alertas (tipo, acta_cliente, cliente_id, acta_nueva, denominacion_nueva, clase_nueva,
            tipo_nuevo, titular_nuevo, cuit_nuevo, agente_nuevo, fecha_presentacion_nueva, fuente_nueva,
            puntaje, nivel, motivos)
        VALUES (%(tipo)s, %(acta_cliente)s, %(cliente_id)s, %(acta_nueva)s, %(denominacion_nueva)s, %(clase_nueva)s,
            %(tipo_nuevo)s, %(titular_nuevo)s, %(cuit_nuevo)s, %(agente_nuevo)s, %(fecha_presentacion_nueva)s,
            %(fuente_nueva)s, %(puntaje)s, %(nivel)s, %(motivos)s)
        ON CONFLICT (tipo, COALESCE(acta_cliente, ''), acta_nueva) DO NOTHING
        """,
        a,
    )
    return cur.rowcount == 1


def _agente_legible(fila) -> str:
    mat = (fila.get("matricula_agente") or "").strip()
    car = (fila.get("caracter") or "").strip()
    if mat and mat != "Part.":
        return f"matrícula {mat}"
    if car:
        return car
    return "sin agente"


def _alerta_base(fila, tipo, cliente_id, acta_cliente, puntaje, nivel, motivos):
    return {
        "tipo": tipo, "acta_cliente": acta_cliente, "cliente_id": cliente_id, "acta_nueva": fila["acta"],
        "denominacion_nueva": fila.get("nombre"), "clase_nueva": fila.get("clase"), "tipo_nuevo": fila.get("tipo"),
        "titular_nuevo": fila.get("titular"), "cuit_nuevo": _solo_digitos(fila.get("cuit")) or None,
        "agente_nuevo": _agente_legible(fila), "fecha_presentacion_nueva": fila.get("fecha_presentacion"),
        "fuente_nueva": "pre-boletín" if fila.get("fuente") == "escaneo_directo" and not fila.get("boletin") else
                        (f"boletín {fila['boletin']}" if fila.get("boletin") else fila.get("fuente")),
        "puntaje": puntaje, "nivel": nivel, "motivos": " · ".join(motivos),
    }


def _evaluar_fila(fila, candidatas, ajustes, mis_cuits, acta_en_cartera):
    """Alertas de similitud de UNA solicitud contra sus candidatas de la
    cartera. Devuelve lista de dicts listos para insertar."""
    umbral = ajustes["config"]["umbral"]
    out = []
    cuit_fila = _solo_digitos(fila.get("cuit"))
    for m in candidatas:
        if m["acta"] == fila["acta"]:
            continue
        # La misma persona/empresa: no es un conflicto, es su propia marca.
        if cuit_fila and (_solo_digitos(m.get("cuit")) == cuit_fila or _solo_digitos(m.get("cliente_cuit")) == cuit_fila):
            continue
        r = _mejor_contra(fila["nombre"], fila.get("clase"), m, ajustes)
        if not r or r["puntaje"] < umbral:
            continue
        niv = sim.nivel(r["puntaje"], ajustes["config"]["nivel_alta"], ajustes["config"]["nivel_media"])
        out.append(_alerta_base(fila, "similitud", m["cliente_id"], m["acta"], r["puntaje"], niv, r["motivos"]))
    return out


def comparar(conn, hoy=None, log=print, limite_filas=None) -> dict:
    """Corrida completa de vigilancia. Devuelve contadores."""
    hoy = hoy or cartera.hoy_ar()
    stats = {"comparadas": 0, "alertas_similitud": 0, "alertas_otro_agente": 0, "marcas_cartera": 0,
             "recomparadas_cartera": 0, "saltadas_ventana": 0}
    with conn.cursor() as cur:
        ajustes = cargar_ajustes(cur)
        marcas, indice = _cartera_en_memoria(cur, ajustes)
        stats["marcas_cartera"] = len(marcas)
        mis_cuits = _cuits_clientes(cur)
        cur.execute("SELECT acta FROM cartera_marcas")
        en_cartera = {(r["acta"] if isinstance(r, dict) else r[0]) for r in cur.fetchall()}
        meses = ajustes["config"]["meses_universo"]
        desde = hoy - _dt.timedelta(days=int(meses * 30.4))
        gen = ajustes["genericas"]

        # ── Pasada A: solicitudes que todavía no se compararon ──
        cur.execute("SELECT acta, firma FROM vigilancia_comparadas")
        comparadas = {(r["acta"], r["firma"]) if isinstance(r, dict) else (r[0], r[1]) for r in cur.fetchall()}
        comparadas_por_acta = {a: f for a, f in comparadas}

        cur.execute(_sql_universo(
            donde_marcas="WHERE COALESCE(m.fecha_presentacion::date, m.creado_en::date) >= %(desde)s",
            y_escaneadas="AND COALESCE(s.fecha_presentacion, s.creado_en::date) >= %(desde)s",
        ), {"desde": desde})
        universo = _dict_rows(cur)
        log(f"Solicitudes en el universo de comparación: {len(universo)}; marcas de la cartera a vigilar: {len(marcas)}")

        nuevas_comparadas = []
        n = 0
        for fila in universo:
            if limite_filas and n >= limite_filas:
                break
            if fila["acta"] in en_cartera or fila.get("estado_tramite") == "Denegada":
                continue
            nombre = fila.get("nombre")
            firma = f"{sim.normalizar(nombre or '', gen)}|{fila.get('clase') or ''}"

            # Otro agente: el cliente presentó algo que no maneja el estudio.
            cuit_fila = _solo_digitos(fila.get("cuit"))
            if cuit_fila in mis_cuits and (hoy - (fila.get("fecha_presentacion") or hoy)).days <= 180 \
                    and fila.get("matricula_agente") not in ajustes["matriculas"] \
                    and comparadas_por_acta.get(fila["acta"] + "#agente") is None:
                for cid in mis_cuits[cuit_fila]:
                    base = _alerta_base(fila, "otro_agente", cid, None, 100, "media",
                                        [f"el cliente presentó una marca nueva con {_agente_legible(fila)}"])
                    if _insertar_alerta(cur, base):
                        stats["alertas_otro_agente"] += 1
                nuevas_comparadas.append((fila["acta"] + "#agente", "1"))

            if not nombre:
                continue  # sin nombre todavía: se compara cuando se recupere
            if comparadas_por_acta.get(fila["acta"]) == firma:
                continue
            n += 1
            stats["comparadas"] += 1
            nuevas_comparadas.append((fila["acta"], firma))
            if not ventana_abierta(fila.get("fecha_publicacion"), hoy):
                stats["saltadas_ventana"] += 1
                continue
            for alerta in _evaluar_fila(fila, _candidatas(indice, nombre, gen), ajustes, mis_cuits, en_cartera):
                if _insertar_alerta(cur, alerta):
                    stats["alertas_similitud"] += 1

        _guardar_comparadas(cur, nuevas_comparadas)

        # ── Pasada B: marcas de la cartera nuevas o con el nombre cambiado ──
        # (contra las solicitudes que siguen abiertas, aunque ya estuvieran
        # comparadas contra el resto de la cartera)
        obsoletas = [m for m in marcas if m.get("vigilancia_firma") != m["firma"] and m["nombres"]]
        if obsoletas:
            indice_b = {}
            for m in obsoletas:
                ks = set()
                for nom in m["nombres"]:
                    ks |= sim.claves_indice(nom, gen)
                for k in ks:
                    indice_b.setdefault(k, []).append(m)
            for fila in universo:
                if fila["acta"] in en_cartera or fila.get("estado_tramite") == "Denegada" or not fila.get("nombre"):
                    continue
                if not ventana_abierta(fila.get("fecha_publicacion"), hoy):
                    continue
                for alerta in _evaluar_fila(fila, _candidatas(indice_b, fila["nombre"], gen), ajustes, mis_cuits, en_cartera):
                    if _insertar_alerta(cur, alerta):
                        stats["alertas_similitud"] += 1
            for m in obsoletas:
                cur.execute("UPDATE cartera_marcas SET vigilancia_firma = %s WHERE acta = %s", (m["firma"], m["acta"]))
            stats["recomparadas_cartera"] = len(obsoletas)
        # Marcas sin nombre (todavía): quedan con firma vacía y se miran cuando lo tengan.
    conn.commit()
    return stats


def _guardar_comparadas(cur, pares):
    for i in range(0, len(pares), 1000):
        lote = pares[i:i + 1000]
        cur.executemany(
            "INSERT INTO vigilancia_comparadas (acta, firma) VALUES (%s, %s) "
            "ON CONFLICT (acta) DO UPDATE SET firma = EXCLUDED.firma, comparada_en = now()",
            lote,
        )


# ── Suma automática por matrícula ────────────────────────────────────────

SQL_CANDIDATAS_MATRICULA = """
    SELECT acta, nombre AS denominacion_inpi, clase, tipo, titular, cuit, matricula_agente, caracter,
           fecha_presentacion, fecha_publicacion, estado_tramite, fecha_concesion, numero_disposicion,
           fecha_vencimiento_marca, tuvo_oposicion, detalle_oposicion, boletin, fuente
    FROM ({universo}) u
    WHERE matricula_agente = ANY(%(matriculas)s)
      AND NOT EXISTS (SELECT 1 FROM cartera_marcas cm WHERE cm.acta = u.acta)
      AND NOT EXISTS (SELECT 1 FROM cartera_descartes d WHERE d.acta = u.acta)
"""


def candidatas_por_matricula(cur, matriculas, limite=2000):
    """Solicitudes presentadas con alguna de las matrículas del estudio que
    todavía no están en la cartera ni se descartaron. Trae los campos extra
    que sirve tener (estado, concesión) de la tabla `marcas` cuando existe."""
    if not matriculas:
        return []
    universo = _sql_universo().replace(
        "m.boletin, m.estado_tramite, COALESCE(m.fuente, 'boletin') AS fuente",
        "m.boletin, m.estado_tramite, COALESCE(m.fuente, 'boletin') AS fuente, "
        "m.fecha_concesion, m.numero_disposicion, m.fecha_vencimiento_marca, m.tuvo_oposicion, m.detalle_oposicion",
    ).replace(
        "s.fecha_presentacion, NULL::date, NULL, NULL, 'escaneo_directo'",
        "s.fecha_presentacion, NULL::date, NULL, NULL, 'escaneo_directo', "
        "NULL::date, NULL::text, NULL::date, NULL::boolean, NULL::text",
    )
    cur.execute(SQL_CANDIDATAS_MATRICULA.format(universo=universo) + " ORDER BY fecha_presentacion DESC NULLS LAST LIMIT %(limite)s",
                {"matriculas": sorted(matriculas), "limite": limite})
    return _dict_rows(cur)


def sumar_por_matricula(conn, log=print) -> dict:
    """Las candidatas cuyo CUIT ya es de UN solo cliente entran solas a su
    cartera. El resto queda como propuesta en Clientes → Por matrícula."""
    stats = {"sumadas": 0, "propuestas": 0}
    with conn.cursor() as cur:
        ajustes = cargar_ajustes(cur)
        candidatas = candidatas_por_matricula(cur, ajustes["matriculas"])
        mapa = _cuits_clientes(cur)
        for fila in candidatas:
            cuit = _solo_digitos(fila.get("cuit"))
            dueños = mapa.get(cuit) if cuit else None
            if dueños and len(dueños) == 1:
                cid = next(iter(dueños))
                if cartera.alta_marca_desde_fila(cur, cid, fila, "sistema", "matricula-auto"):
                    cur.execute(
                        "INSERT INTO cartera_novedades (acta, cliente_id, tipo, texto) VALUES (%s, %s, 'alta_auto', %s)",
                        (fila["acta"], cid, "Se sumó sola a la cartera: presentada con una matrícula del estudio y el CUIT de este cliente"),
                    )
                    stats["sumadas"] += 1
            else:
                stats["propuestas"] += 1
    conn.commit()
    return stats


# ── Buscar parecidas (para probar un nombre desde el panel) ──────────────

def buscar_parecidas(cur, nombre: str, clase=None, limite=60, minimo=None) -> list:
    """Solicitudes que conocemos (todas, sin límite de fecha) parecidas a un
    nombre, ordenadas por puntaje. Es una ayuda orientativa: solo ve lo que
    pasó por los boletines procesados y el escaneo, no toda la base de INPI."""
    ajustes = cargar_ajustes(cur)
    gen = ajustes["genericas"]
    claves = sim.claves_indice(nombre, gen)
    if not claves:
        return []
    minimo = minimo if minimo is not None else max(60, ajustes["config"]["umbral"] - 10)
    falso_m = {"nombres": [nombre], "clase": clase, "vigilar_todas_clases": True}
    cur.execute(_sql_universo())
    out = []
    for fila in _dict_rows(cur):
        nom = fila.get("nombre")
        if not nom or not (sim.claves_indice(nom, gen) & claves):
            continue
        r = _mejor_contra(nom, fila.get("clase"), falso_m, ajustes)
        if not r or r["puntaje"] < minimo:
            continue
        out.append({**{k: fila.get(k) for k in ("acta", "nombre", "clase", "tipo", "titular", "matricula_agente",
                                                "caracter", "fecha_presentacion", "fecha_publicacion", "boletin",
                                                "estado_tramite", "fuente")},
                    "puntaje": r["puntaje"], "motivos": r["motivos"],
                    "nivel": sim.nivel(r["puntaje"], ajustes["config"]["nivel_alta"], ajustes["config"]["nivel_media"])})
    out.sort(key=lambda x: -x["puntaje"])
    return out[:limite]
