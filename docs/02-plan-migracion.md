# Plan de migración — n8n → Pydantic AI + Logfire + Pydantic Evals

> Repo destino: este (`albertogrande/pydantic`). App de referencia: `albertogrande/semantica`.
> Basado en el análisis de [`01-analisis-sistema-actual.md`](./01-analisis-sistema-actual.md).

## Veredicto de viabilidad

**Altamente viable, y como *drop-in replacement*.** Razones:

1. El contrato frontend↔n8n está completamente documentado y centralizado: el nuevo servicio expone los mismos webhooks y la migración del frontend se reduce a **cambiar variables de entorno** (una por fase).
2. El "cerebro" del agente (system prompt de 11,5k chars, esquema de salida, descripciones de tools) está versionado en el repo de semantica → portable con fidelidad.
3. Todos los datos viven ya en Supabase (chunks, definiciones, mensajes, config de agentes) → el servicio Python solo necesita la connection string de Supabase + `OPENAI_API_KEY`.
4. Pydantic AI mejora directamente los puntos frágiles del sistema actual: marcadores+regex → salida estructurada nativa; memoria opaca dentro de n8n → historial explícito desde `messages`; token-tracker webhook → Logfire; validación sin persistir → evaluadores tipados y persistencia.

Los riesgos principales no son de viabilidad sino de **paridad** (sub-workflows perdidos que hay que reconstruir y deriva entre migraciones y BD viva) — se mitigan con una fase 0 de inspección y un harness de paridad contra n8n en vivo. Ver §10.

## 0. Decisiones de diseño

| Decisión | Elección | Por qué |
|---|---|---|
| Acceso a BD | **asyncpg + codec pgvector** (no supabase-py) | La BD viva ha derivado de las migraciones (007 dice 1536-dim; la real es 3072) — SQL directo evita firmas RPC ambiguas; el hybrid search nuevo es solo un string SQL en este repo (sin desplegar migraciones); inserts masivos de vectores 3072 mucho más rápidos; `logfire.instrument_asyncpg()` da spans por query. Conectar por session pooler (5432); si toca transaction pooler (6543), `statement_cache_size=0`. |
| Superficie drop-in | Endpoints con la forma de los webhooks n8n + **canary/passthrough hacia n8n dentro del servicio** | Cutover del frontend = 1 env var por feature; canary y rollback viven en la config del servicio, sin código nuevo en el frontend. |
| Hybrid search | **RRF ponderado (k=60) sobre dense + FTS + ILIKE opcional** en una sola query SQL; misma firma de tool (pesos) que hoy; fuzzy (pg_trgm) diferido | Paridad de comportamiento del agente sin retocar el prompt; RRF es robusto ante escalas incomparables (cosine vs ts_rank). El propio doc de n8n decía que fuzzy por defecto era 0. |
| Context expansion | Reimplementación **nativa en SQL** (fetch de chunks por rangos); fallback conmutable por env a la Edge Function actual para tests de paridad | Elimina la dependencia del otro proyecto Supabase; en esencia es "trae chunks por doc_id + rangos de índice". |
| Salida estructurada | `output_type` de pydantic-ai con un modelo espejo del Structured Output Parser; **las validations salen de las tool calls registradas, no del auto-reporte del modelo** | Mata el esquema USED_DEFINITIONS/VALIDATIONS + regex, pero emite el JSON legacy exacto en el borde HTTP. |
| Modelos | gpt-5.1 (agente, temp 0.1), gpt-4o-mini (validador), text-embedding-3-large (3072 chunks / 1536 definiciones) | Paridad primero. `agents.generation_model` lo hace intercambiable por agente (pydantic-ai es agnóstico de modelo). |
| Token tracking | El servicio cachea el usage por `execution_id` (= trace id de Logfire) y expone `POST /webhooks/token-tracker` con la forma legacy | Riesgo cero en fase 1; en fase 2 la URL hardcodeada del frontend pasa a env var (cambio de 1 línea). |

## 1. Layout del repo

