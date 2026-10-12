"""
Importa a la cartera de clientes (Clientes → Marcas) una lista de actas que ya
se tenían de antes, sin repetir nada.

Para cada acta:
  1. Si el acta YA está en la cartera (de cualquier cliente, sea del tag que sea)
     no se toca: se omite.
  2. Si no, se lee su expediente en INPI (titular, CUIT, clase, estado, agente,
     movimientos y logo) y se carga bajo el cliente que corresponde:
       - el cliente que ya existe con ese CUIT (o, si el expediente no trae CUIT,
         con ese mismo nombre): se le suma la marca y NO se le cambia el tag;
       - si no existe, se crea el cliente con el titular del expediente y el tag
         de referido que se indique (por defecto «Pame»).
  3. El logo del expediente se guarda junto con la marca (tabla analisis_marca),
     así se ve en Clientes y en el Análisis de marca.

Después, con lo que quede de tiempo, completa el logo de las marcas de la cartera
que todavía no se miraron en INPI (--max-logos).

El repo es público: en el log solo salen números de acta, ids y cantidades, nunca
nombres ni CUIT.

Uso:
    DATABASE_URL=... python3 importar_cartera.py --actas "4775001,4648055" --simular
    DATABASE_URL=... python3 importar_cartera.py --actas "4775001,4648055" --tag Pame
"""

import argparse
import os
import re
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import analisis_marca  # noqa: E402
import cartera  # noqa: E402
import inpi_lead  # noqa: E402

import monitor_bloqueo  # noqa: E402
from revisar_cartera import _completar_denominacion_ws  # noqa: E402
from validar_leads import crear_sesion  # noqa: E402

RE_ACTA = re.compile(r"^\d{4,9}$")
USUARIO = "importación"
ORIGEN_CARGA = "importacion"


def parsear_actas(texto: str) -> list:
    """Números de acta de un texto (separados por coma, espacio, salto de línea o punto y coma).
    Acepta el formato con puntos de miles (4.545.450). Sin repetir y respetando el orden.
    Lo que no parece un acta (4 a 9 dígitos) se descarta y se devuelve aparte."""
    vistos, validas = set(), []
    for crudo in re.split(r"[\s,;]+", texto or ""):
        a = re.sub(r"\D", "", crudo)
        if not a or a in vistos:
            continue
        vistos.add(a)
        if RE_ACTA.match(a):
            validas.append(a)
    return validas


def cuit_valido(valor):
    d = re.sub(r"\D", "", valor or "")
    return d if len(d) == 11 else None


def nombre_normalizado(nombre: str) -> str:
    """Para comparar nombres de titulares: sin tildes, sin puntuación, mayúsculas, espacios simples."""
    import unicodedata
    n = unicodedata.normalize("NFKD", nombre or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^A-Za-z0-9 ]+", " ", n).upper()
    return re.sub(r"\s+", " ", n).strip()


def buscar_cliente(cur, cuit, nombre, cache):
    """id del cliente activo que ya existe con ese CUIT o, si no hay CUIT, con ese nombre.
    `cache` guarda lo resuelto en esta misma corrida para no crear dos veces el mismo cliente."""
    clave_cuit = f"cuit:{cuit}" if cuit else None
    clave_nombre = f"nombre:{nombre_normalizado(nombre)}" if nombre else None
    for k in (clave_cuit, clave_nombre):
        if k and k in cache:
            return cache[k]
    if cuit:
        cur.execute("SELECT id FROM clientes WHERE cuit = %s AND activo ORDER BY id LIMIT 1", (cuit,))
        f = cur.fetchone()
        if f:
            return f["id"]
    if nombre and not cuit:
        cur.execute("SELECT id, nombre FROM clientes WHERE activo AND COALESCE(cuit, '') = '' ORDER BY id")
        objetivo = nombre_normalizado(nombre)
        for f in cur.fetchall():
            if nombre_normalizado(f["nombre"]) == objetivo:
                return f["id"]
    return None


def recordar(cache, cuit, nombre, cliente_id):
    if cuit:
        cache[f"cuit:{cuit}"] = cliente_id
    if nombre:
        cache[f"nombre:{nombre_normalizado(nombre)}"] = cliente_id


