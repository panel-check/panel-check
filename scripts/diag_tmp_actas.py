"""Diagnóstico temporal (27/11/2026): grilla y ubicación de unas actas, sin datos personales."""
import sys
sys.path.insert(0, "../panel")
import inpi_lead as il
for acta in sys.argv[1:]:
    info = il.consultar_expediente(acta)
    print(f"== acta {acta}: {info.get('estado_consulta')} {info.get('error') or ''}")
    for k in ("tipo", "clase", "estado_tramite", "ubicacion_actual", "ubicacion_fecha", "ultimo_movimiento",
              "ultimo_movimiento_fecha", "tuvo_oposicion", "detalle_oposicion"):
        print(f"   {k}: {info.get(k)}")
    for c in info.get("grilla_claves") or []:
        print(f"   grilla: {c}")