```
├── pyproject.toml                  # uv; proyecto "semantica-ai"; python >=3.12
├── .env.example
├── .github/workflows/ci.yml       # ruff + pyright + tests + smoke evals
├── Dockerfile
├── src/semantica_ai/
│   ├── main.py                    # FastAPI app factory; wiring Logfire; lifespan (pool BD)
│   ├── config.py                  # pydantic-settings
│   ├── observability.py           # logfire.configure + instrument_* + cache de usage
│   ├── api/
│   │   ├── health.py              # GET /healthz
│   │   ├── rag.py                 # POST /webhooks/rag            (sustituye N8N_RAG_WEBHOOK_URL)
│   │   ├── token_tracker.py       # POST /webhooks/token-tracker  (sustituye webhook hardcodeado)
│   │   ├── ingestion.py           # POST /webhooks/ingestion      (sustituye N8N_WEBHOOK_URL)   [fase 4]
│   │   └── firecrawl.py           # POST /webhooks/firecrawl/start                              [fase 5]
│   ├── contracts/                 # modelos Pydantic byte-compatibles con los webhooks n8n
│   ├── agent/
│   │   ├── rag_agent.py           # build_rag_agent(); run_chat()
│   │   ├── output.py              # AgentOutput (salida estructurada)
│   │   ├── deps.py                # RagDeps (pool, contexto request, validaciones registradas)
│   │   ├── tools.py               # las 4 tools
│   │   ├── memory.py              # historial desde messages → ModelMessage[]
│   │   └── prompts/rag_system.md  # system prompt portado (sin sección de marcadores)
│   ├── core/
│   │   ├── validation.py          # validate_metric_core() — compartido tool + evaluator
│   │   ├── definitions.py         # render_domain_knowledge() — port del nodo Code de n8n
│   │   ├── embeddings.py          # embed 3072 (queries/chunks) y 1536 (definiciones)
│   │   └── pricing.py             # coste vía genai-prices (fallback tabla estática)
│   ├── db/
│   │   ├── pool.py                # pool asyncpg + codec pgvector
│   │   ├── repository.py          # accessors agents/messages/definitions/documents
│   │   ├── search.py              # HYBRID_SEARCH_SQL + run_hybrid_search()
│   │   └── expansion.py           # fetch_chunk_ranges()
│   ├── ingestion/                 # [fase 4] pipeline: download → parse → chunk → embed → write → callback
│   └── evals/                     # [fase 3] datasets YAML + task + evaluators + run.py
├── tests/
│   ├── unit/  ├── integration/    # contra Supabase dev
│   └── parity/compare_n8n.py      # mismos payloads a n8n y al servicio → diff
└── scripts/
    ├── inspect_live_schema.py     # fase 0: verificar dims reales, keys de metadata, columnas
    └── seed_dev_data.py
```

Dependencias: `fastapi`, `uvicorn`, `pydantic-ai-slim[openai]`, `pydantic-evals`, `logfire[fastapi,httpx,asyncpg]`, `asyncpg`, `pgvector`, `openai`, `httpx`, `pydantic-settings`, `genai-prices`; grupo opcional `ingestion`: `docling`; dev: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `pyright`.

## 2. Contratos (modelos Pydantic)

`contracts/rag.py` espeja exactamente lo que envían las 4 rutas y lo que parsea `chat/route.ts:264-354`: `ChatWebhookRequest {message, session_id, agent_id, datastore_ids, workspace_id, definitions[]}` y `ChatWebhookResponse {output[{text, citations[]}], applied_definitions[], validations[], knowledge_gaps[], processing_time_ms, execution_id, *_count}`. Las citas van en camelCase (`documentId`, `pageNumber`…) como en el wire actual.

`agent/output.py` — salida estructurada del agente (sustituye parser + marcadores):

```python
class AgentOutput(BaseModel):
    text: str                          # con marcadores [n] inline
    citations: list[Citation] = []
    used_definitions: list[str] = []   # nombres exactos aplicados
    knowledge_gaps: list[str] = []
```

Claves:
- `validations` NO está en `AgentOutput`: la lista autoritativa se acumula en `RagDeps` cada vez que corre la tool `validate_metric` (elimina la deriva del auto-reporte).
- `applied_definitions` se calcula post-run casando `used_definitions` contra `request.definitions` (port del nodo Parse). El fallback por substring del frontend sigue funcionando intacto.
- La limpieza regex de marcadores que hace el frontend queda como no-op inofensivo.

## 3. Diseño del agente

### 3.1 Construcción y orquestación

`Agent(model=OpenAIChatModel(model_name), deps_type=RagDeps, output_type=AgentOutput, instructions=render_system_prompt, retries=2)`. `run_chat()`: cargar config del agente (tabla `agents`) → cargar historial → `agent.run(message, deps, message_history)` → ensamblar `ChatWebhookResponse` + cachear `result.usage()` bajo `execution_id`.

