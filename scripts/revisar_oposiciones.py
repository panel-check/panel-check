"""
Paso 6 (corre por separado del pipeline principal) — Reviso, para los leads
reales (sin agente/apoderado) ya publicados en el boletín, si apareció una
oposición de tercero o una vista de oficio de INPI durante la ventana de 30
días para oponerse, y EN QUÉ ESTADO ESTÁ, para saber si todavía vale la pena
ofrecerle ayuda al titular.

Estado de la oposición (estado_oposicion; ver oposiciones_expediente.py):
  SIRVEN (nadie la está trabajando):
    sin_notificar        presentada pero INPI todavía no se la notificó al titular
    notificada_en_plazo  notificada, el plazo para contestar está corriendo
    plazo_vencido        venció el plazo y no hay contestación (riesgo de abandono)
    vista_pendiente      vista de oficio de INPI sin contestar
    oposicion_sin_detalle  la Grilla Digital la muestra pero el expediente todavía no
  NO SIRVEN (ya se está trabajando o ya no hay nada que hacer):
    contestada           ya hay contestación -> se marca como "atendida" (sale de los
                         avisos y de los pendientes del panel)
    levantada            la oposición se levantó / se desistió -> idem
    con_apoderado        el titular ya tiene agente/gestor según GESTION DEL TRAMITE
                         -> deja de ser lead (es_lead = false)

Cuando el expediente muestra que el titular sumó un apoderado/gestor, el lead
pasa a es_lead = false con carácter "Apoderado/gestor (se sumó tras la
oposición)": deja de ser un posible cliente, sale de la lista de leads y ya no
se vuelve a revisar. Antes esto se decidía por una fila "Acompaña Poder" de la
Grilla Digital, pero esa fila puede ser del abogado del OPONENTE; ahora el dato
definitivo es el AGENTE/CARACTER del titular en el expediente. Una fila de poder
sin agente del titular queda como "posible apoderado" (oposicion_posible_apoderado)
y el lead sigue vigente.

Esquema de revisión (días contados desde fecha_publicacion de ESE expediente):
  - HITOS: a los 10, 23 y 33 días se hace una revisión de cada lead. Muchas
    oposiciones llegan antes de los 30 días y conviene saberlo con tiempo.
  - Si hay oposición que todavía sirve, se vuelve a mirar TODOS LOS DÍAS hasta el
    día 33 y cada 3 días hasta el día 60 (el estado cambia: se notifica, vence el
    plazo, el titular contesta o suma un representante).
  - Los rechequeos diarios / cada 3 días se saltean los leads ya marcados como
    "contactado" en el panel: ya se les ofreció ayuda, no hace falta seguir
    mirando. Los 3 hitos (10/23/33) se hacen igual. Si se desmarca "contactado",
    vuelve a rechequearse.
  - Los leads con oposición detectada antes de existir el estado (estado_oposicion
    vacío) se completan en la próxima corrida, sin importar la antigüedad.
  - Contadores en marcas: opo_chequeos (0-3, hitos cumplidos),
    opo_ultimo_chequeo_en, oposicion_detectada_en (primera vez que se vio).
  - Los leads ya publicados cuando se puso en marcha este esquema se ponen al
    día con UNA pasada (workflow manual "Oposiciones: puesta al día"): para
    ellos cuenta el hito que les corresponde por edad. Si una corrida se corta
    por tiempo o por bloqueos, lo que falta queda pendiente y se retoma solo.

Cómo se detecta: la Grilla Digital (Home/GrillaDigital + GrillaDigitales, la misma
llamada que ya hacía validar_leads.py para el email) avisa si hay una fila de
oposición/vista ("Recibo de Ingreso" / "Opo. de Marcas"). Cuando hay una (o es el
hito de los 33 días, o ya se la venía siguiendo) se pide además el expediente
(/MarcasConsultas/Resultado), que trae la oposición estructurada: fechas de
presentación, notificación, vencimiento y levantamiento, agente del oponente,
vistas/contestaciones y el AGENTE/CARACTER actual del titular.

Uso:
    DATABASE_URL=... python3 revisar_oposiciones.py
    DATABASE_URL=... python3 revisar_oposiciones.py --max-minutes 90 --delay 1.5
    DATABASE_URL=... python3 revisar_oposiciones.py --reverificar-apoderados [--dry-run]

--reverificar-apoderados: revisa los leads que el sistema había pasado a "con
apoderado" mirando solo la Grilla Digital y devuelve a lead a los que, según el
expediente, el titular sigue sin representante (el poder era del oponente). No
toca a los marcados a mano con "Tiene gestor/apoderado". Con --dry-run solo
cuenta, no escribe.

Alcance: solo es_lead = true (particulares/empresas sin agente ni apoderado).
Las que ya tienen agente quedan afuera a propósito.
"""

