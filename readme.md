# adk-gcp-memory-bank

An ADK agent with **long-term memory** backed by
[Vertex AI Agent Platform Memory Bank](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/adk-quickstart).

The agent remembers facts about each user across *separate sessions* — state a
preference in one conversation and it is recalled in the next.

## How it works

| Direction | Mechanism | Location |
| --- | --- | --- |
| **Write** | `after_agent_callback` ships each turn's events to Memory Bank, which extracts durable facts asynchronously | [`generate_memories_callback`](my_agent/agent.py) |
| **Read** | `PreloadMemoryTool` retrieves relevant memories at the start of every turn and injects them into the system instruction | [`agent.py`](my_agent/agent.py) |

The memory *service* is intentionally **not** constructed in `agent.py`. It is
injected by the runtime via `--memory_service_uri`, so the same agent file works
locally and when deployed.

## Prerequisites

```bash
uv sync
gcloud auth application-default login
```

## Configuration

This project keeps all environment-specific identifiers out of source control.
Set them in your shell:

| Variable | Meaning |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | Your GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | A [supported Memory Bank region](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/overview) |
| `MEMORY_BANK_ID` | Numeric ID of your Memory Bank instance |

`my_agent/.env` holds the project and location for the *agent* and is
gitignored. Keep it that way.

### Creating a Memory Bank instance

```python
import vertexai

client = vertexai.Client(project="YOUR_PROJECT_ID", location="YOUR_LOCATION")
memory_bank = client.agent_engines.create()
print(memory_bank.api_resource.name.split("/")[-1])  # -> MEMORY_BANK_ID
```

## Running locally

> [!IMPORTANT]
> `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` must be **exported in the
> shell**. The memory service is constructed at server startup, *before*
> `my_agent/.env` is loaded, so values in that file are invisible to it.
> Without the exports the server exits with
> `Error: GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION not set.`

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="your-region"
export MEMORY_BANK_ID="your-memory-bank-id"

uv run adk web --memory_service_uri="agentengine://${MEMORY_BANK_ID}"
```

Then open the printed URL and select the **`my_agent`** app.

## Trying it out

Memory extraction is asynchronous and takes a few seconds, so memories written
in one turn generally become retrievable on a later turn.

1. Turn 1 — *"Hi! My name is Alice. I am strictly vegetarian and I always prefer window seats when I fly."*
2. Wait a few seconds, then start a **new session** (same user).
3. Turn 2 — *"I am booking a flight. What meal and seat should I pick?"*

The agent answers using the remembered preferences, despite the new session
having no conversation history.

## Inspecting stored memories

```bash
uv run python -c "
import asyncio, os
from google.adk.memory import VertexAiMemoryBankService
svc = VertexAiMemoryBankService(
    project=os.environ['GOOGLE_CLOUD_PROJECT'],
    location=os.environ['GOOGLE_CLOUD_LOCATION'],
    agent_engine_id=os.environ['MEMORY_BANK_ID'])
r = asyncio.run(svc.search_memory(
    app_name='my_agent', user_id='alice', query='preferences'))
for m in r.memories:
    print('-', m.content.parts[0].text)
