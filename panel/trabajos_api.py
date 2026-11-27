"""
API de la pantalla Seguimientos (/seguimientos): trabajos puntuales de cada cliente.

  GET    /api/trabajos                     tablero (estado=activo|cerrado|todos, q=texto)
  GET    /api/trabajos/{id}                ficha: trabajo + cliente + expediente + bitácora
  POST   /api/trabajos                     nuevo trabajo
  PUT    /api/trabajos/{id}                cambiar datos, etapa, estado, plazos
  DELETE /api/trabajos/{id}                borrar
  POST   /api/trabajos/{id}/bitacora       agregar una nota a la bitácora
  DELETE /api/trabajos/{id}/bitacora/{bid} borrar una nota (y sus menciones)
  GET    /api/trabajos-usuarios            usuarios del panel para arrobar en una nota («@pame»)
  POST   /api/trabajos/{id}/buscar-acta    buscar el acta en la base y, si no, en vivo en el INPI

Vista y oposición (desde el 19/11/2026): la marca ya tiene acta con una vista o una oposición, así que se
buscan las actas de ESE cliente que la tienen (trabajos_core.elegir_actas_conflicto), se cargan todas las
que tienen el mismo nombre (actas_extra si son varias) y, si falta, la fecha de notificación que ya leyó el panel.

Detección del acta: al abrir el tablero (como mucho cada 10 minutos) se buscan en la base las actas
de los trabajos que todavía no tienen (ver trabajos_core.elegir_acta); si una es segura se carga sola,
se anota en la bitácora y, en un registro, la etapa pasa a «Marca publicada» y el acta se suma a la cartera
del cliente (Clientes → Marcas) para que el seguimiento diario la lea en el INPI.

Etapas del registro según el INPI (desde el 19/11/2026): cada vez que se abre el tablero o una ficha, los
registros activos toman la etapa que corresponde a sus actas en la cartera (trabajos_core.etapa_registro: la
más atrasada si la marca tiene varias actas). Solo avanzan, nunca retroceden, y queda anotado en la bitácora.

La lógica (servicios, etapas, vencimientos, alertas, orden) está en trabajos_core.py.
"""

import re
import threading
import time
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import buscador_inpi as bi
import trabajos_core as tc

RE_ACTA = re.compile(r"^\d{4,9}$")
CADA_DETECCION = 600          # segundos entre detecciones automáticas al abrir el tablero
MATRICULAS_FIJAS = {"2906"}   # matrícula de Pame (además de las de Clientes → Ajustes → Matrículas)
ETAPA_PRESENTADA = tc.ETAPA_REG_PUBLICADA   # «Marca publicada» en Registro de marca (ya tiene acta)
_ultima_deteccion = 0.0
_candado_deteccion = threading.Lock()


class TrabajoNuevo(BaseModel):
    cliente_id: int
    marca: str
    servicio: str
    etapa: Optional[int] = 1
    contratacion: Optional[str] = None
    notificacion: Optional[str] = None
    acta: Optional[str] = None
    carpeta_drive: Optional[str] = None
    comprobante: Optional[str] = None
    notas: Optional[str] = None
    responsable: Optional[str] = None


class TrabajoCambios(BaseModel):
    cliente_id: Optional[int] = None
    marca: Optional[str] = None
    servicio: Optional[str] = None
    etapa: Optional[int] = None
    estado: Optional[str] = None
    contratacion: Optional[str] = None
    notificacion: Optional[str] = None
    acta: Optional[str] = None
    carpeta_drive: Optional[str] = None
    comprobante: Optional[str] = None
    notas: Optional[str] = None
    responsable: Optional[str] = None


class NotaBitacora(BaseModel):
    texto: str
    fecha: Optional[str] = None


def _texto(v, largo):
    v = (v or "").strip()
    return v[:largo] or None


def _url(v):
    v = _texto(v, 2000)
    if v and not re.match(r"^https?://", v, re.I):
        raise HTTPException(status_code=400, detail="El link tiene que empezar con http:// o https://")
    return v


def _fecha(v, campo):
    if v in (None, ""):
        return None
    f = tc.a_fecha(v)
    if not f:
        raise HTTPException(status_code=400, detail=f"Fecha inválida en {campo}")
    return f


def _acta(v):
    v = re.sub(r"\D", "", v or "")
    if not v:
        return None
    if not RE_ACTA.match(v):
        raise HTTPException(status_code=400, detail="Número de acta inválido")
    return v


def _campos(body, parcial):
    enviados = body.model_fields_set if parcial else set(type(body).model_fields)
    out = {}
    if "cliente_id" in enviados and body.cliente_id is not None:
        out["cliente_id"] = int(body.cliente_id)
    if "marca" in enviados and body.marca is not None:
        m = tc.marca_limpia(body.marca)
        if not m:
            raise HTTPException(status_code=400, detail="Falta el nombre de la marca")
        out["marca"] = m[:200]
    if "servicio" in enviados and body.servicio is not None:
        if not tc.servicio(body.servicio):
            raise HTTPException(status_code=400, detail="Servicio desconocido")
        out["servicio"] = body.servicio
    if "etapa" in enviados and body.etapa is not None:
        out["etapa"] = max(0, min(int(body.etapa), 20))
    if "estado" in enviados and getattr(body, "estado", None) is not None:
        if body.estado not in tc.ESTADOS:
            raise HTTPException(status_code=400, detail="Estado inválido")
        out["estado"] = body.estado
    for k in ("contratacion", "notificacion"):
        if k in enviados:
            out[k] = _fecha(getattr(body, k), k)
    if "acta" in enviados:
        out["acta"] = _acta(body.acta)
    for k in ("carpeta_drive", "comprobante"):
        if k in enviados:
            out[k] = _url(getattr(body, k))
    if "notas" in enviados:
        out["notas"] = _texto(body.notas, 4000)
    if "responsable" in enviados:
        out["responsable"] = _texto(body.responsable, 60)
    return out


