"""Procesos y plantillas de mails (22/11/2026): lo que estaba en Notion («Procedimientos + Plantillas de
mails») pasa al panel, en la tabla `plantillas`, y se edita en Mails → «Plantillas para clientes».

Formato del cuerpo (texto simple, se copia a Gmail con formato):
- un renglón en blanco separa párrafos; un salto de línea simple es un salto de línea dentro del párrafo;
- **negrita**, _cursiva_;
- líneas que empiezan con «- » forman una lista;
- una tabla se escribe con «|» (la primera fila es el encabezado);
- [algo] es un dato a completar: al armar el mail desde un trabajo, el panel completa los que conoce
  ([nombre], [marca], [clase], [acta], [tipo]…) y pide el resto.

`para_etapa`: etapa del trabajo (número de trabajos_core.SERVICIOS) en la que se suele mandar; la ficha del
trabajo la sugiere primero. 0 = antes de la seña (presupuesto)."""


TABLAS = [
    """CREATE TABLE IF NOT EXISTS plantillas (
        id             SERIAL PRIMARY KEY,
        titulo         TEXT NOT NULL,
        servicio       TEXT NOT NULL DEFAULT 'general',
        para_etapa     INTEGER,
        canal          TEXT NOT NULL DEFAULT 'mail',
        asunto         TEXT,
        cuando         TEXT,
        cuerpo         TEXT NOT NULL DEFAULT '',
        orden          REAL NOT NULL DEFAULT 0,
        clave_carga    TEXT UNIQUE,
        alta_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
        modificado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
        modificado_por TEXT
    )""",
]

CANALES = {"mail": "Mail", "whatsapp": "WhatsApp"}
SERVICIOS_PLANTILLA = {"registro": "Registro de marca", "oposicion": "Oposición", "vista": "Contestación de vista",
                       "oposicion_adm": "Oposición administrativa", "desistimiento": "Desistimiento",
                       "vigilancia": "Vigilancia marcaria",
                       "general": "General"}

_TRANSFERENCIA = """- Alias: pamedguzzardi
- CVU: 0000003100062345351106
- Titular: Pamela Daiana Guzzardi"""

_FORMULARIO = "https://panel.registrodemimarca.com.ar/formulario/persona-fisica"

