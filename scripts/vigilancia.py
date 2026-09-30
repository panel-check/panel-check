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
     contiene a la otra, palabra en común; ajustado por clase. También avisa
     si un cliente (por CUIT) presentó una marca nueva con otro agente.
  3. Recompara las marcas de la cartera que cambiaron (nombre, términos extra,
     clase) contra todo lo que sigue dentro del plazo de oposición.

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


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not dsn:
        sys.exit("Falta DATABASE_URL (o DATABASE_PUBLIC_URL)")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cartera.crear_tablas(cur)
        conn.commit()
        mat = vigilancia_core.sumar_por_matricula(conn)
        print(f"Por matrícula: {mat['sumadas']} sumadas solas a la cartera, {mat['propuestas']} propuestas pendientes.")
        st = vigilancia_core.comparar(conn)
        resumen = (f"Marcas vigiladas: {st['marcas_cartera']}. Solicitudes comparadas: {st['comparadas']}. "
                   f"Alertas nuevas de parecido: {st['alertas_similitud']}. Alertas de otro agente: {st['alertas_otro_agente']}. "
                   f"Marcas de la cartera recomparadas: {st['recomparadas_cartera']}.")
        print(f"::notice::{resumen}")
        print(f"\n{resumen}")
        registrar("vigilancia.yml", {
            "marcas_vigiladas": st["marcas_cartera"], "solicitudes_comparadas": st["comparadas"],
            "alertas_parecido": st["alertas_similitud"], "alertas_otro_agente": st["alertas_otro_agente"],
            "recomparadas": st["recomparadas_cartera"], "sumadas_por_matricula": mat["sumadas"],
            "propuestas_matricula": mat["propuestas"],
        }, conn)
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
