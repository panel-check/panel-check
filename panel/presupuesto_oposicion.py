"""
Presupuesto por el trabajo de una oposición (botón «Presupuesto» de la pestaña
«Análisis de marca» de la ficha del titular).

Cuando se aprieta el botón, el PDF de la marca cambia de formato: en lugar del
análisis escrito sale este presupuesto, con las alternativas a evaluar, los
honorarios de la negociación de la oposición y el presupuesto para registrar una
nueva marca. Los datos de la marca y sus oposiciones salen igual que siempre.

Los montos (que cambian de vez en cuando) se guardan una vez para todo el equipo
en vigilancia_config (clave presupuesto_oposicion, como JSON) y se pueden ajustar
en pantalla para un envío. Este módulo no toca la base: las funciones de lectura
y escritura reciben un cursor.
"""

import json
import re

import presupuestos as pres

CONFIG_CLAVE = "presupuesto_oposicion"

# Valores por defecto (los del modelo de texto acordado).
DEFAULTS = {
    "honorarios_negociacion": 790000,   # gestión de negociación de la oposición
    "horas_incluidas": 3,               # horas de trabajo profesional que incluye
    "hora_adicional": 190000,           # valor de la hora profesional adicional
    "honorarios_registro": 180000,      # estudio de viabilidad + presentación de una nueva marca
    "tasa_inpi": 41259,                 # tasa oficial del INPI por la nueva solicitud
}

ETIQUETAS = {
    "honorarios_negociacion": "Honorarios de la negociación",
    "horas_incluidas": "Horas incluidas",
    "hora_adicional": "Hora profesional adicional",
    "honorarios_registro": "Honorarios del registro de una nueva marca",
    "tasa_inpi": "Tasa oficial del INPI",
}

_LIMITES = {
    "honorarios_negociacion": (1, 99_999_999),
    "horas_incluidas": (1, 100),
    "hora_adicional": (1, 99_999_999),
    "honorarios_registro": (1, 99_999_999),
    "tasa_inpi": (1, 99_999_999),
}


def _entero(v, campo: str) -> int:
    """'790.000', '$790000', 790000 -> 790000. Sin centavos."""
    etiqueta = ETIQUETAS[campo]
    minimo, maximo = _LIMITES[campo]
    if isinstance(v, bool):
        raise ValueError(f"{etiqueta}: escribí un número")
    if isinstance(v, (int, float)):
        if int(v) != v:
            raise ValueError(f"{etiqueta}: escribilo en números enteros, sin centavos")
        n = int(v)
    else:
        t = re.sub(r"[\s$]", "", str(v or ""))
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", t):
            raise ValueError(f"{etiqueta}: escribí un número entero (por ejemplo 790000 o 790.000)")
        n = int(t.replace(".", ""))
    if not minimo <= n <= maximo:
        raise ValueError(f"{etiqueta}: tiene que estar entre {minimo} y {maximo}")
    return n


def validar(valores: dict) -> dict:
    """Limpia y valida los campos que vengan (los que falten no se tocan).
    Levanta ValueError con un mensaje claro."""
    out = {}
    for campo in DEFAULTS:
        v = (valores or {}).get(campo)
        if v not in (None, ""):
            out[campo] = _entero(v, campo)
    return out


def config_efectiva(guardada: dict = None) -> dict:
    """Valores por defecto con lo guardado encima."""
    cfg = dict(DEFAULTS)
    for k, v in (guardada or {}).items():
        if k in DEFAULTS and isinstance(v, int) and not isinstance(v, bool):
            cfg[k] = v
    return cfg


def _leer_json(cur) -> dict:
    cur.execute("SELECT valor FROM vigilancia_config WHERE clave = %s", (CONFIG_CLAVE,))
    f = cur.fetchone()
    crudo = (f["valor"] if isinstance(f, dict) else f[0]) if f else None
    try:
        guardada = json.loads(crudo) if crudo else {}
    except ValueError:
        guardada = {}
    return guardada if isinstance(guardada, dict) else {}


def leer_config(cur) -> dict:
    """Config efectiva (defaults + lo guardado) del presupuesto de oposición."""
    return config_efectiva(_leer_json(cur))


def guardar_config(cur, valores: dict) -> dict:
    """Valida y guarda (mezclando con lo que ya había). Devuelve la config efectiva."""
    nuevos = validar(valores)
    guardado = {**_leer_json(cur), **nuevos}
    cur.execute("INSERT INTO vigilancia_config (clave, valor) VALUES (%s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                (CONFIG_CLAVE, json.dumps(guardado, ensure_ascii=False)))
    return config_efectiva(guardado)


def con_ajustes(cfg: dict, ajustes: dict) -> dict:
    """Config con los valores de UN envío por encima de los guardados."""
    return {**cfg, **validar(ajustes)}


def texto(cfg: dict) -> str:
    """El texto del presupuesto, en el mismo formato que el análisis escrito (una línea en blanco separa
    párrafos; las líneas que empiezan con «- » son viñetas) para armarlo con el mismo PDF. Lo que va entre
    **dobles asteriscos** sale en negrita en el PDF del presupuesto."""
    horas = int(cfg["horas_incluidas"])
    hasta = f"hasta {horas} hora" if horas == 1 else f"hasta {horas} horas"
    return (
        "La primera alternativa que podemos evaluar es:\n"
        "\n"
        "**Negociación con el oponente**, con el objetivo de procurar el retiro de la oposición mediante "
        "un acuerdo entre las partes.\n"
        "\n"
        "Los honorarios correspondientes a la gestión de negociación de la oposición son de "
        f"**{pres.fmt_pesos(cfg['honorarios_negociacion'])}**, e incluyen **{hasta} de trabajo profesional**. "
        "En caso de requerir tiempo adicional, la hora profesional tiene un valor de "
        f"**{pres.fmt_pesos(cfg['hora_adicional'])}**. Este presupuesto corresponde "
        "**exclusivamente a la etapa de negociación** y se aplica por cada oposición recibida y por cada clase.\n"
        "\n"
        "En caso de no arribar a un acuerdo, evaluaremos la conveniencia de continuar con la defensa en el "
        "procedimiento administrativo de oposición ante el INPI. **Esta etapa se cotiza por separado.**\n"
        "\n"
        "En caso de que resulte conveniente registrar una nueva marca, con una denominación que presente un "
        "menor riesgo de conflicto, el presupuesto para el estudio de viabilidad y la presentación de la "
        f"solicitud es de **{pres.fmt_pesos(cfg['honorarios_registro'])}**, más la tasa oficial del INPI de "
        f"**{pres.fmt_pesos(cfg['tasa_inpi'])}** (valores por una marca en una clase). Antes de presentar la "
        "solicitud realizaremos una **búsqueda integral de viabilidad marcaria**, a fin de analizar "
        "antecedentes fonéticos, gráficos y conceptuales y reducir el riesgo de nuevas oposiciones."
    )
