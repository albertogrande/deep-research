# Análisis del sistema actual — Orka (Semantica) con n8n

> Resultado de la exploración del repo `albertogrande/semantica` (2026-07-18).
> Rutas de archivo relativas a la raíz de ese repo.

## 1. Qué es Orka

Plataforma multi-tenant de **agentes RAG**: los usuarios crean *agentes*, los enlazan a *datastores* (colecciones de documentos PDF/DOCX/PPTX/HTML) y chatean con ellos; las respuestas llevan **citas numeradas** `[n]` a páginas concretas. Segundo pilar: **Definitions / Domain Knowledge** — definiciones de negocio (`metric`/`term`/`rule`/`process`) que se inyectan semánticamente en cada consulta para que el agente respete el vocabulario y las fórmulas del negocio, con **validación de métricas** (`validate_metric`) y detección de **knowledge gaps**.

Stack: **Next.js 16 (App Router) + Supabase** (Postgres + pgvector + Storage + Auth). La app **nunca ejecuta inferencia de chat**: todo el trabajo de IA se delega a una instancia n8n (Elestio) vía webhooks. n8n es el runtime completo del agente.

## 2. Superficie de integración n8n

### Llamadas frontend → n8n

| Propósito | Env var | Rutas que llaman |
|---|---|---|
| Chat RAG (síncrono, sin streaming, timeout 10 min) | `N8N_RAG_WEBHOOK_URL` | `app/api/agents/[id]/chat/route.ts` (principal), `app/api/v1/agents/[id]/chat/route.ts`, `app/api/shared/agents/[token]/chat/route.ts`, `app/api/integrations/slack/events/route.ts` |
| Token tracker (fire-and-forget `{execution_id, message_id}`) | *(URL hardcodeada)* | las 3 rutas de chat |
| Ingesta de documentos | `N8N_WEBHOOK_URL` | `app/api/datastores/[id]/documents/route.ts` (con retry en `lib/n8n/retry.ts`) |
| Crawl web (Firecrawl) | `N8N_FIRECRAWL_START_WEBHOOK_URL` | `app/api/datastores/[id]/web-sources/route.ts` |

### Callbacks n8n → frontend

- `POST /api/webhooks/n8n/status` — fin de ingesta → RPC `mark_document_processed`/`mark_document_failed`. Auth opcional con `N8N_WEBHOOK_SECRET`.
- `POST /api/webhooks/n8n/extraction-complete` — definiciones extraídas → borradores en `definitions`.
- `POST /api/webhooks/n8n/extract` — dispara extracción **app-side** (`lib/extraction.ts`, gpt-4o) — precedente útil: la app ya sabe hacer esto sin n8n.

### Contrato del chat (el crítico)

**Request** (idéntico en las 4 rutas):

```json
{ "message": "...", "session_id": "<conversation uuid>", "agent_id": "<uuid>",
  "datastore_ids": ["<uuid>"], "workspace_id": "<uuid>", "definitions": [ ... ] }
```

- Las `definitions` se resuelven **app-side** antes de llamar (embedding de la query → RPC `search_definitions`, threshold 0.3, top 10; fallback a todas las activas).
- **La app NO envía historial ni configuración del agente**: n8n carga la config él mismo y mantiene la memoria (Postgres Chat Memory de LangChain, ventana 20, clave `session_id`).

**Response** que la app parsea (`chat/route.ts:260-354`):

```json
{ "output": [ { "text": "respuesta con [1] [2]",
    "citations": [ {"id":1,"documentId":"uuid","documentName":"X.pdf","pageNumber":5,"excerpt":"...","score":0.95} ] } ],
  "applied_definitions": [ ... ], "validations": [ ... ], "knowledge_gaps": [ ... ],
  "processing_time_ms": 2340, "execution_id": "...",
  "definitions_applied_count": 0, "validations_performed_count": 0,
  "validations_approved_count": 0, "validations_rejected_count": 0 }
```

## 3. El agente actual (workflow versionado en el repo)

`n8n-workflows/rag-agent-v2.3-with-validation.json` — nodo agente LangChain:

