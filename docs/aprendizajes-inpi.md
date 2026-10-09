# Aprendizajes sobre INPI y la revisión de oposiciones

Actualizado el 09/10/2026. Sale de la sesión en la nube que ajustó las reglas de la Grilla Digital (PR #45, rama `claude/gallant-keller-514ju0`). Está escrito para pegarlo tal cual en la memoria del proyecto «panel inpi» de claude.ai, y para que las sesiones en la nube lo lean desde el repo.

Lo **confirmado** viene de capturas del portal o de datos reales (150 actas). Lo marcado como **hipótesis** todavía no lo confirmó nadie.

## 1. Cómo se lee INPI

- Grilla Digital: `POST /Home/GrillaDigital` con `fname = 1-<acta>` y después `POST /Home/GrillaDigitales` con `{acta, limit: 50, offset: 0, direccion: 1}`. Devuelve filas con `Indice`, `Referencia`, `Fecha` y `NombreGde`.
- Expediente: `POST /MarcasConsultas/Resultado` con el acta. Las tablas OPOSICIONES y VISTAS vienen como `opos = JSON.parse('[...]')` y `vistas = JSON.parse('[...]')`. Una fecha vacía es `01/01/0001`.
- La representación del titular sale de **GESTION DEL TRAMITE** (AGENTE / CARACTER). El Agente y el Caracter de cada oposición son los del **oponente**.
- El WAF de INPI bloquea navegadores headless. Con `requests` simple anda. **Desde el contenedor de Claude en la nube el proxy bloquea INPI y no hay `DATABASE_URL`**: todo lo que toque INPI o la base se corre en GitHub Actions.
- Los logs de Actions son públicos (el repo es público): no imprimir nombres ni datos de personas, solo acta, estados y los textos de Indice/Referencia/Fecha.

## 2. La Grilla Digital: lo que hay que saber

- **Indice y Referencia son dos columnas distintas.** A veces INPI agrega texto extra en la celda: comparar por «contiene», sin tildes ni mayúsculas.
- **Viene con lo más nuevo arriba.** La «Cédula de Notificación» aparece *antes* que su «Vista de Marcas»: buscar el par en filas vecinas, en cualquier orden.
- **Las fechas vienen en UTC** (`/Date(ms)/`), no en hora argentina. Lo presentado después de las 21:00 ART cae en el día siguiente.
- Con `limit: 50` alcanza: en 150 actas la Grilla más larga tuvo 13 filas. La pantalla pagina de a 10, la consulta no.
- **Una oposición**: «Recibo de Ingreso» + «Opo. de Marcas». No usar `OPO` suelto: también coincide con «Ratifica Gestión en **Opo**sición».
- **Paquete del oponente**: al presentar la oposición, unas horas después aparecen «Formulario» y «Acompaña Poder / ACOMPAÑA PODER» con la misma hora entre sí. De 59 filas de poder, 50 cayeron dentro de las 12 horas posteriores a una oposición, y ninguna de esas actas tenía representante del titular en el expediente.
- **Notificación al titular**: «Vista de Marcas» + «Cédula de Notificación». También la registra la tabla VISTAS del expediente (Tipo «Oposiciones»), aunque FEC NOTIF de la tabla de oposiciones esté vacía (acta 4759596). Solo 3 de 150 actas estaban notificadas: los leads son recientes e INPI tarda semanas.

## 3. Reglas acordadas el 08/10/2026 (`scripts/oposiciones_expediente.py`)

Todas sobre filas posteriores a la presentación de la oposición.

| Señal en la Grilla | Resultado |
|---|---|
| «Vista de Marcas» + «Cédula de Notificación» (vecinas) | `notificada_en_plazo` (fecha de la cédula) |
| «Recibo de Ingreso» + «Escritos de Marcas» | `contestada` (atendida) — **en revisión, ver §5** |
| «Formula Desistimiento» | `levantada` (ya no `LEVANT`, `DESIST`, `RETIRA` sueltos) |
| «Acompaña Poder», «Ratifica Gestión en Oposición» o «Ratifica Gestión» | `con_apoderado` (el lead se descarta), **solo si** es de la notificación al titular en adelante **y no** del día de una oposición ni del siguiente |

**Por qué el poder tiene esas condiciones:** el acta 4759596 tenía un «Acompaña Poder» que era de uno de los oponentes, no del titular. El poder del oponente entra junto con su oposición, antes de que se notifique al titular. El del titular o su gestor llega después, para contestar. Un poder entre la oposición y la notificación **no descarta por ahora**.

## 4. Decisiones de la ficha del titular

- Al mandar el mail con **✉ Enviar mail de oposición** (botón de la cabecera), la ficha pasa sola a la siguiente de la lista desde la que se abrió. **No pasa** si el mismo CUIT tiene 2 o más marcas con oposición pendiente (tuvo oposición, sigue siendo lead, sin atender y que todavía sirve): ahí se queda para revisarlas a mano. Tampoco pasa si el envío se cancela o falla, ni si era la última o no se abrió desde una lista. El botón «Enviar mail» de la pestaña Mail no cambia.
- **🗂 Ver Grilla Digital** queda siempre que el titular tenga alguna marca con oposición, aunque ya se haya mandado el mail, esté atendida, tenga gestor/apoderado o sea cliente. Con varias marcas con oposición hay un botón por marca («· acta N»).
- «Ver acta en INPI» y «Enviar mail de oposición» siguen solo con el lead pendiente.

## 5. Pendiente de confirmar (09/10/2026)

1. **«Escritos de Marcas»** descarta hoy sin exigir notificación previa. En 7 actas (4778098, 4774246, 4778323, 4765846, 4777099, 4778907, 4766140) el escrito aparece **antes de que se notifique** al titular, y en 4 viene a menos de un día de una oposición. *Hipótesis:* lo presenta el oponente. Recomendación: que cuente solo desde la notificación.
2. **«Ratifica Gestión en Oposición»** (4 actas, siempre pegada a una oposición). *Hipótesis:* es el gestor del oponente ratificando su gestión.
3. **Acta 4765846:** «Ratifica Gestión en Solicitud» + «Acompaña Documento de Prioridad» + «Credencial CPACF» antes de la cédula. Parece un abogado que se suma del lado del titular.
4. ¿Un poder entre la oposición y la notificación debe descartar? ¿Se usa la tabla VISTAS como segunda fuente de notificación?
5. `--reverificar-apoderados` mira solo el expediente: puede devolver a lead a los que la Grilla descartó. No correrlo hasta revisarlo.

## 6. Cómo se trabajó y se probó

- **Diagnóstico de solo lectura:** workflow «Diagnóstico de oposiciones» (`scripts/diagnostico_oposiciones.py`), con sesión `readonly`. Compara el estado guardado con el que calcula el clasificador y lista el vocabulario real de la Grilla. Un workflow nuevo no se puede disparar a mano hasta que esté en `main`: mientras tanto se usó un disparador temporal por `push` en la rama.
- Pruebas: `cd scripts && python3 -m unittest test_oposiciones_expediente test_titular_siguiente test_crm_grilla` (56). Las de `titular.html` y `crm.js` corren el código real con Node y se saltean si no hay Node.
- **CLAUDE.md:** cada cambio de una función del sistema actualiza `panel/static/ayuda.html` (pestañas Ayuda y Manual) en el mismo cambio.
