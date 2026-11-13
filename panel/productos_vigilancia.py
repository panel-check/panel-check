"""
Productos y servicios (protección y limitación) de las marcas que compara la vigilancia (13/11/2026).

El juez con IA (juez_vigilancia.py) compara también qué cubre cada marca: «SUPLEMENTOS NUTRICIONALES»
contra «SUPLEMENTOS ALIMENTICIOS PARA PERSONAS» pesa en una oposición aunque la clase sea la misma. El dato
sale del expediente del INPI (sección DATOS: PROTECCIÓN y LIMITACIÓN) y se guarda en
vigilancia_productos, una fila por acta, para no volver a pedirlo.

Solo se pide al INPI para los pares que van al juez (unos pocos por día), uno por uno con una pausa, y solo
si VIGILANCIA_CONSULTAR_INPI=1 (lo pone el workflow de la vigilancia; el panel y las pruebas no consultan).
Si el INPI bloquea, se deja de pedir en esa corrida y el juez compara con la clase, como antes.
"""

import os
import time

TABLA = """
CREATE TABLE IF NOT EXISTS vigilancia_productos (
    acta          TEXT PRIMARY KEY,
    proteccion    TEXT,
    limitacion    TEXT,
    estado        TEXT NOT NULL,          -- ok | no_existe | error
    consultado_en TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
PAUSA_SEG = 1.5
MAX_POR_CORRIDA = 900
MAX_TEXTO = 1500


def crear_tabla(cur):
    cur.execute(TABLA)


def consultar_habilitado() -> bool:
    return (os.environ.get("VIGILANCIA_CONSULTAR_INPI") or "").strip() in ("1", "true", "si", "sí")


def texto(fila) -> str:
    """Lo que ve el juez: «Toda la clase», «Excepto …» o «Solamente: …» según lo que diga el expediente."""
    if not fila or fila.get("estado") != "ok":
        return ""
    prot = (fila.get("proteccion") or "").strip()
    lim = (fila.get("limitacion") or "").strip()
    if lim:
        t = f"{prot}: {lim}" if prot else lim
    else:
        t = prot
    return t[:MAX_TEXTO] + ("…" if len(t) > MAX_TEXTO else "")


def leer(cur, actas) -> dict:
    actas = sorted({str(a) for a in actas if a})
    if not actas:
        return {}
    cur.execute("SELECT acta, proteccion, limitacion, estado FROM vigilancia_productos WHERE acta = ANY(%s)", (actas,))
    out = {}
    for r in cur.fetchall():
        d = r if isinstance(r, dict) else dict(zip(("acta", "proteccion", "limitacion", "estado"), r))
        out[d["acta"]] = d
    return out


class Consultor:
    """Pide al INPI los que faltan, de a uno, con pausa; corta si el INPI bloquea."""

    def __init__(self, log=print):
        self.log = log
        self.pedidos = 0
        self.bloqueado = False
        self._sesion = None

    def asegurar(self, cur, actas) -> dict:
        actas = sorted({str(a) for a in actas if a})
        ya = leer(cur, actas)
        faltan = [a for a in actas if a not in ya or ya[a]["estado"] == "error"]
        if faltan and consultar_habilitado() and not self.bloqueado:
            import inpi_lead  # solo acá: trae dependencias pesadas (pdfplumber)
            self._sesion = self._sesion or inpi_lead._crear_sesion()
            for a in faltan:
                if self.bloqueado or self.pedidos >= MAX_POR_CORRIDA:
                    break
                if self.pedidos:
                    time.sleep(PAUSA_SEG)
                self.pedidos += 1
                r = inpi_lead.consultar_marca_opuesta(a, s=self._sesion)
                estado = r.get("estado_consulta")
                if estado == "bloqueado":
                    self.bloqueado = True
                    self.log("[productos] el INPI bloqueó la consulta: el juez compara con la clase en lo que queda de la corrida")
                    break
                d = r.get("datos") or {}
                cur.execute(
                    """INSERT INTO vigilancia_productos (acta, proteccion, limitacion, estado, consultado_en)
                       VALUES (%s, %s, %s, %s, now())
                       ON CONFLICT (acta) DO UPDATE SET proteccion = EXCLUDED.proteccion, limitacion = EXCLUDED.limitacion,
                           estado = EXCLUDED.estado, consultado_en = now()""",
                    (a, d.get("proteccion"), d.get("limitacion"), estado if estado in ("ok", "no_existe") else "error"))
            cur.connection.commit()
            ya = leer(cur, actas)
        return ya
