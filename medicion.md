# Medición de la vigilancia (últimos 30 días)

- Marcas vigiladas hoy: **93**
- Ajustes: meses_universo=18, nivel_alta=90, nivel_media=82, umbral=65
- Solicitudes comparadas por día (con corrida): promedio **3851**, mediana 50, máximo 75426 (20 días con corrida)
- Alertas de parecido creadas en el período: **984**
- Alertas por día (días con alertas): promedio **41**, mediana 12.5, máximo 544 (24 días)
- Alertas por día calendario (promedio sobre 30 días): **32.8**
- Si se agrupa por marca (no por acta): **984** alertas (0 duplicadas)

### Por nivel

| Nivel | Alertas |
|---|---|
| baja | 961 |
| media | 20 |
| alta | 3 |

### Por regla que la disparó (primer motivo)

| Regla | Alertas | % | nueva | monitorear | oponer | opuesta | descartada |
|---|---|---|---|---|---|---|---|
| palabra en común | 394 | 40% | 4 | 0 | 0 | 0 | 19 |
| concepto (IA) | 358 | 36% | 85 | 0 | 0 | 0 | 2 |
| parecido escrito | 91 | 9% | 32 | 0 | 0 | 0 | 1 |
| suenan parecido | 67 | 7% | 16 | 0 | 0 | 0 | 0 |
| otro | 62 | 6% | 1 | 0 | 0 | 0 | 0 |
| una contiene a la otra | 8 | 1% | 0 | 0 | 0 | 0 | 1 |
| suenan igual | 4 | 0% | 0 | 0 | 0 | 0 | 0 |

- Alertas abiertas (todas las fechas) con el plazo de oposición ya vencido: **0**
- Total histórico por estado: descartada_ia 914, nueva 140, descartada 73
### Estado y veredicto de la IA (todas las alertas)

| Estado | Veredicto IA | Alertas |
|---|---|---|
| descartada | (sin juzgar) | 73 |
| descartada_ia | cercana | 35 |
| descartada_ia | no_confundible | 879 |
| nueva | cercana | 1 |
| nueva | confundible | 8 |
| nueva | identica | 1 |
| nueva | (sin juzgar) | 130 |

- Alertas abiertas hoy (lo que se ve en «Abiertas»): **140**
- Alertas que juntan varias actas/clases de la misma marca: **924**
### Nuevas sin juzgar: ¿la marca del cliente sigue en la cartera?

| En cartera | Vigilar | Cliente activo | Alertas |
|---|---|---|---|
| True | True | True | 130 |

### Escaneo de actas: guardadas por día (sin agente / con agente / sin dato)

| Día | Sin agente | Con agente | Sin dato |
|---|---|---|---|
| 2026-11-03 | 315 | 394 | 0 |
| 2026-11-04 | 322 | 518 | 0 |
| 2026-11-05 | 401 | 504 | 0 |
| 2026-11-07 | 1 | 7 | 0 |
| 2026-11-08 | 564 | 918 | 0 |
| 2026-11-09 | 38 | 17 | 0 |
| 2026-11-11 | 60 | 11 | 0 |
| 2026-11-12 | 663 | 368 | 0 |

- Última corrida de la vigilancia: {'errores_ia': 0, 'juez_activo': 1, 'juzgadas_ia': 0, 'llamadas_ia': 224, 'recomparadas': 0, 'descartadas_ia': 0, 'reevaluadas_ia': 212, 'alertas_parecido': 0, 'costo_ia_usd_max': 1.1512, 'marcas_vigiladas': 93, 'duplicadas_unidas': 0, 'vencidas_borradas': 0, 'alertas_otro_agente': 0, 'propuestas_matricula': 0, 'sumadas_por_matricula': 0, 'solicitudes_comparadas': 0}
