"""
API de la sección Clientes (cartera + vigilancia marcaria).

Se arma con una fábrica (crear_router) para no importar app.py desde acá
(app.py es quien importa este módulo): app.py le pasa el login, la conexión
y los helpers del CRM que necesita.

Rutas:
  /api/clientes...            clientes y sus marcas
  /api/cartera/...            marcas sueltas, alertas de vigilancia, vencimientos,
                              ajustes, matrículas y "probar un nombre"
"""

import datetime as _dt
import re
from typing import List, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import cartera
import formularios_api
import formularios_core
import inpi_lead
import vigilancia_core as vig

RE_ACTA = re.compile(r"^\d{4,9}$")


class ClienteNuevo(BaseModel):
    nombre: str
    cuit: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None
    contacto: Optional[str] = None
    notas: Optional[str] = None
    origen: Optional[str] = "cartera"
    vigilancia_contratada: Optional[bool] = False
    tipo_persona: Optional[str] = None
    dni: Optional[str] = None
    nacionalidad: Optional[str] = None
    domicilio: Optional[str] = None
    localidad: Optional[str] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None
    domicilio_comercial: Optional[str] = None
    estado_civil: Optional[str] = None
    conyuge_nombre: Optional[str] = None
    conyuge_dni: Optional[str] = None


class ClienteCambios(BaseModel):
    nombre: Optional[str] = None
    cuit: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None
    contacto: Optional[str] = None
    notas: Optional[str] = None
    origen: Optional[str] = None
    vigilancia_contratada: Optional[bool] = None
    activo: Optional[bool] = None
    tipo_persona: Optional[str] = None
    dni: Optional[str] = None
    nacionalidad: Optional[str] = None
    domicilio: Optional[str] = None
    localidad: Optional[str] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None
    domicilio_comercial: Optional[str] = None
    estado_civil: Optional[str] = None
    conyuge_nombre: Optional[str] = None
    conyuge_dni: Optional[str] = None


class ActaNueva(BaseModel):
    acta: str


class BuscarTitular(BaseModel):
    cuit: Optional[str] = ""
    titular: Optional[str] = ""


class MarcaCambios(BaseModel):
    vigilar: Optional[bool] = None
    vigilar_todas_clases: Optional[bool] = None
    terminos_vigilancia: Optional[str] = None
    notas: Optional[str] = None


class Mover(BaseModel):
    cliente_id: int


class AlertaCambios(BaseModel):
    estado: Optional[str] = None
    nota: Optional[str] = None


class AjustesConfig(BaseModel):
    umbral: Optional[int] = None
    nivel_alta: Optional[int] = None
    nivel_media: Optional[int] = None
    meses_universo: Optional[int] = None


class ParClases(BaseModel):
    clase_a: int
    clase_b: int


class Palabra(BaseModel):
    palabra: str


class Probar(BaseModel):
    nombre: str
    clase: Optional[int] = None


class MatriculaNueva(BaseModel):
    matricula: str
    nombre: Optional[str] = None


class Asignar(BaseModel):
    actas: List[str]
    cliente_id: Optional[int] = None
    nuevo_cliente: Optional[ClienteNuevo] = None


class Descartar(BaseModel):
    actas: List[str]


def _solo_digitos(v) -> str:
    return re.sub(r"\D", "", v or "")


def _limpio(v, largo=300):
    v = (v or "").strip()
    if len(v) > largo:
        raise HTTPException(status_code=400, detail="Un campo de texto es demasiado largo")
    return v or None


def _validar_cuit(v) -> Optional[str]:
    d = _solo_digitos(v)
    if not d:
        return None
    if len(d) != 11:
        raise HTTPException(status_code=400, detail="El CUIT tiene que tener 11 dígitos")
    return d


def _validar_acta(a) -> str:
    a = _solo_digitos(a)
    if not RE_ACTA.match(a):
        raise HTTPException(status_code=400, detail=f"Número de acta inválido: {a or '(vacío)'}")
    return a