### 3.2 Las 4 tools (firmas espejo de n8n → el prompt portado no necesita cambios)

```python
async def dynamic_hybrid_search(ctx, query, dense_weight=0.5, sparse_weight=0.5,
                                ilike_weight=0.0, fuzzy_weight=0.0, fuzzy_threshold=0.8) -> list[ChunkHit]
async def fetch_document_hierarchy(ctx, document_id: str) -> dict
async def context_expansion(ctx, requests: list[ExpansionRequest]) -> list[ChunkHit]
async def validate_metric(ctx, definition_name, reported_value, source_context) -> ValidationResult
```

Cada `ChunkHit` pasa el `metadata` JSON crudo de la BD → las instrucciones de citación del prompt (`metadata.doc_id`, `metadata.pages[0]`, `child_ranges`/`parent_ranges`) siguen funcionando sin tocar.

### 3.3 Hybrid search (reconstrucción)

Una sola query SQL vía asyncpg (sin migración en Supabase): CTEs `dense` (cosine sobre `embedding vector(3072)`), `sparse` (`fts @@ websearch_to_tsquery`), `ilike_hits` opcional → **RRF ponderado** `Σ weight_i / (60 + rank_i)` → top `retrieval_top_k`. Profundidad por canal 50. `fuzzy_weight` se acepta pero se trata como 0 (warning en Logfire); pg_trgm solo si la inspección de fase 0 muestra la extensión activa Y las evals detectan huecos de recall. Dense-only es exactamente el brute-force actual → sin regresión de latencia.

### 3.4 Port del system prompt

Fuente: `systemMessage` del nodo "Agentic RAG" en `rag-agent-v2.3-with-validation.json`. Se mantiene verbatim (reglas de domain knowledge, SOP, citación `[n]`); se **elimina** la sección "Response Tracking" (marcadores) y se añaden 2 frases sobre rellenar `used_definitions`/`knowledge_gaps` en la salida estructurada. El bloque de domain knowledge se genera con `render_domain_knowledge()` — port línea a línea del nodo Code de n8n (mismos headings/bold para preservar comportamiento).

### 3.5 Memoria

Sustituye a la Postgres Chat Memory de n8n: leer los últimos 20 mensajes de `messages` por `conversation_id` (descartando el mensaje de usuario entrante, que el frontend inserta ANTES de llamar al webhook) → mapear a `ModelRequest`/`ModelResponse`. Ventaja: el historial queda limpio de marcadores.

## 4. validate_metric: tool de runtime Y evaluador (código compartido)

`core/validation.py::validate_metric_core()` — port directo del prompt y comportamiento de `N8N_VALIDATION_TOOL.md` (gpt-4o-mini, temp 0.1, máx 500 tokens, fail-open en sus dos caminos), implementado como mini-agente pydantic-ai (traceado, contado en usage, modelo intercambiable).

- **Runtime**: la tool lo envuelve y registra el resultado en `deps.recorded_validations`.
- **Evals**: `MetricValidationEvaluator` lo reutiliza para puntuar `approved_ratio` y verificar que el agente llamó a la tool cuando tocaba. Mismo código ⇒ producción y evaluación no pueden divergir.

## 5. Integración Logfire

```python
logfire.configure(service_name="semantica-ai", environment=..., token=LOGFIRE_TOKEN)
logfire.instrument_pydantic_ai(); logfire.instrument_fastapi(app)
logfire.instrument_httpx(); logfire.instrument_asyncpg(); logfire.instrument_openai()
```

- Span raíz por chat (`rag.chat`, con agent_id/workspace/conversation) + span explícito `retrieval.hybrid` (pesos, hits). El resto viene gratis de las instrumentaciones.
- `execution_id` devuelto al frontend = **trace id hex del span raíz** → saltar de un mensaje a su traza en Logfire.
- Usage/coste: `result.usage()` (+ el del validador) → coste con genai-prices.
- Write-back: `POST /webhooks/token-tracker` acepta el `{execution_id, message_id}` legacy y hace `UPDATE messages SET input_tokens, output_tokens, cost` desde una cache TTL de 15 min; opcionalmente persiste también `validations` (la columna existe, migración 033). `processing_time_ms` va en la respuesta síncrona como hoy. Los dashboards de uso no se tocan.

## 6. Harness de evals (nuevo de cero)

