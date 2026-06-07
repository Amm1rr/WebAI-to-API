# Dashboard

The built-in dashboard is an administrative interface for local runtime inspection and limited operations.

The `/ui/*` routes are not part of the public API contract and are excluded from the OpenAPI schema.

## Pages

- `/ui` - overview
- `/ui/apis` - API endpoint catalog
- `/ui/status` - runtime status
- `/ui/auth` - cached authentication state
- `/ui/models` - registered model list
- `/ui/playground` - chat prompt playground with optional file attachments for Gemini WebAPI
- `/ui/conversations` - locally persisted Gemini WebAPI conversation snapshots, with single-delete and bulk-delete actions limited to Gemini WebAPI

## API Discovery (/ui/apis)

The API catalog is dynamically generated from the FastAPI route registry. It provides a human-readable inventory of the available API surface.

- **Categorization**: Endpoints are grouped into Recommended, Compatibility, Advanced, and Legacy sections.
- **Feature Badges**: Cards display supported features such as Streaming (SSE) and Persistence behavior.
- **Interactive Documentation**: Direct links to Swagger UI and ReDoc are provided for deep schema inspection.

## Security posture

The dashboard routes currently have no authentication layer.

Treat the dashboard as an administrative surface, not a public user-facing app.
Recommended deployment options:

- run behind a trusted internal network
- place it behind a reverse proxy with external authentication
- restrict access with an upstream auth gateway or similar control

Do not expose the dashboard publicly unless you add a separate access-control layer.

Conversation actions are scoped to locally persisted Gemini WebAPI snapshots only:

- single delete applies only to a local Gemini WebAPI snapshot
- bulk delete applies only to locally persisted Gemini WebAPI snapshots
- Playwright and Atlas conversations are not affected
- bulk delete is best-effort and may partially succeed

## Docker note

The default container setup exposes the service port directly. If you map that port to a public interface, you also expose the dashboard routes.

Keep the dashboard reachable only from trusted clients unless you have explicit authentication in front of it.

## Static assets

Dashboard CSS and JavaScript assets are served by standard Starlette `StaticFiles` from `/ui/static`.

The playground uses the existing `/v1/chat/completions` JSON contract. When files are attached, they are converted client-side into OpenAI-style `type: "file"` content parts and sent to Gemini WebAPI only. Gemini Playwright and Atlas do not support file parts, and Gemini WebAPI does not preserve exact text/file interleaving order.
The current supported file formats are documented in [API documentation](api.md).

The playground also includes a dedicated `Artifacts` panel. Buffered and streaming artifacts appear there. Rendering is link-first, with no autoplay and no iframe embedding. Response text remains separate from artifacts.

The UI enforces conservative file limits to account for browser-side base64 expansion. Backend validation remains authoritative.
