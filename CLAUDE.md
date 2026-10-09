# Reglas del proyecto

## Documentación obligatoria
Siempre que se agregue, saque o actualice una función del sistema (un proceso
automático / workflow, un script, un filtro o pantalla del panel, un aviso por
mail, una herramienta manual, un cambio de horario, etc.), en el MISMO cambio
hay que actualizar `panel/static/ayuda.html`:
- pestaña **Ayuda** (cómo se usa / qué significa lo que se ve en el panel), y
- pestaña **Manual del sistema** (proceso paso a paso, tabla "Procesos
  automáticos" con horarios en hora Argentina, "Avisos por mail",
  "Herramientas manuales", tarjetas del panel y glosario).

La página tiene que describir siempre el sistema completo tal como está.
Los crons de los workflows están en UTC; en la ayuda van en hora Argentina (UTC-3).

## Entrega de cambios
Al terminar un cambio, subirlo a la rama de trabajo, abrir el pull request
hacia `main` y mergearlo (squash) sin esperar que lo pidan: el usuario no lo
hace a mano y Railway despliega desde `main`. Después revisar que el deploy
en Railway salga bien y avisar el link del PR.
