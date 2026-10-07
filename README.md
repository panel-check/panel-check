# Panel Kom

Pipeline para extraer, estructurar y enriquecer las marcas nuevas de los boletines
del INPI (Argentina), y detectar leads (solicitantes sin agente/apoderado). Basado
en el proceso probado sobre el Boletín Nº 11116 (ver `docs/proceso-original.md`).

## Pasos del pipeline

1. `scripts/listar_boletines.py` — lista boletines de "MARCAS NUEVAS" (HTML plano, sin costo).
2. `scripts/extraer_pdf_apify.py` — extrae el texto de los PDFs vía Apify (actor `automation-lab/pdf-text-extractor`).
3. `scripts/parse_boletin.py` — parsea el texto a filas estructuradas (regex WIPO ST.60, con el fix del campo matrícula).
4. `scripts/completar_mixtas.py` — completa el nombre de las marcas Mixtas/Figurativas contra el webservice SOAP de INPI.
5. `scripts/validar_leads.py` — para las candidatas sin apoderado, entra al expediente (POST directo con `requests`, sin navegador — ver nota abajo), confirma si son leads reales y saca el email del Formulario.
6. `scripts/cargar_db.py` — carga todo a Postgres (Railway), sin duplicar actas.

El workflow `.github/workflows/pipeline.yml` corre estos 6 pasos automáticamente,
4 veces por semana.

## Qué necesitás para levantar esto

### 1. Cuentas

- **GitHub** (ya tenés el repo). Hay que cargar 2 secrets en
  **Settings → Secrets and variables → Actions**:
  - `APIFY_TOKEN`
  - `DATABASE_URL`
- **Apify** — crear cuenta gratis en apify.com, generar un API token
  (Settings → Integrations → API tokens) y usarlo como `APIFY_TOKEN`. Con los
  USD 5 gratis por mes alcanza para varios cientos de boletines.
- **Railway** — crear cuenta en railway.app, un proyecto nuevo con:
  - Un servicio **Postgres** (Railway te da la `DATABASE_URL` armada).
  - Un servicio **NocoDB** conectado a esa misma base, para el panel visual.
- **Resend** — aviso interno diario de leads con oposición/vista nueva
  (`scripts/notificar_oposiciones.py`, corre al final de
  `revisar_oposiciones.yml`). Secrets: `RESEND_API_KEY` (obligatorio) y,
  opcionales, `RESEND_FROM` (default `avisos@quieroregistrarmimarca.com.ar`,
  tiene que ser del dominio verificado en Resend), `NOTIFICAR_A` (default
  `marcas@komunikacion.com.ar`, varios separados por coma) y `PANEL_URL`.
  Cada lead se avisa una sola vez (`notificado_oposicion_en`); si no hay
  novedades no se manda nada.
  Hay **dos cuentas de Resend**: la interna (`RESEND_API_KEY`, avisos al equipo)
  y la de prospectos (`RESEND_API_KEY_PROSPECTOS`, contacto con leads; Resend
  no permite envío en frío, ver su política de uso aceptable). Cuenta, remitente,
  Reply-To, destinatarios y la plantilla de prospectos se configuran en la
  pestaña **Mails** del panel (tablas `mails_config` y `mails_bajas`); las API
  keys nunca se guardan en la base. `RESEND_FROM` y `NOTIFICAR_A` quedan como
  respaldo si no hay nada guardado.
