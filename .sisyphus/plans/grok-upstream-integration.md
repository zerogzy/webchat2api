# Grok Upstream Integration Plan

Scope: optimize only `services/providers/grok/*` and Grok-specific tests. Do not modify other providers. The repository already has unrelated dirty changes; preserve them.

Upstream: `WangXingFan/grok2api`.

## Upstream Advantages Found

1. Account category and pool behavior
   - Uses `ssoBasic` and `ssoSuper` pools.
   - Basic default quota is 80; Super default quota is 140.
   - Model metadata declares tier and cost. Basic models use Basic first; Super/Heavy/high-cost models use higher-tier pools.
   - Tokens with no quota enter cooling; repeated auth failures become expired.

2. Refresh and quota synchronization
   - `/rest/rate-limits` syncs remaining quota and window information.
   - Super-vs-Basic pool can be corrected using `windowSizeSeconds`: large windows are Basic, short windows are Super.
   - Cooling tokens can be refreshed before declaring no account available.
   - Stream success records usage at stream completion, not before.

3. Request/response processing hardening
   - Token/header normalization removes unicode dashes, zero-width chars, NBSP, and unsafe header bytes.
   - Cookie always includes both `sso` and `sso-rw`.
   - Retry/token switching distinguishes rate limit vs transient upstream errors.
   - SSE parsing tolerates `data:` prefixes, embedded payloads, `[DONE]`, HTTP/2 stream issues, and idle timeouts.
   - Recursive image URL extraction and moderation/block handling are more defensive.

4. Features that are upstream advantages but new for this project unless explicitly approved
   - Video generation/upscale and LiveKit/websocket reverse APIs.
   - Asset list/download/delete management endpoints.
   - Accept TOS and set-birth flows.
   - Admin token UI/scheduler/background persistence model from upstream.
   - New public API surfaces beyond existing webchat2api Grok chat/image/image-edit behavior.

## Current Local Baseline

Local Grok already has:

- `accounts.py`: token import/normalization, tier aliases (`basic`, `super`, `heavy`), capability filtering, status mapping, console quota.
- `client.py`: console `/v1/responses`, app-chat `/rest/app-chat/conversations/new`, `/rest/rate-limits` validation, upload/image generation/image edit, Browser Bridge fallback, search source extraction, SSE parsing, retry wrappers.
- `models.py`: many console and app-chat model specs, including `basic`, `super`, `heavy`, image, and image-edit capabilities.
- `chat.py` and `images.py`: adapter bridge.
- `test/test_grok_provider.py`: existing Grok provider tests.

Known existing issue observed before changes: Pyright reports `services.providers.grok` re-export attributes as unknown in `chat.py` and `images.py`. Treat as pre-existing unless touched by implementation.

## Three-Round Plan

### Round 1: Account Category And Token Hygiene Parity

Goal: make existing Grok account normalization and selection match upstream account categories more closely.

Allowed old-feature improvements:

- Add upstream-style token cleanup to Grok token normalization:
  - replace unicode hyphen/minus variants with ASCII `-`;
  - remove zero-width/BOM characters;
  - normalize NBSP-like spaces;
  - strip `sso=` prefix for bare SSO tokens;
  - remove internal whitespace only for bare SSO token values, not full cookie headers.
- Preserve existing cookie-header import support.
- Normalize account tier aliases so `basic/free/fast`, `super/premium/supergrok`, and `heavy/max` route consistently.
- Initialize app-chat quota metadata for Grok accounts in a way compatible with existing fields, without adding global account APIs.
- Add tests for unicode token cleanup, `sso`/`sso-rw` cookie construction, and tier alias selection.

Must not:

- Change GPT/Gemini account behavior.
- Add upstream token manager or admin UI.
- Rebuild account service architecture.

### Round 2: Refresh, Quota, And Selection Behavior Parity

Goal: make refresh and handling behavior consistent with upstream for existing Grok chat/image paths.

Allowed old-feature improvements:

- Enhance Grok `/rest/rate-limits` request payload to use upstream-compatible defaults (`requestKind: DEFAULT`, model name when applicable) while preserving current validation behavior.
- Extract remaining quota and window info from rate-limit payloads when present.
- Map upstream quota/window info into existing Grok account fields:
  - `quota_console` for local console-like quota accounting;
  - `tier` correction based on `windowSizeSeconds` if unambiguous;
  - `status` transitions: active/normal for remaining quota, rate-limited/cooling when exhausted, abnormal for auth failures.
- Add high-effort cost concept locally for Grok app-chat model specs where current metadata already indicates `heavy`, `expert`, or reasoning/high-effort mode. Do not change public model list without permission.
- Mark app-chat usage only after successful completion/stream finalization, mirroring upstream stream usage accounting.
- Add tests for rate-limit payload extraction, tier correction, exhausted quota behavior, and stream completion marking.

