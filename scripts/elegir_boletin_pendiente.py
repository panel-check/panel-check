"""
Para el modo automático del pipeline (pipeline.yml): de TODOS los boletines
"MARCAS NUEVAS" que devuelve listar_boletines.py, elige cuál procesar esta
corrida -- el de número más alto que TODAVÍA no esté importado (estado
'procesado' en la tabla boletines).

Antes el pipeline automático solo miraba el boletín más nuevo y, si ya
estaba importado, no hacía nada más -- lo cual está bien la mayoría de las
veces (los boletines salen en orden), pero si por lo que sea alguno
anterior se saltó o falló, se quedaba sin cargar para siempre. Este script
recorre la lista de más nuevo a más viejo y elige el primero que no esté
"procesado" (no_existe, pendiente o error cuentan como "hay que
procesarlo").

Imprime a stdout:
  - el JSON de un solo boletín (mismo formato que boletines_todos.json) si
    encontró uno pendiente, o
  - la palabra "NINGUNO" si todos los de la lista ya están procesados.

Con --todos (cadena de boletines del pipeline, desde octubre de 2026) imprime
en cambio un ARRAY JSON con todos los pendientes de la ventana (los --ventana
boletines más nuevos de la lista, hasta --max), ordenados del más viejo al más
nuevo; "[]" si no hay ninguno. Así la madrugada del miércoles se procesan los 4
boletines nuevos de la semana, de a uno. Con --solo-nuevos solo cuenta los que
tienen número mayor al más alto ya procesado (sirve para esperar la
publicación sin confundirse con boletines viejos pendientes).

No es para uso interactivo, es para leer desde bash en el workflow.

Uso:
    DATABASE_URL=... python3 elegir_boletin_pendiente.py data/boletines_todos.json
    DATABASE_URL=... python3 elegir_boletin_pendiente.py data/boletines_todos.json --todos --max 4
"""

import argparse
import json
import os
import sys

import psycopg2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archivo")
    ap.add_argument("--todos", action="store_true", help="imprime un array con todos los pendientes de la ventana")
    ap.add_argument("--max", type=int, default=4, help="con --todos: máximo de boletines (se quedan los más nuevos)")
    ap.add_argument("--ventana", type=int, default=6, help="con --todos: solo se miran los N boletines más nuevos de la lista")
    ap.add_argument("--solo-nuevos", action="store_true", help="con --todos: solo número mayor al más alto ya procesado")
    args = ap.parse_args()

    with open(args.archivo, encoding="utf-8") as f:
        candidatos = json.load(f)
    if not candidatos:
        sys.exit("El archivo de boletines está vacío")

    candidatos.sort(key=lambda b: int(b["numero"]), reverse=True)

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT numero, estado FROM boletines")
            estados = dict(cur.fetchall())
    finally:
        conn.close()

    if args.todos:
        procesados = [int(n) for n, e in estados.items() if e == "procesado" and str(n).isdigit()]
        piso = max(procesados) if procesados else -1
        pendientes = [
            c for c in candidatos[: args.ventana]
            if estados.get(c["numero"], "no_existe") != "procesado"
            and (not args.solo_nuevos or int(c["numero"]) > piso)
        ]
        pendientes = pendientes[: args.max]  # candidatos va del más nuevo al más viejo
        pendientes.sort(key=lambda b: int(b["numero"]))
        print(f"{len(pendientes)} boletín(es) pendiente(s): {[c['numero'] for c in pendientes]}", file=sys.stderr)
        print(json.dumps(pendientes, ensure_ascii=False))
        return

    for candidato in candidatos:
        numero = candidato["numero"]
        estado = estados.get(numero, "no_existe")
        if estado != "procesado":
            print(f"Elegido boletín {numero} (estado={estado}).", file=sys.stderr)
            print(json.dumps(candidato, ensure_ascii=False))
            return

    print(
        f"Los {len(candidatos)} boletines de la lista ya están todos importados -- "
        "no hay ninguno nuevo para procesar.",
        file=sys.stderr,
    )
    print("NINGUNO")


if __name__ == "__main__":
    main()
