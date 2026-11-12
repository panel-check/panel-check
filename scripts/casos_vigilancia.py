"""
Casos de prueba de la vigilancia marcaria (idea 1 del plan, 12/11/2026).

CONFLICTO: pares reales de oposición publicados en el INPI (marca anterior del
oponente, clase, solicitud opuesta, clase, fuerte). La marca del oponente
juega el papel de «marca del cliente» y la solicitud opuesta el de «solicitud
nueva»: si la vigilancia hubiera estado del lado del oponente, tendría que
haberla avisado. «fuerte» = casos donde el análisis dio pocas chances al
solicitante (deberían salir como idéntica o confundible).

SIN_CONFLICTO: alertas reales del panel que el equipo marcó como «no tienen
nada que ver» (12/11/2026). No deberían mostrarse.

Solo nombres de marcas y clases (datos públicos del boletín); nada de
titulares ni de clientes.
"""

CONFLICTO = [
    ("SKYPEAK", 18, "PEAKS", 18, False),
    ("ALMENDRA", 43, "ALMENDRO", 43, True),
    ("NEXA LABS", 5, "NOXALAB", 5, True),
    ("MUTE", 25, "MUTE. M.", 25, True),
    ("PROTENSA", 5, "PROTONI", 5, False),
    ("NUBE 9", 41, "NUBE RECORDS", 41, False),
    ("LONDON SCHOOL OF ENGLISH", 41, "LONDON SCHOOL OF ENGLISH", 41, True),
    ("LE LABO", 3, "LABO DE CREMAS", 3, True),
    ("JACANA LODGE", 41, "JACANA", 41, True),
    ("ECOBRASA-LA BRASA ECONOMICA", 4, "ECOBRASA RECICLA", 4, True),
    ("SOCIA", 42, "SOCIA", 42, True),
    ("SUPERPANCHO ESPARTANO", 30, "ESPARTANAS", 30, False),
    ("GRUPO POLIPLAST", 35, "POLIPLAST", 35, False),
    ("GIGARED", 38, "GN GIGANET COMUNICACIONES", 38, True),
    ("EL PUNTO MEDIO", 41, "PUNTO MEDIO", 41, False),
    ("TUKI", 42, "TUKIVERSE", 42, False),
    ("SUSPENSYS", 12, "SUSPENCITY", 12, True),
    ("CYLGEM", 9, "SILEG", 9, False),
    ("ZAHE", 35, "Z ZAFE", 35, False),
    ("SIEMPRE MAS", 36, "MAS", 36, False),
    ("APEX PADEL", 5, "APEX LAB", 5, True),
    ("LION AIR", 11, "LINAIR", 11, False),
    ("OLIVIA", 43, "OLIVIA HOUSE CAFE", 43, False),
    ("RAPPI", 35, "C CAPIMARKET", 35, False),
]

SIN_CONFLICTO = [
    ("TODO LLAVES", 35, "ZUPRA TODO PARA LA LIMPIEZA HOGAR AUTOMOTORES PILETAS PRODUCTOS SUELTOS", 35),
    ("HACELO POR MI VIAJES Y TURISMO", 39, "BORDON TURISMO", 39),
    ("MANAR", 35, "FLUX", 35),
    ("SI VIAJO", 39, "THE GOLDEN TRIP DESTINOS CON PROPOSITOS", 39),
    ("TIMBEROS", 9, "BOIS", 35),
]

# Palabras de uso común medidas en la base el 12/11/2026 (apariciones en 18 meses en esa clase),
# para que las pruebas sin base usen los mismos cortes que la corrida real.
COMUNES_MEDIDAS = {35: {"TODO": 45}, 39: {"TURISMO": 83, "VIAJES": 169}, 41: {"RECORDS": 19, "PUNTO": 20, "ENGLISH": 20},
                   43: {"CAFE": 148, "HOUSE": 32}}
