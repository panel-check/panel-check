"""
Vigilancia marcaria: compara las solicitudes de marca que van apareciendo
(boletines + escaneo directo de actas) contra las marcas de la cartera de
clientes, y deja alertas para revisar.

Lo usan scripts/vigilancia.py (todos los días, en GitHub Actions) y el panel
(probar un nombre, marcas parecidas). Solo habla con la base: no consulta
INPI.

Qué genera:
  * alerta "similitud": una solicitud nueva se parece a una marca de un
    cliente (ver similitud.py para el puntaje): por cómo se escribe o suena,
    o porque nombra el mismo concepto (traducción, sinónimo) según la lista de
    conceptos de la marca (conceptos_vigilancia.py).
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

import collections
import datetime as _dt
import json
import re

import similitud as sim
import cartera
import juez_vigilancia as juez

VENTANA_OPOSICION_DIAS = 30
GRACIA_DIAS = 3
# Versión de la firma de cada solicitud comparada: al cambiar las reglas (12/11/2026: palabras
# comunes por clase, índice más amplio, juez con IA) todo el universo se vuelve a comparar una vez.
# La «P» final hace el doble chequeo: cada solicitud se vuelve a mirar cuando sale en el boletín.
FIRMA_VERSION = "v2"

# Palabra «de uso común» en una clase (calibrado el 12/11/2026 con 74.678 solicitudes de 18 meses:
# TURISMO 83 en la 39, TODO 45 en la 35, CAFE 148 en la 43; NUBE, LABO, APEX, OLIVIA 1 o 2).
COMUN_MIN_CLASE = 15         # apariciones en la clase
COMUN_MIN_CLASE_CHICA = 8    # o esta cantidad si además es el 1 % de la clase
COMUN_MIN_GLOBAL = 60        # o esta cantidad sumando todas las clases
LIMITE_REEVALUAR = 300       # alertas viejas sin veredicto que se evalúan por corrida
LIMITE_JUZGAR = 400          # pares nuevos que pasan por el juez en una corrida (los de mayor puntaje);
                             # el resto se guarda sin veredicto y lo juzga reevaluar_pendientes después
TANDA_JUEZ = 50              # se juzga y se guarda de a tandas: si la corrida se corta, lo hecho queda


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
            "config": cartera.leer_config(cur), "comunes_clase": {}, "comunes_global": frozenset(), "frec": {}}


def calcular_comunes(universo, gen) -> dict:
    """Frecuencia de cada palabra por clase en el universo de solicitudes y, con eso,
    las palabras de uso común de cada clase (no distinguen una marca de otra)."""
    porclase, tot, glob = collections.defaultdict(collections.Counter), collections.Counter(), collections.Counter()
    for f in universo:
        nombre = f.get("nombre")
        if not nombre:
            continue
        c = f.get("clase")
        tot[c] += 1
        for t in set(sim.tokens(nombre, gen)):
            porclase[c][t] += 1
            glob[t] += 1
    comunes_clase = {}
    for c, cnt in porclase.items():
        tam = max(tot[c], 1)
        comunes_clase[c] = frozenset(t for t, n in cnt.items()
                                     if n >= COMUN_MIN_CLASE or (n >= COMUN_MIN_CLASE_CHICA and n / tam >= 0.01))
    return {"comunes_clase": comunes_clase, "frec": porclase,
            "comunes_global": frozenset(t for t, n in glob.items() if n >= COMUN_MIN_GLOBAL)}


def comunes_par(ajustes, clase_a, clase_b) -> frozenset:
    cc = ajustes.get("comunes_clase") or {}
    return (ajustes.get("comunes_global") or frozenset()) | cc.get(clase_a, frozenset()) | cc.get(clase_b, frozenset())


def _frecuencia(ajustes, clase, palabra) -> int:
    return int(((ajustes.get("frec") or {}).get(clase) or {}).get(palabra, 0))


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
        SELECT cm.acta, cm.cliente_id, cm.denominacion, cm.terminos_vigilancia, cm.conceptos_vigilancia, cm.clase, cm.tipo,
               cm.vigilar_todas_clases, cm.umbral_vigilancia, cm.vigilancia_firma, cm.cuit, c.nombre AS cliente_nombre, c.cuit AS cliente_cuit,
               cm.grupo_marca, cm.sensibilidad_vigilancia
        FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
        WHERE cm.vigilar AND c.activo
        """
    )
    marcas = _dict_rows(cur)
    gen = ajustes["genericas"]
    indice = {}
    grupos = {}
    for m in marcas:
        m["grupo"] = clave_grupo(m)
        grupos.setdefault(m["grupo"], []).append(m)
    for m in marcas:
        m["grupo_actas"] = sorted(({"acta": x["acta"], "clase": x.get("clase")} for x in grupos[m["grupo"]]),
                                  key=lambda d: d["acta"])
        nombres = [n.strip() for n in [m["denominacion"] or ""] + re.split(r"[,;\n]", m["terminos_vigilancia"] or "") if n.strip()]
        m["nombres"] = nombres
        m["conceptos"] = sim.separar_conceptos(m.get("conceptos_vigilancia"))
        m["firma"] = firma_cartera(m)
        for k in _claves_marca(m, gen):
            indice.setdefault(k, []).append(m)
    return marcas, indice


