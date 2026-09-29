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
        # bastante antes y bastante después: el nombre puede venir antes
        # (mismo <label>/fila) o después (siguiente <label>).
        crudo = html[max(0, i - 700): i + 300].replace("\n", " ")
        crudo = re.sub(r"\s+", " ", crudo)
        print(f"::notice::HTML alrededor del CUIT @{i}: {crudo}")
    else:
        print("::notice::CUIT no encontrado en esta corrida")

    for palabra in ["RAZON SOCIAL", "APELLIDO", "NOMBRE Y APELLIDO", "DENOMINACION DEL TITULAR"]:
        m = re.search(re.escape(palabra), html, re.I)
        if m:
            i = m.start()
            crudo = html[i: i + 300].replace("\n", " ")
            crudo = re.sub(r"\s+", " ", crudo)
            print(f"::notice::HTML {palabra} @{i}: {crudo}")


if __name__ == "__main__":
    main()