# Copia de las 14 plantillas de Notion (19/11/2026). Cambios: el asunto que estaba escrito arriba del texto
# pasó al campo Asunto, y el link del formulario de la plantilla de vista se corrigió (terminaba en «s»).
PLANTILLAS_INICIALES = [
    # ── Registro de marca ──────────────────────────────────────────────────────────────────────────
    {"clave": "notion:presupuesto-registro", "servicio": "registro", "para_etapa": 0, "canal": "mail", "orden": 1,
     "titulo": "Presupuesto de registro",
     "asunto": "Registro de una nueva marca - Presupuesto",
     "cuando": "Después de la primera charla, junto con el PDF del presupuesto.",
     "cuerpo": f"""Hola [nombre], buenos días. ¿Cómo estás?

Como conversamos, te adjunto en PDF el presupuesto y el detalle del servicio para registrar tu marca.

El valor es de $180.000 (por cada clase y marca) + honorarios del INPI.

Si te interesa avanzar, el primer paso es un informe preliminar de viabilidad marcaria sin cargo, que nos permite saber si ya existen marcas registradas con el mismo nombre exacto. Para hacerlo necesitamos:

- el nombre de la marca tal como lo vas a usar,
- a qué se dedica (productos o servicios concretos),
- el logo, si lo tenés.

Si el informe no muestra marcas iguales, seguimos con el análisis de viabilidad completo, que es más profundo, y con la estrategia de clases a registrar. Para ese análisis pedimos una seña del 50 % de los honorarios de registro de una marca en una clase ($90.000).

Una vez que definamos la viabilidad del nombre y las clases a proteger, se abona el saldo de los honorarios junto con las tasas oficiales del INPI (aprox. $42.000 por clase a hoy).

**Gestión de desistimiento por oposición de marca:** Se sugiere formalizar el desistimiento de la solicitud que recibió oposición. Los honorarios por la toma de intervención en el expediente y la presentación de los escritos correspondientes son de $90.000.

Cualquier duda, estoy a tu disposición.

Saludos,"""},
    {"clave": "notion:informe-preliminar-sena", "servicio": "registro", "para_etapa": 1, "canal": "mail", "orden": 2,
     "titulo": "Envío del informe preliminar y seña", "asunto": "",
     "cuando": "Junto con el PDF del informe preliminar de viabilidad, para que el cliente avance con la seña.",
     "cuerpo": f"""[nombre], buenas tardes. ¿Cómo estás?

Te comparto el informe preliminar de viabilidad de la marca. En el PDF vas a encontrar el resultado de la búsqueda y los próximos pasos.

Si querés avanzar con el análisis de viabilidad completo y la estrategia de clases, estos son los pasos:

**¿Cómo comenzamos?**

**1. Completar la información del caso**
Necesito que completes este formulario, con el mayor detalle posible en la descripción de los productos o servicios. Con esa información armamos el análisis y la lista de productos y servicios a proteger.
Formulario: {_FORMULARIO}
Cuando lo completes, te enviamos el poder para que lo firmes.

**2. Transferencia de la seña**
El análisis comienza con una seña del 50 % de los honorarios de registro de una marca en una clase ($90.000). El saldo se abona cuando definamos la viabilidad del nombre y las clases a registrar, junto con las tasas oficiales del INPI de cada clase.

{_TRANSFERENCIA}

Una vez realizada la transferencia inicial, los plazos para recibir el informe completo son de 7 a 10 días. Una vez que tengamos, coordinaremos para realizar una llamada o reunión virtual.

Cualquier duda, estoy a tu disposición.

Saludos,"""},
    {"clave": "notion:analisis-completo", "servicio": "registro", "para_etapa": 3, "canal": "mail", "orden": 3,
     "titulo": "Envío del análisis de viabilidad completo", "asunto": "Análisis de viabilidad marcaria - [marca]",
     "cuando": "Junto con el PDF del análisis de viabilidad completo. INTERNO: el cliente tiene de 3 a 5 días para responder; dejar una nota en la bitácora para hacerle el seguimiento.",
     "cuerpo": """[nombre], buenos días. ¿Cómo estás?

En el presente correo te adjunto el análisis de viabilidad marcaria para [marca]. El próximo paso sería reunirnos para definir en conjunto cuál será la estrategia a seguir.

Tené en cuenta que, en líneas generales, lo recomendable es no dilatar el registro después de realizar el análisis de viabilidad. De lo contrario, la marca queda expuesta a que terceros presenten solicitudes similares que no hayan sido detectadas en el análisis.

Una vez que nos confirmes la estrategia y las clases a proteger, preparamos la solicitud para presentar ante el INPI en un plazo de 7 a 10 días.

Quedo atenta a tu respuesta para coordinar la reunión.

Saludos,"""},
    {"clave": "notion:marca-presentada", "servicio": "registro", "para_etapa": 4, "canal": "mail", "orden": 4,
     "titulo": "Marca presentada", "asunto": "PRESENTACIÓN SOLICITUD DE MARCA [marca] - clase [clase]",
     "cuando": "Cuando la solicitud ya se presentó en el INPI. Adjuntar la factura.",
     "cuerpo": f"""Hola [nombre],

Te escribo para informarte que ya se realizó la presentación de la solicitud de marca “[marca]” ante el Instituto Nacional de la Propiedad Industrial (INPI).

A continuación, te detallo la información correspondiente:

| Tipo de marca | Clase | Nº de Acta |
| [tipo] | [clase] | [acta] |

A partir de esta presentación, el INPI realizará el examen de forma, para verificar el cumplimiento de los requisitos formales de la solicitud, y el examen de registrabilidad, para evaluar que la marca no incurra en prohibiciones absolutas de registro o vinculadas al orden público, conforme a la Resolución INPI 583/2025.

Una vez superada esta etapa, el siguiente paso será la publicación en el Boletín de Marcas, sobre la cual también te estaré informando oportunamente.

Por último, te adjunto la factura correspondiente a la presentación. El saldo pendiente de pago es de $[honorarios] (honorarios) + $[tasas] (tasas del INPI). Una vez realizada la transferencia, te pido que me envíes el comprobante para poder registrar el pago.

Te dejo los datos para la transferencia:
Alias: pamedguzzardi
CVU: 0000003100062345351106
Titular: Pamela Daiana Guzzardi

Quedo a disposición por cualquier consulta."""},
    {"clave": "notion:vista-recibida-wa", "servicio": "registro", "para_etapa": 4, "canal": "whatsapp", "orden": 4.5,
     "titulo": "Vista recibida (WhatsApp)", "asunto": "",
     "cuando": "Si el INPI dicta una vista sobre la solicitud, antes de la publicación.",
     "cuerpo": """Hola [nombre], ¿cómo estás? Te escribo porque el INPI dictó una vista sobre la solicitud de tu marca [marca] (clase [clase], acta [acta]). Es una observación que hay que contestar dentro del plazo que fija el INPI. Ya la estamos analizando y te paso por mail el detalle y cómo seguimos."""},
    {"clave": "notion:marca-publicada", "servicio": "registro", "para_etapa": 5, "canal": "mail", "orden": 5,
     "titulo": "Marca publicada en el Boletín", "asunto": "Publicación de tu marca [marca] en el Boletín de Marcas",
     "cuando": "El día que la marca sale publicada en el Boletín de Marcas del INPI.",
     "cuerpo": """Hola [nombre],

Te informamos que la marca “[marca]”, solicitada en la clase [clase], fue publicada en el Boletín de Marcas N.º [número de boletín] del Instituto Nacional de la Propiedad Industrial (INPI), con fecha [fecha de publicación].

A partir de esta fecha, comienza a correr el plazo legal de **30 días corridos** para que terceros puedan presentar oposiciones, en caso de considerar que la solicitud afecta derechos previamente adquiridos.

Si no se presenta ninguna oposición, el expediente avanzará a la etapa de **resolución**.

Desde Smarties Consultora realizamos un seguimiento integral del proceso y te mantendremos informada/o ante cualquier novedad.

Ante cualquier consulta, quedamos a disposición.

Saludos,"""},
    {"clave": "notion:oposicion-recibida-wa", "servicio": "registro", "para_etapa": 5, "canal": "whatsapp", "orden": 5.5,
     "titulo": "Oposición recibida (WhatsApp)", "asunto": "",
     "cuando": "Si un tercero presenta una oposición dentro de los 30 días desde la publicación. Después sigue el circuito de Oposición.",
     "cuerpo": """Hola [nombre], ¿cómo estás? Te aviso que [oponente] presentó una oposición contra tu marca [marca] (clase [clase]). No es una resolución en contra: desde que el INPI la notifica, hay un plazo de 3 meses para negociar con la otra parte. Ya la estamos analizando y te paso por mail las alternativas."""},
    {"clave": "notion:pasa-a-resolucion", "servicio": "registro", "para_etapa": 6, "canal": "mail", "orden": 6,
     "titulo": "Sin oposiciones, pasa a resolución", "asunto": "Tu marca [marca] avanza a la etapa de resolución",
     "cuando": "Al cerrar los 30 días desde la publicación sin oposiciones.",
     "cuerpo": """Hola [nombre],

Te informamos que la solicitud de marca “[marca]”, correspondiente a la clase [clase], concluyó la etapa de publicación sin recibir oposiciones por parte de terceros.

El expediente pasa ahora a la sección de resoluciones del INPI. Una vez que la concesión se publique en el Boletín, te informaremos la fecha y procederemos a gestionar el título de la marca. Tené en cuenta que el costo de esa gestión es de $105.000.

Desde Smarties Consultora realizamos un seguimiento continuo del trámite y te mantendremos informada/o ante cualquier novedad.

Ante cualquier consulta, quedamos a disposición.

Saludos,"""},
    {"clave": "notion:marca-concedida", "servicio": "registro", "para_etapa": 7, "canal": "mail", "orden": 7,
     "titulo": "Marca concedida", "asunto": "¡Tu marca [marca] fue concedida!",
     "cuando": "Cuando se publica la concesión. Cobrar el título y ofrecer vigilancia marcaria.",
     "cuerpo": f"""**Hola [nombre],**

Espero que estés muy bien.

Me complace informarte que la marca **“[marca]”** fue concedida por el Instituto Nacional de la Propiedad Industrial (INPI) en la(s) clase(s) [clases], con fecha [fecha de concesión].

Con esta resolución, el INPI emite el **título de propiedad marcaria**, con una vigencia de **10 años**, y te otorga el **derecho exclusivo de uso** sobre la marca en las clases concedidas.

**Título de la marca**

Para gestionar y enviarte el título, el costo es de $105.000. Te dejo los datos para la transferencia:

{_TRANSFERENCIA}

**¿Qué obligaciones tenés como titular de una marca?**

A partir de ahora, es fundamental que cumplas con ciertos requisitos para conservar ese derecho:

- Presentar la **Declaración Jurada de Uso** entre el 5.º y el 6.º año desde la concesión.
- Renovar el registro en el plazo correspondiente (a los 10 años).
- Vigilar que no se registren marcas similares que puedan afectar tu posicionamiento o generar confusión.

**¿Cómo podés delegar todo esto?**

Desde **Smarties Consultora** diseñamos el servicio de **Vigilancia Marcaria**, una solución pensada para que no tengas que ocuparte de los aspectos legales y técnicos posteriores al registro.

Este servicio incluye:

- Monitoreo mensual del Boletín de Marcas.
- Alerta inmediata en caso de marcas similares o idénticas.
- Notificaciones sobre tus obligaciones legales, como la DDJJ de uso y la renovación.
- **Presentación incluida** de la DDJJ.
- Seguimiento normativo activo y asesoramiento ante novedades o conflictos.

Valor mensual: $45.000.

**Si querés asegurarte de que tu marca se mantenga protegida, vigente y correctamente administrada durante toda su vigencia, podés responder a este correo.**

Quedo a disposición para cualquier consulta o para avanzar con la gestión.

Saludos,"""},
    {"clave": "notion:envio-titulo", "servicio": "registro", "para_etapa": 7, "canal": "mail", "orden": 8,
     "titulo": "Envío del título y recordatorio de vigilancia", "asunto": "Título de tu marca [marca]",
     "cuando": "Al enviar el título, si el cliente todavía no contrató la vigilancia. Adjuntar el título en PDF.",
     "cuerpo": """Hola [nombre], ¿cómo estás?

Te adjunto el título de propiedad de tu marca **“[marca]”**, clase [clase], registro N.º [número de registro], vigente hasta el [fecha de vencimiento].

Te recomiendo guardarlo junto con la documentación de tu empresa: es la constancia de tu derecho exclusivo sobre la marca.

**Fechas para tener en cuenta**

- **Declaración Jurada de Uso:** entre el [fecha 5.º año] y el [fecha 6.º año].
- **Renovación:** antes del [fecha de vencimiento].

Te recuerdo que con nuestro servicio de **Vigilancia Marcaria** ($45.000 por mes) nos ocupamos de controlar el Boletín de Marcas todos los meses, te avisamos si aparece una marca parecida a la tuya y presentamos la DDJJ de uso por vos. Si te interesa, respondé este correo y lo activamos.

Gracias por confiar en Smarties Consultora para registrar tu marca. Quedo a disposición para lo que necesites.

Saludos,"""},
    # ── Oposición ──────────────────────────────────────────────────────────────────────────────────
    {"clave": "notion:presupuesto-respuesta-oposicion", "servicio": "oposicion", "para_etapa": 0, "canal": "mail", "orden": 1,
     "titulo": "Presupuesto de respuesta a oposición", "asunto": "Alternativas y pasos a seguir - Oposición a [marca]",
     "cuando": "Cuando conviene responder la oposición (negociación recomendada).",
     "cuerpo": f"""[nombre], buenas tardes.

En relación a lo conversado sobre la oposición a tu marca [marca], a continuación te detallo los caminos posibles para avanzar:

**Opción 1: Negociar un acuerdo de coexistencia (Recomendada)**
Consiste en contactar a la contraparte dentro del plazo legal de 3 meses para explorar un acuerdo que permita el retiro de la oposición. Es la vía más ágil y económica.

- **Honorarios:** $350.000 por oposición (incluye hasta 3 horas de gestión profesional cada una; si la negociación requiere tiempo adicional, se presupuestará su continuidad).

**Opción 2: Defensa administrativa ante el INPI**
Si no hay acuerdo privado y el oponente mantiene la oposición, podemos contestar el traslado. Esta vía implica honorarios y tasas adicionales y, por los obstáculos que conversamos, no es nuestra primera recomendación.

**Opción 3: Presentar una nueva marca (Plan B)**
Si preferís no avanzar con la negociación, podemos buscar un nuevo nombre, evaluar su disponibilidad y presentar una nueva solicitud.

- **Honorarios:** $180.000 + tasas oficiales del INPI (por cada clase a registrar).

**¿Cómo comenzamos?**

Si decidís avanzar con la **Opción 1**, te pido que completes los siguientes dos pasos:

**1. Completar la información del caso**
Necesito que llenes este formulario con el mayor detalle posible en la descripción de los servicios. Usaremos esa información para redactar los fundamentos del acuerdo (te lo enviaremos para tu confirmación antes de presentarlo).
🔗 **Formulario:** {_FORMULARIO}
_(Una vez completo, te enviaremos el poder para que lo firmes)._

**2. Transferencia inicial**
Las gestiones inician con el abono del 50% ($175.000). El 50% restante se abona una vez presentado el acuerdo o finalizada la gestión.

- **Alias:** pamedguzzardi
- **CVU:** 0000003100062345351106
- **Titular:** Pamela Daiana Guzzardi

Cualquier duda, sigo a disposición.

Saludos,"""},
    {"clave": "notion:presupuesto-nueva-marca-oposicion", "servicio": "oposicion", "para_etapa": 0, "canal": "mail", "orden": 2,
     "titulo": "Presupuesto de nueva marca por oposición", "asunto": "Alternativas a oposiciones de [marca]",
     "cuando": "Cuando la recomendación principal es cambiar de nombre (negociación como alternativa).",
     "cuerpo": """Estimada [nombre], muy buenas tardes.

En relación con lo conversado sobre la oposición a [marca] te comparto el presupuesto para las diferentes alternativas.

**1. Presentar una nueva marca.** Nosotros hacemos una búsqueda preliminar de antecedentes para evaluar la disponibilidad del nombre que elijas y detectar posibles conflictos. Luego podemos encargarnos de la presentación y el seguimiento de la nueva solicitud. El costo es de $180.000 + tasas del INPI (por cada clase a registrar).

**2. Intentar una negociación.** Si tu interés es conservar el nombre de la marca, podemos contactar a la contraparte para explorar un acuerdo que permita obtener el retiro de la oposición. Los honorarios para esta gestión son de $350.000 (por opo. recibida e incluyen tres horas de trabajo cada una). Si la negociación requiere tiempo adicional, se presupuestará su continuidad.

**3. Presentar una defensa ante el INPI.** Si transcurren los tres meses sin acuerdo y la oponente mantiene la oposición cumpliendo los requisitos correspondientes, podremos evaluar una defensa en la instancia administrativa. Esta alternativa implica honorarios y tasas adicionales y, por los obstáculos señalados, no sería nuestra primera recomendación.

Quedo atenta a tus comentarios,

Saludos"""},
    {"clave": "notion:oposicion-notificada", "servicio": "oposicion", "para_etapa": 4, "canal": "mail", "orden": 3,
     "titulo": "Oposición notificada y contacto con la contraparte", "asunto": "",
     "cuando": "Al día siguiente de la notificación, cuando ya se contactó al agente del oponente.",
     "cuerpo": """[nombre], buenos días.

Te escribo para informarte que el día de ayer se notificó oficialmente en el INPI la oposición presentada contra tu marca. A partir de esa fecha, comienza a correr el plazo legal de 3 meses.

Queremos transmitirte tranquilidad: ya nos contactamos con el agente de propiedad industrial de la contraparte para proponer un acuerdo amistoso. Ya recibimos su respuesta y nos encontramos en pleno intercambio para alcanzar un entendimiento conveniente.

Tan pronto tengamos redactado el documento con los fundamentos y términos del acuerdo, te lo compartiremos para que lo revises y nos des tu conformidad antes de presentarlo.

Quedamos a disposición ante cualquier duda."""},
    # ── Contestación de vista ──────────────────────────────────────────────────────────────────────
    {"clave": "notion:presupuesto-respuesta-vista", "servicio": "vista", "para_etapa": 0, "canal": "mail", "orden": 1,
     "titulo": "Presupuesto de respuesta a vista", "asunto": "Servicio de respuesta de vista ante el INPI - [marca]",
     "cuando": "Cuando el INPI dictó una vista y el cliente pide presupuesto.",
     "cuerpo": f"""Hola, ¿cómo estás?

Te comparto el detalle del servicio para la respuesta de vista de tu trámite ante el INPI.

En este caso, lo que realizamos es un análisis en profundidad de la vista dictada, la elaboración de los fundamentos correspondientes y la preparación de los escritos solicitados para que la solicitud pueda continuar su proceso de registro.

Nos ocupamos de la gestión integral: realizamos el análisis, redactamos los fundamentos, presentamos los documentos requeridos y realizamos el seguimiento diario del trámite hasta la concesión de la marca.

**Detalle de honorarios y gastos:**

- Costo total de la gestión: $140.000 (honorarios) + $10.000 aprox. (tasas oficiales del INPI en caso de corresponder según el tipo de vista). Este valor aplica por cada vista dictada en cada clase.

(Nota: En caso de que la marca, una vez publicada en el boletín, reciba oposiciones u otras observaciones del INPI, dichos trámites se cotizan por separado por tratarse de servicios independientes).

**Pasos para comenzar:**

**1. Completar el formulario de datos:**
Necesitamos que completes el formulario en el siguiente enlace con el mayor detalle posible sobre los servicios/productos. Usaremos esta información para elaborar los fundamentos (te los enviaremos para tu revisión previa antes de la presentación). Una vez completo, te enviaremos el poder correspondiente para firmar.
🔗 {_FORMULARIO}

**2. Transferencia inicial:**
50% de seña para comenzar ($70.000) y el 50% restante una vez redactado y presentado el escrito ante el INPI.

- Titular: Pamela Daiana Guzzardi
- Alias: pamedguzzardi
- CVU: 0000003100062345351106

Quedo a disposición ante cualquier duda.

Saludos,"""},
]


def crear_tablas(cur):
    """Crea la tabla y, si está vacía, carga las 14 plantillas que había en Notion. Las que se borren o
    editen en el panel no se vuelven a cargar (la carga inicial solo corre con la tabla vacía)."""
    for sql in TABLAS:
        cur.execute(sql)
    cur.execute("SELECT COUNT(*) AS n FROM plantillas")
    fila = cur.fetchone()
    n = fila["n"] if isinstance(fila, dict) else fila[0]
    if n:
        return 0
    for p in PLANTILLAS_INICIALES:
        cur.execute(
            """INSERT INTO plantillas (titulo, servicio, para_etapa, canal, asunto, cuando, cuerpo, orden, clave_carga, modificado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'carga inicial (Notion)') ON CONFLICT (clave_carga) DO NOTHING""",
            (p["titulo"], p["servicio"], p["para_etapa"], p["canal"], p["asunto"] or None, p["cuando"], p["cuerpo"],
             p["orden"], p["clave"]))
    return len(PLANTILLAS_INICIALES)