"
```

---

# Memory Bank fundamentals

Notes gathered while building and load-reasoning about this agent. The quota
numbers below were read live from the Cloud Quotas API for a real project; treat
them as **defaults to verify for your own project**, since quotas are
per-project, per-region and adjustable.

## Scope is the isolation primitive

Memory Bank partitions memories by an arbitrary **scope dictionary**, not by
instance. ADK sets the scope on every read and write to:

```python
scope = {"app_name": <app>, "user_id": <user>}
```

This is what keeps users' memories from leaking into each other. It costs
nothing and has no practical cardinality limit.

For **B2B multi-tenancy**, encode the tenant in `app_name` to get tenant + user
isolation from a single instance:

```python
scope = {"app_name": "acme-corp", "user_id": "u123"}
```

> [!IMPORTANT]
> Do **not** create one Memory Bank instance per end user. A Memory Bank
> instance *is* a `reasoningEngine` resource and is hard-capped (see below).
> Per-user instances are both architecturally wrong and physically impossible
> at scale.

### Where the scope values come from

Nothing in `my_agent/agent.py` sets the scope. ADK derives it from the session
and injects it on both the read and write paths:

| Scope key | Source | Value in this project |
| --- | --- | --- |
| `app_name` | The agent folder name | `my_agent` |
| `user_id` | The caller-supplied user on the session | e.g. `alice` |

In `adk web`, `user_id` comes from the UI's user field. Over REST it is the URL
segment — a call to `/apps/my_agent/users/alice/sessions/s1` is literally what
produces `{"app_name": "my_agent", "user_id": "alice"}`.

The injection happens inside ADK, not your code:

- **Write:** `callback_context.add_events_to_memory(events=...)` →
  `add_events_to_memory(app_name=session.app_name, user_id=session.user_id, ...)`
- **Read:** `PreloadMemoryTool` → `tool_context.search_memory(query)` → same
  two values

Because both paths derive the scope identically, reads and writes always agree.

> [!CAUTION]
> **`user_id` is untrusted caller input — nothing validates it.** Whoever calls
> your API chooses the memory partition. In production you must derive
> `user_id` from an authenticated identity (a verified JWT subject, a signed
> session cookie), never from a client-supplied field. Passing a raw client
> value lets one user read another's memories by guessing an ID.

## Quotas that matter

| Quota | Default | Interval | Container |
| --- | --- | --- | --- |
| `reasoning_engine_service_entities` | **100** | none (hard cap) | project + region |
| `reasoning_engine_service_write_requests` | 10 | per minute | project + region |
| `memory_bank_read_requests` | 300 | per minute | project + region |
| `memory_bank_write_requests` | 100 | per minute | project + region |

Read your own with:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "x-goog-user-project: $GOOGLE_CLOUD_PROJECT" \
  "https://cloudquotas.googleapis.com/v1/projects/$GOOGLE_CLOUD_PROJECT/locations/global/services/aiplatform.googleapis.com/quotaInfos?pageSize=500"
```

### Why per-customer instances are impossible

- The 100-entity cap means at most **100 instances per project per region** —
  nowhere near "millions of customers".
- Even ignoring the cap, instance creation is throttled to 10/min, so creating
  1M instances would take roughly **70 days**.

## Scaling: request rate is the real bottleneck

Instance count is not the constraint — **requests per minute** is. The default
config in this repo is deliberately simple and therefore chatty:

| Behavior | Cost | Saturation point (at default quota) |
| --- | --- | --- |
| `PreloadMemoryTool` retrieves every turn | 1 read/turn | ~5 turns/sec (300 reads/min) |
| Callback writes every turn | 1 write/turn | ~1.7 turns/sec (100 writes/min) |

Both limits are **project + region wide**, shared across all users.

To scale:

1. **Request quota increases** for `memory_bank_read_requests` and
   `memory_bank_write_requests`. These are adjustable — design around the
   100-entity cap instead of fighting it.
2. **Cut write amplification.** Writing every turn is wasteful. Write at session
   end, every N turns, or pass `generation_trigger_config` with an
   `idle_duration` so Memory Bank batches the flush:
   ```python
   await callback_context.add_events_to_memory(
       events=...,
       custom_metadata={"generation_trigger_config":
                        {"generation_rule": {"idle_duration": "60s"}}},
   )
   ```
3. **Cut read amplification.** Swap `PreloadMemoryTool` for `LoadMemoryTool` so
   the model retrieves only when it judges memory relevant, instead of on every
   single turn.
4. **Shard across regions or projects** only after raising quotas. This doubles
   as your data-residency lever.

## When multiple instances *are* justified

At **tenant** granularity (tens), never end-user granularity:

- Hard regulatory or contractual isolation per enterprise customer
- Per-tenant data residency
- Independent retention / TTL policies

All of these stay comfortably under the 100-entity cap.

## Other gotchas

- **The agent folder must be a valid Python identifier.** `my_agent` works;
  `my-agent` loads and appears in the ADK web dropdown but rejects every message
  with `Invalid agent name`.
- **Memory writes are fire-and-forget.** The ingest request is dispatched as a
  background task, so killing the server immediately after a turn can drop the
  write before it reaches Memory Bank.
- **Extraction is asynchronous.** Expect a few seconds before a newly stated
  fact becomes retrievable.
- **The documented callback slice drops the last event.**
  `session.events[-5:-1]` excludes the in-flight model response. Extraction
  still works, but be aware the final event of each turn is never shipped.

## Deploying

```bash
adk deploy cloud_run ... --memory_service_uri="agentengine://${MEMORY_BANK_ID}"
```

On Agent Runtime (`AdkApp`), `VertexAiMemoryBankService` is the default memory
service, so no URI is needed.
