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

Memories are scoped by `{"app_name": ..., "user_id": ...}`, so users never see
each other's memories.

The memory *service* is intentionally **not** constructed in `agent.py`. It is
injected by the runtime via `--memory_service_uri`, so the same agent file works
locally and when deployed.

## Prerequisites

```bash
uv sync
gcloud auth application-default login
```

## Running locally

> [!IMPORTANT]
> `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` must be **exported in the
> shell**. The memory service is constructed at server startup, *before*
> `my_agent/.env` is loaded, so the values in that file are not visible to it.
> Without the exports the server dies with
> `Error: GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION not set.`

```bash
export GOOGLE_CLOUD_PROJECT="deepspace-460917"
export GOOGLE_CLOUD_LOCATION="us-central1"

uv run adk web --memory_service_uri=agentengine://2886171293667819520
```

Then open the printed URL and select the **`my_agent`** app.

### Memory Bank instance

This project uses an existing Memory Bank instance:

```
projects/149217036778/locations/us-central1/reasoningEngines/2886171293667819520
```

To create your own instead:

```python
import vertexai

client = vertexai.Client(project="PROJECT_ID", location="LOCATION")
memory_bank = client.agent_engines.create()
print(memory_bank.api_resource.name.split("/")[-1])  # the ID for the URI above
```

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
import asyncio
from google.adk.memory import VertexAiMemoryBankService
svc = VertexAiMemoryBankService(
    project='deepspace-460917', location='us-central1',
    agent_engine_id='2886171293667819520')
r = asyncio.run(svc.search_memory(
    app_name='my_agent', user_id='alice', query='preferences'))
for m in r.memories:
    print('-', m.content.parts[0].text)
"
```

## Notes and gotchas

- **The agent folder must be a valid Python identifier.** `my_agent` works;
  `my-agent` loads and appears in the ADK web dropdown but rejects every message
  with `Invalid agent name`.
- **Memory writes are fire-and-forget.** The ingest request is dispatched as a
  background task, so killing the server immediately after a turn can drop the
  write before it reaches Memory Bank.
- **Retrieval tool choice.** `PreloadMemoryTool` fetches memories every turn
  (good baseline context). Swap in `LoadMemoryTool` to let the model decide when
  to look them up.

## Deploying

The same agent deploys with the memory service attached:

```bash
adk deploy cloud_run ... --memory_service_uri=agentengine://AGENT_ENGINE_ID
```

On Agent Runtime (`AdkApp`), `VertexAiMemoryBankService` is the default memory
service, so no URI is needed.
