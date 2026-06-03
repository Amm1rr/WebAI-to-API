# Architecture Guide

## Overview

WebAI-to-API is a browser-native API runtime that exposes browser-based AI services through OpenAI-compatible APIs.

The project is built around registered logical providers, provider-specific adapters, and shared browser runtime ownership boundaries.

Request execution is coordinated through `BrowserRequestExecutor`, which owns the shared browser-native request lifecycle while delegating provider-specific behavior to adapters and auth strategies.

Examples:

* Gemini (logical provider)

  * WebAPI backend
  * Playwright backend

This separation allows multiple execution strategies to share common provider logic while maintaining a consistent API surface.

---

## Core Concepts

### Provider

A provider represents an AI vendor or service.

Examples:

* Gemini
* Atlas

Providers are responsible for:

* Request transformation
* Response normalization
* Provider-specific functionality
* Conversation handling

Logical providers own model behavior, prompt transformation, and conversation semantics.

Auth policy and browser-native execution are delegated to provider auth strategies and adapters.

---

### Backend Adapter

A backend adapter is the technical execution layer used by a provider.

For Gemini:

* WebAPI adapter
* Playwright adapter

Adapters are responsible for:

* Backend-specific execution behavior
* Backend-specific validation
* Backend-specific continuity behavior

Shared request execution flows through `BrowserRequestExecutor`.

Auth policy is owned by provider auth strategies.

Streaming ownership lives in shared runtime infrastructure.

---

### Provider Routing

Requests are routed through `/v1/chat/completions`.

Examples:

```text id="fqg9x6"
gemini-3-flash
playwright/gemini/gemini-3.1-pro
atlas/MiniMax-M2
```

The routing layer resolves the appropriate provider and execution backend before processing the request.

Browser-native providers generally use `playwright/<provider>/<model>`.

Legacy Gemini browser routing remains supported with `playwright/<gemini-model>`.

---

## Runtime Layers

### API Layer

FastAPI endpoints expose the public API surface.

Responsibilities:

* Request validation
* Request routing
* Response serialization

---

### Provider Layer

Providers implement business logic and model-specific behavior.

Responsibilities:

* Prompt transformation
* Tool-call processing
* Response normalization
* Conversation management

---

### Runtime Layer

The browser runtime manages browser-backed execution.

Responsibilities:

* `BrowserEngine`: browser process lifecycle, generation invalidation, terminal shutdown
* `ProviderSession`: browser context lifecycle, keepalive page ownership, provider-scoped recovery
* `BrowserRequestExecutor`: request-scoped execution, bridge lifecycle, streaming integration, cleanup
* `AuthManager`: status caching and orchestration

---

## Project Structure

```text id="1yt9u8"
src/
└── app/
    ├── endpoints/
    ├── services/
    │   ├── providers/
    │   └── browser/
    ├── utils/
    └── main.py
```

### Key Directories

#### endpoints/

Public API routers.

Examples:

* chat
* auth
* models
* system

---

#### services/providers/

Provider implementations.

Examples:

* Gemini
* Atlas

---

#### services/browser/

Browser runtime infrastructure.

Examples:

* Browser engine
* Sessions
* Tabs
* Authentication
* Recovery

---

#### utils/

Shared utility functions and helpers.

---

## Conversation Continuity

Conversation continuity depends on the selected provider and backend.

Examples:

* Gemini WebAPI uses snapshot-backed continuity from local conversation persistence.
* Browser-native providers use URL-backed continuity and may reuse existing browser-native conversation state when available.
* Stateless providers do not persist `conversation_id` state locally.

---

## Authentication Model

Authentication is separated into distinct responsibilities:

* `AuthLoader`
* Provider auth strategies
* `AuthManager`

`AuthLoader` discovers available auth material.

Provider auth strategies own selection and fallback policy.

`AuthManager` owns cached status and login/recovery orchestration.

---

## Runtime Contracts

Detailed runtime guarantees are documented separately:

* [Runtime Architecture Overview](specs/runtime-architecture-overview.md)
* [Provider Contract](specs/provider-contract.md)
* [Concurrency Model](specs/concurrency-model.md)
* [Streaming Pipeline](specs/streaming-pipeline.md)
* [Lifecycle and Recovery](specs/lifecycle-and-recovery.md)
* [Error Policy](specs/error-policy.md)
* [API Contract](specs/api-contract.md)
* [Docker Deployment Model](specs/docker-deployment.md)

These documents are authoritative for runtime behavior and implementation invariants.
