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

No es para uso interactivo, es para leer desde bash en el workflow.

Uso:
    DATABASE_URL=... python3 elegir_boletin_pendiente.py data/boletines_todos.json
"""

import json
import os
import sys

import psycopg2


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: elegir_boletin_pendiente.py <archivo_boletines_todos.json>")

    with open(sys.argv[1], encoding="utf-8") as f:
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