def crear_router(verificar_login, conexion, crm) -> APIRouter:
    """`crm` es un objeto con los helpers del CRM de app.py: asegurar_claves,
    clave_existente, actas_de_clave, aplicar_cambios_crm, usuarios."""
    router = APIRouter()

    def nueva_conexion():
        return conexion()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── Todas las marcas de la cartera (vista plana) ──────────────────
    @router.get("/api/cartera/marcas")
    def listar_marcas_cartera(q: str = "", cliente_id: Optional[int] = None, estado: str = "",
                              clase: Optional[int] = None, con_oposicion: bool = False, sin_vigilar: bool = False,
                              alertas: bool = False, vence_dias: Optional[int] = None,
                              _: str = Depends(verificar_login)):
        cond, val = ["c.activo"], []
        if q.strip():
            like = f"%{q.strip()}%"
            cond.append("(cm.acta = %s OR cm.denominacion ILIKE %s OR cm.titular ILIKE %s OR c.nombre ILIKE %s OR cm.terminos_vigilancia ILIKE %s)")
            val += [q.strip(), like, like, like, like]
        if cliente_id:
            cond.append("cm.cliente_id = %s"); val.append(cliente_id)
        if clase:
            cond.append("cm.clase = %s"); val.append(clase)
        if estado == "concedida":
            cond.append("cm.estado_tramite = 'Concedida'")
        elif estado == "denegada":
            cond.append("cm.estado_tramite = 'Denegada'")
        elif estado == "en_tramite":
            cond.append("cm.consultado_en IS NOT NULL AND COALESCE(cm.estado_tramite,'') NOT IN ('Concedida','Denegada')")
        elif estado == "pendiente":
            cond.append("cm.consultado_en IS NULL")
        if con_oposicion:
            cond.append("cm.tuvo_oposicion AND COALESCE(cm.estado_tramite,'') NOT IN ('Concedida','Denegada')")
        if sin_vigilar:
            cond.append("NOT cm.vigilar")
        if alertas:
            cond.append("EXISTS (SELECT 1 FROM vigilancia_alertas a WHERE a.acta_cliente = cm.acta AND a.estado IN ('nueva','monitorear','oponer'))")
        if vence_dias:
            cond.append("cm.fecha_vencimiento_marca BETWEEN current_date AND current_date + %s")
            val.append(int(vence_dias))
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    f"""
                    SELECT cm.acta, cm.cliente_id, c.nombre AS cliente_nombre, cm.denominacion, cm.tipo, cm.clase,
                           cm.titular, cm.estado_tramite, cm.fecha_presentacion, cm.fecha_concesion,
                           cm.fecha_vencimiento_marca, cm.tuvo_oposicion, cm.agente, cm.matricula_agente,
                           cm.vigilar, cm.vigilar_todas_clases, cm.terminos_vigilancia, cm.consultado_en,
                           cm.ultimo_movimiento, cm.ultimo_movimiento_fecha,
                           (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.acta_cliente = cm.acta
                              AND a.estado IN ('nueva','monitorear','oponer')) AS alertas_abiertas
                    FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id
                    WHERE {' AND '.join(cond)}
                    ORDER BY lower(COALESCE(cm.denominacion, '')), cm.acta
                    LIMIT 3000
                    """,
                    val,
                )
                return {"marcas": cur.fetchall()}

    # ── Resumen (chips de arriba) ─────────────────────────────────────
    @router.get("/api/cartera/resumen")
    def resumen(_: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM clientes WHERE activo) AS clientes,
                      (SELECT COUNT(*) FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id WHERE c.activo) AS marcas,
                      (SELECT COUNT(*) FROM clientes WHERE activo AND vigilancia_contratada) AS con_vigilancia,
                      (SELECT COUNT(*) FROM vigilancia_alertas WHERE estado IN ('nueva','monitorear','oponer')) AS alertas_abiertas,
                      (SELECT COUNT(*) FROM vigilancia_alertas a LEFT JOIN clientes c ON c.id = a.cliente_id
                        WHERE a.estado = 'nueva' AND a.tipo = 'similitud' AND a.nivel = 'alta') AS alertas_altas_nuevas,
                      (SELECT COUNT(*) FROM vigilancia_alertas a JOIN clientes c ON c.id = a.cliente_id
                        WHERE a.estado IN ('nueva','monitorear','oponer') AND NOT c.vigilancia_contratada) AS alertas_sin_contratar,
                      (SELECT COUNT(*) FROM vigilancia_alertas WHERE estado = 'nueva' AND tipo = 'otro_agente') AS otro_agente_nuevas,
                      (SELECT COUNT(*) FROM cartera_marcas WHERE consultado_en IS NULL) AS sin_consultar
                    """
                )
                fila = cur.fetchone()
                cur.execute("SELECT COUNT(DISTINCT acta) AS n FROM vigilancia_comparadas")
                fila["comparadas"] = cur.fetchone()["n"]
                cur.execute("SELECT max(comparada_en) AS ult FROM vigilancia_comparadas")
                fila["ultima_vigilancia"] = cur.fetchone()["ult"]
                cur.execute("SELECT COUNT(*) AS n FROM cartera_matriculas")
                fila["matriculas"] = cur.fetchone()["n"]
        return fila

    # ── Clientes ──────────────────────────────────────────────────────
    @router.get("/api/clientes")
    def listar_clientes(q: str = "", activos: bool = True, _: str = Depends(verificar_login)):
        cond, val = [], []
        if activos:
            cond.append("c.activo")
        if q.strip():
            cond.append("(c.nombre ILIKE %s OR c.cuit ILIKE %s OR c.email ILIKE %s OR EXISTS ("
                        "SELECT 1 FROM cartera_marcas m WHERE m.cliente_id = c.id AND (m.acta = %s OR m.denominacion ILIKE %s)))")
            like = f"%{q.strip()}%"
            val += [like, like, like, q.strip(), like]
        donde = ("WHERE " + " AND ".join(cond)) if cond else ""
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    f"""
                    SELECT c.*,
                      (SELECT COUNT(*) FROM cartera_marcas m WHERE m.cliente_id = c.id) AS marcas,
                      (SELECT COUNT(*) FROM cartera_marcas m WHERE m.cliente_id = c.id AND m.tuvo_oposicion
                          AND COALESCE(m.estado_tramite,'') NOT IN ('Concedida','Denegada')) AS con_oposicion,
                      (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.cliente_id = c.id
                          AND a.estado IN ('nueva','monitorear','oponer')) AS alertas_abiertas,
                      (SELECT COUNT(*) FROM cartera_marcas m WHERE m.cliente_id = c.id AND m.consultado_en IS NULL) AS sin_consultar,
                      (SELECT min(m.fecha_vencimiento_marca) FROM cartera_marcas m
                          WHERE m.cliente_id = c.id AND m.fecha_vencimiento_marca >= current_date) AS proximo_vencimiento
                    FROM clientes c {donde}
                    ORDER BY c.activo DESC, lower(c.nombre)
                    LIMIT 2000
                    """,
                    val,
                )
                return {"clientes": cur.fetchall(), "origenes": cartera.ORIGENES_CLIENTE}

    def _campos_cliente(body, parcial=False):
        enviados = body.model_fields_set if parcial else None
        out = {}

        def hay(k):
            return (k in enviados) if parcial else True

        if hay("nombre"):
            nombre = _limpio(body.nombre, 200)
            if not nombre:
                raise HTTPException(status_code=400, detail="El cliente necesita un nombre")
            out["nombre"] = nombre
        if hay("cuit"):
            out["cuit"] = _validar_cuit(body.cuit)
        if hay("email"):
            email = _limpio(body.email, 200)
            if email and "@" not in email:
                raise HTTPException(status_code=400, detail="El email no parece válido")
            out["email"] = email
        for k, largo in (("telefono", 60), ("contacto", 200), ("notas", 4000)) + tuple(
                (c, 300) for c in formularios_core.COLUMNAS_CLIENTE_NUEVAS if c != "tipo_persona"):
            if hay(k):
                out[k] = _limpio(getattr(body, k), largo)
        if hay("tipo_persona"):
            tp = (body.tipo_persona or "").strip() or None
            if tp and tp not in formularios_core.TIPO_LEGIBLE:
                raise HTTPException(status_code=400, detail="Tipo de persona inválido")
            out["tipo_persona"] = tp
        if hay("origen") and body.origen is not None:
            if body.origen not in cartera.ORIGENES_CLIENTE:
                raise HTTPException(status_code=400, detail=f"Origen desconocido: {body.origen}")
            out["origen"] = body.origen
        if hay("vigilancia_contratada") and body.vigilancia_contratada is not None:
            out["vigilancia_contratada"] = bool(body.vigilancia_contratada)
        if hay("activo") and getattr(body, "activo", None) is not None:
            out["activo"] = bool(body.activo)
        return out

    def _insertar_cliente(cur, campos, usuario):
        if campos.get("cuit"):
            cur.execute("SELECT id, nombre FROM clientes WHERE cuit = %s AND activo LIMIT 1", (campos["cuit"],))
            ya = cur.fetchone()
            if ya:
                raise HTTPException(status_code=409, detail=f"Ya hay un cliente con ese CUIT: {ya['nombre']}")
        cols = list(campos.keys())
        if campos.get("vigilancia_contratada"):
            cols.append("vigilancia_desde")
            campos = {**campos, "vigilancia_desde": cartera.hoy_ar()}
        cur.execute(
            f"INSERT INTO clientes ({', '.join(cols)}, alta_por, modificado_por) "
            f"VALUES ({', '.join(['%s'] * len(cols))}, %s, %s) RETURNING *",
            [campos[c] for c in cols] + [usuario, usuario],
        )
        return cur.fetchone()

    @router.post("/api/clientes")
    def crear_cliente(body: ClienteNuevo, usuario: str = Depends(verificar_login)):
        campos = _campos_cliente(body)
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                fila = _insertar_cliente(cur, campos, usuario)
            conn.commit()
        return {"ok": True, "cliente": fila}

    @router.post("/api/clientes/desde-lead")
    def cliente_desde_lead(clave: str = Query(...), usuario: str = Depends(verificar_login)):
        """Convierte un lead del CRM en cliente: copia sus marcas (las que son
        leads: no las que ya venían con agente) a la cartera y pasa la etapa
        del CRM a "cliente"."""
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                crm.asegurar_claves(cur)
                conn.commit()
                real = crm.clave_existente(cur, clave)
                cur.execute("SELECT * FROM clientes WHERE clave_crm = %s", (real,))
                ya = cur.fetchone()
                if ya:
                    return {"ok": True, "cliente": ya, "ya_existia": True, "marcas_sumadas": 0}
                actas = crm.actas_de_clave(cur, real)
                cur.execute(
                    "SELECT * FROM marcas WHERE acta = ANY(%s) AND es_lead IS NOT FALSE "
                    "ORDER BY fecha_presentacion DESC NULLS LAST",
                    (actas,),
                )
                marcas = cur.fetchall()
                if not marcas:
                    raise HTTPException(status_code=400, detail="Ese titular no tiene marcas propias para pasar a la cartera")
                cur.execute("SELECT telefono FROM crm_leads WHERE clave = %s", (real,))
                crm_fila = cur.fetchone() or {}
                nombres = [m["titular"] for m in marcas if (m.get("titular") or "").strip()]
                nombre = max(set(nombres), key=nombres.count) if nombres else real
                cuit = next((_solo_digitos(m["cuit"]) for m in marcas if len(_solo_digitos(m.get("cuit"))) == 11), None)
                email = next((m["email"] for m in marcas if m.get("email")), None) or \
                        next((m["email_apoderado"] for m in marcas if m.get("email_apoderado")), None)
                cliente = None
                if cuit:
                    cur.execute("SELECT * FROM clientes WHERE cuit = %s AND activo ORDER BY id LIMIT 1", (cuit,))
                    cliente = cur.fetchone()
                if cliente:
                    cur.execute(
                        "UPDATE clientes SET clave_crm = COALESCE(clave_crm, %s), convertido_en = COALESCE(convertido_en, now()), "
                        "modificado_en = now(), modificado_por = %s WHERE id = %s RETURNING *",
                        (real, usuario, cliente["id"]),
                    )
                    cliente = cur.fetchone()
                else:
                    cur.execute(
                        "INSERT INTO clientes (nombre, cuit, email, telefono, origen, clave_crm, convertido_en, alta_por, modificado_por) "
                        "VALUES (%s, %s, %s, %s, 'sistema', %s, now(), %s, %s) RETURNING *",
                        (nombre.strip(), cuit, email, crm_fila.get("telefono"), real, usuario, usuario),
                    )
                    cliente = cur.fetchone()
                sumadas = 0
                for m in marcas:
                    if cartera.alta_marca_desde_fila(cur, cliente["id"], m, usuario, "lead"):
                        sumadas += 1
                crm.aplicar_cambios_crm(cur, real, {"etapa": "cliente"}, usuario, "se convirtió en cliente")
            conn.commit()
        return {"ok": True, "cliente": cliente, "ya_existia": False, "marcas_sumadas": sumadas}

    @router.get("/api/clientes/{cliente_id}")
    def ficha_cliente(cliente_id: int, _: str = Depends(verificar_login)):
        hoy = cartera.hoy_ar()
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM clientes WHERE id = %s", (cliente_id,))
                cliente = cur.fetchone()
                if not cliente:
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                cur.execute(
                    "SELECT cm.*, (SELECT COUNT(*) FROM vigilancia_alertas a WHERE a.acta_cliente = cm.acta "
                    "AND a.estado IN ('nueva','monitorear','oponer')) AS alertas_abiertas "
                    "FROM cartera_marcas cm WHERE cm.cliente_id = %s "
                    "ORDER BY cm.fecha_presentacion DESC NULLS LAST, cm.acta DESC",
                    (cliente_id,),
                )
                marcas = cur.fetchall()
                cur.execute(
                    "SELECT id, acta, tipo, texto, detectado_en FROM cartera_novedades WHERE cliente_id = %s "
                    "ORDER BY detectado_en DESC LIMIT 60",
                    (cliente_id,),
                )
                novedades = cur.fetchall()
                alertas = _alertas(cur, {"cliente_id": cliente_id}, limite=200)
                formularios = formularios_api.respuestas_de(cur, "cliente_id = %s", (cliente_id,))
        plazos = []
        for m in marcas:
            m["cliente_nombre"] = cliente["nombre"]
            plazos += cartera.plazos_marca_cartera(m, hoy)
        plazos += [p for p in (cartera.plazo_oposicion_alerta(a, hoy) for a in alertas
                               if a["estado"] in cartera.ESTADOS_ALERTA_ABIERTOS and a["tipo"] == "similitud") if p]
        plazos.sort(key=lambda p: p["fecha"])
        return {"cliente": cliente, "marcas": marcas, "novedades": novedades, "alertas": alertas,
                "plazos": plazos, "formularios": formularios, "tipos_persona": formularios_core.TIPO_LEGIBLE,
                "origenes": cartera.ORIGENES_CLIENTE, "estados_alerta": cartera.ESTADOS_ALERTA}

    @router.put("/api/clientes/{cliente_id}")
    def actualizar_cliente(cliente_id: int, body: ClienteCambios, usuario: str = Depends(verificar_login)):
        campos = _campos_cliente(body, parcial=True)
        if not campos:
            raise HTTPException(status_code=400, detail="No hay cambios para guardar")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT * FROM clientes WHERE id = %s FOR UPDATE", (cliente_id,))
                actual = cur.fetchone()
                if not actual:
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                if campos.get("cuit") and campos["cuit"] != actual["cuit"]:
                    cur.execute("SELECT nombre FROM clientes WHERE cuit = %s AND activo AND id <> %s LIMIT 1",
                                (campos["cuit"], cliente_id))
                    otro = cur.fetchone()
                    if otro:
                        raise HTTPException(status_code=409, detail=f"Ya hay un cliente con ese CUIT: {otro['nombre']}")
                if campos.get("vigilancia_contratada") and not actual["vigilancia_contratada"]:
                    campos["vigilancia_desde"] = cartera.hoy_ar()
                sets = [f"{k} = %s" for k in campos] + ["modificado_en = now()", "modificado_por = %s"]
                cur.execute(f"UPDATE clientes SET {', '.join(sets)} WHERE id = %s RETURNING *",
                            list(campos.values()) + [usuario, cliente_id])
                fila = cur.fetchone()
            conn.commit()
        return {"ok": True, "cliente": fila}

    @router.delete("/api/clientes/{cliente_id}")
    def borrar_cliente(cliente_id: int, _: str = Depends(verificar_login)):
        """Borra el cliente y sus marcas de la cartera (las alertas quedan en
        el historial, sin cliente)."""
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("DELETE FROM clientes WHERE id = %s RETURNING id", (cliente_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                cur.execute("UPDATE vigilancia_alertas SET estado = 'descartada', nota = COALESCE(nota, 'cliente borrado') "
                            "WHERE cliente_id = %s AND estado IN ('nueva','monitorear','oponer')", (cliente_id,))
            conn.commit()
        return {"ok": True}

    # ── Marcas de un cliente ──────────────────────────────────────────
    @router.post("/api/clientes/{cliente_id}/marcas")
    def sumar_marca(cliente_id: int, body: ActaNueva, usuario: str = Depends(verificar_login)):
        """Suma un acta a la cartera del cliente consultando INPI. Si INPI
        bloquea o falla, la marca se guarda igual como pendiente y el
        seguimiento automático la completa. Si el acta no existe, no guarda."""
        acta = _validar_acta(body.acta)
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT id FROM clientes WHERE id = %s", (cliente_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="No existe ese cliente")
                cur.execute("SELECT cm.cliente_id, c.nombre FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id "
                            "WHERE cm.acta = %s", (acta,))
                ya = cur.fetchone()
                if ya:
                    if ya["cliente_id"] == cliente_id:
                        raise HTTPException(status_code=409, detail=f"El acta {acta} ya está en este cliente")
                    raise HTTPException(status_code=409, detail=f"El acta {acta} ya está cargada en el cliente {ya['nombre']}")
        info = inpi_lead.consultar_expediente(acta)
        estado = info.get("estado_consulta")
        if estado == "no_existe":
            raise HTTPException(status_code=404, detail=f"INPI no devuelve el acta {acta}: revisá el número")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cartera.alta_marca_cartera(cur, cliente_id, acta, info, usuario, "acta")
                cur.execute("UPDATE clientes SET modificado_en = now(), modificado_por = %s WHERE id = %s", (usuario, cliente_id))
                cur.execute("SELECT * FROM cartera_marcas WHERE acta = %s", (acta,))
                fila = cur.fetchone()
                _cliente_sin_cuit_hereda(cur, cliente_id, fila)
            conn.commit()
        return {"ok": True, "estado_consulta": estado, "marca": fila,
                "aviso": None if estado == "ok" else "INPI no respondió: el acta quedó cargada y se completa sola en la próxima revisión"}

    def _cliente_sin_cuit_hereda(cur, cliente_id, marca):
        """Un cliente cargado solo con el nombre toma el CUIT de su primera marca."""
        cuit = _solo_digitos(marca.get("cuit")) if marca else ""
        if len(cuit) == 11:
            cur.execute("UPDATE clientes SET cuit = %s WHERE id = %s AND cuit IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM clientes x WHERE x.cuit = %s)", (cuit, cliente_id, cuit))

    @router.post("/api/clientes/{cliente_id}/buscar-titular")
    def buscar_titular(cliente_id: int, body: BuscarTitular, _: str = Depends(verificar_login)):
        """Busca en INPI (webservice) las marcas de un CUIT o un nombre de
        titular, para sumarlas de a varias. Marca cuáles ya están en la cartera."""
        cuit = _solo_digitos(body.cuit)
        titular = (body.titular or "").strip()
        if not cuit and len(titular) < 4:
            raise HTTPException(status_code=400, detail="Poné un CUIT (11 dígitos) o al menos 4 letras del titular")
        resultados = inpi_lead.consultar_titular_ws(cuit=cuit, titular=titular)
        actas = [str(r.get("Acta") or "").strip() for r in resultados if r.get("Acta")]
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT cm.acta, cm.cliente_id, c.nombre FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id "
                            "WHERE cm.acta = ANY(%s)", (actas,))
                en = {r["acta"]: r for r in cur.fetchall()}
        out = []
        for r in resultados:
            acta = str(r.get("Acta") or "").strip()
            if not acta:
                continue
            dueño = en.get(acta)
            out.append({
                "acta": acta, "denominacion": r.get("Denominacion"), "clase": r.get("Clase"), "tipo": r.get("Tipo_Marca"),
                "titulares": r.get("Titulares"), "fecha_ingreso": r.get("Fecha_Ingreso"),
                "en_cartera": bool(dueño), "cliente_id": dueño["cliente_id"] if dueño else None,
                "cliente": dueño["nombre"] if dueño else None,
            })
        return {"resultados": out, "total": len(out)}

    # ── Una marca de la cartera ───────────────────────────────────────
    @router.put("/api/cartera/marcas/{acta}")
    def editar_marca(acta: str, body: MarcaCambios, _: str = Depends(verificar_login)):
        enviados = body.model_fields_set
        sets, val = [], []
        if "vigilar" in enviados and body.vigilar is not None:
            sets.append("vigilar = %s"); val.append(bool(body.vigilar))
        if "vigilar_todas_clases" in enviados and body.vigilar_todas_clases is not None:
            sets.append("vigilar_todas_clases = %s"); val.append(bool(body.vigilar_todas_clases))
        if "terminos_vigilancia" in enviados:
            t = _limpio(body.terminos_vigilancia, 500)
            sets.append("terminos_vigilancia = %s"); val.append(t)
        if "notas" in enviados:
            sets.append("notas = %s"); val.append(_limpio(body.notas, 2000))
        if not sets:
            raise HTTPException(status_code=400, detail="No hay cambios para guardar")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(f"UPDATE cartera_marcas SET {', '.join(sets)} WHERE acta = %s RETURNING *", val + [acta])
                fila = cur.fetchone()
                if not fila:
                    raise HTTPException(status_code=404, detail="Esa marca no está en la cartera")
            conn.commit()
        return {"ok": True, "marca": fila}

    @router.delete("/api/cartera/marcas/{acta}")
    def sacar_marca(acta: str, _: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("DELETE FROM cartera_marcas WHERE acta = %s RETURNING acta", (acta,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Esa marca no está en la cartera")
            conn.commit()
        return {"ok": True}

    @router.post("/api/cartera/marcas/{acta}/mover")
    def mover_marca(acta: str, body: Mover, usuario: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT id FROM clientes WHERE id = %s", (body.cliente_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="No existe el cliente de destino")
                cur.execute("UPDATE cartera_marcas SET cliente_id = %s WHERE acta = %s RETURNING acta", (body.cliente_id, acta))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Esa marca no está en la cartera")
                cur.execute("UPDATE vigilancia_alertas SET cliente_id = %s WHERE acta_cliente = %s", (body.cliente_id, acta))
                cur.execute("UPDATE cartera_novedades SET cliente_id = %s WHERE acta = %s", (body.cliente_id, acta))
            conn.commit()
        return {"ok": True}

    @router.post("/api/cartera/marcas/{acta}/actualizar")
    def actualizar_marca(acta: str, _: str = Depends(verificar_login)):
        """Vuelve a consultar el expediente en INPI ahora mismo."""
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT acta FROM cartera_marcas WHERE acta = %s", (acta,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Esa marca no está en la cartera")
        info = inpi_lead.consultar_expediente(acta)
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                novedades = cartera.aplicar_expediente(cur, acta, info)
                cur.execute("SELECT * FROM cartera_marcas WHERE acta = %s", (acta,))
                fila = cur.fetchone()
            conn.commit()
        return {"ok": info.get("estado_consulta") == "ok", "estado_consulta": info.get("estado_consulta"),
                "novedades": [t for _, t in novedades], "marca": fila,
                "aviso": None if info.get("estado_consulta") == "ok" else "INPI no respondió bien: probá de nuevo en unos minutos"}

    # ── Alertas de vigilancia ─────────────────────────────────────────
    def _alertas(cur, filtros: dict, limite=500):
        cond, val = [], []
        if filtros.get("estado") == "abiertas":
            cond.append("a.estado IN ('nueva','monitorear','oponer')")
        elif filtros.get("estado"):
            cond.append("a.estado = %s"); val.append(filtros["estado"])
        if filtros.get("cliente_id"):
            cond.append("a.cliente_id = %s"); val.append(filtros["cliente_id"])
        if filtros.get("nivel"):
            cond.append("a.nivel = %s"); val.append(filtros["nivel"])
        if filtros.get("tipo"):
            cond.append("a.tipo = %s"); val.append(filtros["tipo"])
        if filtros.get("sin_contratar"):
            cond.append("NOT COALESCE(c.vigilancia_contratada, false)")
        donde = ("WHERE " + " AND ".join(cond)) if cond else ""
        cur.execute(
            f"""
            SELECT a.*, c.nombre AS cliente_nombre, COALESCE(c.vigilancia_contratada, false) AS vigilancia_contratada,
                   cm.denominacion AS denominacion_cliente, cm.clase AS clase_cliente,
                   -- fecha de publicación de la marca nueva: se calcula al momento (puede haberse publicado después)
                   (SELECT CASE WHEN m.boletin IS NULL THEN m.fecha_publicacion
                                ELSE COALESCE(m.fecha_publicacion, b.fecha, b.procesado_en::date) END
                      FROM marcas m LEFT JOIN boletines b ON b.numero = m.boletin WHERE m.acta = a.acta_nueva) AS publicacion_nueva
            FROM vigilancia_alertas a
            LEFT JOIN clientes c ON c.id = a.cliente_id
            LEFT JOIN cartera_marcas cm ON cm.acta = a.acta_cliente
            {donde}
            ORDER BY (a.estado IN ('nueva','monitorear','oponer')) DESC,
                     CASE a.nivel WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, a.creada_en DESC
            LIMIT {int(limite)}
            """,
            val,
        )
        filas = cur.fetchall()
        hoy = cartera.hoy_ar()
        for f in filas:
            p = cartera.plazo_oposicion_alerta(f, hoy) if f["tipo"] == "similitud" else None
            f["plazo_oposicion"] = p["fecha"] if p else None
            f["dias_oposicion"] = p["dias"] if p else None
            f["urgencia"] = p["urgencia"] if p else None
        return filas

    @router.get("/api/cartera/alertas")
    def listar_alertas(estado: str = "abiertas", cliente_id: Optional[int] = None, nivel: str = "", tipo: str = "",
                       sin_contratar: bool = False, _: str = Depends(verificar_login)):
        if estado and estado != "abiertas" and estado not in cartera.ESTADOS_ALERTA:
            raise HTTPException(status_code=400, detail=f"Estado desconocido: {estado}")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                filas = _alertas(cur, {"estado": estado, "cliente_id": cliente_id, "nivel": nivel, "tipo": tipo,
                                       "sin_contratar": sin_contratar})
        return {"alertas": filas, "estados": cartera.ESTADOS_ALERTA}

    @router.post("/api/cartera/alertas/{alerta_id}")
    def decidir_alerta(alerta_id: int, body: AlertaCambios, usuario: str = Depends(verificar_login)):
        enviados = body.model_fields_set
        sets, val = [], []
        if "estado" in enviados:
            if body.estado not in cartera.ESTADOS_ALERTA:
                raise HTTPException(status_code=400, detail=f"Estado desconocido: {body.estado}")
            sets += ["estado = %s", "decidido_por = %s", "decidido_en = now()"]
            val += [body.estado, usuario]
        if "nota" in enviados:
            sets.append("nota = %s"); val.append(_limpio(body.nota, 1000))
        if not sets:
            raise HTTPException(status_code=400, detail="No hay cambios para guardar")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(f"UPDATE vigilancia_alertas SET {', '.join(sets)} WHERE id = %s RETURNING id", val + [alerta_id])
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="No existe esa alerta")
            conn.commit()
        return {"ok": True}

    # ── Vencimientos y plazos de toda la cartera ──────────────────────
    @router.get("/api/cartera/agenda")
    def agenda(_: str = Depends(verificar_login)):
        hoy = cartera.hoy_ar()
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    "SELECT cm.*, c.nombre AS cliente_nombre FROM cartera_marcas cm JOIN clientes c ON c.id = cm.cliente_id "
                    "WHERE c.activo"
                )
                marcas = cur.fetchall()
                alertas = _alertas(cur, {"estado": "abiertas", "tipo": "similitud"}, limite=1000)
        plazos = []
        for m in marcas:
            plazos += cartera.plazos_marca_cartera(m, hoy)
        plazos += [p for p in (cartera.plazo_oposicion_alerta(a, hoy) for a in alertas) if p]
        plazos = [p for p in plazos if p["dias"] <= 400]
        plazos.sort(key=lambda p: (p["fecha"], p["acta"] or ""))
        return {"plazos": plazos, "hoy": hoy}

    # ── Ajustes de la vigilancia ──────────────────────────────────────
    @router.get("/api/cartera/ajustes")
    def ver_ajustes(_: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                conf = cartera.leer_config(cur)
                cur.execute("SELECT clase_a, clase_b FROM vigilancia_clases_relacionadas ORDER BY clase_a, clase_b")
                pares = cur.fetchall()
                cur.execute("SELECT palabra FROM vigilancia_palabras_genericas ORDER BY palabra")
                palabras = [r["palabra"] for r in cur.fetchall()]
        return {"config": conf, "clases_relacionadas": pares, "palabras_genericas": palabras}

    @router.put("/api/cartera/ajustes")
    def guardar_ajustes(body: AjustesConfig, _: str = Depends(verificar_login)):
        valores = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
        if not valores:
            raise HTTPException(status_code=400, detail="No hay cambios para guardar")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                conf = cartera.leer_config(cur)
                conf.update({k: int(v) for k, v in valores.items()})
                u, a, m, meses = (int(conf["umbral"]), int(conf["nivel_alta"]), int(conf["nivel_media"]), int(conf["meses_universo"]))
                if not (40 <= u <= 100 and 40 <= m <= 100 and 40 <= a <= 100):
                    raise HTTPException(status_code=400, detail="Los puntajes van de 40 a 100")
                if not (u <= m <= a):
                    raise HTTPException(status_code=400, detail="Tiene que cumplirse: umbral ≤ nivel medio ≤ nivel alto")
                if not (1 <= meses <= 60):
                    raise HTTPException(status_code=400, detail="La antigüedad va de 1 a 60 meses")
                for k, v in valores.items():
                    cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES (%s, %s) "
                                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor", (k, str(int(v))))
            conn.commit()
        return {"ok": True}

    @router.post("/api/cartera/ajustes/clases")
    def agregar_par(body: ParClases, _: str = Depends(verificar_login)):
        a, b = sorted((body.clase_a, body.clase_b))
        if a == b or not (1 <= a <= 45 and 1 <= b <= 45):
            raise HTTPException(status_code=400, detail="Dos clases distintas entre 1 y 45")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("INSERT INTO vigilancia_clases_relacionadas (clase_a, clase_b) VALUES (%s, %s) ON CONFLICT DO NOTHING", (a, b))
            conn.commit()
        return {"ok": True}

    @router.delete("/api/cartera/ajustes/clases")
    def quitar_par(clase_a: int, clase_b: int, _: str = Depends(verificar_login)):
        a, b = sorted((clase_a, clase_b))
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("DELETE FROM vigilancia_clases_relacionadas WHERE clase_a = %s AND clase_b = %s", (a, b))
            conn.commit()
        return {"ok": True}

    @router.post("/api/cartera/ajustes/palabras")
    def agregar_palabra(body: Palabra, _: str = Depends(verificar_login)):
        p = (body.palabra or "").strip().upper()
        if not p or len(p) > 40 or " " in p:
            raise HTTPException(status_code=400, detail="Una sola palabra (hasta 40 letras)")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("INSERT INTO vigilancia_palabras_genericas (palabra) VALUES (%s) ON CONFLICT DO NOTHING", (p,))
            conn.commit()
        return {"ok": True}

    @router.delete("/api/cartera/ajustes/palabras")
    def quitar_palabra(palabra: str, _: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("DELETE FROM vigilancia_palabras_genericas WHERE palabra = %s", (palabra.strip().upper(),))
            conn.commit()
        return {"ok": True}

    @router.post("/api/cartera/probar")
    def probar_nombre(body: Probar, _: str = Depends(verificar_login)):
        """Qué solicitudes conocidas se parecen a un nombre (para probar una
        marca nueva antes de presentarla, o entender cómo puntúa el sistema)."""
        nombre = (body.nombre or "").strip()
        if len(nombre) < 2:
            raise HTTPException(status_code=400, detail="Escribí un nombre")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                filas = vig.buscar_parecidas(cur, nombre, body.clase)
        return {"resultados": filas, "total": len(filas)}

    # ── Matrículas del estudio y propuestas ───────────────────────────
    @router.get("/api/cartera/matriculas")
    def listar_matriculas(_: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT matricula, nombre, alta_en FROM cartera_matriculas ORDER BY matricula")
                return {"matriculas": cur.fetchall()}

    @router.post("/api/cartera/matriculas")
    def agregar_matricula(body: MatriculaNueva, _: str = Depends(verificar_login)):
        mat = re.sub(r"\s+", "", body.matricula or "")
        if not re.match(r"^\d{1,8}$", mat):
            raise HTTPException(status_code=400, detail="La matrícula es un número")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("INSERT INTO cartera_matriculas (matricula, nombre) VALUES (%s, %s) "
                            "ON CONFLICT (matricula) DO UPDATE SET nombre = COALESCE(EXCLUDED.nombre, cartera_matriculas.nombre)",
                            (mat, _limpio(body.nombre, 100)))
            conn.commit()
        return {"ok": True}

    @router.delete("/api/cartera/matriculas/{matricula}")
    def borrar_matricula(matricula: str, _: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("DELETE FROM cartera_matriculas WHERE matricula = %s", (matricula,))
            conn.commit()
        return {"ok": True}

    @router.get("/api/cartera/por-matricula")
    def por_matricula(_: str = Depends(verificar_login)):
        """Solicitudes presentadas con una matrícula del estudio que todavía no
        están en la cartera, agrupadas por titular (CUIT o nombre)."""
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                mats = vig.cargar_ajustes(cur)["matriculas"]
                filas = vig.candidatas_por_matricula(cur, mats)
                mapa = vig._cuits_clientes(cur)
                cur.execute("SELECT id, nombre FROM clientes WHERE activo")
                nombres = {r["id"]: r["nombre"] for r in cur.fetchall()}
        grupos = {}
        for f in filas:
            cuit = _solo_digitos(f.get("cuit"))
            clave = cuit or ("N:" + re.sub(r"\s+", " ", (f.get("titular") or "").strip().upper()))
            g = grupos.setdefault(clave, {"clave": clave, "cuit": cuit or None, "titular": f.get("titular"), "marcas": [],
                                          "clientes_posibles": []})
            g["marcas"].append({k: f.get(k) for k in ("acta", "denominacion_inpi", "clase", "tipo", "matricula_agente",
                                                      "fecha_presentacion", "fecha_publicacion", "estado_tramite", "boletin", "fuente")})
            if cuit and mapa.get(cuit):
                g["clientes_posibles"] = [{"id": i, "nombre": nombres.get(i)} for i in sorted(mapa[cuit])]
        lista = sorted(grupos.values(), key=lambda g: (-len(g["marcas"]), g["titular"] or ""))
        return {"grupos": lista, "matriculas": sorted(mats), "total_marcas": len(filas)}

    @router.post("/api/cartera/por-matricula/asignar")
    def asignar_por_matricula(body: Asignar, usuario: str = Depends(verificar_login)):
        actas = [_validar_acta(a) for a in body.actas][:500]
        if not actas:
            raise HTTPException(status_code=400, detail="No hay actas para sumar")
        if bool(body.cliente_id) == bool(body.nuevo_cliente):
            raise HTTPException(status_code=400, detail="Elegí un cliente existente o cargá uno nuevo")
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                if body.nuevo_cliente:
                    cliente = _insertar_cliente(cur, _campos_cliente(body.nuevo_cliente), usuario)
                else:
                    cur.execute("SELECT * FROM clientes WHERE id = %s", (body.cliente_id,))
                    cliente = cur.fetchone()
                    if not cliente:
                        raise HTTPException(status_code=404, detail="No existe ese cliente")
                mats = vig.cargar_ajustes(cur)["matriculas"]
                candidatas = {f["acta"]: f for f in vig.candidatas_por_matricula(cur, mats)}
                sumadas = 0
                for a in actas:
                    fila = candidatas.get(a)
                    if fila and cartera.alta_marca_desde_fila(cur, cliente["id"], fila, usuario, "matricula"):
                        sumadas += 1
                cur.execute("SELECT cuit FROM cartera_marcas WHERE cliente_id = %s AND cuit IS NOT NULL LIMIT 1", (cliente["id"],))
                primera = cur.fetchone()
                if primera:
                    _cliente_sin_cuit_hereda(cur, cliente["id"], primera)
            conn.commit()
        return {"ok": True, "cliente": cliente, "sumadas": sumadas}

    @router.post("/api/cartera/por-matricula/descartar")
    def descartar_por_matricula(body: Descartar, usuario: str = Depends(verificar_login)):
        actas = [_validar_acta(a) for a in body.actas][:1000]
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                for a in actas:
                    cur.execute("INSERT INTO cartera_descartes (acta, descartado_por) VALUES (%s, %s) ON CONFLICT DO NOTHING", (a, usuario))
            conn.commit()
        return {"ok": True, "descartadas": len(actas)}

    # ── Para la ficha del CRM ─────────────────────────────────────────
    @router.get("/api/cartera/de-lead")
    def cliente_de_lead(clave: str = Query(...), _: str = Depends(verificar_login)):
        with nueva_conexion() as conn:
            with rcur(conn) as cur:
                return {"cliente": cliente_por_clave(cur, clave)}

    return router


def cliente_por_clave(cur, clave: str):
    """El cliente ligado a una clave del CRM (por conversión del lead o porque
    el CUIT coincide), o None. Lo usa la ficha del CRM."""
    cur.execute("SELECT id, nombre, vigilancia_contratada FROM clientes WHERE clave_crm = %s", (clave,))
    fila = cur.fetchone()
    if not fila and re.match(r"^\d{11}$", clave or ""):
        cur.execute("SELECT id, nombre, vigilancia_contratada FROM clientes WHERE cuit = %s AND activo ORDER BY id LIMIT 1", (clave,))
        fila = cur.fetchone()
    return dict(fila) if fila else None
