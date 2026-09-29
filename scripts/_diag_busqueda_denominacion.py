"""Diagnostico descartable: confirma que buscar_marca_por_denominacion
devuelve resultados utiles (no solo 0) para las 2 denominaciones que
resolver_marca_oponente no pudo auto-resolver, para validar que el boton
"Buscar" del panel funciona en la practica."""
from validar_leads import crear_sesion, buscar_marca_por_denominacion

s = crear_sesion()
for d in ["BALI STONE", "BALISTONE", "MISION DE AMOR", "MISIONDEAMOR"]:
    filas = buscar_marca_por_denominacion(s, d)
    print(f"::notice::--- {d!r}: {len(filas)} resultados ---")
    for f in filas[:8]:
        print(f"::notice::   {f}")
