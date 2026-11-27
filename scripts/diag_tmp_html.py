"""Diagnóstico temporal (27/11/2026): filas de la Grilla Digital (fecha, índice, referencia)."""
import sys
sys.path.insert(0, "../panel")
import inpi_lead as il
acta = sys.argv[1]
s = il._crear_sesion()
arch = il._buscar_archivos_grilla(s, acta, timeout=40)
print(f"== filas: {None if arch is None else len(arch)}")
for a in arch or []:
    print(f"== grilla: {a.get('Fecha')} | {a.get('Indice')} | {a.get('Referencia')}")
