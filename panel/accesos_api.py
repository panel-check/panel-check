"""
Accesos rápidos (el ⚡ del menú de arriba): búsquedas guardadas de la lista de
leads. Una búsqueda guardada es el mismo querystring de filtros que ya arma el
panel en la barra de direcciones (boletín, lead, contactado, oposición,
"últimos N días"…), así que abrirla es ir a `/?<consulta>`.

Cada acceso es personal de quien lo guardó; al guardarlo se puede marcar
"compartir con el equipo" y entonces lo ven (sin poder editarlo) las demás
personas. Solo el dueño lo renombra, lo actualiza, lo comparte/deja de
compartir o lo borra.

  GET    /api/accesos-rapidos              los míos + los compartidos por otros
  GET    /api/accesos-rapidos/cantidades   {id: cuántas marcas da hoy cada uno}
  POST   /api/accesos-rapidos              guardar uno nuevo
  PUT    /api/accesos-rapidos/{id}         renombrar / compartir / actualizar filtros (solo el dueño)
  DELETE /api/accesos-rapidos/{id}         borrar (solo el dueño)
"""

from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

MAX_POR_USUARIO = 50
MAX_NOMBRE = 60
MAX_CONSULTA = 800


def crear_tablas(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS accesos_rapidos (
            id              BIGSERIAL PRIMARY KEY,
            usuario         TEXT NOT NULL,                  -- el dueño
            nombre          TEXT NOT NULL,
            consulta        TEXT NOT NULL,                  -- querystring de filtros del panel
            compartido      BOOLEAN NOT NULL DEFAULT false, -- visible para todo el equipo
            creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
            actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_accesos_rapidos_usuario ON accesos_rapidos(usuario)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_accesos_rapidos_compartido ON accesos_rapidos(compartido) WHERE compartido"
    )


class AccesoNuevo(BaseModel):
    nombre: str
    consulta: str
    compartido: bool = False


class AccesoCambios(BaseModel):
    nombre: Optional[str] = None
    consulta: Optional[str] = None
    compartido: Optional[bool] = None


def normalizar_consulta(consulta: str, claves_permitidas: set) -> str:
    """Deja solo los filtros conocidos (más el orden), sin valores vacíos ni
    repetidos y siempre en el mismo orden, para guardar algo limpio y poder
    comparar. El offset y el límite de página no se guardan."""
    if len(consulta or "") > MAX_CONSULTA * 4:
        raise HTTPException(status_code=400, detail="La búsqueda es demasiado larga")
    valores = {}
    for clave, valor in parse_qsl((consulta or "").lstrip("?"), keep_blank_values=False):
        if clave in claves_permitidas and valor.strip() != "":
            valores[clave] = valor.strip()
    limpia = urlencode(sorted(valores.items()))
    if len(limpia) > MAX_CONSULTA:
        raise HTTPException(status_code=400, detail="La búsqueda es demasiado larga")
    return limpia


def _nombre_valido(nombre: Optional[str]) -> str:
    nombre = " ".join((nombre or "").split())
    if not nombre:
        raise HTTPException(status_code=400, detail="Poné un nombre para el acceso rápido")
    if len(nombre) > MAX_NOMBRE:
        raise HTTPException(status_code=400, detail=f"El nombre es demasiado largo (máximo {MAX_NOMBRE} letras)")
    return nombre


def crear_router(verificar_login, conexion, claves_permitidas: set, contar: Callable[[str], int]) -> APIRouter:
    """claves_permitidas: los filtros que se pueden guardar (los define
    app.filtros_marcas). contar(consulta) → cuántas marcas da esa búsqueda hoy."""
    router = APIRouter()

    def _fila(f: dict, yo: str) -> dict:
        return {
            "id": f["id"], "nombre": f["nombre"], "consulta": f["consulta"],
            "compartido": f["compartido"], "dueno": f["usuario"], "mio": f["usuario"] == yo,
        }

    def _visibles(cur, yo: str) -> list:
        cur.execute(
            """
            SELECT id, usuario, nombre, consulta, compartido
            FROM accesos_rapidos
            WHERE usuario = %s OR compartido
            ORDER BY (usuario <> %s), lower(nombre), id
            """,
            (yo, yo),
        )
        return cur.fetchall()

    @router.get("/api/accesos-rapidos")
    def listar(usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                filas = _visibles(cur, usuario)
        items = [_fila(f, usuario) for f in filas]
        return {
            "usuario": usuario,
            "propios": [i for i in items if i["mio"]],
            "compartidos": [i for i in items if not i["mio"]],
        }

    @router.get("/api/accesos-rapidos/cantidades")
    def cantidades(usuario: str = Depends(verificar_login)):
        """Cuántos resultados da hoy cada acceso visible. Se pide al abrir el
        menú; si una búsqueda falla, ese acceso queda sin número (null)."""
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                filas = _visibles(cur, usuario)
        resultado = {}
        cache = {}
        for f in filas:
            if f["consulta"] not in cache:
                try:
                    cache[f["consulta"]] = contar(f["consulta"])
                except Exception as e:  # una búsqueda rota no tiene que romper el menú
                    print(f"[accesos] no se pudo contar el acceso {f['id']}: {e}")
                    cache[f["consulta"]] = None
            resultado[str(f["id"])] = cache[f["consulta"]]
        return resultado

    @router.post("/api/accesos-rapidos")
    def crear(datos: AccesoNuevo, usuario: str = Depends(verificar_login)):
        nombre = _nombre_valido(datos.nombre)
        consulta = normalizar_consulta(datos.consulta, claves_permitidas)
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT nombre FROM accesos_rapidos WHERE usuario = %s", (usuario,))
                existentes = [f["nombre"] for f in cur.fetchall()]
                if len(existentes) >= MAX_POR_USUARIO:
                    raise HTTPException(status_code=400, detail=f"Ya tenés {MAX_POR_USUARIO} accesos rápidos: borrá alguno antes de guardar otro")
                if nombre.lower() in (n.lower() for n in existentes):
                    raise HTTPException(status_code=400, detail="Ya tenés un acceso rápido con ese nombre")
                cur.execute(
                    """
                    INSERT INTO accesos_rapidos (usuario, nombre, consulta, compartido)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, usuario, nombre, consulta, compartido
                    """,
                    (usuario, nombre, consulta, bool(datos.compartido)),
                )
                fila = cur.fetchone()
            conn.commit()
        return _fila(fila, usuario)

    @router.put("/api/accesos-rapidos/{acceso_id}")
    def cambiar(acceso_id: int, datos: AccesoCambios, usuario: str = Depends(verificar_login)):
        enviados = datos.model_fields_set
        sets, valores = [], []
        with conexion() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM accesos_rapidos WHERE id = %s", (acceso_id,))
                actual = cur.fetchone()
                if not actual:
                    raise HTTPException(status_code=404, detail="Ese acceso rápido ya no existe")
                if actual["usuario"] != usuario:
                    raise HTTPException(status_code=403, detail="Solo quien lo guardó puede modificarlo")
                if "nombre" in enviados:
                    nombre = _nombre_valido(datos.nombre)
                    cur.execute(
                        "SELECT 1 FROM accesos_rapidos WHERE usuario = %s AND lower(nombre) = lower(%s) AND id <> %s",
                        (usuario, nombre, acceso_id),
                    )
                    if cur.fetchone():
                        raise HTTPException(status_code=400, detail="Ya tenés un acceso rápido con ese nombre")
                    sets.append("nombre = %s")
                    valores.append(nombre)
                if "consulta" in enviados:
                    sets.append("consulta = %s")
                    valores.append(normalizar_consulta(datos.consulta or "", claves_permitidas))
                if "compartido" in enviados and datos.compartido is not None:
                    sets.append("compartido = %s")
                    valores.append(bool(datos.compartido))
                if not sets:
                    raise HTTPException(status_code=400, detail="No hay cambios para guardar")
                sets.append("actualizado_en = now()")
                cur.execute(
                    f"UPDATE accesos_rapidos SET {', '.join(sets)} WHERE id = %s "
                    "RETURNING id, usuario, nombre, consulta, compartido",
                    valores + [acceso_id],
                )
                fila = cur.fetchone()
            conn.commit()
        return _fila(fila, usuario)

    @router.delete("/api/accesos-rapidos/{acceso_id}")
    def borrar(acceso_id: int, usuario: str = Depends(verificar_login)):
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT usuario FROM accesos_rapidos WHERE id = %s", (acceso_id,))
                fila = cur.fetchone()
                if not fila:
                    raise HTTPException(status_code=404, detail="Ese acceso rápido ya no existe")
                if fila[0] != usuario:
                    raise HTTPException(status_code=403, detail="Solo quien lo guardó puede borrarlo")
                cur.execute("DELETE FROM accesos_rapidos WHERE id = %s", (acceso_id,))
            conn.commit()
        return {"ok": True}

    return router