def crear_cliente(cur, nombre, cuit, tag):
    cur.execute(
        "INSERT INTO clientes (nombre, cuit, origen, referido, alta_por, modificado_por) "
        "VALUES (%s, %s, 'cartera', %s, %s, %s) RETURNING id",
        (nombre, cuit, tag or None, USUARIO, USUARIO),
    )
    return cur.fetchone()["id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--actas", required=True, help="números de acta separados por coma, espacio o salto de línea")
    ap.add_argument("--tag", default="Pame", help="referido (tag) de los clientes NUEVOS; los que ya existían no se tocan")
    ap.add_argument("--simular", action="store_true", help="solo mira la base y cuenta qué se omitiría; no consulta INPI ni escribe")
    ap.add_argument("--delay", type=float, default=2.0, help="segundos entre consulta y consulta a INPI")
    ap.add_argument("--max-minutes", type=float, default=40, help="corta la corrida pasado este tiempo")
    ap.add_argument("--max-logos", type=int, default=150, help="tope de marcas de la cartera a las que se les completa el logo")
    ap.add_argument("--bloqueos-para-frenar", type=int, default=5)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    actas = parsear_actas(args.actas)
    if not actas:
        sys.exit("No hay ningún número de acta válido en --actas")
    print(f"Actas recibidas (sin repetir): {len(actas)}")

    inicio = time.time()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
            analisis_marca.crear_tablas(cur)
        conn.commit()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT acta, cliente_id FROM cartera_marcas WHERE acta = ANY(%s)", (actas,))
            en_cartera = {f["acta"]: f["cliente_id"] for f in cur.fetchall()}
            cur.execute("SELECT acta FROM marcas WHERE acta = ANY(%s)", (actas,))
            como_lead = {f["acta"] for f in cur.fetchall()}
        conn.commit()

        nuevas = [a for a in actas if a not in en_cartera]
        print(f"Ya estaban en la cartera (se omiten): {len(en_cartera)}")
        for a in actas:
            if a in en_cartera:
                print(f"  acta {a}: ya está en el cliente #{en_cartera[a]}")
        print(f"Para importar: {len(nuevas)}")
        sueltas = [a for a in nuevas if a in como_lead]
        if sueltas:
            print(f"Ojo: {len(sueltas)} de las nuevas figuran también como lead en el panel de leads: "
                  + ", ".join(sueltas))

        if args.simular:
            print(f"::notice::Simulación (no se consultó INPI ni se escribió nada): {len(actas)} actas recibidas, "
                  f"{len(en_cartera)} ya en la cartera (se omitirían), {len(nuevas)} para importar.")
            if en_cartera:
                print("::notice::Ya en la cartera (se omiten): " + ", ".join(a for a in actas if a in en_cartera))
            if sueltas:
                print("::notice::Figuran también como lead en el panel de leads: " + ", ".join(sueltas))
            return

        s = crear_sesion()
        cache = {}
        importadas = clientes_nuevos = con_logo = bloqueadas = errores = inexistentes = sin_titular = 0
        bloqueo_seguido = 0
        fallidas = {}   # acta -> motivo (para anotarlas al final)
        hechas = []     # (acta, cliente_id, cliente_nuevo, tiene_logo)
        for i, acta in enumerate(nuevas, 1):
            if monitor_bloqueo.debe_cortar():
                print("Se corta por bloqueos seguidos de INPI: las que faltan se pueden volver a importar (se omiten las ya cargadas).")
                break
            if (time.time() - inicio) / 60 > args.max_minutes:
                print(f"Se llegó al tope de {args.max_minutes:g} minutos: las que faltan se pueden volver a importar.")
                break
            info = inpi_lead.consultar_expediente(acta, s=s)
            estado = info.get("estado_consulta")
            if estado == "bloqueado":
                bloqueadas += 1
                bloqueo_seguido += 1
                fallidas[acta] = "bloqueada por el WAF"
                print(f"  [{i}/{len(nuevas)}] acta {acta}: bloqueada por el WAF, queda sin importar")
                if bloqueo_seguido >= args.bloqueos_para_frenar:
                    print(f"{bloqueo_seguido} bloqueos seguidos: se corta la corrida.")
                    break
                time.sleep(args.delay * 4)
                continue
            bloqueo_seguido = 0
            if estado == "no_existe":
                inexistentes += 1
                fallidas[acta] = "INPI no la devuelve"
                print(f"  [{i}/{len(nuevas)}] acta {acta}: INPI no la devuelve, queda sin importar")
            elif estado != "ok":
                errores += 1
                fallidas[acta] = "error de consulta"
                print(f"  [{i}/{len(nuevas)}] acta {acta}: error de consulta, queda sin importar")
            else:
                _completar_denominacion_ws(info, acta)
                titular = (info.get("titular") or "").strip()
                cuit = cuit_valido(info.get("cuit"))
                if not titular:
                    sin_titular += 1
                    fallidas[acta] = "el expediente no trae titular"
                    print(f"  [{i}/{len(nuevas)}] acta {acta}: el expediente no trae titular, queda sin importar")
                else:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cliente_id = buscar_cliente(cur, cuit, titular, cache)
                        es_nuevo = cliente_id is None
                        if es_nuevo:
                            cliente_id = crear_cliente(cur, titular, cuit, args.tag)
                            clientes_nuevos += 1
                        recordar(cache, cuit, titular, cliente_id)
                        cartera.alta_marca_cartera(cur, cliente_id, acta, info, USUARIO, ORIGEN_CARGA)
                        cur.execute("UPDATE clientes SET modificado_en = now(), modificado_por = %s WHERE id = %s",
                                    (USUARIO, cliente_id))
                    conn.commit()
                    importadas += 1
                    tiene = bool(info.get("logo"))
                    hechas.append((acta, cliente_id, es_nuevo, tiene))
                    con_logo += tiene
                    print(f"  [{i}/{len(nuevas)}] acta {acta}: ok → cliente #{cliente_id}"
                          f"{' (nuevo)' if es_nuevo else ' (ya existía)'}, logo: {'sí' if tiene else 'no'}")
            time.sleep(args.delay)

        # ── Logos de las marcas de la cartera que todavía no se miraron en INPI ──
        logos_ok = logos_revisadas = 0
        if args.max_logos > 0:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT cm.acta FROM cartera_marcas cm
                    JOIN clientes c ON c.id = cm.cliente_id AND c.activo
                    LEFT JOIN analisis_marca am ON am.acta = cm.acta
                    WHERE cm.acta ~ '^[0-9]+$' AND am.logo_consultado_en IS NULL
                    ORDER BY cm.alta_en DESC, cm.acta
                    LIMIT %s
                    """,
                    (args.max_logos,),
                )
                sin_logo_mirado = [f["acta"] for f in cur.fetchall()]
            conn.commit()
            print(f"Marcas de la cartera sin logo consultado: {len(sin_logo_mirado)} (tope {args.max_logos})")
            bloqueo_seguido = 0
            for i, acta in enumerate(sin_logo_mirado, 1):
                if monitor_bloqueo.debe_cortar() or (time.time() - inicio) / 60 > args.max_minutes:
                    print("Se corta la búsqueda de logos (bloqueos o tiempo): el resto queda para otra corrida.")
                    break
                info = inpi_lead.consultar_expediente(acta, s=s, con_grilla=False)
                if info.get("estado_consulta") == "bloqueado":
                    bloqueo_seguido += 1
                    if bloqueo_seguido >= args.bloqueos_para_frenar:
                        print(f"{bloqueo_seguido} bloqueos seguidos: se corta la búsqueda de logos.")
                        break
                    time.sleep(args.delay * 4)
                    continue
                bloqueo_seguido = 0
                if info.get("estado_consulta") == "ok":
                    with conn.cursor() as cur:
                        cartera.guardar_logo_expediente(cur, acta, info)
                    conn.commit()
                    logos_revisadas += 1
                    logos_ok += bool(info.get("logo"))
                time.sleep(args.delay)

        resumen = (f"Importadas: {importadas} de {len(nuevas)} (omitidas por estar ya cargadas: {len(en_cartera)}). "
                   f"Clientes nuevos: {clientes_nuevos}. Con logo: {con_logo}. Bloqueadas: {bloqueadas}. "
                   f"Sin resultado en INPI: {inexistentes}. Con error: {errores}. Sin titular: {sin_titular}. "
                   f"Logos completados en marcas viejas: {logos_ok} de {logos_revisadas} revisadas.")
        # Anotaciones del run (se leen sin bajar el log): resumen, qué se omitió, qué falló, qué cliente quedó con qué marcas.
        print(f"::notice::{resumen}")
        if en_cartera:
            print("::notice::Omitidas por estar ya en la cartera: " + ", ".join(a for a in actas if a in en_cartera))
        por_cliente = {}
        for acta, cid, nuevo, tiene in hechas:
            por_cliente.setdefault((cid, nuevo), []).append(f"{acta}{'*' if tiene else ''}")
        if por_cliente:
            lineas = [f"cliente #{cid}{' (nuevo)' if nuevo else ' (ya existía)'}: {', '.join(v)}"
                      for (cid, nuevo), v in sorted(por_cliente.items())]
            print("::notice::Cargadas (* = con logo): " + " | ".join(lineas))
        if fallidas:
            print("::warning::Sin importar: " + ", ".join(f"{a} ({m})" for a, m in fallidas.items()))
        pendientes_corte = [a for a in nuevas if a not in fallidas and a not in {h[0] for h in hechas}]
        if pendientes_corte:
            print("::warning::Sin procesar por corte (bloqueos o tiempo): " + ", ".join(pendientes_corte))
        print(f"\n{resumen}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::importar_cartera falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
