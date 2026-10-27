"""
Feriados nacionales de Argentina, para contar los días hábiles de los plazos de las vistas de INPI
(scripts/vistas_pdf.py: «10 días hábiles administrativos»).

Fuente: https://www.argentina.gob.ar/feriados. La página arma el calendario con un JSON público,
`/sites/default/files/holidays-{año}-es.json` (JSON-LD: mainEntity.itemListElement[].item con
`name`, `startDate` y additionalProperty.value = el tipo), así que se lee ese JSON y no el HTML.
Tipos: inamovible | trasladable | no_laborable | turistico | especial.

Se corre todos los días (workflow feriados.yml) porque el Gobierno cambia la lista durante el año
(feriados trasladados, días no laborables nuevos). Guarda en la tabla `feriados`; lo que desaparece
de la página queda con activo = false, no se borra. Si cambió algo que afecta los días hábiles,
recalcula los vencimientos de las vistas (sin consultar a INPI).

Qué días se descuentan: TIPOS_INHABILES (inamovible, trasladable y especial). Los «no laborables»
y los «turísticos» se guardan pero NO se descuentan: no son feriados para todos, y contarlos de
más correría el vencimiento hacia adelante (el cliente creería que le queda más tiempo).

Uso:
    DATABASE_URL=... python3 feriados.py [--anios 2026,2027] [--sin-recalcular]
"""

import argparse
import datetime as dt
import os
import sys
import time

import psycopg2
import requests

URL_PAGINA = "https://www.argentina.gob.ar/feriados"
URL_JSON = "https://www.argentina.gob.ar/sites/default/files/holidays-{anio}-es.json"

TIPOS_CONOCIDOS = ("inamovible", "trasladable", "no_laborable", "turistico", "especial")
TIPOS_INHABILES = ("inamovible", "trasladable", "especial")
# Un año de Argentina tiene bastante más que esto: menos significa que la página cambió de formato.
MIN_FERIADOS = 10

CREAR_TABLA = """
CREATE TABLE IF NOT EXISTS feriados (
    fecha            DATE PRIMARY KEY,
    motivo           TEXT NOT NULL,
    tipo             TEXT NOT NULL,
    activo           BOOLEAN NOT NULL DEFAULT true,
    visto_primera_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    visto_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    cambio_en        TIMESTAMPTZ,
    cambio_detalle   TEXT
);
"""


def _tipo_de(item: dict) -> str:
    prop = item.get("additionalProperty")
    if isinstance(prop, list):
        prop = next((p for p in prop if isinstance(p, dict) and p.get("value")), None)
    valor = prop.get("value") if isinstance(prop, dict) else None
    return str(valor or "").strip().lower()


def parsear_feriados(data: dict, anio: int) -> list[dict]:
    """[{fecha: 'YYYY-MM-DD', motivo, tipo}] ordenado por fecha. ValueError si el JSON no tiene el
    formato esperado, está en otro idioma, trae fechas de otro año o muy pocos feriados."""
    if not isinstance(data, dict):
        raise ValueError("el JSON no es un objeto")
    idioma = str(data.get("inLanguage") or "")
    if idioma and not idioma.lower().startswith("es"):
        raise ValueError(f"el JSON está en «{idioma}», no en español")
    items = (data.get("mainEntity") or {}).get("itemListElement")
    if not isinstance(items, list) or not items:
        raise ValueError("no encontré mainEntity.itemListElement (¿cambió el formato de la página?)")
    por_fecha: dict[str, dict] = {}
    for e in items:
        item = (e or {}).get("item") if isinstance(e, dict) else None
        if not isinstance(item, dict):
            raise ValueError(f"ítem sin «item»: {e!r}")
        crudo = str(item.get("startDate") or "")[:10]
        try:
            fecha = dt.date.fromisoformat(crudo)
        except ValueError:
            raise ValueError(f"fecha inválida «{item.get('startDate')}» en «{item.get('name')}»") from None
        if fecha.year != anio:
            raise ValueError(f"«{item.get('name')}» es del {fecha.year} y el archivo es del {anio}")
        motivo = " ".join(str(item.get("name") or "").split())
        tipo = _tipo_de(item)
        if not motivo or not tipo:
            raise ValueError(f"falta el nombre o el tipo del feriado del {crudo}")
        previo = por_fecha.get(crudo)
        if previo:
            # Dos entradas el mismo día: manda la que descuenta días hábiles.
            if previo["tipo"] not in TIPOS_INHABILES and tipo in TIPOS_INHABILES:
                previo["tipo"] = tipo
            if motivo not in previo["motivo"]:
                previo["motivo"] += f" / {motivo}"
        else:
            por_fecha[crudo] = {"fecha": crudo, "motivo": motivo, "tipo": tipo}
    if len(por_fecha) < MIN_FERIADOS:
        raise ValueError(f"solo {len(por_fecha)} feriados para el {anio} (se esperan {MIN_FERIADOS} o más)")
    return [por_fecha[k] for k in sorted(por_fecha)]


def descargar(anio: int, session=None, reintentos: int = 3):
    """El JSON del año (dict), o None si la página todavía no lo publicó (404)."""
    s = session or requests
    url = URL_JSON.format(anio=anio)
    ultimo = None
    for intento in range(1, reintentos + 1):
        try:
            r = s.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (panel-check feriados)"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            ultimo = e
            if intento < reintentos:
                time.sleep(2 * intento)
    raise RuntimeError(f"no pude bajar {url}: {ultimo}")


