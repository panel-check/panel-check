"""
Escaneo directo de números de acta secuenciales -- adelanta el primer
contacto con un lead en varias semanas respecto de esperar el boletín.

Contexto (confirmado a mano por el usuario el 30/09/2026): el acta es el
número de expediente que INPI asigna al DEPOSITAR la solicitud, no cuando
se publica en el boletín (eso pasa semanas después). Es un contador único
y secuencial para todas las presentaciones de marca. Se probó consultando
a mano el rango 4797001-4797916 (aparecido de un día para el otro) contra
la misma consulta de expediente que ya usa revisar_acta, y el expediente
4797916 ya tenía todos los datos -- incluido el Formulario con el email.

Qué hace esta corrida:
  1. Lee de la tabla escaneo_actas hasta qué número se llegó la última vez.
     Si todavía no hay fila (primera corrida), arranca desde --desde (por
     defecto 4797000 -- CHATTY, la última marca ya conocida a la fecha en
     que se armó este script -- pedido explícito del usuario como punto
     de partida) en vez de intentar adivinarlo de la tabla `marcas`.
  2. Prueba, uno por uno, los números siguientes contra INPI
     (existe_expediente, en validar_leads.py). Si varios seguidos no
     existen todavía, corta esta corrida (el resto los prueba la próxima).
  3. Para cada acta que SÍ existe y resulta ser un lead real (sin
     agente/apoderado en GESTION DEL TRAMITE), guarda clase, denominación,
     tipo, titular, CUIT, email y fecha de depósito -- clase sale de la
     misma página de INPI que ya se consulta para caracter/CUIT, el resto
     del Formulario que ya se descarga para el email (ver revisar_acta).
  4. Guarda el acta con boletin=NULL y fuente='escaneo_directo'. Cuando el
     boletín real la alcance, cargar_db.py va a completar boletin y
     actualizar lo demás sin duplicar la fila (mismo acta = mismo registro).
     A partir de ahí sigue el flujo de siempre (revisar_oposiciones.py
     empieza a mirarla recién cuando fecha_publicacion deja de ser NULL).

Una marca CON agente/apoderado detectada en el escaneo no se guarda: no es
un lead para nosotros y el boletín la va a cargar igual más adelante.

Uso:
    DATABASE_URL=... python3 escanear_actas_nuevas.py
    DATABASE_URL=... python3 escanear_actas_nuevas.py --desde 4797000
    DATABASE_URL=... python3 escanear_actas_nuevas.py --tope 300 --consecutivos-para-frenar 8

Nota: el heurístico de "no existe todavía" (existe_expediente) es
best-effort -- no se pudo probar en vivo desde este entorno (INPI está
bloqueado para este sandbox). Revisar el log de las primeras corridas
reales.
"""

import argparse
import os
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

from validar_leads import calcular_lead_score, crear_sesion, existe_expediente, revisar_acta


