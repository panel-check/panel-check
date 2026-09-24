# Webservice SOAP de INPI (ws.inpi.gob.ar)

URL: `https://ws.inpi.gob.ar/wsinpi.asmx` (producción). WSDL en `?WSDL`.
SOAP 1.1: header `SOAPAction: "http://tempuri.org/<Operacion>"`, `Content-Type: text/xml; charset=utf-8`.
Latencia muy variable (1s a 90s+); reintentar con timeout generoso (45-60s) antes de asumir que falló.

## Operaciones públicas (sin credenciales) — confirmadas funcionando

### ConsultaCuitOTitular
Input: `cuit` (opcional) y/o `titular` (string, nombre a buscar).
Devuelve `GrillaMarcas[]`: `Acta`, `Titulares` (incluye CUIT), `Fecha_Ingreso`, `Clase`,
`Denominacion`, `Tipo_Marca`. Uso principal: completar el nombre real de marcas
Mixtas/Figurativas (que en el boletín no traen texto), cruzando por titular y filtrando
por número de Acta en la respuesta.

⚠️ Sanitizar bien el string de `titular` en el XML. Un apóstrofe en el nombre (ej. "O'Brien",
"CAFFE' PASCUCCI") puede devolver un error de sintaxis SQL crudo desde el servidor — señal
de una inyección SQL del lado de INPI. No intentar explotarlo. Reportar a soportews@inpi.gob.ar
si se encuentra en producción.

### ConsultaDenominacion
Input: `Denominacion` (string, nombre de marca a buscar).
Devuelve `estado`: "Disponible" o "No Disponible", y si no está disponible, la lista de
marcas en conflicto. Es un buscador público de disponibilidad de marca — útil como
herramienta separada (ej. "¿tu marca está disponible?") para ofrecer a clientes antes de
que registren un nombre.

## Operaciones que requieren credenciales (NO usar sin autorización real)

### ConsultaNotificaciones
Input: `expediente` + `datosUsuario` (objeto `Usuario` con `Cuit`, `Clave`, `Cuit_Rel`).
Requiere ser un usuario empadronado real del portal de INPI (agente de propiedad
industrial registrado) — no es de acceso público como las otras dos. Confirmado con
soporte de INPI: **devuelve únicamente las notificaciones dirigidas al CUIT que hace la
petición autenticada** — no sirve para consultar notificaciones de terceros aunque se
pase su CUIT en `Cuit_Rel`. Formato de fecha: `AAAA-MM-DD`. El campo `Adjunto` solo da el
nombre del archivo (no lo descarga). Solo es útil si la agencia o un cliente opera como
apoderado/agente registrado ante INPI con cuenta propia.

### Ingresar_MarcasNuevas / Ingresar_MarcaRenovacion / Ingresar_ModeloNuevo /
### Ingresar_ModeloRenovacion / Ingresar_PatenteInvecionNueva / Ingresar_PatenteUtilidadNueva
Operaciones de alta de trámites (escritura). Requieren alta de acceso al WS (mandar CUIT,
email, nombre y teléfono a soportinformatica@inpi.gob.ar). Hay entorno de testing
(`wstesting.inpi.gob.ar`) separado del de producción. No exploradas en este proyecto —
solo relevantes si en algún momento la agencia empieza a iniciar trámites vía API en
nombre de clientes.

## Páginas web relacionadas (no son parte del webservice SOAP, pero se usan en el pipeline)

- `https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={acta}` (GET simple) —
  página pública liviana, solo trae Clase/Protección/Limitación. NO trae titular, agente,
  ni denominación de Mixtas.
- Flujo completo con más datos: entrar por `MARCAS → Consultas → Consultas de Marcas`,
  buscar por Número de Acta (esto hace un POST a `/MarcasConsultas/Grilla`), y expandir el
  resultado (botón "+") — lleva a una página `Resultado` mucho más completa: Datos
  Generales, Titularidad (con CUIT y domicilio), **Gestión del Trámite** (Agente +
  Carácter), Prioridades, Publicación, Oposiciones, Vistas y Notificaciones, Renuncias,
  Ubicación del Expediente, y el botón **Grilla Digital** (archivos adjuntos, incluyendo
  el Formulario con el email del titular). Esta página no tiene una URL directa armable
  solo con el acta — hay que navegar el flujo (requiere sesión/cookies, por eso conviene
  Playwright en vez de requests sueltos).
