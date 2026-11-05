"""
API de la pestaña «Informes» de la ficha del cliente (ver informes_viabilidad.py).
El informe al cliente nuevo (formato 2) recibe acá su referencia INFPR si el JSON no la trae.

Rutas (todas con sesión):
  GET    /api/clientes/{id}/informes           informes guardados del cliente (el interno, con un resumen)
  POST   /api/clientes/{id}/informes/cliente   pegar el JSON del CLIENTE: se valida y se guarda;
                                               devuelve el id para bajar el PDF
  POST   /api/clientes/{id}/informes/interno   pegar el JSON INTERNO de la IA: se guarda tal cual
  GET    /api/informes                         todos los informes (Más → Informes), agrupados por cliente y marca;
                                               filtros q (marca, cliente o INFPR) y falta (interno|cliente)
  GET    /api/informes/{iid}                   el JSON guardado
  GET    /api/informes/{iid}/pdf               el PDF (solo los del cliente); con ver=1 se abre en el navegador
  GET    /api/informes/{iid}/json              descargar el JSON
  DELETE /api/informes/{iid}                   borrar
"""

import json
from urllib.parse import quote

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import informes_viabilidad as iv


class JsonPegado(BaseModel):
    texto: str


def crear_router(verificar_login, conexion) -> APIRouter:
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

    def _guardar(cliente_id, tipo, marca, datos, usuario, antes=None) -> int:
        """`antes(cur)` corre en la misma transacción, antes de insertar (ej.: asignar la referencia INFPR)."""
        with conexion() as conn:
            with rcur(conn) as cur:
                _cliente(cur, cliente_id)
                if antes:
                    antes(cur)
                cur.execute("INSERT INTO informes_viabilidad (cliente_id, tipo, marca, datos, generado_por) "
                            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (cliente_id, tipo, marca, json.dumps(datos, ensure_ascii=False), usuario))
                iid = cur.fetchone()["id"]
            conn.commit()
        return iid

    @router.get("/api/clientes/{cliente_id}/informes")
    def listar(cliente_id: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                _cliente(cur, cliente_id)
                return {"informes": listar_informes(cur, cliente_id)}

    @router.post("/api/clientes/{cliente_id}/informes/cliente")
    def guardar_cliente(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                nombre = _cliente(cur, cliente_id)["nombre"]
        try:
            d = iv.normalizar_cliente(iv.leer_json(body.texto), nombre)

            def antes(cur):
                if d.get("formato") == 2:  # referencia INFPR: controla duplicados o asigna la siguiente
                    iv.asignar_referencia(cur, cliente_id, d)
                iv.generar_pdf(d)  # que no falle recién al bajarlo

            iid = _guardar(cliente_id, "cliente", d["encabezado"]["marca_pretendida"], d, usuario, antes)
        except iv.InformeInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": iid, "archivo": iv.nombre_archivo(d), "referencia": d["encabezado"].get("referencia", "")}

    @router.post("/api/clientes/{cliente_id}/informes/interno")
    def guardar_interno(cliente_id: int, body: JsonPegado, usuario: str = Depends(verificar_login)):
        try:
            d = iv.normalizar_interno(iv.leer_json(body.texto))
        except iv.InformeInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))
        marca = iv.marca_interno(d)

        def antes(cur):
            # Si el interno no trae la referencia INFPR, toma la del informe al cliente de esa marca.
            caso = d.get("caso") if isinstance(d.get("caso"), dict) else None
            if caso is not None and not iv.normalizar_referencia_silenciosa(caso.get("referencia")):
                ref = iv.referencia_de_marca(cur, cliente_id, marca)
                if ref:
                    caso["referencia"] = ref

        iid = _guardar(cliente_id, "interno", marca, d, usuario, antes)
        return {"id": iid}

    @router.get("/api/informes/{iid}")
    def ver(iid: int, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
                cur.execute("SELECT nombre FROM clientes WHERE id = %s", (f["cliente_id"],))
                c = cur.fetchone()
        return {"id": f["id"], "tipo": f["tipo"], "marca": f["marca"], "datos": f["datos"],
                "cliente_id": f["cliente_id"], "cliente_nombre": c["nombre"] if c else "",
                "generado_por": f["generado_por"], "generado_en": f["generado_en"]}

    @router.get("/api/informes/{iid}/pdf")
    def pdf(iid: int, ver: bool = False, _: str = Depends(verificar_login)):
        with conexion() as conn:
            with rcur(conn) as cur:
                f = _informe(cur, iid)
        if f["tipo"] != "cliente":
            raise HTTPException(status_code=400, detail="El informe interno no tiene PDF")
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
        nombre = base.replace("Informe_Preliminar_", "Informe_IA_interno_" if f["tipo"] == "interno" else "Informe_cliente_")
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
    """Una entrada por cliente + marca, con las versiones del informe al cliente y del interno
    (la más nueva primero). `falta`: "interno" o "cliente" deja solo las marcas a las que les falta ese."""
    grupos = {}
    for f in filas:  # vienen de la más nueva a la más vieja
        d = f.get("datos") or {}
        clave = (f.get("cliente_id"), _norm(f.get("marca")))
        g = grupos.get(clave)
        if g is None:
            g = grupos[clave] = {"cliente_id": f.get("cliente_id"), "cliente_nombre": f.get("cliente_nombre") or "",
                                 "marca": f.get("marca") or "", "ultimo": f["generado_en"], "referencia": "",
                                 "cliente": [], "interno": []}
        item = {"id": f["id"], "generado_en": f["generado_en"], "generado_por": f.get("generado_por") or ""}
        if f["tipo"] == "interno":
            r = iv.resumen_interno(d)
            item.update(referencia=r["referencia"] or "", identicas=r["identicas"], relevantes=len(r["relevantes"]))
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
        out = [g for g in out if nq in _norm(f'{g["marca"]} {g["cliente_nombre"]} {g["referencia"]}')]
    if falta in ("interno", "cliente"):
        out = [g for g in out if not g[falta]]
    return out
