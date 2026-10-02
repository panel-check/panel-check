"""
Nombre de pila a partir del titular que trae INPI.

INPI escribe a las personas como "APELLIDO NOMBRE" en mayúsculas y sin tildes
(p. ej. "BOTTERO TOMAS"). Para saludar con "Hola Tomás:" se busca el primer
nombre de pila conocido después del apellido y se le ponen tilde y mayúscula.
Si el titular es una empresa o no se puede deducir con seguridad, devuelve ""
(y la plantilla queda "Hola:").
"""

import re
import unicodedata

# Nombres de pila frecuentes en Argentina, escritos como tienen que salir.
_NOMBRES = """
Abel Abril Adolfo Adrián Adriana Agostina Agustín Agustina Aída Ailén Alan Alba Alberto Alcira Alejandra Alejandro
Alejo Alexis Alfonso Alfredo Alicia Alma Amalia Amanda Ana Analía Andrea Andrés Ángel Ángela Ángeles Angélica
Anabella Antonella Antonia Antonio Ariel Armando Arturo Augusto Aurora Axel Bárbara Beatriz Belén Benjamín Bernardo
Bianca Blanca Brenda Bruno Camila Camilo Candela Carina Carla Carlos Carmen Carolina Catalina Cecilia Celeste César
Clara Claudia Claudio Constanza Cristian Cristián Cristina Dalma Damián Daniel Daniela Dante Dario Darío David Débora
Delfina Diego Dolores Domingo Eduardo Elena Eliana Elías Elisa Elizabeth Emanuel Emilia Emiliano Emilio Emma Enrique
Enzo Erica Ernesto Esteban Estefanía Estela Esther Eugenia Eugenio Eva Evelyn Ezequiel Fabián Fabiana Fabio Facundo
Federico Felipe Fernanda Fernando Flavia Florencia Francisco Franco Gabriel Gabriela Gastón Gerardo Germán Gimena
Gisela Gladys Gonzalo Graciela Gregorio Guadalupe Guido Guillermo Gustavo Héctor Hernán Horacio Hugo Ignacio Inés
Irene Isabel Iván Jazmín Javier Jesús Jimena Joaquín Jonathan Jorge José Josefina Juan Juana Julia Julián Juliana
Julieta Julio Karina Laura Lautaro Leandro Leonardo Leonel Leticia Liliana Lionel Lorena Lorenzo Lourdes Lucas
Lucía Luciana Luciano Lucio Luis Luisa Luján Macarena Magdalena Malena Manuel Manuela Marcela Marcelo Marcos Marcia
Margarita María Mariana Mariano Maricel Marina Mario Marisa Marta Martín Martina Matías Mauricio Maximiliano Máximo
Melina Mercedes Micaela Miguel Milagros Miriam Mirta Mónica Natalia Nahuel Nancy Nazareno Nélida Nicolás Noelia
Norma Octavio Olga Omar Oscar Pablo Paola Patricia Patricio Paula Paulina Pedro Pilar Rafael Ramiro Ramón Raquel Raúl
Rebeca Regina Ricardo Roberto Rocío Rodolfo Rodrigo Rolando Romina Rosa Rosana Rosario Rubén Ruth Sabrina Samuel
Sandra Santiago Santino Sara Sebastián Sergio Silvana Silvia Simón Sofía Sol Soledad Sonia Susana Tamara Teresa
Thiago Tiago Tomás Ulises Valentín Valentina Valeria Vanesa Vanina Verónica Vicente Víctor Victoria Viviana Walter
Ximena Yamila Yanina Yesica Zoe
""".split()


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").upper()


_CONOCIDOS = {}
for _n in _NOMBRES:
    # Si hay dos versiones (Cristian / Cristián), queda la primera.
    _CONOCIDOS.setdefault(_sin_tildes(_n), _n)

_PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "SAN", "SANTA", "SANTO", "DI", "DA", "DOS", "VAN", "VON", "MC", "MAC"}

# Palabras que indican que el titular es una empresa u organización.
_EMPRESA = re.compile(
    r"\b(S\.?\s?R\.?\s?L|S\.?\s?A\.?\s?S|S\.?\s?A|S\.?\s?A\.?\s?U|S\.?\s?C\.?\s?A|SOCIEDAD|LTDA|LIMITADA|COOPERATIVA|"
    r"ASOCIACION|ASOCIACIÓN|FUNDACION|FUNDACIÓN|MUTUAL|CLUB|INC|LLC|LTD|CORP|CORPORATION|COMPANY|GROUP|GRUPO|"
    r"HOLDING|CONSORCIO|UNIVERSIDAD|INSTITUTO|MINISTERIO|GOBIERNO|MUNICIPALIDAD|SINDICATO|FEDERACION|FEDERACIÓN|"
    r"CAMARA|CÁMARA|COLEGIO|EMPRESA|COMPAÑIA|COMPAÑÍA|CIA|SUCESION|SUCESIÓN|FIDEICOMISO|UTE|SH|S\.H)\b\.?",
    re.IGNORECASE,
)


def primer_nombre(titular: str) -> str:
    t = " ".join((titular or "").split())
    if not t or _EMPRESA.search(t) or any(ch.isdigit() for ch in t):
        return ""
    if "," in t:
        # "APELLIDO, NOMBRE": lo que va después de la coma.
        resto = t.split(",", 1)[1].split()
        if resto:
            p = _sin_tildes(resto[0])
            return _CONOCIDOS.get(p, resto[0].capitalize())
        return ""
    palabras = t.split()
    if len(palabras) < 2:
        return ""
    # Primer nombre conocido después del apellido (la primera palabra).
    for i in range(1, len(palabras)):
        p = _sin_tildes(palabras[i])
        # "SAN MARTIN", "DE LA ROSA", "DEL CARMEN": siguen siendo apellido.
        if _sin_tildes(palabras[i - 1]) in _PARTICULAS:
            continue
        if p in _CONOCIDOS:
            return _CONOCIDOS[p]
    # "APELLIDO NOMBRE" con un nombre que no está en la lista: la segunda palabra.
    if len(palabras) == 2 and palabras[1].isalpha() and len(palabras[1]) > 2:
        return palabras[1].capitalize()
    return ""