def _leer_puntero(conn, desde: int | None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT ultima_acta_confirmada FROM escaneo_actas WHERE id = 1")
        fila = cur.fetchone()
        if fila:
            # Ya se sembró antes -- --desde de esta corrida se ignora (solo
            # aplica la primera vez, para no "retroceder" el puntero por
            # error en una corrida manual posterior).
            return fila[0]
        if desde is not None:
            return desde
        # Sin --desde y primera corrida: no re-escanear todo el historial,
        # arrancar desde la acta más alta que ya tengamos cargada.
        cur.execute("SELECT COALESCE(MAX(acta::bigint), 0) FROM marcas WHERE acta ~ '^[0-9]+$'")
        return cur.fetchone()[0]


def _guardar_puntero(conn, ultima_acta_confirmada: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO escaneo_actas (id, ultima_acta_confirmada, actualizado_en)
            VALUES (1, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                ultima_acta_confirmada = EXCLUDED.ultima_acta_confirmada,
                actualizado_en = now()
            """,
            (ultima_acta_confirmada,),
        )
    conn.commit()


def _guardar_lead(conn, acta: str, info: dict, score: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO marcas (
                acta, boletin, clase, tipo, denominacion, fecha_presentacion,
                titular, cuit, caracter, es_lead, email, email_apoderado,
                lead_score, motivo_sin_email, fecha_publicacion, fuente
            ) VALUES (
                %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'escaneo_directo'
            )
            ON CONFLICT (acta) DO UPDATE SET
                -- Si por lo que sea ya existía (re-corrida, o el boletín se
                -- adelantó), no pisamos nada -- este script solo agrega,
                -- cargar_db.py es quien manda cuando hay boletín real.
                actualizado_en = now()
            """,
            (
                acta, info.get("clase"), info.get("tipo_formulario"), info.get("denominacion_formulario"),
                info.get("fecha_presentacion_formulario"), info.get("titular_formulario"),
                info.get("cuit"), info.get("caracter") or None, info.get("es_lead"),
                info.get("email") or None, info.get("email_apoderado") or None,
                score, info.get("motivo_sin_email") or None, info.get("fecha_publicacion"),
            ),
        )
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tope", type=int, default=300, help="máximo de números de acta a probar en esta corrida")
    ap.add_argument("--consecutivos-para-frenar", type=int, default=8,
                     help="si esta cantidad de actas seguidas no existe todavía, se corta la corrida")
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta")
    ap.add_argument("--desde", type=int, default=4797000,
                     help="punto de partida SOLO si es la primera corrida (todavía no hay fila en "
                          "escaneo_actas) -- default: 4797000 (CHATTY, pedido explícito del usuario "
                          "el 30/09/2026). Se ignora en cualquier corrida posterior.")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")

    conn = psycopg2.connect(dsn)
    try:
        ultima_confirmada = _leer_puntero(conn, args.desde)
        print(f"Último acta confirmada: {ultima_confirmada}. Probando hasta {args.tope} números siguientes...")

        s = crear_sesion()
        probadas = 0
        encontradas = 0
        leads_nuevos = 0
        consecutivos_sin_existir = 0
        bloqueado_seguido = 0

        numero = ultima_confirmada
        ultimo_confirmado = ultima_confirmada  # el puntero real a guardar -- solo avanza en un HIT
        while probadas < args.tope:
            numero += 1
            probadas += 1
            acta = str(numero)

            existe, _texto = existe_expediente(s, acta)
            if existe is None:
                # Un bloqueo suelto es normal: esperar y reintentar una vez.
                print(f"  acta {acta}: bloqueado por el WAF, reintento en 30 s")
                time.sleep(30)
                existe, _texto = existe_expediente(s, acta)
            if existe is None:
                # WAF bloqueó la consulta -- no sabemos si existe, cortamos
                # esta corrida entera para no seguir "avanzando" el puntero
                # sobre números que en realidad no se llegaron a revisar.
                bloqueado_seguido += 1
                print(f"  acta {acta}: bloqueado por el WAF de INPI, frenando esta corrida")
                break

            if not existe:
                consecutivos_sin_existir += 1
                if consecutivos_sin_existir >= args.consecutivos_para_frenar:
                    print(f"  acta {acta}: no existe todavía ({consecutivos_sin_existir} seguidas) -- frenando")
                    break
                time.sleep(args.delay)
                continue

            consecutivos_sin_existir = 0
            ultimo_confirmado = numero  # único lugar donde el puntero avanza
            encontradas += 1

            info = revisar_acta(s, acta)
            if info["es_lead"] is True and info.get("email"):
                score = calcular_lead_score({"matricula_agente": "", "es_lead": True, "email": info["email"]})
                _guardar_lead(conn, acta, info, score)
                leads_nuevos += 1
                print(f"  acta {acta}: LEAD nuevo detectado antes del boletín (score {score})")
            elif info["es_lead"] is True:
                # Lead real pero sin email todavía -- se guarda igual (motivo
                # queda en motivo_sin_email, igual que el flujo del boletín;
                # reintentar_sin_email.py lo va a volver a probar solo).
                score = calcular_lead_score({"matricula_agente": "", "es_lead": True, "email": ""})
                _guardar_lead(conn, acta, info, score)
                leads_nuevos += 1
                print(f"  acta {acta}: LEAD nuevo, sin email por ahora ({info.get('motivo_sin_email')})")
            else:
                # Tiene agente/apoderado, o no se pudo confirmar (WAF puntual
                # en GESTION DEL TRAMITE) -- no es un lead para nosotros; el
                # boletín real la va a cargar de todas formas más adelante.
                print(f"  acta {acta}: no es lead (caracter={info.get('caracter') or 'sin confirmar'})")

            time.sleep(args.delay)

        _guardar_puntero(conn, ultimo_confirmado)
        resumen = (
            f"Probadas: {probadas}. Actas nuevas encontradas: {encontradas}. "
            f"Leads nuevos guardados: {leads_nuevos}. Puntero quedó en acta {ultimo_confirmado}."
        )
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::escanear_actas_nuevas falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
