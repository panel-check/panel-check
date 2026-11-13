"""
Vigilancia marcaria: compara las solicitudes nuevas de INPI con las marcas de
la cartera de clientes y crea alertas (Clientes → Vigilancia).

Corre después de cada escaneo de actas y del pipeline de boletines, que son
los que traen solicitudes nuevas:
  1. Suma a la cartera las solicitudes presentadas con una matrícula del
     estudio cuando el CUIT es de un cliente (el resto queda como propuesta en
     Clientes → Por matrícula).
  2. Compara cada solicitud nueva (boletines + escaneo directo, todavía no
     comparada) con las marcas vigiladas: parecido escrito, sonoro, una
     contiene a la otra, palabra en común y mismo concepto (traducciones y
     sinónimos de la lista que arma la IA en el panel, ver
     panel/conceptos_vigilancia.py); ajustado por clase. También avisa
     si un cliente (por CUIT) presentó una marca nueva con otro agente.
  3. Recompara las marcas de la cartera que cambiaron (nombre, términos extra,
     conceptos, clase) contra todo lo que sigue dentro del plazo de oposición.
  4. (Desde el 12/11/2026) Cada alerta nueva pasa por el juez con IA
     (panel/juez_vigilancia.py, Claude) si está ANTHROPIC_API_KEY; antes,
     borra las alertas con el plazo vencido y une las duplicadas.

Solo se alertan solicitudes cuyo plazo de oposición sigue abierto (todavía
sin publicar o publicadas hace menos de ~33 días). Una misma pareja nunca se
alerta dos veces. No usa INPI: solo la base. Sin datos personales en el log.

Uso:
    DATABASE_URL=... python3 vigilancia.py
"""

import os
import sys
import traceback

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "panel"))
import cartera  # noqa: E402
import vigilancia_core  # noqa: E402

from registro import registrar  # noqa: E402


def avisar_altas_por_telegram(conn):
    """Alertas ALTAS nuevas al tema «Vigilancia marcaria» del grupo de Telegram, apenas termina la corrida
    (el aviso de cada 15 minutos usa la misma marca de hasta dónde avisó: nunca se repiten). No rompe la corrida."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("Telegram: sin TELEGRAM_BOT_TOKEN, las alertas ALTAS salen en la próxima pasada de avisos.")
        return
    try:
        import avisos_telegram
        import telegram_avisos
        with conn.cursor() as cur:
            cur.execute(telegram_avisos.CREAR_TABLA)
        conn.commit()
        n = avisos_telegram.bloque_vigilancia(conn)
        print(f"Telegram: {n} alerta(s) ALTA avisada(s).")
    except Exception as ex:  # noqa: BLE001
        print(f"::warning::Telegram: no se pudieron avisar las alertas ALTAS ({type(ex).__name__}: {ex})")


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
        conn.commit()
        man = vigilancia_core.mantenimiento(conn)
        print(f"Mantenimiento: {man['vencidas_borradas']} alertas con el plazo vencido borradas, "
              f"{man['duplicadas_unidas']} duplicadas unidas, {man.get('pasadas_a_pendiente', 0)} ocultas hasta que las revise la IA, "
              f"{man.get('fuera_de_umbral', 0)} borradas por no llegar al umbral según la clase.")
        mat = vigilancia_core.sumar_por_matricula(conn)
        print(f"Por matrícula: {mat['sumadas']} sumadas solas a la cartera, {mat['propuestas']} propuestas pendientes.")
        st = vigilancia_core.comparar(conn)
        resumen = (f"Marcas vigiladas: {st['marcas_cartera']}. Solicitudes comparadas: {st['comparadas']}. "
                   f"Alertas nuevas de parecido: {st['alertas_similitud']}. Alertas de otro agente: {st['alertas_otro_agente']}. "
                   f"Marcas de la cartera recomparadas: {st['recomparadas_cartera']}. "
                   f"Juez IA: {'activo' if st['juez_activo'] else 'apagado'}; pares juzgados {st['juzgadas']}, "
                   f"descartados por IA {st['descartadas_ia']}, quedan sin revisar (ocultas) {st.get('pendientes_ia', 0)}, "
                   f"llamadas {st['llamadas_ia']}, errores {st['errores_ia']}, costo máx. USD {st['costo_ia_usd_max']}. "
                   f"Expedientes leídos para productos y servicios: {st.get('consultas_productos_inpi', 0)}.")
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
        registrar("vigilancia.yml", {
            "marcas_vigiladas": st["marcas_cartera"], "solicitudes_comparadas": st["comparadas"],
            "alertas_parecido": st["alertas_similitud"], "alertas_otro_agente": st["alertas_otro_agente"],
            "recomparadas": st["recomparadas_cartera"], "sumadas_por_matricula": mat["sumadas"],
            "propuestas_matricula": mat["propuestas"],
            "juez_activo": st["juez_activo"], "juzgadas_ia": st["juzgadas"], "descartadas_ia": st["descartadas_ia"],
            "reevaluadas_ia": st["reevaluadas"], "llamadas_ia": st["llamadas_ia"], "errores_ia": st["errores_ia"],
            "costo_ia_usd_max": st["costo_ia_usd_max"], "vencidas_borradas": man["vencidas_borradas"],
            "duplicadas_unidas": man["duplicadas_unidas"], "pendientes_ia": st.get("pendientes_ia", 0),
            "fuera_de_umbral": man.get("fuera_de_umbral", 0),
            "consultas_productos_inpi": st.get("consultas_productos_inpi", 0),
        }, conn)
        avisar_altas_por_telegram(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        linea = f"{type(e).__name__}: {e}".replace("\n", " ")
        print(f"::error::vigilancia falló: {linea}")
        traceback.print_exc()
        sys.exit(1)
