# Dashboard

The built-in dashboard is an administrative interface for local runtime inspection and limited operations.

## Pages

- `/ui` - overview
- `/ui/status` - runtime status
- `/ui/auth` - cached authentication state
- `/ui/models` - registered model list
- `/ui/playground` - chat prompt playground
- `/ui/conversations` - locally persisted Gemini WebAPI conversation snapshots

## Security posture

The dashboard routes currently have no authentication layer.

Treat the dashboard as an administrative surface, not a public user-facing app.
Recommended deployment options:

- run behind a trusted internal network
- place it behind a reverse proxy with external authentication
- restrict access with an upstream auth gateway or similar control

Do not expose the dashboard publicly unless you add a separate access-control layer.

## Docker note

The default container setup exposes the service port directly. If you map that port to a public interface, you also expose the dashboard routes.

Keep the dashboard reachable only from trusted clients unless you have explicit authentication in front of it.

## Static assets

Dashboard CSS and JavaScript assets are served by standard Starlette `StaticFiles` from `/ui/static`.
