# Architecture Guide

## Overview

WebAI-to-API is a browser-native AI runtime that exposes browser-based AI services through OpenAI-compatible APIs.

The project is built around a provider-centric architecture that separates logical AI providers from their underlying execution mechanisms.

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

---

### Backend Adapter

A backend adapter is the technical execution layer used by a provider.

For Gemini:

* WebAPI adapter
* Playwright adapter

Adapters are responsible for:

* Authentication activation
* Request execution
* Streaming integration
* Backend-specific behavior

---

### Provider Routing

Requests are routed through `/v1/chat/completions`.

Examples:

```text id="fqg9x6"
gemini-3-flash
playwright/gemini-3-pro
atlas/MiniMax-M2
```

The routing layer resolves the appropriate provider and execution backend before processing the request.

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

* Browser lifecycle management
* Session management
* Authentication activation
* Recovery orchestration
* Streaming infrastructure

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

Conversation handling depends on the selected provider and backend.

Examples:

* Gemini WebAPI uses local session persistence.
* Gemini Playwright uses provider conversation continuity.
* Atlas is stateless.

---

## Authentication Model

Authentication is separated into distinct responsibilities:

* Discovery
* Selection
* Validation
* Activation

This separation allows providers and backends to implement authentication workflows independently while sharing common orchestration.

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
