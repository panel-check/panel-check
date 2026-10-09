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

## Aprendizajes sobre INPI
Antes de tocar la lectura de la Grilla Digital, la revisión de oposiciones o la
ficha del titular, leer `docs/aprendizajes-inpi.md`: ahí están cómo responde INPI
(orden, fechas en UTC, paquete del oponente), las reglas acordadas, las decisiones
de la ficha y lo que sigue sin confirmar. Cuando se aprenda algo nuevo en una
sesión (sobre todo en la nube, que no actualiza la memoria del proyecto de
claude.ai), sumarlo a ese archivo en el mismo cambio.
