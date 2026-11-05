"""
Informe Oposición IA: el análisis interno de una oposición del INPI contra una
marca de un cliente (o de un lead), que arma la skill «revisador-de-oposiciones».

La skill entrega el análisis en texto (para leer en el chat) y, además, este JSON,
que se pega en el panel (Más → Informes → Nuevo informe → Oposición IA, o desde la
ficha del cliente o del lead) y queda guardado en informes_viabilidad con
tipo 'oposicion'. Es de uso interno: no arma PDF ni se le manda al cliente.

Esquema (versión 1). Todos los textos en español; las fechas como «DD/MM/AAAA».
{
  "tipo": "informe_oposicion_ia",
  "version": 1,
  "fecha_analisis": "05/11/2026",
  "resumen_express": {                       # obligatorio: la ficha ⚡ del texto
    "cliente_marca": "...", "oponente": "...", "que_cita": "...", "plazos": "...",
    "chances": "...", "ojo": "...", "camino_sugerido": "...", "falta": "..."
  },
  "resumen": "2 o 3 líneas",
  "marca_cliente": {                         # obligatorio: denominacion
    "denominacion": "PEAKS", "tipo": "denominativa|mixta|figurativa",
    "titular": "...", "cuit": "...", "agente": "...",
    "actas_opuestas": [{"acta", "clase", "productos", "limitacion", "fecha_presentacion",
                        "boletin", "fecha_publicacion", "estado"}],
    "otras_actas": [{"acta", "denominacion", "clase", "estado", "nota"}]
  },
  "oposiciones": [                           # obligatorio: una por acta opuesta
    {"acta_cliente", "clase", "fecha_oposicion", "en_termino": true, "oponente",
     "agente_oponente", "marcas_citadas": ["acta", ...], "articulos": ["art. 3 inc. a Ley 22.362", ...],
     "fundamento", "fecha_notificacion", "notificacion": "real|esperada",
     "vencimiento_negociacion": "DD/MM/AAAA"}
  ],
  "marcas_oponente": [
    {"acta", "registro", "denominacion", "clase", "tipo", "productos", "limitacion",
     "origen": "solicitud nueva|renovación", "fecha_presentacion", "fecha_concesion",
     "vigencia_hasta", "estado", "titular", "es_del_oponente": true,
     "anterior_al_cliente": true, "falta_de_uso_posible": false, "nota"}
  ],
  "comparacion": {
    "signos": {"grafica", "fonetica", "conceptual", "conclusion"},
    "productos": [{"item", "situacion": "idéntico|incluido|afuera", "detalle"}],
    "convivencia": {"resumen", "marcas": [{"acta", "denominacion", "clase", "titular",
                                           "estado", "se_opuso_el_oponente"}]}
  },
  "palancas": [{"palanca", "detalle"}],
  "chances": {"nivel": "muy bajas|bajas|bajas a medias|medias|medias a altas|altas", "motivo": "..."},
  "caminos": [{"titulo", "detalle", "costo", "recomendado": true}],
  "preguntas_cliente": ["..."],
  "a_confirmar_pame": ["..."],
  "limitaciones": ["..."]
}
"""

import datetime as _dt
import re

TIPO = "informe_oposicion_ia"
NIVELES = ["muy bajas", "bajas", "bajas a medias", "medias", "medias a altas", "altas", "muy altas"]


class OposicionInvalida(ValueError):
    pass


def _txt(v, largo=400) -> str:
    if v is None:
        return ""
    return " ".join(str(v).replace("\r", "").split())[:largo]


def es_oposicion(d: dict) -> bool:
    if d.get("tipo") == TIPO:
        return True
    return d.get("tipo") in (None, "") and "oposiciones" in d and "marca_cliente" in d


def nivel_chances(v) -> str:
    """«Bajas», «bajas_a_medias», «Media», «BAJAS A MEDIAS» → uno de NIVELES (o "" si no se entiende)."""
    t = _txt(v, 60).lower().replace("_", " ").replace("-", " ")
    t = re.sub(r"\bmedia\b", "medias", re.sub(r"\balta\b", "altas", re.sub(r"\bbaja\b", "bajas", t)))
    t = " ".join(t.split())
    return t if t in NIVELES else ""


def marca(d: dict) -> str:
    mc = d.get("marca_cliente") if isinstance(d.get("marca_cliente"), dict) else {}
    return _txt(mc.get("denominacion"), 200)


def normalizar(d: dict) -> dict:
    """Controla lo mínimo para que el informe se pueda listar y leer. Devuelve el mismo dict."""
    if d.get("tipo") in ("informe_viabilidad_cliente", "informe_viabilidad_interno"):
        raise OposicionInvalida("Ese es un JSON del informe preliminar, no el de oposición.")
    if not es_oposicion(d):
        raise OposicionInvalida("No es un informe de oposición (se espera «tipo»: «informe_oposicion_ia»).")
    if not marca(d):
        raise OposicionInvalida("Falta la marca del cliente («marca_cliente.denominacion»).")
    rx = d.get("resumen_express")
    if not isinstance(rx, dict) or not any(_txt(v) for v in rx.values()):
        raise OposicionInvalida("Falta el «resumen_express» (la ficha ⚡ con cliente, oponente, plazos, chances y camino).")
    ops = d.get("oposiciones")
    if not isinstance(ops, list) or not ops or not all(isinstance(o, dict) for o in ops):
        raise OposicionInvalida("Falta la lista «oposiciones» (una por cada acta del cliente que se opuso).")
    for i, o in enumerate(ops, 1):
        if not _txt(o.get("oponente")):
            raise OposicionInvalida(f"A la oposición {i} le falta el «oponente».")
    ch = d.get("chances")
    if isinstance(ch, str):
        d["chances"] = ch = {"nivel": ch, "motivo": ""}
    if isinstance(ch, dict) and ch.get("nivel") and not nivel_chances(ch["nivel"]):
        raise OposicionInvalida(f"«chances.nivel» tiene que ser uno de: {', '.join(NIVELES)} (vino «{_txt(ch['nivel'], 40)}»).")
    if isinstance(ch, dict) and ch.get("nivel"):
        ch["nivel"] = nivel_chances(ch["nivel"])
    texto = str(d)
    if "[FIRMA]" in texto:
        raise OposicionInvalida("Quedó «[FIRMA]» en el JSON: el informe de oposición es interno y no lleva firma.")
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
    ops = [o for o in (d.get("oposiciones") or []) if isinstance(o, dict)]
    ch = d.get("chances") if isinstance(d.get("chances"), dict) else {}
    vtos = sorted(f for f in (_fecha(o.get("vencimiento_negociacion")) for o in ops) if f)
    rx = d.get("resumen_express") if isinstance(d.get("resumen_express"), dict) else {}
    oponentes = []
    for o in ops:
        n = _txt(o.get("oponente"), 120)
        if n and n not in oponentes:
            oponentes.append(n)
    return {
        "chances": nivel_chances(ch.get("nivel")),
        "motivo": _txt(ch.get("motivo"), 300),
        "oponentes": oponentes[:5],
        "actas": [_txt(o.get("acta_cliente"), 20) for o in ops if o.get("acta_cliente")][:10],
        "oposiciones": len(ops),
        "vencimiento": vtos[0].isoformat() if vtos else "",
        "camino": _txt(rx.get("camino_sugerido"), 300),
    }