def clave_grupo(m) -> tuple:
    """Las actas de un mismo cliente que son la misma marca (otra clase, denominativa y mixta)
    generan UNA alerta por solicitud nueva. Mismo criterio que la lista de Marcas: grupo puesto
    a mano o el nombre normalizado."""
    nombre = (m.get("grupo_marca") or "").strip() or sim.normalizar(m.get("denominacion") or "") or m.get("acta")
    return (m.get("cliente_id"), nombre.upper())


def _claves_marca(m, gen) -> set:
    claves = sim.claves_conceptos(m.get("conceptos"), gen)
    for n in m["nombres"]:
        claves |= sim.claves_indice(n, gen)
    return claves


def firma_cartera(m) -> str:
    return "|".join([sim.normalizar(m.get("denominacion") or ""), (m.get("terminos_vigilancia") or "").strip().upper(),
                     str(m.get("clase") or ""), "T" if m.get("vigilar_todas_clases") else "C"]) \
        + (f"|U{m['umbral_vigilancia']}" if m.get("umbral_vigilancia") else "") \
        + (f"|K{(m.get('conceptos_vigilancia') or '').strip().upper()}" if (m.get("conceptos_vigilancia") or "").strip() else "")
    # sin umbral propio ni conceptos, la firma no cambia (no se recompara toda la cartera de golpe)


def _mejor_contra(nombre_nuevo, clase_nueva, m, ajustes):
    mejor = None
    comunes = comunes_par(ajustes, m.get("clase"), clase_nueva)
    for n in m["nombres"]:
        r = sim.puntaje_marcas(n, m.get("clase"), nombre_nuevo, clase_nueva, ajustes["genericas"],
                               ajustes["pares"], m.get("vigilar_todas_clases"), comunes)
        if r and (mejor is None or r["puntaje"] > mejor["puntaje"]):
            mejor = r
    # Mismo concepto (traducción, sinónimo): solo si gana a lo escrito.
    base, motivos = sim.puntaje_conceptos(m.get("conceptos"), m.get("denominacion") or (m["nombres"] or [""])[0],
                                          nombre_nuevo, ajustes["genericas"], comunes)
    if base and (mejor is None or base > mejor["puntaje"]):
        r = sim.ajustar_por_clase(base, motivos, m.get("clase"), clase_nueva, ajustes["pares"], m.get("vigilar_todas_clases"))
        if r and (mejor is None or r["puntaje"] > mejor["puntaje"]):
            mejor = r
    return mejor


def _candidatas(indice, nombre, gen):
    vistos, out = set(), []
    for k in sim.claves_indice(nombre, gen) | sim.claves_busqueda_conceptos(nombre, gen):
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


_CAMPOS_IA = ("estado", "actas_cliente", "ia_veredicto", "ia_nivel", "ia_razon", "ia_modelo", "ia_dominantes", "nivel_motor")