Must not:

- Add a new scheduler/background auto-refresh system.
- Change public API response formats outside existing Grok behavior.
- Touch shared `account_service.py` unless there is no Grok-local route; prefer Grok-local helper functions and existing account update hooks.

### Round 3: Processing Robustness Parity

Goal: harden existing request/stream/image processing using upstream patterns.

Allowed old-feature improvements:

- Ensure app-chat SSE parser covers upstream variants already seen in upstream utils:
  - raw JSON lines;
  - `data:` lines;
  - embedded multiple `data:` blocks;
  - `[DONE]` sentinel;
  - final metadata and soft-stop final events.
- Improve app-chat error classification without leaking sensitive upstream body details.
- Ensure retry/token switching on rate-limit/transient errors avoids immediately reusing failed tokens when existing code supports excluded tokens.
- Improve image URL extraction and moderation/block detection only for existing image/image-edit endpoints.
- Add focused tests for parser variants, final metadata termination, rate-limit/transient classification, and moderation image handling.

Must not:

- Add video, LiveKit, asset management, TOS, birth, or new reverse endpoints.
- Add new public capabilities or model IDs without user permission.
- Modify web UI, config UI, or other provider modules.

## Permission-Gated New Features

Ask the user before implementing any of these:

1. New Grok video generation/upscale support.
2. Grok assets list/download/delete APIs.
3. Accept-TOS or set-birth automation.
4. Token manager scheduler/admin UI/persistent upstream pool model.
5. New public model IDs or API endpoints not already represented in local `models.py`.
6. Any global account-service API changes that affect non-Grok providers.

## Delegation Strategy

Implementation should be delegated after this plan passes review:

- Agent A: Round 1 token/account normalization and tests. Files: `services/providers/grok/accounts.py`, `services/providers/grok/client.py` only if cookie helper tests require it, `test/test_grok_provider.py`.
- Agent B: Round 2 rate-limit extraction/quota/refresh behavior and tests. Files: `services/providers/grok/accounts.py`, `services/providers/grok/client.py`, `test/test_grok_provider.py`.
- Agent C: Round 3 processing/SSE/error/image robustness and tests. Files: `services/providers/grok/client.py`, `test/test_grok_provider.py`.

Parallelism note: Agents B and C may conflict in `client.py` and tests, so either serialize them or assign non-overlapping function blocks and reconcile manually.

## Verification

Round-specific QA scenarios:

1. Round 1 token/account normalization
   - Tool: `uv run pytest test/test_grok_provider.py -k "token or cookie or tier or account"`.
   - Steps: add/verify tests that import Grok tokens containing `sso=`, unicode dash/minus characters, NBSP/zero-width characters, and cookie headers with both SSO and Cloudflare cookies.
   - Expected: normalized bare SSO tokens contain no copied unicode artifacts; full cookie headers remain parseable; `app_chat_headers()` emits both `sso` and `sso-rw`; tier aliases route as `basic`, `super`, or `heavy`.

2. Round 2 refresh/quota/selection
   - Tool: `uv run pytest test/test_grok_provider.py -k "rate_limit or quota or refresh or usage or selection"`.
   - Steps: add/verify mocked `/rest/rate-limits` responses with `remainingTokens`, `remainingQueries`, `windowSizeSeconds`, auth failures, and 429/rate-limit failures.
   - Expected: refresh marks auth failures abnormal, 429/exhausted quota limited/cooling according to existing local status vocabulary, unambiguous window size corrects tier, high-cost model usage deducts higher local quota only on successful completion, and excluded failed tokens are not immediately reused when retry paths exist.

3. Round 3 stream/error/image processing
   - Tool: `uv run pytest test/test_grok_provider.py -k "stream or sse or final_metadata or moderation or image"`.
   - Steps: add/verify parser fixtures for raw JSON lines, `data:` lines, embedded multiple `data:` payloads, `[DONE]`, soft-stop/finalMetadata events, app-chat moderation payloads, and image URL payload variants.
   - Expected: stream parser emits the same semantic events for all supported upstream line formats, final events terminate collection, sensitive upstream errors are sanitized, moderation produces an existing image error path, and existing image/image-edit tests still pass.

Final verification wave:

- Run `lsp_diagnostics` on changed Grok files.
- Run `uv run pytest test/test_grok_provider.py`.
- If provider adapter contracts are touched, run `uv run pytest test/test_account_provider.py test/test_account_api_sanitization.py` only if those tests are already present and runnable.
- Inspect `git diff -- services/providers/grok test/test_grok_provider.py .sisyphus/plans/grok-upstream-integration.md` to ensure only intended Grok provider/test/plan files changed.
- Inspect full `git status --short` and explicitly separate pre-existing non-Grok dirty files from this work.
- Do not push until verification passes and user permission issues are resolved.
