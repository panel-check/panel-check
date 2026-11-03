"""«Contactado» por marca del mismo nombre (pedido del 03/11/2026).

«Contactado» es por acta, pero si a un titular ya se le escribió por una marca, una oposición o una vista
en OTRA acta suya con el MISMO NOMBRE (aunque sea de otra clase o de otro tipo, denominativa o mixta) ya
está cubierta por ese contacto: esa acta se marca contactada sola.

Mismo nombre = la denominación (o la de INPI si falta) comparada sin tildes, mayúsculas ni signos/espacios.
Mismo titular = la clave del CRM (tabla crm_claves que mantiene el panel) o, si el acta todavía no está
ahí, el mismo CUIT. Las marcas sin nombre (figurativas) no se cruzan.

El panel hace lo mismo en el momento de contactar (_propagar_contactado_misma_marca en panel/app.py, con
el mismo criterio); este módulo cubre el otro momento: cuando un proceso detecta una oposición o vista
nueva en un acta cuya gemela ya estaba contactada.
"""

NOMBRE_NORMALIZADO_SQL = """upper(regexp_replace(translate(COALESCE(NULLIF(m.denominacion, ''), m.denominacion_inpi, ''),
    'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN'), '[^A-Za-z0-9]', '', 'g'))"""

SQL_MARCAR = f"""
WITH base AS (
    SELECT m.acta,
           m.contactado IS TRUE AS contactado,
           (m.tuvo_oposicion IS TRUE OR m.tuvo_vista IS TRUE) AS con_alerta,
           COALESCE(cc.clave, NULLIF(trim(m.cuit), '')) AS titular,
           {NOMBRE_NORMALIZADO_SQL} AS nombre
    FROM marcas m
    LEFT JOIN crm_claves cc ON cc.acta = m.acta
    WHERE m.contactado IS TRUE OR m.tuvo_oposicion IS TRUE OR m.tuvo_vista IS TRUE
),
a_marcar AS (
    SELECT DISTINCT a.acta
    FROM base a
    JOIN base b ON b.contactado AND b.acta <> a.acta AND b.titular = a.titular AND b.nombre = a.nombre
    WHERE NOT a.contactado AND a.con_alerta AND a.titular IS NOT NULL AND a.nombre <> ''
)
UPDATE marcas t SET contactado = true, contactado_en = COALESCE(t.contactado_en, now())
FROM a_marcar x
WHERE t.acta = x.acta AND t.contactado IS NOT TRUE
RETURNING t.acta
"""


def marcar_por_misma_marca(conn) -> list:
    """Marca contactadas las actas con oposición o vista cuya gemela (mismo titular y mismo nombre) ya está
    contactada. Hace commit. Devuelve las actas marcadas. De mejor esfuerzo: si falla, no corta el proceso."""
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_MARCAR)
            actas = sorted(r[0] for r in cur.fetchall())
        conn.commit()
    except Exception as e:  # noqa: BLE001 — es un agregado; no debe tirar abajo la revisión
        conn.rollback()
        print(f"No se pudieron marcar las contactadas por mismo nombre: {e}", flush=True)
        return []
    if actas:
        print(f"Marcadas como contactadas por tener el mismo nombre que una marca ya contactada del titular: "
              f"{len(actas)} ({', '.join(actas[:30])}{'…' if len(actas) > 30 else ''})", flush=True)
    return actas