def ya_alertada(cur, a: dict) -> bool:
    """¿Ya hay una alerta de esta solicitud nueva contra la misma marca del cliente (cualquiera
    de sus actas)? Si la hay, se le actualiza la lista de actas/clases y no se crea otra."""
    if a.get("tipo") != "similitud":
        return False
    actas = [x["acta"] for x in json.loads(a.get("actas_cliente") or "[]")] or [a.get("acta_cliente")]
    cur.execute("SELECT id FROM vigilancia_alertas WHERE tipo = 'similitud' AND acta_nueva = %s AND acta_cliente = ANY(%s) LIMIT 1",
                (a["acta_nueva"], actas))
    f = cur.fetchone()
    if not f:
        return False
    if a.get("actas_cliente"):
        cur.execute("UPDATE vigilancia_alertas SET actas_cliente = %s WHERE id = %s",
                    (a["actas_cliente"], f["id"] if isinstance(f, dict) else f[0]))
    return True


def _insertar_alerta(cur, a: dict) -> bool:
    a = {**{k: None for k in _CAMPOS_IA}, **a}
    a["estado"] = a.get("estado") or "nueva"
    cur.execute(
        """
        INSERT INTO vigilancia_alertas (tipo, acta_cliente, cliente_id, acta_nueva, denominacion_nueva, clase_nueva,
            tipo_nuevo, titular_nuevo, cuit_nuevo, agente_nuevo, fecha_presentacion_nueva, fuente_nueva,
            puntaje, nivel, motivos, estado, actas_cliente, ia_veredicto, ia_nivel, ia_razon, ia_modelo, ia_dominantes,
            nivel_motor, ia_en)
        VALUES (%(tipo)s, %(acta_cliente)s, %(cliente_id)s, %(acta_nueva)s, %(denominacion_nueva)s, %(clase_nueva)s,
            %(tipo_nuevo)s, %(titular_nuevo)s, %(cuit_nuevo)s, %(agente_nuevo)s, %(fecha_presentacion_nueva)s,
            %(fuente_nueva)s, %(puntaje)s, %(nivel)s, %(motivos)s, %(estado)s, %(actas_cliente)s, %(ia_veredicto)s,
            %(ia_nivel)s, %(ia_razon)s, %(ia_modelo)s, %(ia_dominantes)s, %(nivel_motor)s,
            CASE WHEN %(ia_veredicto)s IS NULL THEN NULL ELSE now() END)
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
        # Cada marca puede tener su propio umbral (Clientes → ficha de la marca); si no, el de Ajustes.
        if not r or r["puntaje"] < (m.get("umbral_vigilancia") or umbral):
            continue
        niv = sim.nivel(r["puntaje"], ajustes["config"]["nivel_alta"], ajustes["config"]["nivel_media"])
        grupo_actas = m.get("grupo_actas") or [{"acta": m["acta"], "clase": m.get("clase")}]
        a = _alerta_base(fila, "similitud", m["cliente_id"], grupo_actas[0]["acta"], r["puntaje"], niv, r["motivos"])
        a["nivel_motor"] = niv
        a["actas_cliente"] = json.dumps(grupo_actas)
        a["_grupo"] = m.get("grupo") or (m["cliente_id"], m["acta"])
        a["_sensibilidad"] = m.get("sensibilidad_vigilancia")
        a["_par"] = par_para_juez(m, grupo_actas, fila, r, ajustes)
        out.append(a)
    # Una sola alerta por marca del cliente (la del mejor puntaje), con todas sus actas y clases.
    por_grupo = {}
    for a in out:
        g = a["_grupo"]
        if g not in por_grupo or a["puntaje"] > por_grupo[g]["puntaje"]:
            por_grupo[g] = a
    return list(por_grupo.values())


def par_para_juez(m, grupo_actas, fila, r, ajustes) -> dict:
    """Lo que ve el juez: solo datos públicos del boletín, nada del cliente."""
    gen = ajustes["genericas"]
    comp = sorted(set(sim.tokens(m.get("denominacion") or "", gen)) & set(sim.tokens(fila.get("nombre") or "", gen)))
    return {
        "marca_cliente": m.get("denominacion") or "", "tipo_cliente": m.get("tipo"),
        "clases_cliente": sorted({x.get("clase") for x in grupo_actas if x.get("clase") is not None}),
        "otros_nombres": (m.get("terminos_vigilancia") or "").strip(),
        "marca_nueva": fila.get("nombre") or "", "tipo_nuevo": fila.get("tipo"), "clase_nueva": fila.get("clase"),
        "relacion": r.get("relacion_clases"), "motivos": " · ".join(r.get("motivos") or []),
        "compartidas": [(t, _frecuencia(ajustes, fila.get("clase"), t)) for t in comp if len(t) >= 2],
    }


def aplicar_veredicto(a: dict, v: dict, sensibilidad) -> dict:
    """Completa la alerta con el veredicto del juez: nivel de la IA y, si no alcanza la
    sensibilidad de la marca, estado «descartada_ia» (no se muestra, no se avisa)."""
    a["ia_veredicto"] = v["veredicto"]
    a["ia_nivel"] = v["nivel"]
    a["ia_razon"] = v["razon"]
    a["ia_modelo"] = v["modelo"]
    a["ia_dominantes"] = " / ".join(x for x in (v.get("dominante_cliente"), v.get("dominante_nueva")) if x) or None
    a["nivel"] = v["nivel"]
    a["estado"] = "nueva" if juez.alcanza(v["veredicto"], sensibilidad) else "descartada_ia"
    return a


def _limpias(a: dict) -> dict:
    return {k: v for k, v in a.items() if not k.startswith("_")}


def juzgar_e_insertar(cur, alertas: list, stats: dict, log=print, uso=None) -> None:
    """Las alertas nuevas de parecido pasan por el juez (si hay clave) y se guardan, de a tandas
    (cada tanda queda guardada aunque la corrida se corte). Se juzgan primero las de mayor
    puntaje y, como mucho, LIMITE_JUZGAR por corrida: las demás se guardan sin veredicto
    («todavía sin revisar por la IA») y las juzga reevaluar_pendientes en las próximas."""
    nuevas = sorted((a for a in alertas if not ya_alertada(cur, a)), key=lambda a: -(a.get("puntaje") or 0))
    if not nuevas:
        return
    a_juzgar = {id(a) for a in nuevas[:LIMITE_JUZGAR]} if juez.configurado() else set()
    for i in range(0, len(nuevas), TANDA_JUEZ):
        tanda = nuevas[i:i + TANDA_JUEZ]
        con_juez = [a for a in tanda if id(a) in a_juzgar]
        veredictos = juez.evaluar_lote([a["_par"] for a in con_juez], uso=uso, log=log) if con_juez else []
        for a, v in zip(con_juez, veredictos):
            if v:
                aplicar_veredicto(a, v, a.get("_sensibilidad"))
                stats["juzgadas"] += 1
        for a in tanda:
            if _insertar_alerta(cur, _limpias(a)):
                stats["alertas_similitud"] += 1
                if a.get("estado") == "descartada_ia":
                    stats["descartadas_ia"] += 1
        cur.connection.commit()
        if con_juez:
            log(f"[juez] {min(i + TANDA_JUEZ, len(nuevas))} de {len(nuevas)} pares nuevos guardados")


def comparar(conn, hoy=None, log=print, limite_filas=None) -> dict:
    """Corrida completa de vigilancia. Devuelve contadores.

    Desde el 12/11/2026: las palabras de uso común se calculan con el propio
    universo (por clase), cada solicitud se vuelve a mirar cuando sale en el
    boletín (doble chequeo), sale una sola alerta por marca del cliente y, si
    hay ANTHROPIC_API_KEY, cada alerta nueva pasa por el juez con IA."""
    hoy = hoy or cartera.hoy_ar()
    stats = {"comparadas": 0, "alertas_similitud": 0, "alertas_otro_agente": 0, "marcas_cartera": 0,
             "recomparadas_cartera": 0, "saltadas_ventana": 0, "juzgadas": 0, "descartadas_ia": 0,
             "reevaluadas": 0, "juez_activo": juez.configurado()}
    uso = juez.Uso()
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
        ajustes.update(calcular_comunes(universo, gen))
        log(f"Solicitudes en el universo de comparación: {len(universo)}; marcas de la cartera a vigilar: {len(marcas)}; "
            f"juez con IA: {'activo' if stats['juez_activo'] else 'apagado (falta ANTHROPIC_API_KEY)'}")

        pendientes = []
        nuevas_comparadas = []
        n = 0
        for fila in universo:
            if limite_filas and n >= limite_filas:
                break
            if fila["acta"] in en_cartera or fila.get("estado_tramite") == "Denegada":
                continue
            nombre = fila.get("nombre")
            firma = firma_solicitud(fila, gen)

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
            pendientes += _evaluar_fila(fila, _candidatas(indice, nombre, gen), ajustes, mis_cuits, en_cartera)

        # ── Pasada B: marcas de la cartera nuevas o con el nombre cambiado ──
        # (contra las solicitudes que siguen abiertas, aunque ya estuvieran
        # comparadas contra el resto de la cartera)
        obsoletas = [m for m in marcas if m.get("vigilancia_firma") != m["firma"] and m["nombres"]]
        if obsoletas:
            indice_b = {}
            for m in obsoletas:
                for k in _claves_marca(m, gen):
                    indice_b.setdefault(k, []).append(m)
            for fila in universo:
                if fila["acta"] in en_cartera or fila.get("estado_tramite") == "Denegada" or not fila.get("nombre"):
                    continue
                if not ventana_abierta(fila.get("fecha_publicacion"), hoy):
                    continue
                pendientes += _evaluar_fila(fila, _candidatas(indice_b, fila["nombre"], gen), ajustes, mis_cuits, en_cartera)
            stats["recomparadas_cartera"] = len(obsoletas)
        # Marcas sin nombre (todavía): quedan con firma vacía y se miran cuando lo tengan.

        # Un mismo par puede salir en las dos pasadas: se juzga una vez.
        unicas = {}
        for a in pendientes:
            k = (a["_grupo"], a["acta_nueva"])
            if k not in unicas or a["puntaje"] > unicas[k]["puntaje"]:
                unicas[k] = a
        log(f"Pares candidatos para alertar: {len(unicas)}")
        juzgar_e_insertar(cur, list(unicas.values()), stats, log=log, uso=uso)
        _guardar_comparadas(cur, nuevas_comparadas)
        for m in obsoletas:
            cur.execute("UPDATE cartera_marcas SET vigilancia_firma = %s WHERE acta = %s", (m["firma"], m["acta"]))
        conn.commit()

        # Alertas abiertas que todavía no pasaron por el juez (las de antes del 12/11/2026 o las
        # que fallaron en una corrida anterior): se evalúan ahora.
        if stats["juez_activo"]:
            stats["reevaluadas"] = reevaluar_pendientes(cur, ajustes, marcas, log=log, uso=uso)
    conn.commit()
    stats.update(uso.resumen())
    stats["costo_ia_usd_max"] = juez.costo_estimado(uso)
    return stats


def firma_solicitud(fila, gen) -> str:
    """Si cambia, la solicitud se vuelve a comparar: nombre, clase y si ya se publicó
    (doble chequeo cuando sale en el boletín) y la versión de las reglas."""
    publicada = "P" if fila.get("fecha_publicacion") or fila.get("boletin") else ""
    return f"{FIRMA_VERSION}|{sim.normalizar(fila.get('nombre') or '', gen)}|{fila.get('clase') or ''}|{publicada}"


SQL_SIN_VEREDICTO = """
    SELECT a.id, a.acta_cliente, a.acta_nueva, a.denominacion_nueva, a.clase_nueva, a.tipo_nuevo, a.motivos, a.actas_cliente,
           cm.denominacion, cm.tipo, cm.clase, cm.terminos_vigilancia, cm.cliente_id, cm.grupo_marca, cm.sensibilidad_vigilancia
    FROM vigilancia_alertas a JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
    WHERE a.tipo = 'similitud' AND a.estado = 'nueva' AND a.ia_veredicto IS NULL
    ORDER BY a.creada_en DESC
    LIMIT %s
"""


def reevaluar_pendientes(cur, ajustes, marcas=None, log=print, uso=None, limite=LIMITE_REEVALUAR) -> int:
    """Pasa por el juez las alertas «nuevas» que no tienen veredicto. Las que no alcanzan
    la sensibilidad de la marca quedan «Descartada por IA». No toca las que ya decidió una persona."""
    cur.execute(SQL_SIN_VEREDICTO, (limite,))
    filas = _dict_rows(cur)
    if not filas:
        return 0
    sens_grupo = {}
    for m in marcas or ():
        if m.get("sensibilidad_vigilancia"):
            sens_grupo[m["grupo"]] = m["sensibilidad_vigilancia"]
    pares = []
    for f in filas:
        grupo_actas = f["actas_cliente"] if isinstance(f["actas_cliente"], list) else json.loads(f["actas_cliente"] or "[]")
        grupo_actas = grupo_actas or [{"acta": f["acta_cliente"], "clase": f["clase"]}]
        m = {"denominacion": f["denominacion"], "tipo": f["tipo"], "terminos_vigilancia": f["terminos_vigilancia"]}
        fila = {"nombre": f["denominacion_nueva"], "tipo": f["tipo_nuevo"], "clase": f["clase_nueva"]}
        rel = sim.relacion_clases(f["clase"], f["clase_nueva"], ajustes["pares"])
        pares.append(par_para_juez(m, grupo_actas, fila, {"relacion_clases": rel, "motivos": (f["motivos"] or "").split(" · ")}, ajustes))
        f["_sens"] = f.get("sensibilidad_vigilancia") or sens_grupo.get(clave_grupo(f))
    log(f"[juez] reevaluando {len(filas)} alerta(s) abiertas sin veredicto")
    hechas = 0
    for i in range(0, len(filas), TANDA_JUEZ):
        hechas += _reevaluar_tanda(cur, filas[i:i + TANDA_JUEZ], pares[i:i + TANDA_JUEZ], uso, log)
        cur.connection.commit()
    return hechas


def _reevaluar_tanda(cur, filas, pares, uso, log) -> int:
    veredictos = juez.evaluar_lote(pares, uso=uso, log=log)
    hechas = 0
    for f, v in zip(filas, veredictos):
        if not v:
            continue
        a = aplicar_veredicto({}, v, f["_sens"])
        cur.execute(
            "UPDATE vigilancia_alertas SET ia_veredicto = %s, ia_nivel = %s, ia_razon = %s, ia_modelo = %s, ia_dominantes = %s, "
            "ia_en = now(), nivel_motor = COALESCE(nivel_motor, nivel), nivel = %s, estado = %s "
            "WHERE id = %s AND estado = 'nueva'",
            (a["ia_veredicto"], a["ia_nivel"], a["ia_razon"], a["ia_modelo"], a["ia_dominantes"], a["nivel"], a["estado"], f["id"]))
        hechas += 1
    return hechas


def reaplicar_sensibilidad(cur, actas: list) -> int:
    """Cuando cambia la sensibilidad de una marca, sus alertas juzgadas se vuelven a mostrar u
    ocultar según el veredicto que ya tienen (no se le vuelve a preguntar a la IA)."""
    cur.execute("SELECT cm.acta, cm.sensibilidad_vigilancia FROM cartera_marcas cm WHERE cm.acta = ANY(%s)", (actas,))
    sens = {(r["acta"] if isinstance(r, dict) else r[0]): (r["sensibilidad_vigilancia"] if isinstance(r, dict) else r[1])
            for r in cur.fetchall()}
    cambios = 0
    for acta, s in sens.items():
        mostrar = list(juez.SENSIBILIDADES.get(s or juez.SENSIBILIDAD_DEFAULT)[1])
        cur.execute("UPDATE vigilancia_alertas SET estado = 'nueva' WHERE acta_cliente = %s AND estado = 'descartada_ia' "
                    "AND ia_veredicto = ANY(%s)", (acta, mostrar))
        cambios += cur.rowcount
        cur.execute("UPDATE vigilancia_alertas SET estado = 'descartada_ia' WHERE acta_cliente = %s AND estado = 'nueva' "
                    "AND ia_veredicto IS NOT NULL AND NOT (ia_veredicto = ANY(%s)) AND NOT COALESCE(ia_desacuerdo, false)",
                    (acta, mostrar))
        cambios += cur.rowcount
    return cambios


# ── Mantenimiento: alertas vencidas y duplicadas ─────────────────────────

SQL_PUBLICACION = """
    (SELECT CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                 ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END
       FROM marcas m LEFT JOIN boletines b ON b.numero = m.boletin WHERE m.acta = a.acta_nueva)
"""


def mantenimiento(conn, hoy=None, log=print) -> dict:
    """Idempotente. Lo corren la vigilancia y el panel al arrancar.
      1. Borra las alertas de parecido abiertas (o descartadas por IA) cuyo plazo de oposición ya
         venció: ya no se puede hacer nada con ellas (pedido del 12/11/2026). Las que una persona
         marcó como «oposición presentada» o descartó a mano no se tocan.
      2. Une las duplicadas: una sola alerta por marca del cliente y solicitud nueva, con todas
         las actas y clases de esa marca (actas_cliente). Se queda la que alguien ya trabajó o,
         si ninguna, la de mayor puntaje."""
    hoy = hoy or cartera.hoy_ar()
    st = {"vencidas_borradas": 0, "duplicadas_unidas": 0}
    with conn.cursor() as cur:
        cur.execute(
            f"""DELETE FROM vigilancia_alertas a
                WHERE a.tipo = 'similitud' AND a.estado IN ('nueva', 'monitorear', 'oponer', 'descartada_ia')
                  AND {SQL_PUBLICACION} IS NOT NULL
                  AND {SQL_PUBLICACION} + %s < %s""", (VENTANA_OPOSICION_DIAS, hoy))
        st["vencidas_borradas"] = cur.rowcount

        cur.execute("""SELECT a.id, a.acta_cliente, a.acta_nueva, a.estado, a.puntaje, a.actas_cliente, a.decidido_en,
                              cm.cliente_id, cm.denominacion, cm.grupo_marca, cm.clase
                       FROM vigilancia_alertas a JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
                       WHERE a.tipo = 'similitud'""")
        alertas = _dict_rows(cur)
        cur.execute("SELECT acta, cliente_id, denominacion, grupo_marca, clase FROM cartera_marcas")
        grupos = {}
        for m in _dict_rows(cur):
            grupos.setdefault(clave_grupo(m), []).append({"acta": m["acta"], "clase": m["clase"]})
        por_par = {}
        for a in alertas:
            por_par.setdefault((clave_grupo(a), a["acta_nueva"]), []).append(a)
        prioridad = {"opuesta": 0, "oponer": 1, "monitorear": 2, "descartada": 3, "nueva": 4, "descartada_ia": 5}
        for (g, _), lista in por_par.items():
            actas_g = json.dumps(sorted(grupos.get(g, []), key=lambda d: d["acta"]))
            lista.sort(key=lambda a: (prioridad.get(a["estado"], 9), -(a["puntaje"] or 0)))
            guardar, sobran = lista[0], lista[1:]
            if sobran:
                cur.execute("DELETE FROM vigilancia_alertas WHERE id = ANY(%s)", ([x["id"] for x in sobran],))
                st["duplicadas_unidas"] += len(sobran)
            actual = guardar["actas_cliente"] if isinstance(guardar["actas_cliente"], (list, type(None))) else json.loads(guardar["actas_cliente"])
            if json.dumps(sorted(actual or [], key=lambda d: d["acta"])) != actas_g:
                cur.execute("UPDATE vigilancia_alertas SET actas_cliente = %s WHERE id = %s", (actas_g, guardar["id"]))
    conn.commit()
    if st["vencidas_borradas"] or st["duplicadas_unidas"]:
        log(f"[vigilancia] mantenimiento: {st['vencidas_borradas']} alerta(s) vencidas borradas, "
            f"{st['duplicadas_unidas']} duplicada(s) unidas")
    return st


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
    filas = _dict_rows(cur)
    ajustes.update(calcular_comunes(filas, gen))
    out = []
    for fila in filas:
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