- **Google Calendar** — pantalla **Calendario** del panel, sincronizada en las dos
  direcciones con el calendario de Google del estudio (`panel/calendario_core.py`).
  En Google Cloud: proyecto con la *Google Calendar API* habilitada y una **cuenta
  de servicio** con clave JSON; en Google Calendar, compartir el calendario con el mail de
  esa cuenta con permiso «Hacer cambios en eventos». Se cargan solo en el servicio del
  panel en Railway (no en GitHub): `GOOGLE_SERVICE_ACCOUNT_JSON` (el JSON completo) y
  `GOOGLE_CALENDAR_ID` (el mail del calendario principal). Un evento cuya nota dice
  «Acta N» queda vinculado al cliente o lead de esa marca. Tablas `calendario_eventos`
  y `calendario_estado`.
  **Mail «AGENDA»**: todas las noches a las 20 hs (hora Argentina) el panel manda, desde
  `avisos@quieroregistrarmimarca.com.ar` (cuenta Interna), las reuniones y llamadas del día
  siguiente; si no hay ninguna, no manda nada. `AGENDA_DIARIA_HORA` cambia la hora (o `off`);
  destinatario en Mails → «Agenda diaria» (por defecto `CALENDARIO_AVISO_EQUIPO` o el mail
  del calendario). Tabla `calendario_agenda_envios`.
  Para **generar links de Meet** y mandar el mail de aviso al agendar, el panel tiene que
  usar la propia cuenta de Google del estudio (la cuenta de servicio no puede): cliente
  OAuth de Google Cloud (app «En producción»), `GOOGLE_OAUTH_CLIENT_ID` y
  `GOOGLE_OAUTH_CLIENT_SECRET` en Railway, y el botón **Conectar con Google** de la pantalla
  Calendario, que entrega el `GOOGLE_OAUTH_REFRESH_TOKEN`. Los avisos salen por Resend
  (cuenta de prospectos); la copia al equipo va a `CALENDARIO_AVISO_EQUIPO` (opcional).
  El evento «MEET PAME / TOMI» no se muestra en el panel; `CALENDARIO_OCULTAR` (títulos
  separados por `;`) cambia qué eventos se ocultan. `/agendar-llamada` abre solo el formulario de agendar.
  «✨ Completar con IA» (pegar un texto y que se llene el formulario, preguntando lo que falta) usa la misma
  IA que «Mejorar texto» (`IA_API_KEY`).

### 2. Cargar los secrets en GitHub

En el repo: **Settings → Secrets and variables → Actions → New repository secret**,
uno por cada variable de `.env.example`.

### 3. Habilitar Actions

Ya viene habilitado por default en un repo nuevo. Se verifica en
**Settings → Actions → General → Allow all actions**.

## Primera prueba (paso a paso)

Antes de confiar en el disparo automático, conviene validar cada pieza por separado
con el boletín de referencia (11116), que ya tiene resultados conocidos (693 marcas,
347/379 nombres recuperados):

```bash
pip install -r requirements.txt
playwright install chromium

export APIFY_TOKEN=...
export DATABASE_URL=...

# 1) extraer
python3 scripts/extraer_pdf_apify.py \
  --url https://portaltramites.inpi.gob.ar/Uploads/Boletines/11116_3_.pdf \
  --out data/textos/11116_text.txt

# 2) parsear
python3 scripts/parse_boletin.py --numero 11116 --in data/textos/11116_text.txt --out data/csv/11116.csv

# 3) completar mixtas
python3 scripts/completar_mixtas.py --in data/csv/11116.csv --out data/csv/11116_completo.csv

# 4) validar unos pocos leads (limitado, para no tardar)
python3 scripts/validar_leads.py --in data/csv/11116_completo.csv --out data/csv/11116_leads.csv --limit 10

# 5) cargar a Railway
python3 scripts/cargar_db.py --in data/csv/11116_leads.csv --boletin 11116 --fecha 2026-09-09
```

Si el total de marcas y el % de nombres recuperados coinciden con los números
conocidos del 11116, el pipeline está validado. Recién ahí conviene probar con
`workflow_dispatch` en GitHub Actions sobre un boletín real y nuevo.

## Estructura

```
scripts/            código del pipeline (ver arriba)
db/schema.sql        esquema de Postgres
references/          documentación de los campos del boletín y del webservice de INPI
.github/workflows/   automatización
data/                salidas locales (ignorado por git)
```

## Pendientes conocidos (ver docs/proceso-original.md)

- Revisar a mano las marcas Mixtas/Figurativas sin nombre recuperado.
- Reportar a `soportews@inpi.gob.ar` el bug de inyección SQL encontrado en
  `ConsultaCuitOTitular` con titulares que tienen apóstrofe.
- Definir la vía legal para el envío de emails de prospección antes de conectar
  Resend/Mailrelay.
- `validar_leads.py` usa `requests` puro (sin navegador) porque el WAF de INPI
  bloquea las visitas hechas con Chromium/Playwright headless ("Web Page
  Blocked! Attack ID: 20000051"), pero no bloquea peticiones HTTP simples. El
  dato de CARACTER se obtiene con un POST a `/MarcasConsultas/Resultado`
  (no con el GET `?acta=` que aparece en el link del CSV, que no devuelve la
  sección "GESTION DEL TRAMITE"). Si INPI cambia el HTML o esos endpoints,
  revisar las constantes/regex al principio del script.
