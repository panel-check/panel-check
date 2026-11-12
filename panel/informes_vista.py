"""
Informe Vista IA: el análisis interno de una vista de fondo del INPI (registrabilidad, por ejemplo
art. 2 inc. a de la Ley 22.362) contra la solicitud de un cliente (o de un lead), que arma la skill
«respondervistas» cuando se le pide solo el análisis.

La skill entrega el análisis en texto (para leer en el chat) y, además, este JSON, que se pega en el
panel (Más → Informes → Nuevo informe → Informe Vista IA, o desde la ficha del cliente o del lead) y
queda guardado en informes_viabilidad con tipo 'vista'. Es de uso interno: no arma PDF ni se le manda
al cliente. Las vistas van separadas de las oposiciones (otro trámite, otros plazos).

Esquema (versión 1). Todos los textos en español; las fechas como «DD/MM/AAAA».
{
  "tipo": "informe_vista_ia",
  "version": 1,
  "fecha_analisis": "12/11/2026",
  "resumen_express": {                       # obligatorio: la ficha ⚡ del texto
    "cliente_marca": "...", "vista": "...", "que_objeta": "...", "plazos": "...",
    "chances": "...", "ojo": "...", "camino_sugerido": "...", "falta": "..."
  },
  "resumen": "2 o 3 líneas",
  "marca_cliente": {                         # obligatorio: denominacion
    "denominacion": "...", "tipo": "denominativa|mixta|...", "titular": "...", "cuit": "...", "agente": "...",
    "actas_observadas": [{"acta", "tipo", "clase", "productos", "limitacion", "fecha_presentacion", "estado"}],
    "otras_actas": [{"acta", "denominacion", "clase", "estado", "nota"}]
  },
  "vistas": [                                # obligatorio: una por acta observada
    {"acta_cliente", "clase", "tipo_vista", "articulos": ["art. 2 inc. a Ley 22.362"], "fundamento",
     "fecha_vista", "if_vista", "fecha_notificacion", "if_cedula", "vencimiento": "DD/MM/AAAA",
     "vencimiento_inpi", "prorroga"}
  ],
  "antecedentes": {
    "busquedas": [{"criterio", "clase", "fecha_consulta", "resultados", "detalle"}],
    "favorables": [{"acta", "registro", "denominacion", "tipo", "clase", "estado", "productos", "nota"}],
    "en_contra": [{"acta", "denominacion", "tipo", "resolucion", "estado", "motivo", "nota"}],
    "conclusion": "..."
  },
  "palancas": [{"palanca", "detalle"}],
  "chances": {"nivel": "muy bajas|bajas|bajas a medias|medias|medias a altas|altas|muy altas", "motivo": "..."},
  "caminos": [{"titulo", "detalle", "costo", "recomendado": true}],
  "preguntas_cliente": ["..."],
  "a_confirmar_pame": ["..."],
  "limitaciones": ["..."]
}
"""

import datetime as _dt

from informes_oposicion import NIVELES, nivel_chances  # mismas chances que el de oposición

TIPO = "informe_vista_ia"


class VistaInvalida(ValueError):
    pass


def _txt(v, largo=400) -> str:
    if v is None:
        return ""
    return " ".join(str(v).replace("\r", "").split())[:largo]


def es_vista(d: dict) -> bool:
    if d.get("tipo") == TIPO:
        return True
    return d.get("tipo") in (None, "") and "vistas" in d and "marca_cliente" in d and "oposiciones" not in d


def marca(d: dict) -> str:
    mc = d.get("marca_cliente") if isinstance(d.get("marca_cliente"), dict) else {}
    return _txt(mc.get("denominacion"), 200)


def normalizar(d: dict) -> dict:
    """Controla lo mínimo para que el informe se pueda listar y leer. Devuelve el mismo dict."""
    if d.get("tipo") in ("informe_viabilidad_cliente", "informe_viabilidad_interno"):
        raise VistaInvalida("Ese es un JSON del informe preliminar, no el de vista.")
    if d.get("tipo") == "informe_oposicion_ia":
        raise VistaInvalida("Ese es el JSON del informe de oposición: va en «Informe Oposición IA».")
    if not es_vista(d):
        raise VistaInvalida("No es un informe de vista (se espera «tipo»: «informe_vista_ia»).")
    if not marca(d):
        raise VistaInvalida("Falta la marca del cliente («marca_cliente.denominacion»).")
    rx = d.get("resumen_express")
    if not isinstance(rx, dict) or not any(_txt(v) for v in rx.values()):
        raise VistaInvalida("Falta el «resumen_express» (la ficha ⚡ con cliente, vista, plazos, chances y camino).")
    vs = d.get("vistas")
    if not isinstance(vs, list) or not vs or not all(isinstance(v, dict) for v in vs):
        raise VistaInvalida("Falta la lista «vistas» (una por cada acta del cliente observada).")
    for i, v in enumerate(vs, 1):
        if not _txt(v.get("acta_cliente")):
            raise VistaInvalida(f"A la vista {i} le falta el «acta_cliente».")
    ch = d.get("chances")
    if isinstance(ch, str):
        d["chances"] = ch = {"nivel": ch, "motivo": ""}
    if isinstance(ch, dict) and ch.get("nivel") and not nivel_chances(ch["nivel"]):
        raise VistaInvalida(f"«chances.nivel» tiene que ser uno de: {', '.join(NIVELES)} (vino «{_txt(ch['nivel'], 40)}»).")
    if isinstance(ch, dict) and ch.get("nivel"):
        ch["nivel"] = nivel_chances(ch["nivel"])
    if "[FIRMA]" in str(d):
        raise VistaInvalida("Quedó «[FIRMA]» en el JSON: el informe de vista es interno y no lleva firma.")
    d["tipo"] = TIPO
    d.setdefault("version", 1)
    return d


def _fecha(v):
    t = _txt(v, 40)
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(t[:10], f).date()
        except ValueError:
            pass
    return None


def resumen(d: dict) -> dict:
    """Lo que se ve en las listas sin abrir el informe."""
    vs = [v for v in (d.get("vistas") or []) if isinstance(v, dict)]
    ch = d.get("chances") if isinstance(d.get("chances"), dict) else {}
    vtos = sorted(f for f in (_fecha(v.get("vencimiento")) for v in vs) if f)
    rx = d.get("resumen_express") if isinstance(d.get("resumen_express"), dict) else {}
    articulos = []
    for v in vs:
        arts = v.get("articulos") if isinstance(v.get("articulos"), list) else [v.get("articulos")]
        for a in arts:
            a = _txt(a, 80)
            if a and a not in articulos:
                articulos.append(a)
    return {
        "chances": nivel_chances(ch.get("nivel")),
        "motivo": _txt(ch.get("motivo"), 300),
        "articulos": articulos[:5],
        "actas": [_txt(v.get("acta_cliente"), 20) for v in vs if v.get("acta_cliente")][:10],
        "vistas": len(vs),
        "vencimiento": vtos[0].isoformat() if vtos else "",
        "camino": _txt(rx.get("camino_sugerido"), 300),
    }
