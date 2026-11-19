"""
API de la pantalla Seguimientos (/seguimientos): trabajos puntuales de cada cliente.

  GET    /api/trabajos                     tablero (estado=activo|cerrado|todos, q=texto)
  GET    /api/trabajos/{id}                ficha: trabajo + cliente + expediente + bitácora
  POST   /api/trabajos                     nuevo trabajo
  PUT    /api/trabajos/{id}                cambiar datos, etapa, estado, plazos
  DELETE /api/trabajos/{id}                borrar
  POST   /api/trabajos/{id}/bitacora       agregar una nota a la bitácora
  DELETE /api/trabajos/{id}/bitacora/{bid} borrar una nota
  POST   /api/trabajos/{id}/buscar-acta    buscar el acta en la base y, si no, en vivo en el INPI

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
    SELECT t.*, c.nombre AS cliente, c.email AS cliente_email, c.telefono AS cliente_telefono,
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
        """Los registros activos avanzan solos a la etapa que dice el INPI (nunca retroceden)."""
        cond = "servicio = 'registro' AND estado = 'activo'" + (" AND id = %s" if solo_id else "")
        cur.execute(f"SELECT id, cliente_id, marca, acta, etapa FROM trabajos WHERE {cond}", (solo_id,) if solo_id else None)
        trabajos = cur.fetchall()
        if not trabajos:
            return
        cartera_filas = _cartera_de(cur, trabajos)
        etapas = tc.POR_ID["registro"]["etapas"]
        for t in trabajos:
            info = tc.etapa_registro(tc.actas_del_trabajo(t, cartera_filas))
            if info and info["etapa"] and info["etapa"] > (t["etapa"] or 0):
                cur.execute("UPDATE trabajos SET etapa = %s, modificado_en = now(), modificado_por = 'sistema' "
                            "WHERE id = %s AND etapa < %s", (info["etapa"], t["id"], info["etapa"]))
                if cur.rowcount:
                    cur.execute("INSERT INTO trabajos_bitacora (trabajo_id, fecha, texto, usuario) VALUES (%s, %s, %s, 'sistema')",
                                (t["id"], tc.hoy_ar(), tc.texto_avance(etapas[info["etapa"] - 1], info)))

    def _sincronizar_seguro(conn, cur, solo_id=None):
        try:
            cur.execute("SAVEPOINT sinc")
            _sincronizar_registros(cur, solo_id)
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
        cond = "t.acta IS NULL AND t.estado = 'activo'" + (" AND t.id = %s" if solo_id else "")
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

    def _deteccion_periodica(conn, cur):
        global _ultima_deteccion
        if time.time() - _ultima_deteccion < CADA_DETECCION or not _candado_deteccion.acquire(blocking=False):
            return
        try:
            _ultima_deteccion = time.time()
            _detectar(cur)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[seguimientos] detección de actas: {e}")
        finally:
            _candado_deteccion.release()

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
            cur.execute("SELECT estado, COUNT(*) AS n FROM trabajos GROUP BY estado")
            cuentas = {f["estado"]: f["n"] for f in cur.fetchall()}
        return {"hoy": hoy, "servicios": tc.servicios_publicos(), "trabajos": filas,
                "cuentas": cuentas, "notion_procesos": tc.NOTION_PROCESOS}

    @router.get("/api/trabajos/{tid}")
    def ficha(tid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            _sincronizar_seguro(conn, cur, tid)
            t = _uno(cur, tid)
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
                cur.execute(
                    """SELECT tipo, texto, detectado_en FROM cartera_novedades
                       WHERE acta = %s ORDER BY detectado_en DESC LIMIT 40""", (t["acta_efectiva"],))
                expediente = {"acta": t["acta_efectiva"], "automatica": bool(t.get("acta_auto")),
                              "en_cartera": bool(marca), "marca": marca, "novedades": cur.fetchall()}
            cur.execute(
                "SELECT id, fecha, texto, usuario, creado_en FROM trabajos_bitacora "
                "WHERE trabajo_id = %s ORDER BY fecha DESC, id DESC", (tid,))
            bitacora = cur.fetchall()
            cur.execute(
                "SELECT id, tipo, marca, generado_en, generado_por FROM informes_viabilidad "
                "WHERE cliente_id = %s AND archivado_en IS NULL ORDER BY generado_en DESC LIMIT 50", (t["cliente_id"],))
            informes, otros_informes = tc.separar_informes(t, cur.fetchall())
        return {"trabajo": t, "expediente": expediente, "bitacora": bitacora, "inpi": inpi,
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
                if t["servicio"] == "registro":
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
        texto = _texto(body.texto, 4000)
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
            cur.execute("UPDATE trabajos SET modificado_en = now(), modificado_por = %s WHERE id = %s", (usuario, tid))
            conn.commit()
            return {"nota": nota}

    @router.delete("/api/trabajos/{tid}/bitacora/{bid}")
    def borrar_nota(tid: int, bid: int, _: str = Depends(verificar_login)):
        with conexion() as conn, rcur(conn) as cur:
            cur.execute("DELETE FROM trabajos_bitacora WHERE id = %s AND trabajo_id = %s", (bid, tid))
            conn.commit()
            return {"ok": cur.rowcount > 0}

    return router