- **Datasets** (`pydantic_evals.Dataset`, YAML): inputs `{message, agent_id, datastore_ids, workspace_id, definitions, history?}`; metadata `{expected_definitions, expected_metrics, must_cite_documents, tags}`.
- **Casos dorados iniciales**: (1) los 4 casos de `scripts/test-validation-tool.js`; (2) los 3 escenarios de `N8N_VALIDATION_TOOL.md`; (3) 10-15 Q&A a mano sobre `clayton-state-business-ops-manual.pdf` + `test-docs/` ingestados en un workspace dev dedicado; (4) queries reales de producción curadas y congeladas.
- **Evaluadores**: `CitationIntegrity` (determinista: cada `[n]` tiene cita, cada `documentId` ∈ chunks recuperados, ids secuenciales); `CitationFaithfulness` (LLMJudge: cada claim soportado por su excerpt); `DefinitionApplication`; `MetricValidationEvaluator` (§4); `LatencyCost` (umbrales p95).
- **Ejecución**: `uv run python -m semantica_ai.evals.run --dataset golden_rag` → experimentos comparables en la UI de Evals de Logfire. Subset smoke (5 casos, asserts duros) en CI por PR; run completo nightly.

## 7. Ingesta (fase 4)

Parser **docling** (PDF/DOCX/PPTX/HTML, buena extracción de tablas y proveniencia de página). Chunking jerárquico dos niveles (padres → hijos ~800-1200 tokens) emitiendo metadata **compatible con lo que consumen retrieval y prompt** (`doc_id`, `doc_name`, `pages`, `chunk_index`, `child_ranges`, `parent_ranges` — set exacto confirmado contra filas reales en fase 0; los chunks existentes no se tocan). Embedding 3072 en lotes de 64. Insert con `executemany`. Flujo endpoint: `202` inmediato → tarea de fondo → callback a `callback_url` con `{document_id, status, chunks_count, document_headline, document_summary}` (+ Bearer secret si está configurado). Spans Logfire por etapa.

## 8. Rollout por fases con rollback (n8n sigue caliente)

Mecanismo canary transversal: `AI_CANARY_WORKSPACES` (ids separados por coma; `*` = todos) + `N8N_FALLBACK_*_URL`; los workspaces no-canary se proxyean transparentemente a n8n; `FALLBACK_ON_ERROR=true` reintenta contra n8n si el run local falla.

| Fase | Entregable | Cambio en frontend | Rollback |
|---|---|---|---|
| **0** (0,5-1 d) | Scaffold, config, pool BD, Logfire, `/healthz`, `inspect_live_schema.py` (gate duro: dims 3072, keys de metadata, columnas out-of-band, pg_trgm), CI | ninguno | n/a |
| **1** (3-5 d) | Agente de chat + 4 tools + hybrid search + memoria + `/webhooks/rag` (con canary) + stub token-tracker; harness de paridad en verde | `N8N_RAG_WEBHOOK_URL=https://<servicio>/webhooks/rag` (cubre las 4 rutas) | env var de vuelta a n8n, o `AI_CANARY_WORKSPACES=""` (instantáneo) |
| **2** (1-2 d) | Write-back de usage; persistir `validations` (verificar migración 033 aplicada); dashboards + alertas Logfire | PR de 1 línea: URL hardcodeada del token-tracker → env var | env var de vuelta; el workflow n8n sigue existiendo |
| **3** (2-3 d) | Harness de evals, datasets dorados, gate smoke en CI, run nightly | ninguno | n/a (offline) |
| **4** (4-6 d) | Pipeline de ingesta + `/webhooks/ingestion` (canary por workspace) | `N8N_WEBHOOK_URL=https://<servicio>/webhooks/ingestion` | env var de vuelta; docs fallidos re-subibles desde la UI |
| **5** (2-4 d, diferible) | Firecrawl + hardening Slack (dedupe) + limpieza de contrato (streaming, `/v2/chat`) | `N8N_FIRECRAWL_START_WEBHOOK_URL=...` | env var de vuelta |
| **Decomisión** | Tras 2-4 semanas con todo el tráfico y dashboards limpios: pausar workflows n8n (30 días), archivar sus JSON en el repo, borrar passthrough | quitar env vars de fallback | JSONs archivados |

Diferido explícitamente: streaming (requiere frontend, no es drop-in), minería del feedback 👍/👎 hacia el dataset de evals.