import argparse
import os
import sys
import time
import psycopg2

import monitor_bloqueo
from registro import registrar
import psycopg2.extras

from oposiciones_expediente import (
    ESTADOS_CERRADOS,
    clasificar_estado_oposicion,
    consultar_expediente,
    titular_con_representante,
)
from validar_leads import (
    buscar_archivos_grilla,
    buscar_fila_oposicion,
    crear_sesion,
    descargar_formulario_oposicion,
)


HITOS = (10, 23, 33)          # días desde la publicación en que se revisa cada lead
VENTANA_BUSQUEDA_HASTA = 35   # pasado este día NO se busca más si hay oposición (el plazo es de 30 días;
                              # los 5 extra cubren la demora de INPI en cargarla y corridas atrasadas)
RECHEQUEO_DIARIO_HASTA = 33   # con oposición que sirve: todos los días hasta este día...
RECHEQUEO_CADA_3_HASTA = 60   # ...y cada 3 días hasta este otro

# Estados que ya no hace falta volver a mirar (ver ESTADOS_CERRADOS)
_CERRADOS_SQL = ", ".join(f"'{e}'" for e in ESTADOS_CERRADOS)

SQL_PENDIENTES = f"""
    SELECT acta, titular, fecha_publicacion,
           (CURRENT_DATE - fecha_publicacion) AS edad,
           oponente_nombre, tuvo_oposicion
    FROM marcas
    WHERE es_lead = true
      AND fecha_publicacion IS NOT NULL
      AND fecha_publicacion <= CURRENT_DATE - {HITOS[0]}
      AND (
        -- Búsqueda de oposiciones: solo dentro del plazo. Un boletín que salió hace más
        -- de VENTANA_BUSQUEDA_HASTA días ya no puede recibir oposiciones, así que no
        -- entra en el proceso diario (se puede buscar a mano desde /boletines).
        (fecha_publicacion >= CURRENT_DATE - {VENTANA_BUSQUEDA_HASTA} AND (
          COALESCE(opo_chequeos, 0) < 1
          OR (COALESCE(opo_chequeos, 0) < 2 AND fecha_publicacion <= CURRENT_DATE - {HITOS[1]})
          OR (COALESCE(opo_chequeos, 0) < 3 AND fecha_publicacion <= CURRENT_DATE - {HITOS[2]})
        ))
        -- Seguimiento de una oposición YA detectada (cambia de estado hasta que se resuelve)
        OR (
          tuvo_oposicion IS TRUE
          AND contactado IS NOT TRUE
          AND representacion_posterior_oposicion IS NOT TRUE
          AND oposicion_atendida IS NOT TRUE
          AND COALESCE(estado_oposicion, '') NOT IN ({_CERRADOS_SQL})
          AND (
            -- oposición detectada antes de que existiera el estado: se completa una vez
            estado_oposicion IS NULL
            OR (
              fecha_publicacion > CURRENT_DATE - {RECHEQUEO_CADA_3_HASTA}
              AND (
                opo_ultimo_chequeo_en IS NULL
                OR opo_ultimo_chequeo_en::date <= CURRENT_DATE - (
                     CASE WHEN fecha_publicacion > CURRENT_DATE - {RECHEQUEO_DIARIO_HASTA} THEN 1 ELSE 3 END)
              )
            )
          )
        )
      )
    ORDER BY CASE WHEN fecha_publicacion > CURRENT_DATE - {RECHEQUEO_CADA_3_HASTA} THEN 0 ELSE 1 END,
             fecha_publicacion DESC
"""


