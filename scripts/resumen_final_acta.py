"""
Paso separado de inspeccionar_acta.py (workflow inspeccionar_acta.yml):
GitHub Actions solo guarda hasta 10 annotations por step, y el paso de
diagnóstico ya las agotó. Este paso corre aparte, con su propio cupo.

Ronda 2: ya confirmamos estado_tramite/fecha_vencimiento_marca/cuit. Faltan
fecha_concesion/numero_disposicion (el regex de DISPOSICION no matcheó) y el
nombre real del titular (el header de tabla que encontramos no tenía la fila
de datos al lado). Volcamos HTML crudo (no texto plano) alrededor de esos dos
puntos para ver la estructura real.

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

    html = r.text

    resolucion = parsear_resolucion(html)
    m_cuit = RE_CUIT_SPAN.search(html)
    print(f"::notice::RESUMEN acta {acta} | resolucion={resolucion} | cuit={m_cuit.group(1) if m_cuit else None}")

    # HTML crudo alrededor de "DISPOSICION" (para ver el separador real entre
    # Fecha y Numero — el regex asume "Fecha ... - Numero ...").
    for m in list(re.finditer("DISPOSICION", html, re.I))[:2]:
        i = m.start()
        crudo = html[i:i + 400].replace("\n", " ")
        crudo = re.sub(r"\s+", " ", crudo)
        print(f"::notice::HTML DISPOSICION @{i}: {crudo}")

    # HTML crudo del primer "TIPO Y NOMBRE DEL TITULAR" (para ver la fila real).
    m_tit = re.search("TIPO Y NOMBRE DEL TITULAR", html, re.I)
    if m_tit:
        i = m_tit.start()
        crudo = html[i:i + 600].replace("\n", " ")
        crudo = re.sub(r"\s+", " ", crudo)
        print(f"::notice::HTML TITULAR @{i}: {crudo}")


if __name__ == "__main__":
    main()