# Acta efectiva: la cargada a mano o, si no hay, la marca de la cartera del mismo cliente con
# el mismo nombre (la más reciente). Así el expediente aparece solo para las marcas que ya
# están en Clientes → Marcas.
SQL_LISTA = """
    SELECT t.*, c.nombre AS cliente, c.cuit AS cliente_cuit, c.contacto AS cliente_contacto, c.email AS cliente_email, c.telefono AS cliente_telefono,
           COALESCE(t.acta, cm.acta) AS acta_efectiva, (t.acta IS NULL AND cm.acta IS NOT NULL) AS acta_auto,
           cm.estado_tramite, cm.ultimo_movimiento, cm.ultimo_movimiento_fecha,
           (SELECT COUNT(*) FROM informes_viabilidad i
             WHERE i.cliente_id = t.cliente_id AND i.archivado_en IS NULL
               AND upper(btrim(i.marca)) = upper(btrim(t.marca))) AS informes
    FROM trabajos t
    JOIN clientes c ON c.id = t.cliente_id
    LEFT JOIN LATERAL (
        SELECT m.acta, m.estado_tramite, m.ultimo_movimiento, m.ultimo_movimiento_fecha
        FROM cartera_marcas m
        WHERE (t.acta IS NOT NULL AND m.acta = t.acta)
           OR (t.acta IS NULL AND m.cliente_id = t.cliente_id AND upper(m.denominacion) = upper(t.marca))
        ORDER BY (m.acta = t.acta) DESC, m.fecha_presentacion DESC NULLS LAST
        LIMIT 1
    ) cm ON true
"""


