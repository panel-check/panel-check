"""
Paso separado de inspeccionar_acta.py (workflow inspeccionar_acta.yml):
GitHub Actions solo guarda hasta 10 annotations por step. Este paso corre
aparte, con su propio cupo.

Ronda 3: ya tenemos estado_tramite/fecha_concesion/numero_disposicion/
fecha_vencimiento_marca/cuit. Falta el nombre real del titular — la tabla
"TIPO Y NOMBRE DEL TITULAR" que encontramos antes era de historial de
cambios de rubro, no la ficha principal. Volcamos HTML crudo alrededor del
CUIT (20408863210 en la corrida anterior) y de "RAZON SOCIAL"/"APELLIDO"/
"NOMBRE", que suelen estar en la misma fila/label que el CUIT real.

Uso:
    python3 resumen_final_acta.py 4534497
"""

import re
import sys

from validar_leads import BASE, crear_sesion, _get_con_reintentos, RE_CUIT_SPAN


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

    m_cuit = RE_CUIT_SPAN.search(html)
    if m_cuit:
        i = m_cuit.start()
        # Ventana grande hacia atrás: el bloque TITULARIDAD tiene varios
        # <label class="input"> antes del de CUIT (PAIS, TIPO, y el nombre
        # debería estar entre ellos) — 700 no alcanzó, probamos con 1800.
        inicio = max(0, i - 1800)
        crudo = html[inicio:i + 50].replace("\n", " ")
        crudo = re.sub(r"\s+", " ", crudo)
        crudo = re.sub(r"<(?!label|/label|span|/span)[^>]*>", "", crudo)  # solo dejamos label/span
        print(f"::notice::HTML antes del CUIT @{i}: {crudo[-950:]}")


if __name__ == "__main__":
    main()
