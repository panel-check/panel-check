"""
Paso separado de inspeccionar_acta.py (workflow inspeccionar_acta.yml):
GitHub Actions solo guarda hasta 10 annotations por step, y el paso de
diagnóstico ya las agotó. Este paso corre aparte, con su propio cupo, y
imprime en UNA sola línea todo lo que hace falta para armar el insert
manual: RESOLUCIÓN parseada + CUIT + nombre del titular.

Uso:
    python3 resumen_final_acta.py 4534497
"""

import re
import sys

from validar_leads import BASE, crear_sesion, _get_con_reintentos, parsear_resolucion, RE_CUIT_SPAN


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python3 resumen_final_acta.py <acta>")
    acta = sys.argv[1]

    s = crear_sesion()
    r = _get_con_reintentos(
        lambda: s.post(
            f"{BASE}/MarcasConsultas/Resultado",
            headers={"Referer": f"{BASE}/MarcasConsultas/Grilla"},
            data={"acta": acta},
            timeout=30,
        )
    )
    if "Web Page Blocked" in r.text or "Attack ID" in r.text:
        print(f"::error::acta {acta} bloqueada por el WAF de INPI")
        sys.exit(1)

    resolucion = parsear_resolucion(r.text)

    m_cuit = RE_CUIT_SPAN.search(r.text)
    cuit = m_cuit.group(1) if m_cuit else None

    texto = re.sub(r"<[^>]+>", " ", r.text)
    texto = re.sub(r"\s+", " ", texto)
    m_tabla = re.search(r"TIPO Y NOMBRE DEL TITULAR(.{0,500})", texto.upper())
    tabla_titular = m_tabla.group(1).strip() if m_tabla else None

    print(f"::notice::RESUMEN acta {acta} | resolucion={resolucion} | cuit={cuit} | tabla_titular={tabla_titular}")


if __name__ == "__main__":
    main()
