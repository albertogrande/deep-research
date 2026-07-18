# semantica-ai

Servicio Python que sustituye a n8n como motor de IA de **Orka** ([`albertogrande/semantica`](https://github.com/albertogrande/semantica)), construido con:

- **[Pydantic AI](https://ai.pydantic.dev)** — framework de agentes (el agente RAG, sus tools y salidas estructuradas).
- **[Logfire](https://logfire.pydantic.dev)** — trazabilidad y observabilidad (trazas por chat, tokens, coste, latencia).
- **[Pydantic Evals](https://ai.pydantic.dev/evals/)** — harness de evaluación (datasets dorados, jueces de citas y validación de métricas).

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/00-bitacora.md`](docs/00-bitacora.md) | Diario del proceso, sesión a sesión |
| [`docs/01-analisis-sistema-actual.md`](docs/01-analisis-sistema-actual.md) | Cómo funciona Orka hoy con n8n (contratos, esquema, huecos) |
| [`docs/02-plan-migracion.md`](docs/02-plan-migracion.md) | Veredicto de viabilidad y plan de migración por fases |

## Estado

📋 **Fase de análisis completada** — pendiente de arrancar la Fase 0 (scaffold + inspección del esquema real de la BD). Ver el plan de migración para las fases y sus entregables.