# Búsqueda manual (--boletin): todos los leads de ESE boletín, sin importar la edad
# ni los hitos cumplidos. La dispara el botón «Buscar oposiciones» de /boletines.
SQL_DE_BOLETIN = """
    SELECT acta, titular, fecha_publicacion,
           (CURRENT_DATE - fecha_publicacion) AS edad,
           oponente_nombre, tuvo_oposicion
    FROM marcas
    WHERE es_lead = true
      AND fecha_publicacion IS NOT NULL
      AND boletin = %s
    ORDER BY acta
"""

CARACTER_APODERADO = "Apoderado/gestor (se sumó tras la oposición)"

# Un lead cuyo titular ya sumó un apoderado/gestor después de la oposición deja
# de ser un posible cliente: pasa a es_lead = false con ese carácter (se ve en
# la columna Lead, igual que "con agente"), sale de la lista de leads y de todas
# las revisiones. El score se recalcula con la misma regla que
# validar_leads.calcular_lead_score para es_lead = false.
SQL_PASAR_A_APODERADO = """
    UPDATE marcas
    SET es_lead = false,
        caracter = '""" + CARACTER_APODERADO + """',
        lead_score = (CASE WHEN TRIM(COALESCE(matricula_agente, '')) IN ('', 'Part.') THEN 50 ELSE 0 END)
                     - 100 + (CASE WHEN COALESCE(email, '') <> '' THEN 20 ELSE 0 END),
        actualizado_en = now()
    WHERE es_lead IS TRUE AND representacion_posterior_oposicion IS TRUE
"""

# Inverso de lo anterior, para --reverificar-apoderados: el titular sigue sin
# representante según el expediente, así que vuelve a ser lead.
SQL_VOLVER_A_LEAD = """
    UPDATE marcas
    SET es_lead = true,
        caracter = '',
        representacion_posterior_oposicion = false,
        detalle_representacion_posterior = NULL,
        lead_score = (CASE WHEN TRIM(COALESCE(matricula_agente, '')) IN ('', 'Part.') THEN 50 ELSE 0 END)
                     + 50 + (CASE WHEN COALESCE(email, '') <> '' THEN 20 ELSE 0 END),
        actualizado_en = now()
    WHERE acta = %s AND es_lead IS FALSE AND caracter = '""" + CARACTER_APODERADO + """'
      AND con_gestor_manual IS NOT TRUE
"""

NUEVAS_COLUMNAS = (
    ("opo_chequeos", "INTEGER"), ("opo_ultimo_chequeo_en", "TIMESTAMPTZ"),
    ("oposicion_detectada_en", "TIMESTAMPTZ"), ("oposicion_atendida", "BOOLEAN"),
    ("oposicion_atendida_en", "TIMESTAMPTZ"), ("oposicion_atendida_por", "TEXT"),
    # Estado de la oposición, leído del expediente de INPI (oposiciones_expediente.py)
    ("estado_oposicion", "TEXT"), ("estado_oposicion_detalle", "TEXT"),
    ("estado_oposicion_en", "TIMESTAMPTZ"), ("oposicion_sirve", "BOOLEAN"),
    ("oposicion_fecha_presentacion", "DATE"), ("oposicion_fecha_notificacion", "DATE"),
    ("oposicion_fecha_vencimiento", "DATE"), ("oposicion_fecha_levantamiento", "DATE"),
    ("oposicion_agente_oponente", "TEXT"), ("oposicion_posible_apoderado", "BOOLEAN"),
    ("contactado", "BOOLEAN"),  # lo crea el panel; el rechequeo lo usa para saltear contactados
    ("con_gestor_manual", "BOOLEAN"),  # lo crea el panel; lo usa --reverificar-apoderados
    # 2 = clasificado con las reglas actuales (contestación solo si es posterior a la
    # oposición). Las contestadas/levantadas de versiones anteriores se recalculan.
    ("estado_oposicion_version", "INTEGER"),
)

# Cuándo terminó la última búsqueda manual completa de un boletín (botón de /boletines)
SQL_COLUMNA_BOLETIN = "ALTER TABLE boletines ADD COLUMN IF NOT EXISTS oposiciones_buscadas_en TIMESTAMPTZ"


