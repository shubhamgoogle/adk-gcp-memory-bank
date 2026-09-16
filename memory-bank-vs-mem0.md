# Vertex AI Memory Bank vs Mem0 Cloud

A comparison of the two managed agent-memory services, focused on the things
that actually constrain a production deployment: quota shape, scaling limits,
isolation model, and lock-in.

> [!NOTE]
> **Sourcing and freshness.** Memory Bank figures were read live from the Cloud
> Quotas API (`cloudquotas.googleapis.com`) against a real GCP project on
> 2026-09-16, and are **per-project, per-region defaults that are adjustable** —
> verify your own. Mem0 figures come from their public pricing page and FAQ on
> the same date, which are marketing sources and less authoritative. Both
> vendors change limits; re-check before committing to an architecture.

## TL;DR

- The two vendors **don't express limits in the same unit**: Google publishes
  per-minute throughput, Mem0 publishes monthly allowances.
- On sustained volume, Memory Bank's *defaults* are far larger; on burst
  flexibility, Mem0's monthly budget is more forgiving.
- Their read/write ratios are **inverted**, which decides which one suits your
  agent's retrieval pattern.
- Both agree on the key architectural point: **scope per user, never an
  instance per user.**

## Quota model: the structural difference

| | Vertex AI Memory Bank | Mem0 Cloud |
| --- | --- | --- |
| Limit shape | **Per-minute rate**, no monthly cap | **Monthly request allowance**, no published per-minute rate |
| Scoped to | Project + region | Account / plan |
| Adjustable | Yes — quota increase request | Yes — upgrade plan, or usage-based deal |
| Burst behavior | Hard-throttled at the per-minute ceiling | Not publicly documented |

A search of Mem0's API reference and FAQ pages for rate limits / `429` /
per-minute returned **no results**. Their burst ceiling is therefore not
something you can design against from public docs — ask their team before
assuming headroom.

## Concrete numbers

### Mem0 Cloud plans

| Plan | Price | Add requests | Retrieval requests | Projects |
| --- | --- | --- | --- | --- |
| Hobby | $0 | 10,000 / mo | 1,000 / mo | 1 |
| Starter | $19 / mo | 50,000 / mo | 5,000 / mo | — |
| Pro | $249 / mo | 500,000 / mo | 50,000 / mo | Unlimited |
| Enterprise | Custom | Unlimited | Unlimited | Unlimited |

End users are unlimited on every plan.

### Memory Bank defaults

| Quota | Default | Interval | Container |
| --- | --- | --- | --- |
| `memory_bank_write_requests` | 100 | per minute | project + region |
| `memory_bank_read_requests` | 300 | per minute | project + region |
| `reasoning_engine_service_entities` | 100 | none (hard cap) | project + region |
| `reasoning_engine_service_write_requests` | 10 | per minute | project + region |

Read your own:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "x-goog-user-project: $GOOGLE_CLOUD_PROJECT" \
  "https://cloudquotas.googleapis.com/v1/projects/$GOOGLE_CLOUD_PROJECT/locations/global/services/aiplatform.googleapis.com/quotaInfos?pageSize=500"
```

### Normalized to a common unit

Converting Memory Bank's per-minute rates to sustained monthly volume:

| Operation | Memory Bank (per min) | Memory Bank (≈ per month) | Mem0 Pro (per month) |
| --- | --- | --- | --- |
| Writes / adds | 100 | **~4.3 M** | 500,000 |
| Reads / retrievals | 300 | **~13 M** | 50,000 |

On sustained throughput, Memory Bank's out-of-the-box defaults are roughly
**8× Mem0 Pro on writes** and **~260× on retrievals**, with no subscription
tier. The tradeoff: Memory Bank throttles at 100 writes/min even if your
monthly total is tiny, while Mem0 lets you spend a monthly budget in whatever
shape you like.

## The inverted read/write ratio

> [!IMPORTANT]
> Mem0 gives you **10× more adds than retrievals**. Memory Bank gives you
> **3× more reads than writes**. This is the single most important difference
> when picking between them.

- **Mem0's shape suits write-heavy ingestion** — bulk-loading facts, then
  querying them sparingly.
- **Memory Bank's shape suits read-heavy personalization** — consulting memory
  on most turns.

### Worked example: a preload-every-turn agent

An agent using `PreloadMemoryTool` plus a write-back callback costs
**1 read + 1 write per turn**. Sustained saturation points:

| Platform | Binding limit | Saturation |
| --- | --- | --- |
| Memory Bank (defaults) | 100 writes/min | **~1.7 turns/sec** |
| Mem0 Pro | 50,000 retrievals/mo | **~1.1 turns/min** |

On Mem0 Pro, retrieval is exhausted at a very modest conversation rate, so
conditional retrieval (`LoadMemoryTool`-style, where the model decides when to
look up) matters even more there than on Memory Bank.

## Isolation and scoping

| | Memory Bank | Mem0 |
| --- | --- | --- |
| Primitive | Arbitrary `scope` dictionary | Named entities: `user_id`, `agent_id`, `app_id`, `run_id` |
| ADK binding | Hardcoded to `{app_name, user_id}` | n/a |
| Query expressiveness | Exact scope match | Composable filters with `AND` / `OR` |
| End users | Unlimited | Unlimited (all plans) |
| Hard resource cap | **100 instances** per project per region | Projects: 1 on Hobby, unlimited on Pro |

Mem0 filter example:

```json
{"AND": [{"user_id": "alice"}, {"agent_id": "bot"}]}
```

Mem0's read-side filter language is more expressive. Memory Bank's scope is a
flat exact-match dictionary, and ADK further constrains it to two keys — so
multi-dimensional slicing means encoding extra dimensions into `app_name`
(e.g. `{"app_name": "acme-corp", "user_id": "u123"}` for B2B tenancy).

Both platforms converge on the same rule: **isolate with scope/entities, not
with one instance per customer.** On Memory Bank that's enforced by the
100-instance cap; on Mem0 it's simply how the product is designed.

## Other axes

| | Memory Bank | Mem0 Cloud |
| --- | --- | --- |
| Pricing model | Consumption, no subscription | Flat subscription tiers + usage-based option |
| Data residency | You choose the GCP region | Managed; on-prem on Enterprise |
| Lock-in | GCP / ADK-native | SaaS, but **Apache-2.0 OSS self-host escape hatch** |
| Compliance | Inherits GCP posture (IAM, VPC-SC, CMEK) | SSO + audit logs on Enterprise |
| Extraction | Server-side, async, consolidates revisions | Server-side, auto-categorizes |

Mem0's open-source engine under Apache 2.0 is a real differentiator: if the
SaaS economics stop working you can self-host the same engine. Memory Bank has
no equivalent.

## Choosing

**Memory Bank fits when:**
- You're already on GCP and want IAM / VPC-SC / CMEK to apply automatically
- Your agent is read-heavy (consults memory most turns)
- You want consumption pricing with no subscription floor
- You need explicit regional data residency

**Mem0 fits when:**
- You're multi-cloud or not on GCP at all
- Your workload is write-heavy with sparse retrieval
- You want predictable flat monthly cost
- You want multi-dimensional memory filtering out of the box
- You want the option to self-host later

## Caveats

- **Cost is not compared here.** The pricing-model row describes *structure*,
  not total cost. A real TCO comparison needs Vertex's LLM-extraction and
  storage rates, which were not verified.
- **Mem0's numbers are from marketing pages**, not an API, and no per-minute
  rate limits are published at all.
- **Memory Bank quotas are defaults** for one project in one region, and are
  adjustable on request.
