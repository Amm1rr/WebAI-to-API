# Atlas Cloud Provider Review

## Summary

- Added an Atlas Cloud provider path to the existing OpenAI-compatible `/v1/chat/completions` endpoint.
- Kept the Gemini request path intact and isolated Atlas routing behind `provider: "atlas"` or an `atlas/` model prefix.
- Added local environment loading so Atlas credentials can live in `.env.local` without being committed.
- Updated `README.md` with Atlas Cloud introduction text, logo, UTM link, setup notes, and request examples.

## Code Changes

- `src/app/endpoints/chat.py`
  - Added provider resolution for `gemini` and `atlas`.
  - Routed Atlas requests directly to the Atlas Cloud OpenAI-compatible API.
  - Preserved streaming behavior by proxying SSE chunks through FastAPI.
  - Added Atlas model visibility to `/v1/models`.

- `src/app/services/atlas_client.py`
  - Added a small async Atlas client using `httpx`.
  - Reads `ATLASCLOUD_API_KEY` and `ATLASCLOUD_BASE_URL`.
  - Returns upstream JSON directly and supports streaming pass-through.

- `src/app/env.py`
  - Added lightweight `.env.local` and `.env` loading without introducing a new dependency.

- `src/app/config.py`
  - Loads local env files during startup so Atlas credentials are available early.

- `src/schemas/request.py`
  - Added optional `provider` field to OpenAI-compatible chat requests.

- `README.md`
  - Added Atlas Cloud logo and product intro.
  - Added the required UTM link:
    `https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=WebAI-to-API`
  - Added setup and usage examples for Atlas Cloud.

- `.env.example`
  - Added Atlas Cloud env var template.

- `.gitignore`
  - Ignored `.env.local`.

## Local Validation

- Syntax validation
  - `python3 -m compileall src`

- Project API validation
  - Non-streaming Atlas request via local `/v1/chat/completions`: passed
  - Streaming Atlas request via local `/v1/chat/completions`: passed
  - `/v1/models` includes `atlas/deepseek-ai/DeepSeek-V3-0324`: passed

- Direct upstream Atlas validation
  - `https://api.atlascloud.ai/v1/chat/completions` with model `deepseek-ai/DeepSeek-V3-0324`: passed

## Notes

- Atlas Cloud worked with the real model name `deepseek-ai/DeepSeek-V3-0324`.
- The generic sample model `deepseek-v3` returned `400 {"code":400,"msg":"not found"}` for this key/environment, so docs and examples were updated to the verified model.
- Gemini startup currently times out in this environment during upstream initialization. This is an existing runtime issue outside the Atlas code path; Atlas routing still works because it does not depend on Gemini initialization success.