def hito_por_edad(edad: int) -> int:
    """Cuántos hitos (1-3) ya cumplió un lead con esta antigüedad en días."""
    return sum(1 for h in HITOS if edad >= h)


def asegurar_columnas(conn):
    with conn.cursor() as cur:
        for col, tipo in NUEVAS_COLUMNAS:
            cur.execute(f"ALTER TABLE marcas ADD COLUMN IF NOT EXISTS {col} {tipo}")
        cur.execute(SQL_COLUMNA_BOLETIN)
    conn.commit()


def reverificar_apoderados(conn, args):
    """Devuelve a lead a los pasados a "con apoderado" por una fila de poder de la
    Grilla Digital cuando el expediente muestra que el titular sigue sin agente."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT acta FROM marcas
            WHERE es_lead IS FALSE AND caracter = %s AND con_gestor_manual IS NOT TRUE
            ORDER BY acta
            """,
            (CARACTER_APODERADO,),
        )
        actas = [r["acta"] for r in cur.fetchall()]
    print(f"Leads pasados a 'apoderado/gestor' por la Grilla Digital a reverificar: {len(actas)}")
    s = crear_sesion()
    volvieron = confirmados = sin_consulta = 0
    for i, acta in enumerate(actas, 1):
        if monitor_bloqueo.debe_cortar():
            print("Se corta por bloqueos seguidos de INPI: el resto queda para otra corrida.")
            break
        exp = consultar_expediente(s, acta)
        if exp.get("bloqueado"):
            sin_consulta += 1
            time.sleep(args.delay)
            continue
        if titular_con_representante(exp):
            confirmados += 1
            print(f"  [{i}/{len(actas)}] acta {acta}: el titular sí tiene representante, queda como está")
        else:
            volvieron += 1
            print(f"  [{i}/{len(actas)}] acta {acta}: el titular sigue sin representante -> vuelve a ser lead")
            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(SQL_VOLVER_A_LEAD, (acta,))
                    cur.execute(
                        "UPDATE marcas SET estado_oposicion = NULL, oposicion_posible_apoderado = true, "
                        "opo_ultimo_chequeo_en = NULL WHERE acta = %s", (acta,))
                conn.commit()
        time.sleep(args.delay)
    print(f"\nVuelven a ser lead: {volvieron}{' (dry-run: no se escribió nada)' if args.dry_run else ''}. "
          f"Confirmados con representante: {confirmados}. Sin poder consultar: {sin_consulta}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--max-minutes", type=float, default=None,
        help="tope de tiempo de la corrida; lo que falte queda pendiente para la próxima",
    )
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre acta y acta (freno de mano)")
    ap.add_argument("--limit", type=int, default=None, help="tope de actas a revisar, útil para pruebas")
    ap.add_argument("--boletin", default=None,
                    help="búsqueda manual: revisa TODOS los leads de este boletín, aunque ya haya vencido el plazo")
    ap.add_argument("--reverificar-apoderados", action="store_true",
                    help="reverifica los leads pasados a 'con apoderado' solo por la Grilla Digital")
    ap.add_argument("--dry-run", action="store_true", help="con --reverificar-apoderados: no escribe")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Falta la variable de entorno DATABASE_URL")

    inicio = time.time()
    conn = psycopg2.connect(dsn)
    try:
        asegurar_columnas(conn)
        if args.reverificar_apoderados:
            reverificar_apoderados(conn, args)
            return

        with conn.cursor() as cur:
            # Alta inicial de los contadores: los leads que ya fueron revisados
            # con el esquema anterior (una sola revisión, a los 33+ días) cuentan
            # con los 3 hitos cumplidos; el resto arranca en 0.
            cur.execute(
                """
                UPDATE marcas
                SET opo_chequeos = CASE WHEN revisado_oposicion_en IS NOT NULL THEN 3 ELSE 0 END
                WHERE opo_chequeos IS NULL AND es_lead = true
                """
            )
            # Reparación (05/10/2026): la primera versión daba por "contestada" una
            # oposición porque el titular había contestado una vista VIEJA
            # (actas 4688778 y 4726688), o porque leía como contestada una vista
            # sin contestar (4748835). No se sabe qué otras clasificaciones de esa
            # versión quedaron mal, así que TODO lo que se clasificó con ella se vuelve
            # a revisar una vez (estado_oposicion_version < 2), salvo "con_apoderado"
            # (sale de la lista de leads; para esos está --reverificar-apoderados).
            # Se desmarca lo que había puesto el sistema como "atendida" (lo que marcó
            # una persona a mano no se toca) y se reclasifica en esta misma corrida.
            cur.execute(
                """
                UPDATE marcas
                SET oposicion_atendida    = CASE WHEN oposicion_atendida_por LIKE 'sistema:%' THEN NULL ELSE oposicion_atendida END,
                    oposicion_atendida_en = CASE WHEN oposicion_atendida_por LIKE 'sistema:%' THEN NULL ELSE oposicion_atendida_en END,
                    oposicion_atendida_por = CASE WHEN oposicion_atendida_por LIKE 'sistema:%' THEN NULL ELSE oposicion_atendida_por END,
                    estado_oposicion = NULL, oposicion_sirve = NULL
                WHERE estado_oposicion IS NOT NULL AND estado_oposicion <> 'con_apoderado'
                  AND COALESCE(estado_oposicion_version, 1) < 2
                """
            )
            if cur.rowcount:
                print(f"Oposiciones clasificadas con la versión anterior que se vuelven a revisar: {cur.rowcount}")
            # Corrección de los leads que ya tenían un apoderado/gestor posterior a
            # la oposición (detectado antes de que esto cambiara el estado).
            cur.execute(SQL_PASAR_A_APODERADO)
            if cur.rowcount:
                print(f"Leads que pasaron a 'apoderado/gestor' (dejan de ser leads y de revisarse): {cur.rowcount}")
        conn.commit()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.boletin:
                cur.execute(SQL_DE_BOLETIN, (args.boletin,))
            else:
                cur.execute(SQL_PENDIENTES)
            pendientes = cur.fetchall()

        pendientes_todos = pendientes
        if args.limit:
            pendientes = pendientes[: args.limit]

        if args.boletin:
            print(f"Búsqueda manual de oposiciones en el boletín {args.boletin}: {len(pendientes)} leads")
        else:
            print(f"Leads con revisión de oposición pendiente (hitos {HITOS[0]}/{HITOS[1]}/{HITOS[2]} días + rechequeos): {len(pendientes)}")

        s = crear_sesion()
        con_oposicion = 0
        pasados_a_apoderado = 0
        revisadas = 0
        sin_consulta = 0
        por_estado: dict[str, int] = {}
        cortada = False
        for i, fila in enumerate(pendientes, 1):
            if args.max_minutes and (time.time() - inicio) / 60 >= args.max_minutes:
                print(f"Se llegó al tope de {args.max_minutes:g} minutos: el resto sigue en la próxima corrida.")
                cortada = True
                break
            if monitor_bloqueo.debe_cortar():
                print("Se corta la corrida por bloqueos seguidos de INPI: el resto sigue en la próxima.")
                cortada = True
                break
            revisadas += 1
            edad = int(fila["edad"])
            acta = fila["acta"]
            fecha_pub = fila["fecha_publicacion"].isoformat()
            archivos = buscar_archivos_grilla(s, acta)
            if not archivos:
                # bloqueo del WAF u otro fallo de red: no marcamos como revisado,
                # para que la próxima corrida lo vuelva a intentar.
                sin_consulta += 1
                print(f"  [{i}/{len(pendientes)}] acta {acta}: no se pudo consultar Grilla Digital, reintento en la próxima corrida")
                # commit "vacío" para cerrar la transacción del SELECT inicial
                # aunque no haya cambios — ver revisar_estado.py para el
                # motivo (incidente del 29/09/2026 en el manual).
                conn.commit()
                time.sleep(args.delay)
                continue

            fila_opo = buscar_fila_oposicion(archivos, fecha_pub)

            # El expediente (oposición estructurada + agente actual del titular)
            # se pide cuando hay algo que clasificar: oposición/vista en la Grilla,
            # una oposición que ya se venía siguiendo, o el último hito (33 días),
            # como control cruzado por si la Grilla no la mostró.
            if args.boletin or fila_opo or fila.get("tuvo_oposicion") or hito_por_edad(edad) >= len(HITOS):
                exp = consultar_expediente(s, acta)
                if exp.get("bloqueado"):
                    sin_consulta += 1
                    print(f"  [{i}/{len(pendientes)}] acta {acta}: INPI bloqueó la consulta del expediente, reintento en la próxima corrida")
                    conn.commit()
                    time.sleep(args.delay)
                    continue
                if exp.get("error_lectura"):
                    print(f"  [{i}/{len(pendientes)}] acta {acta}: aviso: no se pudo leer del todo la tabla de oposiciones del expediente")
            else:
                exp = {"oposiciones": [], "vistas": [], "titular_caracter": "",
                       "titular_agente": "", "titular_matricula": "", "error_lectura": False}

            est = clasificar_estado_oposicion(exp, archivos, fecha_pub)
            estado = est["estado"]
            tuvo_oposicion = estado != "sin_oposicion"
            por_estado[estado] = por_estado.get(estado, 0) + 1
            detalle = (
                f"{fila_opo.get('Fecha', '')} - {fila_opo.get('Indice', '')} - {fila_opo.get('Referencia', '')}"
                if fila_opo else ""
            )

            # Si es una oposición de TERCERO (no una vista de oficio de
            # INPI), bajamos y parseamos el Formulario real para sacar quién
            # se opone y por qué (ver descargar_formulario_oposicion) —
            # mejor esfuerzo: si falla o no está, seguimos solo con el
            # detalle crudo de Grilla Digital, no frena la detección.
            detalle_rico = {}
            if fila_opo and not fila.get("oponente_nombre") and "OPO" in (fila_opo.get("Referencia") or "").upper():
                detalle_rico = descargar_formulario_oposicion(s, archivos, fila_opo, acta_propia=acta)

            # El representante del titular se confirma con GESTION DEL TRAMITE del
            # expediente (no con una fila de poder de la Grilla, que puede ser del
            # oponente). Si está confirmado, el lead deja de serlo (SQL_PASAR_A_APODERADO).
            representacion_posterior = est["representacion_confirmada"] if tuvo_oposicion else None
            detalle_representacion = est["detalle"] if est["representacion_confirmada"] else None

            # "revisado_oposicion_en" queda como antes: solo cuando ya no hay nada
            # más que mirar (sin oposición pasado el día 33, o estado cerrado).
            cerrada = estado in ESTADOS_CERRADOS
            revisado_en = "now()" if ((not tuvo_oposicion and edad >= HITOS[-1]) or cerrada) else None
            hito = hito_por_edad(edad)
            # Contestada / levantada: ya se está trabajando (o ya no hay nada), así que
            # se marca como "atendida" y sale de los avisos y de los pendientes.
            auto_atendida = estado in ("contestada", "levantada")

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE marcas
                    SET tuvo_oposicion = %s, detalle_oposicion = %s,
                        revisado_oposicion_en = {revisado_en or 'revisado_oposicion_en'},
                        opo_chequeos = GREATEST(COALESCE(opo_chequeos, 0), %s),
                        opo_ultimo_chequeo_en = now(),
                        oposicion_detectada_en = CASE WHEN %s THEN COALESCE(oposicion_detectada_en, now())
                                                      ELSE oposicion_detectada_en END,
                        oponente_nombre = COALESCE(%s, oponente_nombre),
                        oponente_tipo_doc = COALESCE(%s, oponente_tipo_doc),
                        oponente_numero_doc = COALESCE(%s, oponente_numero_doc),
                        oponente_cuit = COALESCE(%s, oponente_cuit),
                        fundamento_oposicion = COALESCE(%s, fundamento_oposicion),
                        actas_marca_oponente = COALESCE(%s, actas_marca_oponente),
                        marca_oponente_denominacion = COALESCE(%s, marca_oponente_denominacion),
                        marca_oponente_numero_registro = COALESCE(%s, marca_oponente_numero_registro),
                        representacion_posterior_oposicion = %s,
                        detalle_representacion_posterior = %s,
                        estado_oposicion = %s,
                        estado_oposicion_version = 2,
                        estado_oposicion_detalle = %s,
                        estado_oposicion_en = now(),
                        oposicion_sirve = %s,
                        oposicion_fecha_presentacion = %s,
                        oposicion_fecha_notificacion = %s,
                        oposicion_fecha_vencimiento = %s,
                        oposicion_fecha_levantamiento = %s,
                        oposicion_agente_oponente = %s,
                        oposicion_posible_apoderado = %s,
                        oposicion_atendida = CASE WHEN %s THEN true ELSE oposicion_atendida END,
                        oposicion_atendida_en = CASE WHEN %s AND oposicion_atendida IS NOT TRUE THEN now()
                                                     ELSE oposicion_atendida_en END,
                        oposicion_atendida_por = CASE WHEN %s AND oposicion_atendida IS NOT TRUE THEN %s
                                                      ELSE oposicion_atendida_por END
                    WHERE acta = %s
                    """,
                    (
                        tuvo_oposicion, (detalle or None) if tuvo_oposicion else None,
                        hito, tuvo_oposicion,
                        detalle_rico.get("oponente_nombre"),
                        detalle_rico.get("oponente_tipo_doc"),
                        detalle_rico.get("oponente_numero_doc"),
                        detalle_rico.get("oponente_cuit"),
                        detalle_rico.get("fundamento_oposicion"),
                        detalle_rico.get("actas_marca_oponente"),
                        detalle_rico.get("marca_oponente_denominacion"),
                        detalle_rico.get("marca_oponente_numero_registro"),
                        representacion_posterior,
                        detalle_representacion,
                        estado if tuvo_oposicion else None,
                        est["detalle"] or None,
                        est["sirve"],
                        est["presentacion"], est["notificacion"], est["vencimiento"], est["levantamiento"],
                        est["agente_oponente"],
                        est["posible_apoderado"] if tuvo_oposicion else None,
                        auto_atendida, auto_atendida, auto_atendida, f"sistema: {estado}",
                        acta,
                    ),
                )
            if representacion_posterior:
                with conn.cursor() as cur:
                    cur.execute(SQL_PASAR_A_APODERADO + " AND acta = %s", (acta,))
                pasados_a_apoderado += 1
            conn.commit()

            if tuvo_oposicion:
                con_oposicion += 1
                # No se imprime el titular ni el detalle/oponente: son datos
                # personales de terceros (nombre, CUIT/DNI, fundamento) que no
                # deben quedar en los logs de Actions (repo público). Sí se
                # siguen guardando en la base (UPDATE de arriba), sin cambios.
                extra = " (el titular ya tiene representante: deja de ser lead)" if representacion_posterior else ""
                print(f"  [{i}/{len(pendientes)}] acta {acta}: OPOSICIÓN/VISTA — {estado}{extra}")
            else:
                print(f"  [{i}/{len(pendientes)}] acta {acta}: sin oposición")

            time.sleep(args.delay)

        print(f"\nRevisadas: {revisadas - sin_consulta} de {len(pendientes)} pendientes "
              f"({sin_consulta} sin poder consultar). Con oposición/vista detectada: {con_oposicion}.")
        if por_estado:
            print("Por estado: " + ", ".join(f"{k}={v}" for k, v in sorted(por_estado.items())))
        if args.boletin:
            # Se anota solo si la búsqueda manual revisó TODOS los leads del boletín.
            if cortada or sin_consulta or len(pendientes) < len(pendientes_todos):
                print("La búsqueda manual no llegó a revisar todos los leads: no se anota la fecha en /boletines.")
            else:
                with conn.cursor() as cur:
                    cur.execute("UPDATE boletines SET oposiciones_buscadas_en = now() WHERE numero = %s",
                                (args.boletin,))
                conn.commit()
        registrar("revisar_oposiciones.yml", {
            "revisadas": revisadas - sin_consulta, "con_oposicion": con_oposicion,
            "sin_oposicion": revisadas - sin_consulta - con_oposicion,
            "pendientes_al_arrancar": len(pendientes),
            "pasados_a_apoderado": pasados_a_apoderado,
        }, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
