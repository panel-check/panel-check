"""
API de los informes preliminares (ver informes_viabilidad.py): pestaña «Informes» de la ficha del
cliente y de la del lead, y sección Más → Informes. Un informe es de un cliente (cliente_id) o de un
lead que todavía no es cliente (lead_clave: clave del titular en el CRM); al convertirse, pasa al cliente.
El informe al cliente nuevo (formato 2) recibe acá su referencia INFPR si el JSON no la trae.

Rutas (todas con sesión):
  GET    /api/clientes/{id}/informes           informes guardados del cliente (el interno, con un resumen)
  POST   /api/clientes/{id}/informes/cliente   pegar el JSON del CLIENTE: se valida y se guarda;
                                               devuelve el id para bajar el PDF
  POST   /api/clientes/{id}/informes/interno   pegar el JSON INTERNO de la IA: se guarda tal cual
  GET    /api/informes                         todos los informes (Más → Informes), agrupados por dueño y marca;
                                               filtros q (marca, cliente/lead, INFPR, oponente o acta), falta
                                               (interno|cliente|leads|oposiciones),
                                               cliente_id y lead (clave del CRM: la solapa Informes de la ficha del lead)
  GET    /api/informes/destinos?q=             «Nuevo informe»: clientes y leads de los boletines que coinciden
  POST   /api/clientes/{id}/informes/oposicion pegar el JSON del Informe Oposición IA (informes_oposicion.py)
  POST   /api/informes/cargar                  pegar un JSON (cliente, interno u oposición; se reconoce solo) para un
                                               cliente (cliente_id) o un lead que no es cliente (lead_clave)
  GET    /api/informes/{iid}                   el JSON guardado
  GET    /api/informes/{iid}/pdf               el PDF (solo los del cliente); con ver=1 se abre en el navegador
  GET    /api/informes/{iid}/json              descargar el JSON
  DELETE /api/informes/{iid}                   borrar
"""

import json
from urllib.parse import quote

import psycopg2.extras
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

import informes_oposicion as io_
import informes_viabilidad as iv


class JsonPegado(BaseModel):
    texto: str


class JsonCarga(BaseModel):
    """Más → Informes y ficha del lead: el JSON (cliente o interno, se detecta solo) y a quién va."""
    texto: str
    cliente_id: Optional[int] = None
    lead_clave: Optional[str] = None
    tipo: Optional[str] = None  # "cliente" | "interno" | "oposicion"; sin tipo se reconoce por el JSON


TIPOS = ("cliente", "interno", "oposicion")
NOMBRE_TIPO = {"cliente": "Informe al cliente (PDF)", "interno": "Informe IA interno", "oposicion": "Informe Oposición IA"}


def tipo_de_json(d: dict) -> str:
    """Qué informe es el JSON pegado: el de oposición, el interno o el del cliente."""
    if io_.es_oposicion(d):
        return "oposicion"
    return "interno" if es_json_interno(d) else "cliente"


def es_json_interno(d: dict) -> bool:
    if d.get("tipo") == "informe_viabilidad_interno":
        return True
    if d.get("tipo") == "informe_viabilidad_cliente" or ("secciones" in d and "encabezado" in d):
        return False
    return "busqueda_inpi" in d or "caso" in d


