# Data Science Notebook

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-03 | Pendiente | Rodrigo Zanin |

## Propósito

Este documento describirá el plan de análisis exploratorio de datos (EDA) sobre los datasets de consumo, facturación e inspecciones provenientes de Oracle, previo a la construcción del Motor de Inteligencia Energética. Su objetivo es documentar el proceso de exploración, limpieza y preparación de datos, así como la evaluación comparativa de modelos, de forma que las decisiones tomadas en AI_ENGINE_SPEC.md queden respaldadas por evidencia empírica.

## Contenido previsto

- Descripción de los datasets fuente (origen Oracle, volumen, período cubierto, calidad de datos).
- Análisis exploratorio de datos (EDA): distribución de consumos, estacionalidad, valores atípicos, datos faltantes.
- Proceso de limpieza y normalización de datos previo al modelado.
- Ingeniería de features candidatas y su justificación estadística.
- Comparación de algoritmos de detección de anomalías evaluados (incluyendo Isolation Forest).
- Métricas de evaluación utilizadas (precisión, recall, F1, AUC) y resultados obtenidos por modelo.
- Análisis de sesgos o desbalance en los datos de inspecciones históricas.
- Conclusiones y recomendaciones que alimentan el diseño final del motor de IA.
