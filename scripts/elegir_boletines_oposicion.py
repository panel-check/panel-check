"""
Elige qué boletines revisar esta noche en la rotación automática de
oposiciones (decidido el 15/10/2026, reemplaza al esquema de hitos a los
10/23/33 días de revisar_oposiciones.py). Cada noche se vuelve a mirar el
boletín ENTERO -- todos sus leads, misma lógica que el botón manual «Buscar
oposiciones» de /boletines (revisar_oposiciones.py --boletin) -- eligiendo
los --max boletines (5 por defecto) que todavía están dentro de la ventana
de oposición: plazo de 30 días abierto, o ya cerrado pero pendiente de que
INPI notifique a los titulares (INPI notifica en tanda, todos los
miércoles; ver _fecha_notificacion más abajo). Entre esos, prioriza los que
están más cerca de cerrarse -- o ya cerrados, esperando el miércoles de
notificación, que son los más urgentes.

Ese universo y ese cálculo de fecha de notificación son los MISMOS que usa
/boletines para decidir qué mostrar por defecto (panel/app.py,
_fecha_notificacion_boletin): si se cambia uno, hay que revisar el otro.

Solo entran boletines ya importados ("procesado") que tengan al menos un
lead (es_lead = true) -- si no tiene ningún lead no hay nada que revisar.

Imprime a stdout un JSON array de números de boletín (strings), del más
urgente al menos urgente, con hasta --max elementos ("[]" si no hay
ninguno pendiente). Pensado para leerse desde bash en el workflow, no para
uso interactivo.

Uso:
    DATABASE_URL=... python3 elegir_boletines_oposicion.py --max 5
"""

import argparse
import datetime as dt
import json
import os
import sys

import psycopg2
import psycopg2.extras


def _fecha_notificacion(fecha: dt.date) -> dt.date:
    """Mismo cálculo que panel/app.py:_fecha_notificacion_boletin -- si se
    cambia uno, cambiar el otro. INPI no avisa a los titulares el mismo día
    que cierra el plazo de 30 días: notifica las oposiciones en tanda,
    todos los miércoles (el primero en la fecha de cierre o después)."""
    cierre = fecha + dt.timedelta(days=30)
    return cierre + dt.timedelta(days=(2 - cierre.weekday()) % 7)  # 2 = miércoles


def elegir(conn, hoy: dt.date, maximo: int) -> list[str]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT b.numero, b.fecha
            FROM boletines b
            WHERE b.estado = 'procesado' AND b.fecha IS NOT NULL
              AND EXISTS (SELECT 1 FROM marcas m WHERE m.boletin = b.numero AND m.es_lead = true)
            """
        )
        filas = cur.fetchall()

    candidatos = []
    for f in filas:
        notif = _fecha_notificacion(f["fecha"])
        if hoy <= notif:
            cierre = f["fecha"] + dt.timedelta(days=30)
            candidatos.append({"numero": f["numero"], "cierre": cierre})

    # Más urgente primero: el que cierra (o ya cerró) antes.
    candidatos.sort(key=lambda c: c["cierre"])
    print(f"Boletines en ventana de oposición: {len(candidatos)}. Elegidos esta corrida: "
          f"{min(len(candidatos), maximo)}.", file=sys.stderr)
    return [c["numero"] for c in candidatos[:maximo]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max", type=int, default=5, help="tope de boletines a elegir esta corrida (default 5)")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    hoy = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date()  # hoy en Argentina (UTC-3)

    conn = psycopg2.connect(dsn)
    try:
        elegidos = elegir(conn, hoy, args.max)
    finally:
        conn.close()

    print(json.dumps(elegidos))


if __name__ == "__main__":
    main()
