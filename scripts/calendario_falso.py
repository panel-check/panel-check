"""
Base en memoria para probar el calendario interno (calendario_core.py) sin Postgres.

Entiende solo los pedidos que hacen crear/editar/borrar/marcar hecho/postergar y el pase
automático de recordatorios sobre la tabla calendario_eventos. Se usa así:

    base = BaseCalendario()
    with base.parche():                       # reemplaza execute_batch de calendario_core
        nuevo = cc.crear_evento(base.conexion, datos, "marcos")
    base.filas[nuevo]                         # la fila guardada
"""

import contextlib
import itertools


class _Cursor:
    def __init__(self, base, dic):
        self.base, self.dic = base, dic
        self._resp, self.rowcount = [], 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _fila(self, f, campos=None):
        if self.dic:
            return dict(f) if campos is None else {c: f[c] for c in campos}
        return tuple(f[c] for c in campos)

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        b = self.base
        b.sqls.append((s, params))
        self._resp, self.rowcount = [], 0
        if s.startswith("INSERT INTO calendario_eventos"):
            p = dict(params)
            existente = next((f for f in b.filas.values()
                              if f["calendar_id"] == p["calendar_id"] and f["google_id"] == p["google_id"]), None)
            if existente:
                existente.update(p)
            else:
                p["id"] = next(b._ids)
                b.filas[p["id"]] = p
            self.rowcount = 1
        elif "FROM marcas WHERE acta = ANY" in s:
            self._resp = [(n,) for n in b.actas_reales if n in params["n"]]
        elif s.startswith("SELECT id FROM calendario_eventos WHERE calendar_id = %s AND google_id = %s"):
            cal, gid = params
            self._resp = [self._fila(f, ["id"]) for f in b.filas.values() if f["calendar_id"] == cal and f["google_id"] == gid]
        elif s.startswith("SELECT * FROM calendario_eventos WHERE id = %s AND calendar_id = %s"):
            i, cal = params
            f = b.filas.get(i)
            self._resp = [dict(f)] if f and f["calendar_id"] == cal else []
        elif s.startswith("SELECT id, tipo, hecho, inicio, fin FROM calendario_eventos WHERE id = %s AND calendar_id = %s"):
            i, cal = params
            f = b.filas.get(i)
            self._resp = [self._fila(f, ["id", "tipo", "hecho", "inicio", "fin"])] if f and f["calendar_id"] == cal else []
        elif s.startswith("SELECT id, inicio, fin FROM calendario_eventos WHERE calendar_id = %s AND tipo = %s AND NOT hecho AND inicio < %s"):
            cal, tipo, desde = params
            filas = sorted((f for f in b.filas.values()
                            if f["calendar_id"] == cal and f["tipo"] == tipo and not f["hecho"] and f["inicio"] < desde),
                           key=lambda f: (f["inicio"], f["id"]))
            self._resp = [self._fila(f, ["id", "inicio", "fin"]) for f in filas]
        elif s.startswith("DELETE FROM calendario_eventos WHERE id = %s AND calendar_id = %s"):
            i, cal = params
            if i in b.filas and b.filas[i]["calendar_id"] == cal:
                del b.filas[i]
                self.rowcount = 1
        elif s.startswith("UPDATE calendario_eventos SET hecho = %s"):
            hecho, i = params
            b.filas[i]["hecho"] = hecho
            self.rowcount = 1
        elif s.startswith("UPDATE calendario_eventos SET inicio = %s, fin = %s"):
            ini, fin, i = params
            b.filas[i].update(inicio=ini, fin=fin)
            self.rowcount = 1
        else:
            raise AssertionError(f"pedido no simulado: {s[:120]}")

    def fetchone(self):
        return self._resp[0] if self._resp else None

    def fetchall(self):
        return list(self._resp)


class _Conexion:
    def __init__(self, base):
        self.base = base

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self, cursor_factory=None, **kw):
        return _Cursor(self.base, cursor_factory is not None)

    def commit(self):
        self.base.commits += 1

    def rollback(self):
        pass


class BaseCalendario:
    def __init__(self):
        self.filas, self.sqls = {}, []
        self.actas_reales = set()
        self.commits = 0
        self._ids = itertools.count(1)

    def conexion(self):
        return _Conexion(self)

    def agregar(self, **campos):
        """Carga una fila como si ya estuviera en la base (por ejemplo, una copiada de Google antes del cambio)."""
        i = next(self._ids)
        fila = {"id": i, "calendar_id": "panel", "google_id": f"g{i}", "titulo": "t", "descripcion": "", "lugar": None,
                "todo_el_dia": False, "actas": [], "origen": "panel", "creado_por": None, "link": None, "etag": None,
                "actualizado_google": None, "modalidad": None, "meet_url": None, "email_aviso": None,
                "tipo": "evento", "hecho": False}
        fila.update(campos)
        self.filas[i] = fila
        return i

    @contextlib.contextmanager
    def parche(self, cc):
        """Hace que el INSERT por lotes de calendario_core pase por el cursor falso."""
        original = cc.psycopg2.extras.execute_batch

        def lote(cur, sql, filas):
            for f in filas:
                cur.execute(sql, f)

        cc.psycopg2.extras.execute_batch = lote
        try:
            yield self
        finally:
            cc.psycopg2.extras.execute_batch = original
