**Proceso de extracción y enriquecimiento de datos**

Boletín de Marcas del INPI --- Nº 11116

*Documento explicativo para uso interno del equipo*

# 1. Objetivo

[Armar una base de datos estructurada con todas las marcas nuevas
publicadas en un boletín del INPI (Instituto Nacional de la Propiedad
Industrial), incluyendo el nombre de la marca, el titular, la clase, la
fecha, el apoderado (si tiene) y un link directo a la ficha oficial ---
para que se pueda filtrar, buscar y monitorear fácilmente.]{.mark}

[El boletín se publica como un PDF de cientos de páginas con un formato
de texto plano poco amigable (códigos numéricos tipo WIPO), así que el
proceso tuvo tres etapas: extraer el texto del PDF, estructurarlo en una
planilla, y completar los datos faltantes de las marcas \"Mixtas\"
(texto + logo) cruzando contra el sistema oficial de INPI.]{.mark}

# 2. [Paso 1 --- Extracción del texto del PDF (Apify)]{.mark}

[Usamos Apify, una plataforma de automatización/scraping con actores
(herramientas) ya armadas para tareas comunes, entre ellas extraer texto
de PDFs.]{.mark}

1.  Comparamos varios \"actors\" de extracción de texto de PDF por
    > precio.

2.  Probamos primero el más barato (eliai/pdf-text-extractor, USD 0,003
    > por PDF), pero falló por timeout: el boletín tiene 91 páginas y
    > 18,6 MB, y ese actor no permite configurar un tiempo de espera más
    > largo.

3.  Usamos entonces santamaria-automations/pdf-extractor, que sí permite
    > subir el timeout. Con eso extrajimos el texto completo: 546.076
    > caracteres, confirmando que es texto nativo (no escaneado, no
    > necesita OCR).

Resultado de este paso: un archivo de texto plano con el contenido
completo del boletín, listo para procesar.

# 3. [Paso 2 --- Parseo y estructuración en Excel]{.mark}

[El texto extraído sigue un formato de códigos estándar (similar al
sistema WIPO ST.60) que identifica cada campo de cada solicitud de
marca:]{.mark}

-   \(21\) Acta --- número de expediente/trámite

-   \(51\) Clase --- rubro según el nomenclador de Niza (1 a 45)

-   \(40\) Tipo de marca --- D = Denominativa (solo texto), M = Mixta
    > (texto + logo), F = Figurativa (solo logo), T = Tridimensional