def crear_router(verificar_login, conexion, crm=None) -> APIRouter:
    """`crm`: helpers del CRM de app.py (asegurar_claves, cte_marcas) para los informes de leads."""
    router = APIRouter()

    def rcur(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def _cliente(cur, cliente_id):
        cur.execute("SELECT id, nombre FROM clientes WHERE id = %s", (cliente_id,))
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="No existe ese cliente")
        return c

    def _informe(cur, iid):
        cur.execute("SELECT * FROM informes_viabilidad WHERE id = %s", (iid,))
        f = cur.fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="No existe ese informe")
        return f

    def _dueno(cur, cliente_id=None, lead_clave=None) -> dict:
        """{cliente_id, lead_clave, nombre}. Un lead que ya es cliente (convertido) guarda en el cliente."""
        if cliente_id:
            c = _cliente(cur, cliente_id)
            return {"cliente_id": c["id"], "lead_clave": None, "nombre": c["nombre"]}
        clave = (lead_clave or "").strip()
        if not clave:
            raise HTTPException(status_code=400, detail="Falta a quién va el informe (cliente o lead)")
        cur.execute("SELECT id, nombre FROM clientes WHERE clave_crm = %s", (clave,))
        c = cur.fetchone()
        if c:
            return {"cliente_id": c["id"], "lead_clave": None, "nombre": c["nombre"]}
        cur.execute("SELECT m.titular FROM crm_claves cc JOIN marcas m ON m.acta = cc.acta "
                    "WHERE cc.clave = %s AND coalesce(m.titular, '') <> '' LIMIT 1", (clave,))
        f = cur.fetchone()
        if not f:  # marca cargada después del último recálculo de claves: por CUIT
            cur.execute("SELECT titular FROM marcas WHERE trim(cuit) = %s AND coalesce(titular, '') <> '' LIMIT 1", (clave,))
            f = cur.fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="No se encontró ese lead")
        return {"cliente_id": None, "lead_clave": clave, "nombre": " ".join(f["titular"].split())}

    def _guardar(cur, dn, tipo, marca, datos, usuario) -> int:
        cur.execute("INSERT INTO informes_viabilidad (cliente_id, lead_clave, lead_nombre, tipo, marca, datos, generado_por) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (dn["cliente_id"], dn["lead_clave"], dn["nombre"] if dn["lead_clave"] else None,
                     tipo, marca, json.dumps(datos, ensure_ascii=False), usuario))
        return cur.fetchone()["id"]

    def _cargar(tipo, texto, usuario, cliente_id=None, lead_clave=None) -> dict:
        """Valida, asigna la referencia INFPR (cliente) o la hereda (interno) y guarda, todo en una transacción."""
        try:
            crudo = iv.leer_json(texto)
            es = tipo_de_json(crudo)
            if tipo is None:
                tipo = es
            elif tipo != es:
                raise iv.InformeInvalido(f"Ese es el JSON del «{NOMBRE_TIPO[es]}»: va en ese cuadro, no en «{NOMBRE_TIPO[tipo]}».")
            with conexion() as conn:
                with rcur(conn) as cur:
                    dn = _dueno(cur, cliente_id, lead_clave)
                    quien = iv.dueno(dn["cliente_id"], dn["lead_clave"])
                    if tipo == "cliente":
                        d = iv.normalizar_cliente(crudo, dn["nombre"])
                        if d.get("formato") == 2:  # referencia INFPR: controla duplicados o asigna la siguiente
                            iv.asignar_referencia(cur, quien, d)
                        iv.generar_pdf(d)  # que no falle recién al bajarlo
                        marca = d["encabezado"]["marca_pretendida"]
                    elif tipo == "oposicion":
                        try:
                            d = io_.normalizar(crudo)
                        except io_.OposicionInvalida as e:
                            raise iv.InformeInvalido(str(e))
                        marca = io_.marca(d)
                    else:
                        d = iv.normalizar_interno(crudo)
                        marca = iv.marca_interno(d)
                        # Si el interno no trae la referencia INFPR, toma la del informe al cliente de esa marca.
                        caso = d.get("caso") if isinstance(d.get("caso"), dict) else None
                        if caso is not None and not iv.normalizar_referencia_silenciosa(caso.get("referencia")):
                            ref = iv.referencia_de_marca(cur, quien, marca)
                            if ref:
                                caso["referencia"] = ref
                    iid = _guardar(cur, dn, tipo, marca, d, usuario)
                conn.commit()
        except iv.InformeInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))
        out = {"id": iid, "tipo": tipo, "marca": marca, "cliente_id": dn["cliente_id"], "lead_clave": dn["lead_clave"]}
        if tipo == "oposicion":
            out["resumen"] = io_.resumen(d)
        if tipo == "cliente":
            out.update(archivo=iv.nombre_archivo(d), referencia=d["encabezado"].get("referencia", ""))
        return out

    @router.get("/api/clientes/{cliente_id}/informes")
    def listar(cliente_id: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                _cliente(cur, cliente_id)
                return {"informes": listar_informes(cur, cliente_id)}

    @router.post("/api/clientes/{cliente_id}/informes/cliente")
    def guardar_cliente(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        return _cargar("cliente", body.texto, usuario, cliente_id=cliente_id)

    @router.post("/api/clientes/{cliente_id}/informes/oposicion")
    def guardar_oposicion(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        return _cargar("oposicion", body.texto, usuario, cliente_id=cliente_id)

    @router.post("/api/clientes/{cliente_id}/informes/interno")
    def guardar_interno(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        return _cargar("interno", body.texto, usuario, cliente_id=cliente_id)

    @router.post("/api/informes/cargar")
    def cargar(body: JsonCarga, usuario: str = Depends(verificar_login)):
        """Pegar un JSON (el del cliente o el interno; con `tipo` se controla que sea ese) para un cliente o un lead."""
        if not body.cliente_id and not (body.lead_clave or "").strip():
            raise HTTPException(status_code=400, detail="Elegí a quién va el informe")
        tipo = body.tipo if body.tipo in TIPOS else None
        return _cargar(tipo, body.texto, usuario, cliente_id=body.cliente_id, lead_clave=body.lead_clave)

    @router.get("/api/informes")
    def todos(q: str = "", falta: str = "", cliente_id: Optional[int] = None, lead: str = "",
              _: str = Depends(verificar_login)):
        """Más → Informes (todos) o la solapa de la ficha del lead (lead=<clave>), agrupados por dueño y marca."""
        donde, params = [], []
        if cliente_id:
            donde.append("i.cliente_id = %s"); params.append(cliente_id)
        if lead:
            donde.append("(i.lead_clave = %s OR c.clave_crm = %s)"); params += [lead, lead]
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT i.id, i.tipo, i.marca, i.datos, i.generado_por, i.generado_en, i.cliente_id, "
                            "i.lead_clave, i.lead_nombre, c.nombre AS cliente_nombre FROM informes_viabilidad i "
                            "LEFT JOIN clientes c ON c.id = i.cliente_id "
                            + ("WHERE " + " AND ".join(donde) + " " if donde else "")
                            + "ORDER BY i.generado_en DESC LIMIT 3000", params)
                filas = cur.fetchall()
        return {"grupos": agrupar_informes(filas, q, falta)}

    @router.get("/api/informes/destinos")
    def destinos(q: str = Query(..., min_length=2), _: str = Depends(verificar_login)):
        """Para «Nuevo informe»: clientes (por nombre o marca en cartera) y leads de los boletines
        (por marca o titular) que coinciden, para elegir a quién va sin convertirlo en cliente."""
        patron = f"%{q.strip()}%"
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute(
                    "SELECT c.id, c.nombre, array_remove(array_agg(DISTINCT cm.denominacion) "
                    "FILTER (WHERE cm.denominacion ILIKE %s), NULL) AS marcas "
                    "FROM clientes c LEFT JOIN cartera_marcas cm ON cm.cliente_id = c.id "
                    "WHERE c.activo AND (c.nombre ILIKE %s OR cm.denominacion ILIKE %s) "
                    "GROUP BY c.id, c.nombre ORDER BY c.nombre LIMIT 15", (patron, patron, patron))
                clientes = [dict(f) for f in cur.fetchall()]
                leads = []
                if crm is not None:
                    try:
                        crm.asegurar_claves(cur)
                        conn.commit()
                    except Exception as e:  # la búsqueda sirve igual con las claves que haya
                        conn.rollback()
                        print(f"[informes] no se pudieron recalcular las claves: {e}")
                    cur.execute(
                        f"WITH {crm.cte_marcas()} SELECT acta, clase, boletin, es_lead, titular, clave, fecha_presentacion, "
                        "COALESCE(NULLIF(denominacion_inpi, ''), denominacion) AS denominacion FROM mk "
                        "WHERE (denominacion ILIKE %s OR denominacion_inpi ILIKE %s OR titular ILIKE %s) "
                        "AND clave NOT LIKE 'ACTA %%' "
                        "ORDER BY fecha_presentacion DESC NULLS LAST LIMIT 60", (patron, patron, patron))
                    marcas = cur.fetchall()
                    claves = list({m["clave"] for m in marcas})
                    cur.execute("SELECT clave_crm, id, nombre FROM clientes WHERE clave_crm = ANY(%s)", (claves,))
                    ya_clientes = {f["clave_crm"]: {"id": f["id"], "nombre": f["nombre"]} for f in cur.fetchall()}
                    por_clave = {}
                    for m in marcas:
                        g = por_clave.setdefault(m["clave"], {"clave": m["clave"], "titular": " ".join((m["titular"] or "").split()),
                                                              "cliente": ya_clientes.get(m["clave"]), "marcas": []})
                        if len(g["marcas"]) < 6:
                            g["marcas"].append({"acta": m["acta"], "denominacion": m["denominacion"] or "", "clase": m["clase"],
                                                "boletin": m["boletin"], "es_lead": m["es_lead"]})
                    leads = list(por_clave.values())[:20]
        return {"clientes": clientes, "leads": leads}

    @router.get("/api/informes/{iid}")
    def ver(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
                cur.execute("SELECT nombre FROM clientes WHERE id = %s", (f["cliente_id"],))
                c = cur.fetchone()
        return {"id": f["id"], "tipo": f["tipo"], "marca": f["marca"], "datos": f["datos"],
                "cliente_id": f["cliente_id"], "cliente_nombre": c["nombre"] if c else "",
                "lead_clave": f.get("lead_clave"), "lead_nombre": f.get("lead_nombre") or "",
                "generado_por": f["generado_por"], "generado_en": f["generado_en"]}

    @router.get("/api/informes/{iid}/pdf")
    def pdf(iid: int, ver: bool = False, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        if f["tipo"] != "cliente":
            raise HTTPException(status_code=400, detail="Solo el informe al cliente tiene PDF")
        d = f["datos"]
        headers = {"Content-Disposition": f"{'inline' if ver else 'attachment'}; filename*=UTF-8''{quote(iv.nombre_archivo(d))}"}
        if ver:  # para verlo dentro de la página /informes/{id} (mismo criterio que los archivos de formularios)
            headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"
        return Response(content=iv.generar_pdf(d), media_type="application/pdf", headers=headers)

    @router.get("/api/informes/{iid}/json")
    def bajar_json(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        base = iv.nombre_archivo({"encabezado": {"marca_pretendida": f["marca"] or ""}}, "json")
        nombre = base.replace("Informe_Preliminar_", {"interno": "Informe_IA_interno_", "oposicion": "Informe_Oposicion_IA_"}.get(f["tipo"], "Informe_cliente_"))
        return Response(content=json.dumps(f["datos"], ensure_ascii=False, indent=2).encode("utf-8"),
                        media_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})

    @router.get("/api/informes")
    def todos(q: str = "", falta: str = "", _: str = Depends(verificar_login)):
        """Sección Más → Informes: todos los informes de todos los clientes, agrupados por cliente y marca."""
        with conexion() as conn:
            with rcur(conn) as cur:
                cur.execute("SELECT i.id, i.tipo, i.marca, i.datos, i.generado_por, i.generado_en, i.cliente_id, "
                            "c.nombre AS cliente_nombre FROM informes_viabilidad i "
                            "LEFT JOIN clientes c ON c.id = i.cliente_id ORDER BY i.generado_en DESC LIMIT 3000")
                filas = cur.fetchall()
        return {"grupos": agrupar_informes(filas, q, falta)}

    @router.delete("/api/informes/{iid}")
    def borrar(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM informes_viabilidad WHERE id = %s", (iid,))
            conn.commit()
        return {"ok": True}

    return router


def listar_informes(cur, cliente_id: int) -> list:
    """Lista para la ficha (sin el JSON completo; el interno trae su resumen)."""
    cur.execute("SELECT id, tipo, marca, datos, generado_por, generado_en FROM informes_viabilidad "
                "WHERE cliente_id = %s ORDER BY generado_en DESC LIMIT 50", (cliente_id,))
    out = []
    for f in cur.fetchall():
        d = f.pop("datos") or {}
        if f["tipo"] == "interno":
            f["resumen"] = iv.resumen_interno(d)
        elif f["tipo"] == "oposicion":
            f["resumen"] = io_.resumen(d)
        else:
            enc = d.get("encabezado") or {}
            f["fecha_informe"] = enc.get("fecha") or enc.get("fecha_informe", "")
            f["referencia"] = enc.get("referencia", "")
            f["formato"] = d.get("formato") or 1
        out.append(f)
    return out


def _norm(s) -> str:
    """Para agrupar y buscar sin que importen mayúsculas, tildes, comillas ni espacios de más."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return " ".join(t.replace("“", "").replace("”", "").replace('"', "").split())


def agrupar_informes(filas: list, q: str = "", falta: str = "") -> list:
    """Una entrada por dueño + marca + familia, con las versiones (la más nueva primero).
    Familia «preliminar»: informe al cliente + IA interno. Familia «oposicion»: los Informes Oposición IA,
    aparte porque son otro trámite. `falta`: "interno" o "cliente" deja los preliminares a los que les
    falta ese; "leads" los de leads; "oposiciones" solo las oposiciones."""
    grupos = {}
    for f in filas:  # vienen de la más nueva a la más vieja
        d = f.get("datos") or {}
        familia = "oposicion" if f["tipo"] == "oposicion" else "preliminar"
        clave = (f.get("cliente_id") or "lead:" + (f.get("lead_clave") or ""), _norm(f.get("marca")), familia)
        g = grupos.get(clave)
        if g is None:
            g = grupos[clave] = {"cliente_id": f.get("cliente_id"), "cliente_nombre": f.get("cliente_nombre") or "",
                                 "lead_clave": None if f.get("cliente_id") else f.get("lead_clave"),
                                 "lead_nombre": "" if f.get("cliente_id") else (f.get("lead_nombre") or ""),
                                 "marca": f.get("marca") or "", "ultimo": f["generado_en"], "referencia": "",
                                 "familia": familia, "cliente": [], "interno": [], "oposicion": [], "texto": ""}
        item = {"id": f["id"], "generado_en": f["generado_en"], "generado_por": f.get("generado_por") or ""}
        if f["tipo"] == "interno":
            r = iv.resumen_interno(d)
            item.update(referencia=r["referencia"] or "", identicas=r["identicas"], relevantes=len(r["relevantes"]))
        elif f["tipo"] == "oposicion":
            item.update(io_.resumen(d))
            g["texto"] += " " + " ".join(item["oponentes"] + item["actas"])
        else:
            enc = d.get("encabezado") or {}
            item.update(referencia=enc.get("referencia", "") or "", fecha_informe=enc.get("fecha") or enc.get("fecha_informe", ""),
                        formato=d.get("formato") or 1)
        g[f["tipo"]].append(item)
        if not g["referencia"] and item.get("referencia"):
            g["referencia"] = item["referencia"]
    out = list(grupos.values())
    nq = _norm(q)
    if nq:
        out = [g for g in out if nq in _norm(f'{g["marca"]} {g["cliente_nombre"]} {g["lead_nombre"]} {g["referencia"]} {g["texto"]}')]
    if falta in ("interno", "cliente"):
        out = [g for g in out if g["familia"] == "preliminar" and not g[falta]]
    elif falta == "oposiciones":
        out = [g for g in out if g["familia"] == "oposicion"]
    elif falta == "leads":  # solo los de leads que todavía no son clientes
        out = [g for g in out if g["lead_clave"]]
    return out
