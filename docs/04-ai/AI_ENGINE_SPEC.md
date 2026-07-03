# AI Engine Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-03 | Pendiente | Rodrigo Zanin |

## Propósito

Este documento especificará el diseño del Motor de Inteligencia Energética de EnergIA, el componente encargado de analizar los consumos generados en cada lote de facturación y detectar comportamientos anómalos combinando reglas de negocio, análisis estadístico e Isolation Forest. Describirá el pipeline de procesamiento, el cálculo del Índice de Riesgo Energético (IRE) y del Impacto Económico Estimado (IEE), y los mecanismos de explicabilidad que permiten justificar cada resultado ante los analistas. También cubrirá el ciclo de vida del modelo, incluyendo su reentrenamiento periódico.

## Contenido previsto

- Arquitectura del pipeline de análisis: ingesta de consumos, preprocesamiento, ejecución de reglas, análisis estadístico y modelo de IA.
- Ingeniería de features utilizadas para la detección de anomalías (consumo histórico, estacionalidad, categoría tarifaria, etc.).
- Configuración y justificación del uso de Isolation Forest como algoritmo de detección de anomalías.
- Fórmula y criterios de cálculo del Índice de Riesgo Energético (IRE), en escala 0-100.
- Fórmula y criterios de cálculo del Impacto Económico Estimado (IEE).
- Mecanismo de explicabilidad de resultados (qué factores influyeron en cada clasificación).
- Proceso de reentrenamiento del modelo a partir de los resultados de inspección (feedback loop).
- Métricas de evaluación del modelo (precisión, recall, falsos positivos/negativos) y umbrales de aceptación.
- Versionado de modelos y trazabilidad entre versión de modelo y resultados generados.