-   \(54\) Denominación --- el nombre de la marca (solo viene completo
    > en las Denominativas; en Mixtas/Figurativas el nombre está
    > \"dentro\" del logo, así que el PDF no lo trae como texto)

-   \(22\) Fecha de presentación

-   \(73\) Titular --- quién presentó la solicitud, y su país

-   \(74\) Matrícula del agente/apoderado (si actuó con uno)

Con un script [armamos un parser que identifica cada bloque y extrae
estos campos de las 693 solicitudes del boletín, y les agregamos a cada
una un link directo a su ficha en el portal de trámites de INPI.]{.mark}

[Cómo distinguimos un lead real de uno que ya tiene ayuda: el campo de
matrícula del boletín en realidad puede traer tres valores distintos, no
dos. "Sin dato" significa que el boletín no trae el marcador (74) en
absoluto --- es información genuinamente ausente. "Particular" significa
que el boletín trae explícitamente la palabra "Part." en ese campo,
indicando que el solicitante se presentó por su cuenta según el propio
INPI. Pero "Particular" no es sinónimo de "sin ayuda": dentro del
expediente completo (accediendo por número de acta a la sección "GESTION
DEL TRAMITE"), hay un campo AGENTE (que muestra "0 PARTICULAR" cuando no
hay agente matriculado) y, al lado, un campo CARACTER que puede venir
vacío, o con valores como "Apoderado" o "Gestor Ratificado". Si CARACTER
tiene cualquier valor no vacío, quiere decir que alguien (un apoderado
especial o un gestor) tramitó en nombre del titular y este lo ratificó
después --- por ende no nos sirve como lead para ofrecer servicios. Solo
cuando CARACTER está vacío estamos ante un solicitante genuinamente sin
ayuda. Cómo obtener el contacto real (email): dentro de la página
completa del expediente ("Resultado" al buscar por acta desde Consultas
de Marcas) hay un botón "GRILLA DIGITAL" que lista todos los archivos
adjuntos del trámite, incluyendo el "Formulario" original que completó
el solicitante. Ese PDF trae, entre otros datos, el campo EMAIL del
titular --- lo confirmamos con un caso real (acta 4698827, ROMEU
DAIANA): el formulario trajo "EMAIL:
[[xoanavgarcia@gmail.com]{.underline}](mailto:xoanavgarcia@gmail.com)",
además de DNI, domicilio real, y en la sección REPRESENTACION el nombre
y correo del apoderado especial autorizado, si lo hay. El link de
descarga del PDF se puede pedir directo por curl (sin login) una vez
obtenido su ID, pero ese ID es opaco y solo aparece después de navegar
el flujo completo (buscar por acta → entrar al expediente → Grilla
Digital) --- no se puede armar de antemano solo con el número de
acta.]{.mark}

[Herramienta para automatizar esto: no requiere Apify ni tiene costo
adicional. Se resuelve con Python + Playwright (navegador automatizado
sin interfaz) corriendo dentro del mismo GitHub Action: repite los
mismos clicks que hicimos a mano (buscar acta, expandir resultado,
Grilla Digital, descargar Formulario) y lee el PDF resultante. Tarda
unos 10-15 segundos por marca revisada --- para el volumen de
"Particulares" genuinos de un año (unos cientos), son minutos de
cómputo, dentro de las horas gratis de GitHub Actions.]{.mark}

[Boletines anteriores (prueba retroactiva): probamos también extraer
boletines de todo 2026 (157 boletines de "MARCAS NUEVAS" encontrados,
filtrando por el campo Comentario de la página de listado de INPI). Al
procesar boletines más pesados (hasta 126 páginas / 40 MB) encontramos
que el actor de Apify usado originalmente
(santamaria-automations/pdf-extractor) tiene un límite de memoria
interno (\~128 MB) no configurable, y falla con esos boletines más
grandes. El reemplazo validado es el actor
automation-lab/pdf-text-extractor, que sí los procesó bien (probado
hasta 253 MB sin problema, y también procesando varios PDFs juntos en
una sola corrida) --- este es el actor a usar en el pipeline
definitivo.]{.mark}

[Con eso armamos el primer Excel, con 693 filas.]{.mark}

# 4. [Paso 3 --- Completar las marcas Mixtas/Figurativas]{.mark}

[El problema: en 379 de las 693 marcas (las Mixtas y Figurativas) el
campo de nombre venía vacío en el PDF del boletín, porque el nombre
visual está dentro de un logo (imagen), no como texto.]{.mark}

Probamos abrir el link de \"Resultado de acta\" del portal de trámites,
pero esa página solo muestra Clase/Protección/Productos --- no trae el
nombre ni el titular.

[La solución la encontramos en un servicio distinto: INPI tiene un
webservice público]{.mark} (SOAP, en ws.inpi.gob.ar) pensado
originalmente para que estudios y gestores consulten trámites. Tiene una
[operación llamada \"ConsultaCuitOTitular\" que, buscando por el nombre
del titular, devuelve todas sus marcas --- y ahí sí viene el campo
\"Denominación\" completo, incluso para las Mixtas]{.mark}, porque es el
dato que el sistema interno de INPI tiene guardado (más allá de cómo se
vea en el PDF del boletín).

Armamos un script que:

4.  Toma cada titular único de las 379 marcas Mixtas/Figurativas (252
    > titulares distintos, varios tienen más de una marca).

5.  Le consulta al webservice de INPI el nombre de cada una de sus
    > marcas.

6.  Cruza la respuesta con la fila original por número de Acta.

7.  Completa la columna \"Denominación (INPI)\" y agrega el CUIT del
    > titular, que también viene en esa respuesta.

Resultado: 347 de las 379 marcas Mixtas/Figurativas (91,5%) quedaron con
su nombre real completado. Las 32 restantes quedaron marcadas en
amarillo en el Excel para revisión manual --- en general son casos de
nombres con acentos o formato distinto al que tiene registrado INPI
internamente.

# 5. [Paso 4 --- Completar Pame]{.mark}

# 

# 6. Resultado final

  -----------------------------------------------------------------------
  **Métrica**                         **Valor**
  ----------------------------------- -----------------------------------
  Boletín procesado                   N.º 11116 --- 09/09/2026

  Total de marcas nuevas              693

  Denominativas (texto)               313

  Mixtas (texto + logo)               357

  Figurativas (solo logo)             22

  Tridimensionales                    1

  Con apoderado registrado            398

  Autopresentadas (sin apoderado)     295

  Mixtas/Figurativas con nombre       347 de 379 (91,5%)
  recuperado vía INPI                 
  -----------------------------------------------------------------------

El Excel final tiene 693 filas con: Acta, Clase, Tipo, Denominación (la
del boletín y la de INPI, cuando difieren), Fecha, Titular, País, CUIT,
Matrícula de agente (o \"Sin apoderado\"), y link directo a la ficha
oficial de cada acta.

# 

# 7. [Costos del proceso y Rendimiento]{.mark}

#### **6.1 [Apify (Extracción de PDFs)]{.mark}**

[El costo de extraer el texto del boletín gigante con Apify fue de
aproximadamente **\~USD 0,006** (menos de un centavo).]{.mark}

-   [**El presupuesto:** La plataforma nos regala \$5 USD por
    > mes.]{.mark}

-   [**¿Para cuánto alcanza?** Esos \$5 gratis nos alcanzan para
    > procesar unos **833 boletines**.]{.mark} Considerando que salen 4
    > por semana, ese saldo nos permite procesar las marcas nuevas del
    > mes y, además, nos da el margen para descargar **4 años enteros de
    > boletines antiguos** (más de 500.000 marcas procesadas) sin poner
    > un peso.

-   

#### **6.2 Consumo de Inteligencia Artificial (Claude)**

El análisis inicial y la creación del \"cerebro\" del sistema se
hicieron utilizando una cuenta paga de Claude.

-   **Costo futuro: Cero.** El costo ya está cubierto por la suscripción
    > mensual. Una vez que el sistema esté programado, no necesitaremos
    > usar la Inteligencia Artificial para \"leer\" los boletines cada
    > semana. El sistema lo hará solo de forma automática.

### 8. Próximos pasos inmediatos (Operativos)

1.  Revisar a mano las 32 marcas (solo logos) a las que no se les pudo
    > encontrar el nombre automáticamente en este primer boletín de
    > prueba.

### 

### 9. Plan de automatización y Sistema Propio (A futuro)

La meta final es que todo este trabajo de extraer marcas del INPI y
encontrar sus nombres reales **se haga completamente solo**.

Queremos construir un **\"Panel de Control Inteligente\"**. En lugar de
ver un Excel interminable de 11.000 marcas por mes, el sistema las
analizará solas, descartará las que no nos sirven y nos dejará
\"servidos en bandeja\" (en un formato tipo tablero visual) a los
mejores clientes potenciales.

#### **8.1 Las piezas del sistema Profesional (Costo fijo: \$25 USD/mes)**

[Claude suscripcion mensual 20 usd. Para que esto funcione 24/7 sin
trabarse y soporte el crecimiento de años de información, vamos a
conectar estas herramientas:]{.mark}

Railway primer mes gratis

  -----------------------------------------------------------------------
  **¿Qué necesitamos que haga el         **Herramienta   **Costo aprox.
  sistema?**                             elegida**       mensual**
  -------------------------------------- --------------- ----------------
  **El Despertador:** Que se fije solo   *GitHub         **\$0**
  si hay un boletín nuevo y ponga a      Actions*        
  andar el proceso, 4 veces a la semana.                 

  **El Lector:** Extraer todo el texto   *Apify*         **\$0 USD** (5
  de los PDFs gigantes del INPI.                         usd gratis por
                                                         mes nos rinde
                                                         para el mes y
                                                         para historial
                                                         de años)

  **El Organizador:** Acomodar los       *Código Propio  **\$0**
  textos en columnas y conectarse al     (Python)*       
  INPI para completar datos.                             

  **El Servidor (Archivo + Panel):** Es  *Railway* (Plan **\$5 USD**
  el \"motor\" del proyecto. Acá         profesional)    
  guardaremos el medio millón de marcas                  
  sin límite de espacio, y alojaremos el                 
  panel (*NocoDB*) para que esté siempre                 
  encendido y cargue rapidísimo.                         

  **El Cartero (Emails Automáticos):**   *Resend o       **\$0**
  La herramienta para contactar a los    Mailrelay*      (Permiten miles
  prospectos sin caer en Spam.                           de envíos
                                                         gratis)
  -----------------------------------------------------------------------

#### **8.2 La Estrategia Comercial (Los 2 Frentes de ataque)**

Tener este sistema nos permite atacar por dos lados distintos para
conseguir clientes:

**Frente A: El día a día (Marcas Nuevas)**

El INPI tiene una página web donde avisan qué boletines salen. Nuestro
sistema entrará ahí automáticamente, revisará si salió algo etiquetado
como \"MARCAS NUEVAS\", y si es así, arrancará el proceso.

-   *Objetivo:* Ofrecer a estos prospectos ayuda para contestar vistas,
    > oposiciones o terminar su trámite recién iniciado.

**Frente B: La Máquina del Tiempo (Historial)**

Como Apify nos permite procesar 800 boletines, vamos a programar el
sistema para que trabaje de noche, procesando boletines de los años
2024, 2025 y hacia atrás. Extraeremos a todas las personas que
registraron su marca hace años y lo hicieron sin abogado.

-   *Objetivo:* Ofrecerles el servicio de DDJJ (si esta cerca de los 5
    > años), **Renovación de Marca** (si está cerca de los 10 años) o
    > **Vigilancia** (para que nadie registre algo parecido).

#### **8.3 ¿Es legal y seguro extraer tanta información antigua?**

**Totalmente.** Investigamos los manuales técnicos del INPI y
confirmamos que la \"puerta\" que estamos usando para extraer la
información (ConsultaCuitOTitular) es un Web Service oficial y público
del Gobierno diseñado específicamente para desarrolladores y sistemas
automatizados.

-   **Medida de seguridad:** Para asegurar que el INPI nunca nos bloquee
    > por ir demasiado rápido (al extraer miles de marcas antiguas),
    > nuestro sistema le pondrá un \"freno de mano\" al proceso: hará
    > una consulta al INPI, esperará 2 segundos, y hará la siguiente.
    > Como el servidor de *Railway* está encendido siempre, el sistema
    > se quedará trabajando lenta y silenciosamente todo el fin de
    > semana cumpliendo las reglas.

#### **8.4 ¿Cómo filtra el sistema la \"Basura\"? (El Lead Score)**

Este es el corazón del proyecto. El sistema le pondrá un **\"Puntaje\"
(Lead Score)** a cada marca histórica o nueva que descubra.

-   *Ejemplo:* Si la marca la presentó una empresa y lo hizo **SIN
    > ABOGADO/APODERADO** (Suma muchos puntos), el sistema sabrá que es
    > un prospecto excelente para ofrecerle los servicios de la agencia.

-   Si la marca ya la presentó un abogado, el sistema la descarta
    > automáticamente y ni nos la muestra en el panel, ahorrándonos
    > miles de horas de revisar datos inútiles.