def crear_router(verificar_login, conexion) -> APIRouter:
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _uno(cur, tid):
        cur.execute(SQL_LISTA + " WHERE t.id = %s", (tid,))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="No existe ese trabajo")
        return tc.completar(dict(t))

    def _matriculas(cur):
        try:
            cur.execute("SELECT matricula FROM cartera_matriculas")
            return MATRICULAS_FIJAS | {str(f["matricula"]) for f in cur.fetchall()}
        except Exception:
            return set(MATRICULAS_FIJAS)

    def _candidatas_db(cur, trabajos):
        """Solicitudes con un nombre parecido en el escaneo diario, en los boletines y en la cartera."""
        patrones = sorted({tc.patron_ilike(t["marca"]) for t in trabajos})
        clientes = sorted({t["cliente_id"] for t in trabajos})
        out = []
        consultas = [
            ("escaneo", """SELECT acta, denominacion, titular, cuit, fecha_presentacion AS fecha,
                                  matricula_agente AS matricula, clase, NULL::int AS cliente_id
                           FROM solicitudes_escaneadas WHERE denominacion ILIKE ANY(%s)""", (patrones,)),
            ("boletin", """SELECT acta, COALESCE(NULLIF(denominacion_inpi, ''), denominacion) AS denominacion, titular, cuit,
                                  fecha_presentacion::date AS fecha, matricula_agente AS matricula, clase, NULL::int AS cliente_id
                           FROM marcas WHERE COALESCE(NULLIF(denominacion_inpi, ''), denominacion) ILIKE ANY(%s)""", (patrones,)),
            ("cartera", """SELECT acta, denominacion, titular, cuit, fecha_presentacion AS fecha,
                                  matricula_agente AS matricula, clase, cliente_id
                           FROM cartera_marcas WHERE cliente_id = ANY(%s)""", (clientes,)),
        ]
        for fuente, sql, val in consultas:
            try:
                cur.execute("SAVEPOINT cand")
                cur.execute(sql, val)
                out += [{**dict(f), "fuente": fuente} for f in cur.fetchall()]
                cur.execute("RELEASE SAVEPOINT cand")
            except Exception as e:   # una tabla que falta no frena las demás
                cur.execute("ROLLBACK TO SAVEPOINT cand")
                print(f"[seguimientos] candidatas de {fuente}: {e}")
        return out

    def _a_cartera(cur, cliente_id, acta, marca, usuario):
        """Suma el acta de un registro a la cartera del cliente (si no está) para que el seguimiento diario
        (scripts/revisar_cartera.py) lea su ubicación y estado en el INPI. Queda pendiente de consultar."""
        try:
            cur.execute("SAVEPOINT a_cartera")
            cur.execute("SELECT 1 FROM cartera_marcas WHERE acta = %s", (acta,))
            if not cur.fetchone():
                import cartera
                cartera.alta_marca_cartera(cur, cliente_id, acta, {"estado_consulta": "pendiente",
                                           "error": "cargada desde Seguimientos, todavía sin consultar"},
                                           usuario, origen_carga="seguimientos")
                cur.execute("UPDATE cartera_marcas SET denominacion = COALESCE(denominacion, %s) WHERE acta = %s",
                            (marca, acta))
            cur.execute("RELEASE SAVEPOINT a_cartera")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT a_cartera")
            print(f"[seguimientos] acta {acta} a la cartera: {e}")

    def _cartera_de(cur, trabajos):
        clientes = sorted({t["cliente_id"] for t in trabajos})
        actas = sorted({t["acta"] for t in trabajos if t.get("acta")})
        cur.execute(
            """SELECT acta, cliente_id, denominacion, grupo_marca, clase, estado_tramite, fecha_publicacion,
                      ubicacion_actual, ubicacion_fecha, consultado_en
               FROM cartera_marcas WHERE cliente_id = ANY(%s) OR acta = ANY(%s)""", (clientes, actas))
        return cur.fetchall()

    def _sincronizar_registros(cur, solo_id=None):
        """Los registros activos avanzan solos a la etapa que dice el INPI. Nunca retroceden solos, y si alguien
        los vuelve atrás a mano no se los adelanta de nuevo hasta que el INPI muestre una etapa más avanzada
        que la última que aplicó el sistema (etapa_inpi): así no se repite la misma nota en la bitácora."""
        _corregir_boletin(cur)
        _borrar_notas_boletin(cur)
        cond = "servicio = 'registro' AND estado = 'activo'" + (" AND id = %s" if solo_id else "")
        cur.execute(f"SELECT id, cliente_id, marca, acta, etapa, etapa_inpi FROM trabajos WHERE {cond}", (solo_id,) if solo_id else None)
        trabajos = cur.fetchall()
        if not trabajos:
            return
        cartera_filas = _cartera_de(cur, trabajos)
        etapas = tc.POR_ID["registro"]["etapas"]
        for t in trabajos:
            info = tc.etapa_registro(tc.actas_del_trabajo(t, cartera_filas))
            if not (info and info["etapa"]) or info["etapa"] <= (t.get("etapa_inpi") or 0):
                continue
            cur.execute("UPDATE trabajos SET etapa_inpi = %s WHERE id = %s", (info["etapa"], t["id"]))
            if info["etapa"] > (t["etapa"] or 0):
                cur.execute("UPDATE trabajos SET etapa = %s, modificado_en = now(), modificado_por = 'sistema' "
                            "WHERE id = %s AND etapa < %s", (info["etapa"], t["id"], info["etapa"]))
                if cur.rowcount:
                    cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                                (t["id"], tc.hoy_ar(), tc.texto_avance(etapas[info["etapa"] - 1], info)))

    def _sincronizar_tomas(cur, solo_id=None):
        """Vista y oposición: cuando la grilla del expediente muestra que tomamos intervención (desde la
        contratación), la etapa «Toma de intervención» se tilda sola, con una nota. Igual que el registro: no
        retrocede nada y si alguien la vuelve atrás a mano no se vuelve a marcar (etapa_inpi). Las oposiciones
        que presentamos nosotros (alerta_id) no cuentan: ahí el expediente es el de la otra marca."""
        cond = ("servicio IN ('vista', 'oposicion') AND estado = 'activo' AND alerta_id IS NULL "
                "AND COALESCE(etapa, 0) < %s AND COALESCE(etapa_inpi, 0) < %s") + (" AND id = %s" if solo_id else "")
        params = [tc.ETAPA_TOMA, tc.ETAPA_TOMA] + ([solo_id] if solo_id else [])
        cur.execute(f"SELECT id, cliente_id, marca, acta, actas_extra, contratacion FROM trabajos WHERE {cond}", params)
        trabajos = cur.fetchall()
        if not trabajos:
            return
        cartera = _cartera_de(cur, trabajos)
        por_trabajo = {}
        for t in trabajos:
            actas = [a for a in [t.get("acta")] + list(t.get("actas_extra") or []) if a]
            if not actas:   # sin acta cargada: la de la cartera del mismo cliente con el mismo nombre
                actas = [m["acta"] for m in tc.actas_del_trabajo(t, cartera)]
            por_trabajo[t["id"]] = actas
        cur.execute("SELECT acta, grilla_claves FROM cartera_marcas WHERE acta = ANY(%s)",
                    (sorted({a for v in por_trabajo.values() for a in v}),))
        grillas = {r["acta"]: r["grilla_claves"] for r in cur.fetchall()}
        nombre = tc.POR_ID["vista"]["etapas"][tc.ETAPA_TOMA - 1]
        for t in trabajos:
            actas = por_trabajo[t["id"]]
            encontradas = [(f, a) for a in actas for f in [tc.toma_en_grilla(grillas.get(a), t.get("contratacion"))] if f]
            if not encontradas:
                continue
            fecha, acta = min(encontradas)
            cur.execute("UPDATE trabajos SET etapa = %s, etapa_inpi = %s, modificado_en = now(), modificado_por = 'sistema' "
                        "WHERE id = %s AND COALESCE(etapa, 0) < %s", (tc.ETAPA_TOMA, tc.ETAPA_TOMA, t["id"], tc.ETAPA_TOMA))
            if cur.rowcount:
                cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                            (t["id"], tc.hoy_ar(), f"Etapa actualizada sola según el INPI: «{nombre}» (se tomó intervención "
                             f"el {fecha.strftime('%d/%m/%Y')} en el acta {acta})."))

    def _borrar_notas_boletin(cur):
        """Una sola vez (24/11/2026), después de _corregir_boletin: borra las notas automáticas «Etapa actualizada
        sola según el INPI: «Marca en boletín»…» de los registros que NO están en boletín (quedaron del error del
        sector Publicación). Las notas del equipo no se tocan."""
        cur.execute("INSERT INTO trabajos_migraciones (nombre) VALUES ('registro-boletin-notas-2026-11-24') ON CONFLICT DO NOTHING")
        if not cur.rowcount:
            return
        cur.execute("""DELETE FROM trabajos_bitacora b USING trabajos t
                       WHERE b.trabajo_id = t.id AND b.usuario = 'sistema' AND t.servicio = 'registro' AND t.etapa < %s
                         AND b.texto LIKE %s""",
                    (tc.ETAPA_REG_BOLETIN, "Etapa actualizada sola según el INPI: «Marca en boletín»%"))

    def _corregir_boletin(cur):
        """Una sola vez (24/11/2026): hasta ahora el sector «Publicación» del INPI se tomaba como «Marca en
        boletín». Los registros que el sistema pasó a boletín sin que la marca haya salido vuelven a la etapa
        que corresponde, con una nota; y se borran las notas automáticas repetidas (quedó una por cada vez que
        alguien lo volvió atrás a mano y el sistema lo adelantó de nuevo)."""
        cur.execute("INSERT INTO trabajos_migraciones (nombre) VALUES ('registro-boletin-2026-11-24') ON CONFLICT DO NOTHING")
        if not cur.rowcount:
            return
        cur.execute("""DELETE FROM trabajos_bitacora b USING trabajos_bitacora o
                       WHERE b.usuario = 'sistema' AND o.usuario = 'sistema' AND b.trabajo_id = o.trabajo_id
                         AND b.texto = o.texto AND b.texto LIKE 'Etapa actualizada sola según el INPI%%' AND b.id > o.id""")
        cur.execute("SELECT id, cliente_id, marca, acta, etapa FROM trabajos "
                    "WHERE servicio = 'registro' AND estado = 'activo' AND modificado_por = 'sistema' AND etapa = %s",
                    (tc.ETAPA_REG_BOLETIN,))
        trabajos = cur.fetchall()
        if not trabajos:
            return
        cartera_filas = _cartera_de(cur, trabajos)
        etapas = tc.POR_ID["registro"]["etapas"]
        for t in trabajos:
            info = tc.etapa_registro(tc.actas_del_trabajo(t, cartera_filas))
            if info and info["etapa"] and info["etapa"] < t["etapa"]:
                cur.execute("UPDATE trabajos SET etapa = %s, etapa_inpi = %s, modificado_en = now(), modificado_por = 'sistema' "
                            "WHERE id = %s", (info["etapa"], info["etapa"], t["id"]))
                cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                            (t["id"], tc.hoy_ar(), f"Etapa corregida: vuelve a «{etapas[info['etapa'] - 1]}». La marca todavía no "
                             "salió en el boletín según el INPI (estar en el sector Publicación es esperar la publicación, no haber salido)."))

    SQL_PUBLICACION = """(SELECT CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                                      ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END
                            FROM marcas m LEFT JOIN boletines b ON b.numero = m.boletin WHERE m.acta = %s)"""

    def _sincronizar_oposiciones(cur, solo_id=None):
        """Oposiciones que presentamos nosotros sin plazo todavía: cuando la marca nueva sale en el boletín,
        el plazo para presentarla (30 días corridos) se completa solo y queda en la bitácora."""
        cond = "alerta_id IS NOT NULL AND plazo_presentar IS NULL AND estado = 'activo'" + (" AND id = %s" if solo_id else "")
        cur.execute(f"SELECT id, acta_contraria, contraparte FROM trabajos WHERE {cond}", (solo_id,) if solo_id else None)
        for t in cur.fetchall():
            cur.execute("SELECT " + SQL_PUBLICACION + " AS pub", (t["acta_contraria"],))
            pub = (cur.fetchone() or {}).get("pub")
            plazo = tc.plazo_para_oponerse(pub)
            if plazo:
                cur.execute("UPDATE trabajos SET plazo_presentar = %s, modificado_en = now(), modificado_por = 'sistema' "
                            "WHERE id = %s AND plazo_presentar IS NULL", (plazo, t["id"]))
                cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                            (t["id"], tc.hoy_ar(), f"«{t['contraparte'] or 'La marca'}» salió publicada el "
                             f"{tc.a_fecha(pub).strftime('%d/%m/%Y')}: hay tiempo para presentar la oposición hasta el "
                             f"{plazo.strftime('%d/%m/%Y')}."))

    def _sincronizar_seguro(conn, cur, solo_id=None):
        try:
            cur.execute("SAVEPOINT sinc")
            _sincronizar_registros(cur, solo_id)
            _sincronizar_tomas(cur, solo_id)
            _sincronizar_oposiciones(cur, solo_id)
            cur.execute("RELEASE SAVEPOINT sinc")
            conn.commit()
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sinc")
            print(f"[seguimientos] etapas según el INPI: {e}")

    def _asignar(cur, t, elegida, evaluadas, origen):
        texto = tc.texto_deteccion(elegida, evaluadas, origen)
        cur.execute(
            "UPDATE trabajos SET acta = %s, etapa = CASE WHEN servicio = 'registro' AND etapa < %s THEN %s ELSE etapa END, "
            "modificado_en = now(), modificado_por = 'sistema' WHERE id = %s AND acta IS NULL",
            (elegida["acta"], ETAPA_PRESENTADA, ETAPA_PRESENTADA, t["id"]))
        if cur.rowcount:
            cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                        (t["id"], tc.hoy_ar(), texto))
            if t.get("servicio") == "registro":
                _a_cartera(cur, t["cliente_id"], elegida["acta"], t["marca"], "sistema")
        return texto

    def _detectar(cur, solo_id=None):
        """Busca en la base el acta de los trabajos activos sin acta. Devuelve {id: (elegida, evaluadas, texto)}."""
        cond = ("t.acta IS NULL AND t.estado = 'activo' AND t.servicio NOT IN ('vista', 'oposicion')"
                + (" AND t.id = %s" if solo_id else ""))
        cur.execute(f"SELECT t.id, t.marca, t.servicio, t.contratacion, t.cliente_id, c.nombre AS cliente_nombre, c.cuit AS cliente_cuit "
                    f"FROM trabajos t JOIN clientes c ON c.id = t.cliente_id WHERE {cond}", (solo_id,) if solo_id else None)
        trabajos = cur.fetchall()
        if not trabajos:
            return {}
        candidatas = _candidatas_db(cur, trabajos)
        matriculas = _matriculas(cur)
        res = {}
        for t in trabajos:
            suyas = [c for c in candidatas if c["fuente"] != "cartera" or c.get("cliente_id") == t["cliente_id"]]
            cliente = {"nombre": t["cliente_nombre"], "cuit": t["cliente_cuit"]}
            elegida, evaluadas = tc.elegir_acta(t, suyas, cliente, matriculas)
            texto = _asignar(cur, t, elegida, evaluadas, "escaneo diario del INPI y base del panel") if elegida else None
            res[t["id"]] = (elegida, evaluadas, texto)
        return res

    def _solicitudes_cliente(cur, cliente):
        """Todas las solicitudes del cliente que conoce el panel, fusionadas por acta: base de leads `marcas`
        (boletines y pre-boletín) y `solicitudes_escaneadas` (todo lo que entra al INPI, con o sin agente)
        por su CUIT o su nombre como titular, y su cartera (Clientes → Marcas)."""
        # Además del CUIT y el nombre de la ficha, se usan el CUIT y el titular tal como figuran en el INPI en
        # las marcas que el cliente ya tiene en la cartera (el INPI suele escribir «APELLIDO NOMBRE»).
        cur.execute("SELECT acta, cuit, titular FROM cartera_marcas WHERE cliente_id = %s", (cliente["id"],))
        cart = cur.fetchall()
        de_cartera = [f["acta"] for f in cart]
        cuits = sorted({re.sub(r"\D", "", c or "") for c in [cliente.get("cuit"), *(f["cuit"] for f in cart)]} - {""})
        nombres = sorted({x for n in [cliente.get("nombre"), *(f["titular"] for f in cart)] if n
                          for x in ((n or "").strip().upper(), tc.comparable(n))} - {""})
        por_titular = """(regexp_replace(COALESCE(cuit, ''), '\\D', '', 'g') = ANY(%s)
                          OR upper(btrim(titular)) = ANY(%s) OR acta = ANY(%s))"""
        val = (cuits, nombres, de_cartera)
        consultas = [
            (f"""SELECT acta, COALESCE(NULLIF(denominacion_inpi, ''), denominacion) AS denominacion, clase, tipo, titular, cuit,
                        tuvo_vista, vista_fecha_notificacion, tuvo_oposicion, oposicion_fecha_notificacion,
                        'boletin' AS fuente
                 FROM marcas WHERE {por_titular}""", val),
            (f"""SELECT acta, denominacion, clase, tipo, titular, cuit, fecha_presentacion, 'escaneo' AS fuente
                 FROM solicitudes_escaneadas WHERE {por_titular}""", val),
            ("""SELECT acta, denominacion, clase, tipo, titular, cuit, fecha_presentacion, estado_tramite, tuvo_oposicion,
                       detalle_oposicion, ultimo_movimiento, ultimo_movimiento_fecha, 'cartera' AS fuente
                FROM cartera_marcas WHERE cliente_id = %s""", (cliente["id"],)),
        ]
        filas = []
        for sql, v in consultas:
            try:
                cur.execute("SAVEPOINT sol")
                cur.execute(sql, v)
                filas += [dict(f) for f in cur.fetchall()]
                cur.execute("RELEASE SAVEPOINT sol")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT sol")
                print(f"[seguimientos] solicitudes del cliente: {e}")
        return tc.fusionar_solicitudes(filas)

    def _detectar_conflictos(cur, solo_id=None):
        """Vista y oposición: las actas son las del cliente con vista u oposición. {id: (elegidas, del_tipo, texto)}
        - sin acta: se cargan las de mismo nombre (o la única con ese problema) y la notificación si falta;
        - con acta pero SIN el problema (por ejemplo cargada desde la planilla con otra clase de la marca):
          si otra solicitud del mismo nombre lo tiene, pasa a ser el acta principal y la anterior queda en
          «También»."""
        # (las oposiciones que presentamos nosotros, creadas desde una alerta, no: su acta es la marca del cliente)
        cond = ("t.estado = 'activo' AND t.servicio IN ('vista', 'oposicion') AND t.alerta_id IS NULL"
                + (" AND t.id = %s" if solo_id else ""))
        cur.execute(f"SELECT t.id, t.marca, t.servicio, t.notificacion, t.acta, t.actas_extra, t.cliente_id, c.nombre, c.cuit "
                    f"FROM trabajos t JOIN clientes c ON c.id = t.cliente_id WHERE {cond}", (solo_id,) if solo_id else None)
        res = {}
        for t in cur.fetchall():
            sol = _solicitudes_cliente(cur, {"id": t["cliente_id"], "nombre": t["nombre"], "cuit": t["cuit"]})
            elegidas, motivo, del_tipo = tc.elegir_actas_conflicto(t, sol)
            texto = None
            if t["acta"]:
                con_problema = {c["acta"] for c in del_tipo}
                if motivo == "mismo nombre de marca" and t["acta"] not in con_problema:
                    otras = [a for a in [t["acta"], *(t["actas_extra"] or [])] if a not in {c["acta"] for c in elegidas}]
                    extra = [c["acta"] for c in elegidas[1:]] + otras
                    notif = tc.notificacion_de(elegidas, t["servicio"])
                    que = "vista" if t["servicio"] == "vista" else "oposición"
                    texto = (f"Acta principal cambiada sola a {elegidas[0]['acta']}: es la solicitud de «{t['marca']}» con {que}. "
                             f"La {t['acta']} queda entre las otras solicitudes de la marca.")
                    cur.execute("UPDATE trabajos SET acta = %s, actas_extra = %s, notificacion = COALESCE(notificacion, %s), "
                                "modificado_en = now(), modificado_por = 'sistema' WHERE id = %s AND acta = %s",
                                (elegidas[0]["acta"], extra or None, notif, t["id"], t["acta"]))
                    if cur.rowcount:
                        cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                                    (t["id"], tc.hoy_ar(), texto))
                        _a_cartera(cur, t["cliente_id"], elegidas[0]["acta"], t["marca"], "sistema")
                res[t["id"]] = (elegidas, del_tipo, texto)
                continue
            if elegidas:
                notif = tc.notificacion_de(elegidas, t["servicio"])
                texto = tc.texto_conflicto(elegidas, motivo, t["servicio"], notif if not t["notificacion"] else None)
                cur.execute(
                    "UPDATE trabajos SET acta = %s, actas_extra = %s, notificacion = COALESCE(notificacion, %s), "
                    "modificado_en = now(), modificado_por = 'sistema' WHERE id = %s AND acta IS NULL",
                    (elegidas[0]["acta"], [c["acta"] for c in elegidas[1:]] or None, notif, t["id"]))
                if cur.rowcount:
                    cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                                (t["id"], tc.hoy_ar(), texto))
                    _a_cartera(cur, t["cliente_id"], elegidas[0]["acta"], t["marca"], "sistema")
            res[t["id"]] = (elegidas, del_tipo, texto)
        return res

    def _deteccion_periodica(conn, cur):
        global _ultima_deteccion
        if time.time() - _ultima_deteccion < CADA_DETECCION or not _candado_deteccion.acquire(blocking=False):
            return
        try:
            _ultima_deteccion = time.time()
            _detectar(cur)
            _detectar_conflictos(cur)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[seguimientos] detección de actas: {e}")
        finally:
            _candado_deteccion.release()

    @router.post("/api/trabajos/desde-alerta/{alerta_id}")
    def desde_alerta(alerta_id: int, usuario: str = Depends(verificar_login)):
        """«Hay que oponer» en una alerta de vigilancia: la alerta pasa a «Oponer» y se crea (una sola vez) el
        trabajo en «Oposición + negociación», con la marca del cliente, la marca nueva a la que nos oponemos y el
        plazo para presentar la oposición (30 días corridos desde su publicación)."""
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT id FROM trabajos WHERE alerta_id = %s", (alerta_id,))
            ya = cur.fetchone()
            cur.execute(
                f"""SELECT a.id, a.cliente_id, a.acta_cliente, a.acta_nueva, a.denominacion_nueva, a.clase_nueva,
                          cm.denominacion AS denominacion_cliente, cm.cliente_id AS cliente_marca,
                          {SQL_PUBLICACION.replace('%s', 'a.acta_nueva')} AS publicacion
                   FROM vigilancia_alertas a LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
                   WHERE a.id = %s""", (alerta_id,))
            a = cur.fetchone()
            if not a:
                raise HTTPException(status_code=404, detail="No existe esa alerta")
            cur.execute("UPDATE vigilancia_alertas SET estado = 'oponer', decidido_por = %s, decidido_en = now() "
                        "WHERE id = %s AND estado <> 'oponer'", (usuario, alerta_id))
            if ya:
                conn.commit()
                return {"id": ya["id"], "creado": False}
            cliente_id = a["cliente_id"] or a["cliente_marca"]
            if not cliente_id:
                raise HTTPException(status_code=400, detail="La alerta no tiene cliente")
            plazo = tc.plazo_para_oponerse(a["publicacion"])
            marca = a["denominacion_cliente"] or "(marca del cliente)"
            cur.execute(
                """INSERT INTO trabajos (cliente_id, marca, servicio, acta, etapa, estado, contratacion, alerta_id,
                                         acta_contraria, contraparte, plazo_presentar, alta_por, modificado_por)
                   VALUES (%s, %s, 'oposicion', %s, 0, 'activo', %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (cliente_id, marca, a["acta_cliente"], tc.hoy_ar(), alerta_id, a["acta_nueva"],
                 a["denominacion_nueva"], plazo, usuario, usuario))
            tid = cur.fetchone()["id"]
            texto = (f"Creado desde una alerta de vigilancia: nos oponemos a «{a['denominacion_nueva'] or 'marca sin nombre'}» "
                     f"(clase {a['clase_nueva'] if a['clase_nueva'] is not None else '?'}, acta {a['acta_nueva']}). "
                     + (f"Plazo para presentar la oposición: hasta el {plazo.strftime('%d/%m/%Y')}."
                        if plazo else "Todavía no salió publicada: el plazo de 30 días empieza con la publicación."))
            cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, %s)",
                        (tid, tc.hoy_ar(), texto, usuario))
            conn.commit()
        return {"id": tid, "creado": True}

    @router.get("/api/trabajos")
    def listar(estado: str = "activo", q: str = "", _: str = Depends(verificar_login)):
        cond, val = [], []
        if estado in tc.ESTADOS:
            cond.append("t.estado = %s"); val.append(estado)
        if q.strip():
            like = f"%{q.strip()}%"
            cond.append("(t.marca ILIKE %s OR c.nombre ILIKE %s OR COALESCE(t.acta, '') = %s OR COALESCE(t.notas, '') ILIKE %s)")
            val += [like, like, q.strip(), like]
        donde = (" WHERE " + " AND ".join(cond)) if cond else ""
        with conexion() as conn, rcur(conn) as cur:
            _deteccion_periodica(conn, cur)
            _sincronizar_seguro(conn, cur)
            cur.execute(SQL_LISTA + donde, val)
            hoy = tc.hoy_ar()
            filas = tc.ordenar([tc.completar(dict(f), hoy) for f in cur.fetchall()])
            _resultados_inpi(cur, filas)
            cur.execute("SELECT estado, COUNT(*) AS n FROM trabajos GROUP BY estado")
            cuentas = {f["estado"]: f["n"] for f in cur.fetchall()}
            vigilancia = _clientes_con_vigilancia(cur, q)
        return {"hoy": hoy, "servicios": tc.servicios_publicos(), "trabajos": filas,
                "cuentas": cuentas, "notion_procesos": tc.NOTION_PROCESOS, "vigilancia": vigilancia}

    def _resultados_inpi(cur, filas):
        """t["resultado_inpi"]: el último dictamen o resolución de la grilla (en el acta del trabajo o sus actas
        extra) desde la contratación, para marcarlo en la tarjeta (27/11/2026, MATEACIDO). Nunca frena la lista."""
        import notificaciones_inpi as ni
        por_acta = {}
        for t in filas:
            for a in [t.get("acta_efectiva"), *(t.get("actas_extra") or [])]:
                if a:
                    por_acta.setdefault(str(a), []).append(t)
        if not por_acta:
            return
        try:
            cur.execute("SAVEPOINT res_inpi")
            cur.execute(
                """SELECT acta, texto, detectado_en FROM cartera_novedades
                   WHERE tipo = 'movimiento' AND acta = ANY(%s)
                     AND (texto ILIKE '%%dictamen%%' OR texto ILIKE '%%resoluci%%')
                   ORDER BY detectado_en""", (list(por_acta),))
            for f in cur.fetchall():
                res = ni.resultado_inpi(f["texto"])
                if not res:
                    continue
                m = re.search(r"(\d{2}/\d{2}/\d{4})", f["texto"] or "")
                for t in por_acta.get(str(f["acta"]), []):
                    desde = t.get("contratacion")
                    if desde and f["detectado_en"].date() < tc.a_fecha(desde):
                        continue
                    t["resultado_inpi"] = {**res, "acta": f["acta"], "fecha": m.group(1) if m else None}
            cur.execute("RELEASE SAVEPOINT res_inpi")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT res_inpi")
            print(f"[seguimientos] dictámenes del INPI: {e}")

    def _clientes_con_vigilancia(cur, q=""):
        """Columna «Vigilancia marcaria» del tablero: los clientes activos que tienen la vigilancia contratada de
        verdad (Clientes → «Vigilancia contratada»), con cuántas marcas se vigilan y las alertas sin revisar."""
        try:
            cur.execute("SAVEPOINT vig")
            cond, val = "", []
            if q.strip():
                cond, val = " AND c.nombre ILIKE %s", [f"%{q.strip()}%"]
            cur.execute(
                f"""SELECT c.id, c.nombre, c.vigilancia_desde,
                          (SELECT COUNT(*) FROM cartera_marcas cm WHERE cm.cliente_id = c.id AND cm.vigilar) AS marcas,
                          (SELECT COALESCE(json_agg(json_build_object('d', cm.denominacion, 'c', cm.clase)
                                    ORDER BY cm.denominacion, cm.clase), '[]'::json)
                             FROM cartera_marcas cm WHERE cm.cliente_id = c.id AND cm.vigilar) AS marcas_lista,
                          (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.cliente_id = c.id AND a.estado = 'nueva') AS alertas_nuevas,
                          (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.cliente_id = c.id
                             AND a.estado IN ('monitorear', 'oponer')) AS alertas_en_curso
                   FROM clientes c
                   WHERE c.activo AND c.vigilancia_contratada{cond}
                   ORDER BY (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.cliente_id = c.id AND a.estado = 'nueva') DESC,
                            c.nombre""", val)
            out = cur.fetchall()
            cur.execute("RELEASE SAVEPOINT vig")
            return out
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT vig")
            print(f"[seguimientos] clientes con vigilancia: {e}")
            return []

    @router.get("/api/trabajos/{tid}")
    def ficha(tid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            _sincronizar_seguro(conn, cur, tid)
            try:   # vista/oposición: que el acta principal sea la que tiene el problema (al momento, no cada 10 min)
                cur.execute("SAVEPOINT conf_ficha")
                _detectar_conflictos(cur, solo_id=tid)
                cur.execute("RELEASE SAVEPOINT conf_ficha")
                conn.commit()
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT conf_ficha")
                print(f"[seguimientos] actas con vista/oposición (ficha {tid}): {e}")
            t = _uno(cur, tid)
            # Todas las solicitudes de la marca (una por clase), primero la que tiene la vista u oposición
            solicitudes = tc.solicitudes_de_marca(t, _solicitudes_cliente(
                cur, {"id": t["cliente_id"], "nombre": t.get("cliente"), "cuit": t.get("cliente_cuit")}))
            inpi = None
            if t["servicio"] == "registro":
                actas = tc.actas_del_trabajo(t, _cartera_de(cur, [t]))
                inpi = tc.etapa_registro(actas) or {"etapa": None, "nombre": None, "actas": []}
                pend = {m["acta"] for m in actas if not m.get("consultado_en")}
                for d in inpi["actas"]:
                    d["pendiente"] = d["acta"] in pend
            expediente = None
            if t.get("acta_efectiva"):
                cur.execute(
                    """SELECT acta, denominacion, tipo, clase, titular, fecha_presentacion, fecha_publicacion,
                              estado_tramite, fecha_concesion, numero_disposicion, fecha_vencimiento_marca,
                              tuvo_oposicion, detalle_oposicion, ultimo_movimiento, ultimo_movimiento_fecha,
                              consultado_en, agente
                       FROM cartera_marcas WHERE acta = %s""", (t["acta_efectiva"],))
                marca = cur.fetchone()
                # Las novedades de todas las actas del trabajo (la principal y las extra), con el acta a la vista
                # cuando hay más de una; los dictámenes y resoluciones van resaltados (27/11/2026).
                actas_t = [str(a) for a in [t["acta_efectiva"], *(t.get("actas_extra") or [])] if a]
                cur.execute(
                    """SELECT acta, tipo, texto, detectado_en FROM cartera_novedades
                       WHERE acta = ANY(%s) ORDER BY detectado_en DESC LIMIT 60""", (actas_t,))
                import notificaciones_inpi as ni   # el INPI carga dos veces la toma de intervención y el poder
                novs = []
                todas = cur.fetchall()
                for a in actas_t:
                    for n in ni.novedades_sin_repetir([f for f in todas if str(f["acta"]) == a]):
                        n = dict(n)
                        n["importante"] = ni.resultado_inpi(n["texto"]) if n.get("tipo") == "movimiento" else None
                        n["mostrar_acta"] = len(actas_t) > 1
                        novs.append(n)
                novs.sort(key=lambda n: n["detectado_en"], reverse=True)
                expediente = {"acta": t["acta_efectiva"], "automatica": bool(t.get("acta_auto")),
                              "en_cartera": bool(marca), "marca": marca,
                              "novedades": novs[:40],
                              # «MÁS (clase 36, acta 4790681, MIXTA)», para el botón de copiar al lado del acta
                              "para_copiar": tc.texto_solicitud(t.get("marca") or (marca or {}).get("denominacion"),
                                                                marca) if marca else None}
            cur.execute(
                "SELECT id, fecha, texto, usuario, creado_en FROM trabajos_bitacora "
                "WHERE trabajo_id = %s ORDER BY fecha DESC, id DESC", (tid,))
            bitacora = cur.fetchall()
            cur.execute(
                "SELECT id, tipo, marca, generado_en, generado_por FROM informes_viabilidad "
                "WHERE cliente_id = %s AND archivado_en IS NULL ORDER BY generado_en DESC LIMIT 50", (t["cliente_id"],))
            informes, otros_informes = tc.separar_informes(t, cur.fetchall())
        return {"trabajo": t, "expediente": expediente, "bitacora": bitacora, "inpi": inpi, "solicitudes": solicitudes,
                "informes": informes, "otros_informes": otros_informes,
                "servicios": tc.servicios_publicos(), "notion_procesos": tc.NOTION_PROCESOS}

    @router.post("/api/trabajos")
    def crear(body: TrabajoNuevo, usuario: str = Depends(verificar_login)):
        campos = _campos(body, parcial=False)
        if not campos.get("marca") or not campos.get("servicio"):
            raise HTTPException(status_code=400, detail="Faltan la marca o el servicio")
        campos.setdefault("contratacion", tc.hoy_ar())
        campos.pop("estado", None)
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT 1 FROM clientes WHERE id = %s", (campos["cliente_id"],))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="No existe ese cliente")
            cols = list(campos)
            cur.execute(
                f"INSERT INTO trabajos ({', '.join(cols)}, alta_por, modificado_por) "
                f"VALUES ({', '.join(['%s'] * len(cols))}, %s, %s) RETURNING id",
                [campos[c] for c in cols] + [usuario, usuario])
            tid = cur.fetchone()["id"]
            conn.commit()
            return {"trabajo": _uno(cur, tid)}

    @router.put("/api/trabajos/{tid}")
    def cambiar(tid: int, body: TrabajoCambios, usuario: str = Depends(verificar_login)):
        campos = _campos(body, parcial=True)
        if not campos:
            raise HTTPException(status_code=400, detail="No hay cambios")
        sets = [f"{k} = %s" for k in campos] + ["modificado_en = now()", "modificado_por = %s"]
        extra = []
        if "estado" in campos:
            sets.append("cerrado_en = CASE WHEN %s = 'cerrado' THEN now() ELSE NULL END")
            extra.append(campos["estado"])
        if "acta" in campos and not campos["acta"]:
            sets.append("actas_extra = NULL")   # se borró el acta: también las otras que se habían detectado
        if campos.get("acta") and "etapa" not in campos:
            # Se cargó el acta: si era un registro todavía sin presentar, pasa a «Marca publicada».
            sets.append("etapa = CASE WHEN servicio = 'registro' AND etapa < %s THEN %s ELSE etapa END")
            extra += [ETAPA_PRESENTADA, ETAPA_PRESENTADA]
        val = list(campos.values()) + [usuario] + extra
        with conexion() as conn, rcur(conn) as cur:
            if "cliente_id" in campos:
                cur.execute("SELECT 1 FROM clientes WHERE id = %s", (campos["cliente_id"],))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="No existe ese cliente")
            cur.execute(f"UPDATE trabajos SET {', '.join(sets)} WHERE id = %s", val + [tid])
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="No existe ese trabajo")
            if campos.get("acta"):
                cur.execute("SELECT cliente_id, marca, servicio FROM trabajos WHERE id = %s", (tid,))
                t = cur.fetchone()
                if t["servicio"] in ("registro", "vista", "oposicion"):
                    _a_cartera(cur, t["cliente_id"], campos["acta"], t["marca"], usuario)
            conn.commit()
            return {"trabajo": _uno(cur, tid)}

    @router.post("/api/trabajos/{tid}/buscar-acta")
    def buscar_acta(tid: int, usuario: str = Depends(verificar_login)):
        """Primero en la base; si no aparece segura, en vivo en el INPI por denominación. Si tampoco es
        segura, devuelve las candidatas para que se elija una en la ficha."""
        with conexion() as conn, rcur(conn) as cur:
            t = _uno(cur, tid)
            if t.get("acta"):
                return {"asignada": t["acta"], "texto": "El trabajo ya tiene acta.", "candidatas": []}
            if t["servicio"] in tc.SERVICIOS_CON_CONFLICTO:
                elegidas, del_tipo, texto = _detectar_conflictos(cur, solo_id=tid).get(tid, ([], [], None))
                conn.commit()
                que = "vista" if t["servicio"] == "vista" else "oposición"
                cands = [{"acta": c["acta"], "denominacion": c.get("denominacion"), "titular": c.get("titular"),
                          "clase": c.get("clase"), "estado": f"con {que}", "fuente": c.get("fuente"),
                          "segura": c in elegidas} for c in del_tipo]
                if elegidas:
                    return {"asignada": elegidas[0]["acta"], "texto": texto, "candidatas": cands}
                if not del_tipo:
                    texto = (f"No encontré actas de este cliente con {que} en el panel (se buscan por su CUIT, su "
                             "nombre como titular y sus marcas de Clientes → Marcas). Cargá el acta a mano en Datos.")
                else:
                    texto = f"El cliente tiene {len(del_tipo)} actas con {que} y ninguna con el nombre «{t['marca']}»: elegí la correcta."
                return {"asignada": None, "texto": texto, "candidatas": cands}
            res = _detectar(cur, solo_id=tid).get(tid)
            conn.commit()
            if res and res[0]:
                return {"asignada": res[0]["acta"], "texto": res[2], "candidatas": _publicas(res[1])}
            evaluadas_db = res[1] if res else []
            contr = tc.a_fecha(t.get("contratacion"))
            desde = (contr - tc._dt.timedelta(days=tc.DIAS_ANTES_CONTRATACION)).isoformat() if contr else ""
            try:
                r = bi.buscar_avanzada({"denominacion": t["marca"], "ingreso_desde": desde, "limit": 100})
            except bi.BuscadorError as e:
                return {"asignada": None, "texto": f"El INPI no respondió: {e}", "candidatas": _publicas(evaluadas_db),
                        "error_inpi": True}
            except Exception as e:
                return {"asignada": None, "texto": f"El INPI no respondió ({e.__class__.__name__}). Probá de nuevo en un rato.",
                        "candidatas": _publicas(evaluadas_db), "error_inpi": True}
            cand = [{"acta": f.get("acta"), "denominacion": f.get("denominacion"), "titular": f.get("titulares"),
                     "cuit": f.get("cuit"), "fecha": f.get("fecha_ingreso"), "clase": f.get("clase"),
                     "estado": f.get("estado"), "fuente": "inpi"} for f in r.get("filas") or []]
            cur.execute("SELECT nombre, cuit FROM clientes WHERE id = %s", (t["cliente_id"],))
            cliente = cur.fetchone() or {}
            elegida, evaluadas = tc.elegir_acta(t, cand + evaluadas_db, cliente, _matriculas(cur))
            if elegida:
                texto = _asignar(cur, t, elegida, evaluadas, "búsqueda en el INPI")
                conn.commit()
                return {"asignada": elegida["acta"], "texto": texto, "candidatas": _publicas(evaluadas)}
            if not evaluadas:
                texto = (f"No hay en el INPI ninguna solicitud «{t['marca']}» presentada desde el "
                         f"{(contr - tc._dt.timedelta(days=tc.DIAS_ANTES_CONTRATACION)).strftime('%d/%m/%Y') if contr else 'principio'}. "
                         "Si se presentó hoy, puede tardar un día en aparecer.")
            else:
                texto = "Hay solicitudes con ese nombre, pero ninguna es seguro que sea la nuestra: elegí la correcta."
            return {"asignada": None, "texto": texto, "candidatas": _publicas(evaluadas)}

    def _publicas(evaluadas):
        return [{k: e.get(k) for k in ("acta", "denominacion", "titular", "fecha", "clase", "estado", "fuente", "segura", "motivos")}
                for e in evaluadas][:30]

    @router.delete("/api/trabajos/{tid}")
    def borrar(tid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM trabajos WHERE id = %s", (tid,))
            conn.commit()
            return {"ok": cur.rowcount > 0}

    @router.post("/api/trabajos/{tid}/bitacora")
    def agregar_nota(tid: int, body: NotaBitacora, usuario: str = Depends(verificar_login)):
        texto = _texto(body.texto, 50000)   # notas largas (un escrito, un mail pegado): hasta 50.000 caracteres
        if not texto:
            raise HTTPException(status_code=400, detail="La nota está vacía")
        fecha = _fecha(body.fecha, "fecha") or tc.hoy_ar()
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT 1 FROM trabajos WHERE id = %s", (tid,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="No existe ese trabajo")
            cur.execute(
                "INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, %s) "
                "RETURNING id, fecha, texto, usuario, creado_en", (tid, fecha, texto, usuario))
            nota = cur.fetchone()
            # @usuario en la nota → aviso en su campanita 🔔 (26/11/2026)
            cur.execute("SELECT usuario FROM usuarios_panel WHERE activo")
            para = tc.menciones_en(texto, [r["usuario"] for r in cur.fetchall()], autor=usuario)
            for u in para:
                cur.execute("INSERT INTO trabajos_menciones (nota_id, trabajo_id, para, de) VALUES (%s, %s, %s, %s)",
                            (nota["id"], tid, u, usuario))
            cur.execute("UPDATE trabajos SET modificado_en = now(), modificado_por = %s WHERE id = %s", (usuario, tid))
            conn.commit()
            return {"nota": nota, "mencionados": para}

    @router.get("/api/trabajos-usuarios")
    def usuarios_para_mencionar(usuario: str = Depends(verificar_login)):
        """Para el autocompletar de «@» en las notas: los usuarios activos, menos el que escribe."""
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("SELECT usuario FROM usuarios_panel WHERE activo ORDER BY usuario")
            return {"usuarios": [r["usuario"] for r in cur.fetchall() if r["usuario"] != usuario], "yo": usuario}

    @router.delete("/api/trabajos/{tid}/bitacora/{bid}")
    def borrar_nota(tid: int, bid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM trabajos_bitacora WHERE id = %s AND trabajo_id = %s", (bid, tid))
            conn.commit()
            return {"ok": cur.rowcount > 0}

    return router
