"""
Herramienta manual (workflow «Probar verificador de marca»): corre el verificador de la web
contra INPI de verdad, sin guardar nada ni mandar mails, y muestra qué devuelve para cada marca.

Sirve para chequear tres cosas antes de confiar en el resultado:
  1. que la búsqueda de INPI funciona desde el servidor (marca conocida → tiene que traer filas;
     si trae cero, el verificador podría decir «disponible» por error);
  2. cómo cargó INPI los campos (estados, clases) de las marcas que aparecen;
  3. qué veredicto sale para cada nombre.

Solo se imprimen datos públicos de marcas (acta, denominación, clase, estado), nunca titulares:
los logs de Actions son públicos.

Uso:
    python3 probar_verificador.py "NIKE,Smarties,Mi marca inventada"
"""

import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

import verificador_core as vc  # noqa: E402

CONOCIDA = "NIKE"  # marca que seguro existe en INPI: si no aparece, algo anda mal con la búsqueda


def aviso(titulo: str, lineas: list, nivel: str = "notice"):
    # Un solo anotado por marca (GitHub corta en 10 por paso): las líneas van unidas por %0A.
    cuerpo = "%0A".join(l.replace("%", "%25").replace("\r", "").replace("\n", " ") for l in lineas)
    print(f"::{nivel} title={titulo}::{cuerpo}")


def probar(marca: str) -> dict:
    t0 = time.time()
    try:
        filas = vc.buscar_inpi(marca)
    except vc.ErrorInpi as e:
        aviso(f"{marca}: ERROR", [f"INPI no respondió bien: {e}", f"tardó {time.time() - t0:.1f} s"], "error")
        return {"marca": marca, "error": str(e)}
    seg = time.time() - t0
    r = vc.evaluar(marca, filas)
    lineas = [f"Veredicto: {r['verdict']} · idénticas {r['exactCount']} · parecidas {r['similarCount']} · "
              f"filas de INPI {len(filas)}{' (página llena: puede haber más)' if r['posible_mas'] else ''} · {seg:.1f} s"]
    estados = Counter(f.get("estado") or "(vacío)" for f in filas)
    if estados:
        lineas.append("Estados vistos: " + "; ".join(f"{k} ×{v}" for k, v in estados.most_common(8)))
    clases = Counter(str(f.get("clase")) for f in filas)
    if clases:
        lineas.append("Clases vistas: " + ", ".join(f"{k} ×{v}" for k, v in clases.most_common(8)))
    for m in r["muestras"][:8]:
        lineas.append(f"  {m['tipo']:8} {m['puntaje']:3}  acta {m['acta']}  «{m['denominacion']}»  clase {m['clase']}  {m['estado'] or '—'}")
    aviso(f"{marca}: {r['verdict']}", lineas)
    return {"marca": marca, "filas": len(filas), "veredicto": r["verdict"]}


def main():
    marcas = [m.strip() for m in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if m.strip()][:6]
    if not marcas:
        print("Uso: probar_verificador.py \"MARCA1,MARCA2\"")
        sys.exit(2)
    resultados = []
    for i, m in enumerate([CONOCIDA] + [x for x in marcas if x.upper() != CONOCIDA]):
        if i:
            time.sleep(vc.PAUSA_ENTRE_PEDIDOS)
        resultados.append(probar(m))
    control = resultados[0]
    if control.get("error") or not control.get("filas"):
        aviso("CONTROL FALLÓ", [f"La marca conocida «{CONOCIDA}» no devolvió filas.",
                                "El verificador podría estar diciendo «disponible» por error: no habilitarlo en la web hasta revisar."], "error")
        sys.exit(1)
    print(f"Control OK: «{CONOCIDA}» devolvió {control['filas']} filas.")


if __name__ == "__main__":
    main()