## 9. Verificación por fase

- **F0**: script de inspección contra Supabase **dev**; `/healthz` verde; CI corre ruff+pyright+pytest.
- **F1**: unit (round-trip de contratos con fixtures JSON capturados de respuestas reales de n8n; mapping de memoria; render de domain knowledge vs salida del nodo Code); integración (overlap ≥70%@10 del hybrid search vs RPC dense para 10 queries; expansión nativa vs Edge Function); **harness de paridad** (15 prompts → n8n y servicio, diff de forma + juez LLM de equivalencia semántica); cutover en staging con click-test de la UI (citas, shared, Slack); canary de producción 48h en 1-2 workspaces internos comparando error rate y p95.
- **F2**: chat en workspace canary → columnas `input_tokens/output_tokens/cost/validations` pobladas; `/api/usage` las muestra; coste del span == coste en BD.
- **F3**: dos runs del dataset → experimentos comparables en Logfire; el gate de CI falla con un prompt roto a propósito (sanity del gate).
- **F4**: subir cada fixture de `test-docs/` + el PDF de Clayton State a un datastore dev → `processed`, `chunks_count > 0`, headline/summary; preguntar al agente cosas que solo responde ese doc (prueba end-to-end); re-run de evals sin regresión; test del camino de fallo (PDF corrupto → `failed`).
- **Siempre**: dashboards de n8n vigilados en paralelo hasta decomisar; ensayo de rollback (flip de env y confirmar chat) durante staging de F1.

## 10. Riesgos y mitigaciones

1. **BD viva ≠ migraciones** (dims, columnas out-of-band) → gate duro en fase 0; todo el SQL vive en este repo.
2. **Metadata de chunks desconocida** (rompería citas y expansión) → inspección de filas reales en fase 0; expansión validada contra la Edge Function aún viva (`CONTEXT_EXPANSION_MODE=edge`).
3. **Deriva de comportamiento del hybrid search** (sub-workflow perdido; RRF ≠ su fusión) → tests de overlap, harness de paridad, suite de evals antes de ampliar canary; pesos/k tunables por env.
4. **Peculiaridades de gpt-5.1** (p. ej. rechazo de `temperature`; interacción structured output + tools) → `ModelSettings` defensivo por familia de modelo; smoke de evals lo cubre.
5. **Cache miss del token-tracker** (multi-instancia o restart) → single-instance al inicio; TTL + métrica de warning; opción futura de que el servicio escriba el usage directamente.
6. **Latencia del scan brute-force 3072** → igual que hoy (sin regresión); alerta p95 en Logfire; opción futura `halfvec`/HNSW medida por las evals.
7. **Paridad de memoria en rollback** (n8n no verá los turnos servidos por el servicio) → aceptable: ventana de 20 y `messages` es el registro durable; el passthrough mantiene caliente la memoria de n8n para tráfico no-canary.
8. **asyncpg + PgBouncer** (prepared statements en transaction pooler) → session pooler; si no, `statement_cache_size=0`.
9. **Coste/flakiness de jueces LLM** → evaluadores deterministas como primera línea; LLMJudge solo en nightly; smoke de 5 casos en PRs.
10. **Timing de Slack y entregas duplicadas** → mismo contrato síncrono que n8n en F1 (sin cambio de comportamiento); dedupe por `event_id` en F5.

## Requisitos previos para arrancar

- `OPENAI_API_KEY` (agente + embeddings).
- `LOGFIRE_TOKEN` (write token del proyecto Logfire — gratis en logfire.pydantic.dev).
- Connection string de un **Supabase dev** (y más adelante el de producción, session pooler).
- Opcional para esta sesión de Claude Code: **logfire-mcp** (servidor MCP oficial de Logfire) con un read token, para consultar trazas y evals directamente desde aquí.

## Archivos críticos de referencia (en el repo semantica)

- `n8n-workflows/rag-agent-v2.3-with-validation.json` — system prompt, tools, esquema de salida, ventana de memoria.
- `app/api/agents/[id]/chat/route.ts` — contrato exacto request/response (token-tracker en L384, write de validations comentado en L366).
- `N8N_VALIDATION_TOOL.md` — spec completa de validate_metric.
- `supabase/migrations/006_n8n_database_consolidation.sql` — esquema de `document_chunks`.
- `app/api/datastores/[id]/documents/route.ts` — payload de ingesta + semántica de callback/retry.