def sincronizar(cur, anio: int, feriados: list[dict]) -> list[str]:
    """Deja la tabla igual a la página para ese año. Devuelve los cambios, uno por línea de texto."""
    cur.execute("SELECT fecha::text, motivo, tipo, activo FROM feriados WHERE EXTRACT(year FROM fecha) = %s", (anio,))
    guardados = {f: (m, t, a) for f, m, t, a in cur.fetchall()}
    cambios = []
    for f in feriados:
        previo = guardados.get(f["fecha"])
        if previo is None:
            cur.execute("INSERT INTO feriados (fecha, motivo, tipo) VALUES (%s, %s, %s)", (f["fecha"], f["motivo"], f["tipo"]))
            cambios.append(f"AGREGADO {f['fecha']} {f['motivo']} ({f['tipo']})")
            continue
        motivo, tipo, activo = previo
        if (motivo, tipo, activo) != (f["motivo"], f["tipo"], True):
            detalle = f"antes: {motivo} ({tipo}){'' if activo else ', estaba quitado'}"
            cur.execute(
                "UPDATE feriados SET motivo = %s, tipo = %s, activo = true, visto_en = now(), cambio_en = now(), "
                "cambio_detalle = %s WHERE fecha = %s", (f["motivo"], f["tipo"], detalle, f["fecha"]))
            cambios.append(f"MODIFICADO {f['fecha']} {f['motivo']} ({f['tipo']}) — {detalle}")
        else:
            cur.execute("UPDATE feriados SET visto_en = now() WHERE fecha = %s", (f["fecha"],))
    en_pagina = {f["fecha"] for f in feriados}
    for fecha, (motivo, tipo, activo) in sorted(guardados.items()):
        if activo and fecha not in en_pagina:
            cur.execute(
                "UPDATE feriados SET activo = false, cambio_en = now(), cambio_detalle = %s WHERE fecha = %s",
                ("ya no figura en la página", fecha))
            cambios.append(f"QUITADO {fecha} {motivo} ({tipo})")
    return cambios


def cargar_feriados(conn, tipos=TIPOS_INHABILES) -> frozenset:
    """Fechas ISO de los días no hábiles guardados. Vacío (con aviso) si la tabla todavía no existe:
    la revisión de vistas no puede romperse por esto, solo vuelve a no descontar feriados."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fecha::text FROM feriados WHERE activo AND tipo = ANY(%s)", (list(tipos),))
            return frozenset(r[0] for r in cur.fetchall())
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        print("::warning::Todavía no hay tabla de feriados: los días hábiles se cuentan sin descontarlos", flush=True)
        return frozenset()


def _hoy_argentina() -> str:
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date().isoformat()


def _resumen_github(lineas: list[str]) -> None:
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if not ruta or not lineas:
        return
    with open(ruta, "a", encoding="utf-8") as f:
        f.write("### Cambios en los feriados\n\n" + "\n".join(f"- {l}" for l in lineas) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Sincroniza los feriados nacionales desde argentina.gob.ar")
    ap.add_argument("--anios", default="", help="años a sincronizar, separados por coma (por defecto: el actual y el siguiente)")
    ap.add_argument("--sin-recalcular", action="store_true", help="no recalcula los vencimientos de las vistas aunque cambien los feriados")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        print("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)", file=sys.stderr)
        sys.exit(1)
    hoy = _hoy_argentina()
    actual = int(hoy[:4])
    anios = [int(a) for a in args.anios.split(",") if a.strip()] or [actual, actual + 1]

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(CREAR_TABLA)
    conn.commit()
    antes = cargar_feriados(conn)

    cambios, errores = [], []
    with requests.Session() as s:
        for anio in anios:
            try:
                data = descargar(anio, s)
                if data is None:
                    # El del año que viene se publica recién hacia fin de año; el actual tiene que estar.
                    msg = f"{anio}: la página todavía no publicó los feriados ({URL_JSON.format(anio=anio)})"
                    if anio <= actual:
                        errores.append(msg)
                    else:
                        print(f"Aviso: {msg}", flush=True)
                    continue
                feriados = parsear_feriados(data, anio)
                desconocidos = sorted({f["tipo"] for f in feriados} - set(TIPOS_CONOCIDOS))
                if desconocidos:
                    print(f"::warning::{anio}: tipos de feriado nuevos que no se descuentan hasta revisarlos: {', '.join(desconocidos)}", flush=True)
                with conn.cursor() as cur:
                    nuevos = sincronizar(cur, anio, feriados)
                conn.commit()
                cambios += nuevos
                print(f"{anio}: {len(feriados)} feriados en la página, {len(nuevos)} cambios", flush=True)
            except Exception as e:  # noqa: BLE001 — un año con problemas no frena al otro
                conn.rollback()
                errores.append(f"{anio}: {e}")

    for c in cambios:
        print(f"::warning::Feriados — {c}", flush=True)
    _resumen_github(cambios)

    despues = cargar_feriados(conn)
    if antes != despues and not args.sin_recalcular:
        agregados, quitados = sorted(despues - antes), sorted(antes - despues)
        print(f"Cambió el conjunto de días no hábiles (+{len(agregados)} −{len(quitados)}): recalculo los vencimientos de las vistas", flush=True)
        from probar_vistas_preboletin import reinterpretar
        reinterpretar(conn, hoy, avisar=False)

    conn.close()
    if errores:
        for e in errores:
            print(f"::error::{e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