- **Modelo**: gpt-5.1, temperatura 0.1. **Memoria**: Postgres Chat Memory (ventana 20). **Salida**: Structured Output Parser + marcadores `USED_DEFINITIONS:` / `VALIDATIONS:` al final del texto, parseados con **regex** (frágil; la app además hace detección fallback por substring).
- **System prompt de 11,5k caracteres** (reglas de domain knowledge, SOP de 3 pasos, reglas de citación `[n]`) — está completo en el JSON → portable con fidelidad.
- **4 tools**:
  1. `Dynamic Hybrid Search` → sub-workflow **NO versionado**. Retrieval híbrido dense/sparse/ilike/fuzzy con pesos elegidos por el LLM (deben sumar 1) sobre Supabase.
  2. `Fetch Document Hierarchy` → lookup en tabla `documents`.
  3. `Context Expansion` → Edge Function de Supabase **en otro proyecto** (`nfldyebqdkolqboiapxh`): trae chunks vecinos/padre dado `{doc_id, chunk_ranges}`.
  4. `validate_metric` → sub-workflow **NO versionado** pero especificado por completo en `N8N_VALIDATION_TOOL.md`: llamada enfocada a gpt-4o-mini (temp 0.1, json, máx 500 tokens) → `{approved, adjusted_value, reasoning, instruction}`. **Fail-open** (aprueba si no hay definición o falla la API).

## 4. Esquema Supabase relevante

- `document_chunks` (el vector store): `embedding vector(3072)` (text-embedding-3-large a dimensión completa, escrito por n8n), `fts tsvector` (GIN). **Sin índice vectorial** (3072 > límite 2000 de pgvector) → cosine por fuerza bruta. RPC `search_document_chunks(query_embedding vector(3072), match_count, filter_datastore_ids[])`.
- `definitions`: `embedding vector(1536)` (escrito por la app) con índice HNSW. RPC `search_definitions(...)`.
- `agents`: fila de configuración enorme (prompts, `retrieval_top_k=10`, `similarity_threshold=0.70`, `generation_model`, `temperature`, `enable_streaming`…) — **n8n la ignora en gran parte hoy**.
- `conversations` + `messages`: historial durable (con `citations`, `applied_definitions`, `knowledge_gaps`, `processing_time_ms` JSONB/columnas).
- Storage: bucket privado `documents` (50 MB), rutas `datastores/<id>/<ts>_<nombre>`.

### ⚠️ Deriva entre migraciones y BD real (landmines)

- La migración `007` "corrige" chunks a 1536-dim + ivfflat, pero la BD viva usa 3072 brute-force (lo que escribe n8n).
- `messages.{input_tokens, output_tokens, cost}`, `messages.knowledge_gaps`, `documents.{ingestion_cost, *_tokens}` y la tabla `definition_agents` existen **solo en la BD viva** (y en `types/database.ts`), sin migración en el repo.
- `messages.validations` (migración 033) existe pero **el write está comentado** en `chat/route.ts:366`.

## 5. Ingesta actual

Upload → Supabase Storage → signed URL (1h) → webhook n8n `process-document` con `callback_url` → n8n hace OCR/chunking/embedding (3072) y escribe `document_chunks` con service role → callback a `/api/webhooks/n8n/status` (`chunks_count`, `document_headline`, `document_summary`). El pipeline de ingesta de n8n **no está versionado** (solo prosa en `N8N_INTEGRATION_PLAN.md`). Variante web: Firecrawl (start + workflow de polling por cron, tampoco versionados).

## 6. Trazabilidad hoy

**No hay tracing** (ni OTel, ni spans, ni APM). Solo:
- `processing_time_ms` que devuelve n8n → columna en `messages`.
- Tokens/coste: webhook "token tracker" asíncrono de n8n que escribe columnas en `messages` a posteriori.
- Los dashboards de uso (`/api/usage`, `/api/usage/logs`, `/api/ingestion/logs`, `/api/billing`) **leen solo de Supabase** → si el nuevo servicio escribe las mismas columnas, los dashboards siguen funcionando sin tocar el frontend.

## 7. Evals hoy

**Prácticamente inexistentes** — terreno casi virgen para Pydantic Evals:
- Único mecanismo: `validate_metric` (LLM-judge de conformidad métrica↔definición). Sus resultados llegan al cliente pero **ni se persisten ni se renderizan** (`validations-panel.tsx` es código muerto).
- Único "harness": `scripts/test-validation-tool.js` — 4 queries hardcodeadas sin asserts.
- Nada evalúa calidad de respuesta, factualidad ni **corrección de citas** (hueco claro). El feedback 👍/👎 de la UI es un stub sin persistir.

## 8. Workflows NO versionados (a reconstruir, no recuperar)

1. Sub-workflow de Hybrid Search (el retrieval real).
2. Sub-workflow validate_metric (documentado en prosa — reconstruible con fidelidad).
3. Pipeline principal de ingesta (OCR → chunk → embed).
4. Workflow del token tracker.
5. Workflows Firecrawl (start + polling).
