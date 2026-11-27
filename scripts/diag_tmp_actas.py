"""Diagnóstico temporal de solo lectura (27/11/2026): qué guardó el panel de unas actas. Sin datos personales."""
import os, sys, json
import psycopg2, psycopg2.extras
sys.path.insert(0, "../panel")
import notificaciones_inpi as ni
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.set_session(readonly=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
for acta in sys.argv[1:]:
    cur.execute("""SELECT grilla_claves, ultimo_movimiento, ultimo_movimiento_fecha, ubicacion_actual, ubicacion_fecha,
                          estado_tramite, consultado_en FROM cartera_marcas WHERE acta = %s""", (acta,))
    m = cur.fetchone()
    print(f"== acta {acta}: {'en cartera' if m else 'NO está en cartera_marcas'}")
    if m:
        for k in ("ultimo_movimiento", "ultimo_movimiento_fecha", "ubicacion_actual", "ubicacion_fecha", "estado_tramite", "consultado_en"):
            print(f"   {k}: {m[k]}")
        for c in (m["grilla_claves"] or []):
            print(f"   grilla: {c}   → {(ni.resultado_inpi(str(c).replace('|', ' ')) or {}).get('titulo')}")
    cur.execute("SELECT tipo, texto, detectado_en FROM cartera_novedades WHERE acta = %s ORDER BY detectado_en DESC LIMIT 12", (acta,))
    for n in cur.fetchall():
        print(f"   novedad {n['detectado_en']:%d/%m %H:%M} [{n['tipo']}] {n['texto'][:140]}")
cur.execute("SELECT id, marca, servicio, acta, actas_extra, contratacion FROM trabajos WHERE marca ILIKE 'MATEACIDO%%'")
for t in cur.fetchall():
    print(f"trabajo {t['id']} {t['marca']} {t['servicio']} acta={t['acta']} extra={t['actas_extra']} contr={t['contratacion']}")
