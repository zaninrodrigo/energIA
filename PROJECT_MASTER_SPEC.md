# PROJECT_MASTER_SPEC.md

# EnergIA — Índice Maestro de Documentación

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-03 | Borrador | Rodrigo Zanin |

## Propósito

Este documento es el índice maestro de toda la documentación de EnergIA. Reúne, en una única tabla, el estado real de cada documento del repositorio, y deja registrada la deuda documental conocida —inconsistencias, secciones vacías y brechas de trazabilidad— para que quede visible en un solo lugar en lugar de descubrirse de forma dispersa al leer cada documento por separado.

## Documentos

| Documento | Descripción | Estado |
|---|---|---|
| `docs/01-business/PRODUCT_VISION.md` | Visión de producto: problema, misión, propuesta de valor, roadmap de versiones | Completo (borrador v1.0.0) |
| `docs/01-business/BUSINESS_ANALYSIS.md` | Análisis de negocio: procesos AS-IS/TO-BE, reglas de negocio, glosario, KPIs | Borrador (anexos integrados, secciones pendientes) |
| `docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` | Especificación de requisitos de software (SRS) bajo estándar IEEE 29148 | Completo |
| `docs/02-requirements/USER_STORIES.md` | Backlog de producto: historias de usuario | Completo |
| `docs/02-requirements/ACCEPTANCE_CRITERIA.md` | Criterios de aceptación por requisito e historia de usuario | Completo |
| `docs/03-architecture/DOMAIN_MODEL.md` | Modelo de dominio DDD: ubiquitous language, bounded contexts, entidades, agregados | Completo (inconsistencias pendientes de revisión) |
| `docs/03-architecture/DATABASE_DESIGN.md` | Diseño lógico y físico de la base de datos PostgreSQL | Borrador |
| `docs/03-architecture/SOFTWARE_ARCHITECTURE_DOCUMENT.md` | Documento de arquitectura de software | Esqueleto (sin contenido) |
| `docs/03-architecture/API_SPEC.md` | Especificación de la API REST del backend | Pendiente |
| `docs/04-ai/AI_ENGINE_SPEC.md` | Especificación del Motor de Inteligencia Energética | Pendiente |
| `docs/04-ai/DATA_SCIENCE_NOTEBOOK.md` | Plan de análisis exploratorio de datos | Pendiente |
| `docs/05-devops/SECURITY_SPEC.md` | Especificación de seguridad (autenticación, autorización, OWASP) | Pendiente |
| `docs/05-devops/TESTING_SPEC.md` | Estrategia de testing (unitario, integración, E2E) | Pendiente |
| `docs/05-devops/DEPLOYMENT_SPEC.md` | Estrategia de despliegue (Docker, entornos, CI/CD) | Pendiente |
| `docs/05-devops/ROADMAP.md` | Consolidación del roadmap de versiones v1-v4 | Pendiente |

## Deuda documental conocida

1. ~~**Tres esquemas de numeración de reglas de negocio en conflicto.**~~ **Resuelto.** `BUSINESS_ANALYSIS.md` (sección 15) es ahora la fuente canónica única de reglas de negocio, RN-001 a RN-013 (las siete reglas que definía `SOFTWARE_REQUIREMENTS_SPECIFICATION.md` se conciliaron contra ese catálogo: seis eran duplicados semánticos de reglas ya existentes y una, sin equivalente, se incorporó como RN-013). `SOFTWARE_REQUIREMENTS_SPECIFICATION.md` (sección 11) ya no define reglas propias: referencia las reglas canónicas relevantes mediante una tabla. `DOMAIN_MODEL.md` renombró su numeración propia de RN-001–RN-049 a RD-001–RD-049 (Regla de Dominio), dejando claro que se trata de invariantes de nivel de entidad y no de reglas de negocio; donde un invariante de dominio implementa de forma directa una regla de negocio canónica, se anotó con "(implementa RN-xxx)".

2. **`SOFTWARE_ARCHITECTURE_DOCUMENT.md` es un esqueleto vacío.** Sus 25 secciones contienen únicamente encabezados y listas de palabras clave, sin contenido redactado. La sección de Decisiones Arquitectónicas solo lista placeholders (ADR-001, ADR-002, ...) sin ningún ADR redactado.

3. **`DATABASE_DESIGN.md` no cubre todo el modelo de dominio.** Define 15 tablas, pero aproximadamente 9-10 entidades del dominio descriptas en `DOMAIN_MODEL.md` no tienen tabla asociada (entre ellas Categoría Tarifaria, Plan de Inspección, Asignación de Inspector, Tarea RRHH, Dataset Etiquetado y Métricas/Versionado del Modelo de IA). Además, el diseño no define restricciones CHECK ni NOT NULL, y las claves foráneas son solo anotaciones textuales sin cláusula REFERENCES real.

4. **Brechas de trazabilidad entre requisitos, historias y criterios de aceptación.** 18 de las 40 historias de usuario no tienen un criterio de aceptación asociado; 7 de los 20 requisitos funcionales tampoco. Existen además dos matrices de trazabilidad distintas y no equivalentes: la de `SOFTWARE_REQUIREMENTS_SPECIFICATION.md` (sección 16) tiene 4 filas y solo vincula Objetivos, RF y Casos de Uso (sin columnas de US ni AC); la de `ACCEPTANCE_CRITERIA.md` tiene 9 filas y sí vincula RF, US y AC, pero cubre solo una fracción de los requisitos totales.

5. **Glosario incompleto.** `docs/01-business/BUSINESS_ANALYSIS.md` (sección Glosario) no define términos usados de forma recurrente en `DOMAIN_MODEL.md` y `DATABASE_DESIGN.md`: Localidad, Barrio, Motor de Inteligencia Energética y Cuadrilla, además de los estados de entidades (por ejemplo, los estados de un Lote de Facturación o de una Orden de Inspección). Se observa además una inconsistencia de nombres entre documentos: `DOMAIN_MODEL.md` y `DATABASE_DESIGN.md` usan "Motor de Inteligencia Artificial", mientras que `PRODUCT_VISION.md` y `BUSINESS_ANALYSIS.md` usan "Motor de Inteligencia Energética" para referirse aparentemente al mismo componente.
