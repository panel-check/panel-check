"""Red de seguridad: toda ruta /api/... tiene que exigir sesión (verificar_login o
verificar_admin), salvo las que están a propósito en la lista blanca de abajo.

Hoy esa protección depende de que cada ruta nueva se acuerde de agregar
`Depends(verificar_login)` (o admin) — no hay nada que lo obligue de forma
automática. Este test no corrige eso (seguiría dependiendo del Depends en cada
ruta), pero sí avisa si alguna ruta nueva se olvida: lee el código fuente de los
routers (sin importar la app ni necesitar base de datos) y falla si encuentra una
ruta /api/... sin Depends(verificar_...) que no esté en la lista blanca.

Si agregás una ruta pública nueva a propósito, agregala a RUTAS_PUBLICAS con un
comentario de por qué. Si el test falla con una ruta que no agregaste a esa
lista, es casi seguro que te olvidaste del Depends.

Ejecutar: cd scripts && python3 -m unittest test_rutas_protegidas
"""
import os
import re
import unittest

RAIZ = os.path.join(os.path.dirname(__file__), "..", "panel")

ARCHIVOS_CON_RUTAS = [
    "app.py", "auth_api.py", "calendario_api.py", "cartera_api.py",
    "formularios_api.py", "mails_api.py", "verificador_api.py",
    "accesos_api.py", "analisis_api.py",
]

# Rutas /api/... a propósito sin sesión. Cada una tiene que tener su propia
# protección (público a propósito, no un descuido): login/logout (no hay sesión
# todavía), /api/version (sin datos), y todo lo que vive bajo /api/publico/
# (formularios de leads, verificador de marca del home, webhook de Resend,
# link público de PDF) — cada uno con su propia barrera (rate limit, honeypot,
# Turnstile, token largo al azar, firma del webhook, etc.), no con sesión.
RUTAS_PUBLICAS = ("/api/login", "/api/logout", "/api/version", "/api/publico/")

PATRON_DECORADOR = re.compile(
    r'^\s*@(?:app|router)\.(?:get|post|put|delete|patch)\(\s*["\'](/api/[^"\']*)["\']'
)


def _rutas_del_archivo(ruta_archivo):
    """[(linea, metodo_y_ruta, tiene_depends_verificar)] para cada ruta /api/... del archivo."""
    with open(ruta_archivo, encoding="utf-8") as f:
        lineas = f.readlines()
    encontradas = []
    for i, linea in enumerate(lineas):
        m = PATRON_DECORADOR.match(linea)
        if not m:
            continue
        ruta = m.group(1)
        # Junta las líneas de la definición de la función (hasta que los
        # paréntesis de la firma cierran) para buscar el Depends ahí.
        bloque, j, vista_def, profundidad = [], i, False, 0
        while j < len(lineas) and j < i + 15:
            bloque.append(lineas[j])
            if "def " in lineas[j]:
                vista_def = True
            if vista_def:
                profundidad += lineas[j].count("(") - lineas[j].count(")")
                if profundidad <= 0:
                    break
            j += 1
        firma = "".join(bloque)
        encontradas.append((i + 1, ruta, "Depends(verificar" in firma))
    return encontradas


class RutasProtegidas(unittest.TestCase):
    def test_toda_ruta_api_tiene_sesion_salvo_la_lista_blanca(self):
        sin_proteger = []
        vistas = 0
        for nombre in ARCHIVOS_CON_RUTAS:
            ruta_archivo = os.path.join(RAIZ, nombre)
            if not os.path.isfile(ruta_archivo):
                continue
            for linea, ruta, protegida in _rutas_del_archivo(ruta_archivo):
                vistas += 1
                es_publica = any(ruta == pub or (pub.endswith("/") and ruta.startswith(pub))
                                  for pub in RUTAS_PUBLICAS)
                if not protegida and not es_publica:
                    sin_proteger.append(f"{nombre}:{linea} {ruta}")
        # Si esto es 0, algo rompió el escaneo (cambiaron los decoradores de
        # lugar, etc.) — mejor que el test falle por las dudas y no quede
        # "pasando" sin haber revisado nada.
        self.assertGreater(vistas, 50, "se esperaban más de 50 rutas /api/... — ¿cambió algo en los routers?")
        self.assertEqual(
            sin_proteger, [],
            "Rutas /api/... sin Depends(verificar_login/admin) y fuera de la lista blanca "
            "(¿se olvidó el Depends, o hay que agregarla a RUTAS_PUBLICAS a propósito?): "
            + ", ".join(sin_proteger),
        )


if __name__ == "__main__":
    unittest.main()
