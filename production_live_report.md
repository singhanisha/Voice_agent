# Production Live Feature Report

Date: 2026-05-30

## Release Mode

The backend now supports a single production live profile. Set this on Railway to enable completed platform features without managing dozens of separate flags:

```env
PLATFORM_FEATURE_PROFILE=live
```

Rollback is one environment change:

```env
PLATFORM_FEATURE_PROFILE=shadow
```

`FEATURE_AUTH_ENFORCE_BACKEND` is intentionally excluded from the live profile because current frontend pages do not yet send server-verifiable identity on every request. Turning that on early would lock users out. It remains individually controllable.

## Functionality Map

| Area | Backend | Frontend | Use |
| --- | --- | --- | --- |
| Voice demo/runtime | `/api/voice-demo`, `/api/voice-live`, `flows/runtime.py`, STT/TTS providers | `useVoiceSocket.js`, Talk Live/Demo UI | Browser mic streams PCM to backend, STT creates transcripts, StateManager follows the live agent schema, TTS streams audio back. |
| Flow editor live publish | `/api/agents/{id}/flow-v2-draft`, Flow V2 validation, runtime schema publisher | Agents page Flow modal | Admin edits/adds flow nodes; when saved, validated Flow V2 is published into the existing live v1 runtime JSON used by calls. |
| Website intelligence | `/api/intelligence/*`, crawler, extraction pipeline, generated draft APIs | Agents Generate Script modal, Intelligence page | Admin enters a website URL, backend scrapes/extracts knowledge, creates script/flow draft, review gate checks quality, approved draft can publish live. |
| Campaign launch | `/api/leads/upload`, `/api/campaigns/start`, demo runner, telephony runner | Client dashboard Launch Campaign modal and CSV upload | Client uploads/manual-adds leads, starts campaign, backend stores leads/campaign and dispatches demo or provider calls. |
| Campaign lifecycle | `/api/campaigns/{id}/archive`, `restore`, `DELETE`, lifecycle summary | Campaigns page archive/delete controls | Admin can archive, restore, and soft-delete campaigns without losing results, transcripts, or recordings. |
| Worker V2 control plane | `campaigns/worker_v2.py`, campaign execution DB tables | Campaign start response and readiness panels | Live launch creates durable execution metadata for pause/resume/cancel/retry tracking while preserving the stable v1 call runner. |
| Live dashboard events | `/ws/dashboard`, `ws_hub.py` | Client dashboard live feed, Monitor page | Campaign/demo call events are sent to tenant-scoped dashboard channels; global monitor is admin-oriented. |
| Results/transcripts | `/api/campaigns/{id}/results`, `/api/results/{leadId}/transcript` | Results page, Campaign dashboard | Stores and retrieves call results, transcript turns, lead details, and recording references with optional client scope. |
| Recording access | `/recordings/*`, `/api/recordings/protected` | Results playback | Existing static playback remains; protected route supports tenant-scoped recording retrieval. |
| Telephony numbers | `/api/telephony/numbers/*`, provider registry, routes | Numbers page | Admin searches/buys provider numbers, assigns them to a client, routes them to an agent, and keeps number ownership tenant-scoped. |
| CRM integration | `/api/crm/*` | CRM Readiness page | Stores CRM connections, preflight, outbox, approval, retry, and sandbox/canary dispatch metadata. |
| Agent memory | `/api/memory/agents/*` | Backend API-ready | Tenant-scoped memory collections/items for future isolated RAG usage; reset and event audit paths are in place. |
| Readiness/canary reports | `/api/demo/qa/readiness`, `/api/tenant/*readiness`, `/api/provider-metrics` | Monitor, Intelligence, Campaigns, Numbers, CRM readiness pages | Admin-facing safety checks for demo runtime, tenant isolation, telephony, campaign E2E, website intelligence, CRM, rollback, and provider latency. |

## Production Flags

Backend production should use `PLATFORM_FEATURE_PROFILE=live`. Local/test/rollback defaults remain shadow-safe. Individual `FEATURE_*` variables can still disable any feature for rollback.

Frontend Vercel must expose matching `NEXT_PUBLIC_*` switches. See `frontend-next/.env.example`.

## Rollback

1. Railway: set `PLATFORM_FEATURE_PROFILE=shadow`.
2. Vercel: turn off the matching `NEXT_PUBLIC_*` feature switch if a UI needs to be hidden.
3. Flow runtime rollback: each Flow V2 live publish writes a runtime backup path in the publish response and agent metadata.
4. Campaign rollback: worker-v2 live metadata does not replace the v1 call runner; disabling `FEATURE_CAMPAIGN_WORKER_V2=false` removes the metadata/control-plane layer.

## Notes

Audio contracts remain unchanged: PCM16 mono, existing websocket endpoints, current STT/TTS provider interfaces, current demo and campaign runners.
