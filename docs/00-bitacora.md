# Bitácora del proceso — Migración de Orka (Semantica) de n8n a Pydantic AI

> Diario de trabajo. Cada sesión añade una entrada nueva al final.

## 2026-07-18 — Sesión 1: Análisis de viabilidad

**Objetivo**: evaluar la viabilidad de eliminar n8n como motor de IA de Orka (repo `albertogrande/semantica`) y sustituirlo por un servicio Python con **Pydantic AI** (agentes), **Logfire** (trazabilidad) y **Pydantic Evals** (evaluación), manteniendo el frontend Next.js.

**Qué se hizo**:
1. Se clonó `albertogrande/semantica` en el entorno de trabajo para su análisis.
2. Se lanzaron 3 agentes de exploración en paralelo sobre el código:
   - Agente 1: mapa completo de la superficie de integración n8n (workflows, webhooks, contratos de payload).
   - Agente 2: arquitectura frontend/API/Supabase (rutas, flujo de chat, esquema de BD, ingesta).
   - Agente 3: trazabilidad y evals/validación existentes.
3. Con los hallazgos consolidados, un agente de diseño produjo el plan de migración detallado.
4. Se documentó todo en este repo:
   - [`01-analisis-sistema-actual.md`](./01-analisis-sistema-actual.md) — cómo funciona Orka hoy con n8n.
   - [`02-plan-migracion.md`](./02-plan-migracion.md) — plan por fases, diseño técnico, riesgos y verificación.

**Veredicto**: la migración es **altamente viable** y puede hacerse como *drop-in replacement* (el frontend solo cambia variables de entorno). Detalle en los documentos enlazados.

**Pendiente / siguiente paso**: decisión del usuario sobre arrancar la Fase 0 (scaffold del servicio + script de inspección del esquema real de la BD) y credenciales necesarias (`OPENAI_API_KEY`, `LOGFIRE_TOKEN`, connection string de Supabase dev).
