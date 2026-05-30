Title: Investigate whether ProviderSession._setup() should always call close_resources() during cold start

Background

ProviderSession._setup() currently begins with an unconditional:

await self.close_resources(save_state=False)

After separating initial session initialization from browser generation rollover, the previous false-positive rollover warning during cold start has been eliminated.

Current Findings

During the first initialization path:

* last_browser_generation starts as None.
* No rollover cleanup is triggered.
* _setup() still invokes close_resources(save_state=False).
* At this point all tracked resources are uninitialized (context, keepalive page, tasks, tabs, etc.).
* close_resources() effectively becomes a no-op and performs only fast-path checks.

Assessment

This is not a bug and has no measurable operational impact.

The current behavior provides a defensive "start from a clean state" invariant and may be preferable to adding additional branching logic.

Future Review

If the session lifecycle is refactored in the future, re-evaluate whether unconditional teardown at the beginning of _setup() remains desirable or whether explicit initialization-state handling would improve clarity without increasing complexity.
