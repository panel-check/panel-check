# Códigos de campo del texto extraído del boletín (estilo WIPO ST.60)

Cada solicitud de marca en el texto del boletín sigue este patrón (con saltos de línea
variables entre campos, por eso el regex en `scripts/parse_boletin.py` usa `\s*` liberal):

```
(21) Acta 4605494 - (51) Clase
9
(40) D (54) CLARY
(22) 12/11/2025 13:18:00.610 - (73)
UNOBRAVO S.R.L. SOCIETÀ BENEFIT - IT *
(57) SOLAMENTE
TERMINOS INGRESADOS MANUALMENTE: SOFTWARE DE COMPUTADORA...
(30) // 302025000146644 - 24/09/25 - IT
(74) 927
1399 - (44)09/09/2026
```

| Código | Campo | Notas |
|---|---|---|
| (21) | Acta | Número de expediente/trámite |
| (51) | Clase | Clase de Niza, 1 a 45 |
| (40) | Tipo de marca | D=Denominativa, M=Mixta, F=Figurativa, T=Tridimensional, R=Renovación/otro |
| (54) | Denominación | Nombre de la marca. Vacío en M/F (el nombre está en un logo/imagen) |
| (22) | Fecha de presentación | `DD/MM/AAAA HH:MM:SS.mmm` |
| (73) | Titular | Nombre + país (separados por ` - XX *`) |
| (57) | Productos/servicios | Texto libre, puede ser muy largo |
| (30) | Prioridad internacional | No siempre presente |
| (74) | Matrícula de agente | Ver nota de bug abajo. Puede ser un número, la palabra "Part." (Particular, declarado explícitamente), o estar ausente del todo |
| (44) | Fecha de publicación | Pega directo después de (74) sin espacio, ej `(44)09/09/2026` |

## Bug del campo (74) y su fix

A veces el texto extraído del PDF mete un número de basura extra entre la matrícula real
y el cierre `- (44)`, probablemente por interferencia de una columna vecina durante la
extracción del PDF (el boletín tiene diseño a dos columnas). Ejemplo real:

```
(74) 927
1399 - (44)09/09/2026
```

Acá `927` es la matrícula real; `1399` es basura. Un regex ingenuo que busca
`\(74\)\s*(\S*)\s*-\s*\(44\)` falla en capturar nada acá porque `1399` rompe el patrón
esperado inmediatamente después del número. El fix es no exigir que el `- (44)` venga
pegado: `\(74\)\s*(Part\.|\d+)` — captura solo el primer token válido después de `(74)` y
listo, ignora lo que venga después. Esto recuperó ~34% de las matrículas que antes se
perdían (149 de 295 en un boletín de prueba, 230 de 379 en otros dos).
