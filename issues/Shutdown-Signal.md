Investigate deployment-independent early shutdown signaling for Playwright runtime.

Current status:
- _shutdown_started decoupling completed.
- No proven pre-shutdown hook found in Uvicorn/FastAPI lifecycle.
- Warning logs during SIGINT/SIGTERM are currently accepted as diagnostic noise.

Future work:
- Verify shutdown ordering against pinned Uvicorn version.
- Evaluate supported signal interception points.
- Validate behavior under Docker, systemd, and Kubernetes.
