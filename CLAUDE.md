# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WebAI-to-API is a modular FastAPI web server that exposes browser-based LLMs (primarily Google Gemini) as local API endpoints. The project supports two operational modes:

1. **WebAI-to-API Server** (Primary): FastAPI-based server connecting to Gemini web interface using browser cookies
2. **gpt4free Server** (Fallback): Secondary server powered by the g4f library for broader LLM access

## Development Commands

### Setup and Installation
```bash
# Install dependencies using Poetry
poetry install

# Create configuration file from template
cp config.conf.example config.conf

# Run the server (default: localhost:6969)
poetry run python src/run.py

# Run with custom host/port
poetry run python src/run.py --host 0.0.0.0 --port 8000

# Run with auto-reload for development
poetry run python src/run.py --reload
```

### Dependencies Management
- Uses Poetry for dependency management
- Main dependencies: FastAPI, uvicorn, gemini-webapi, browser-cookie3, httpx, curl-cffi, g4f
- Windows-specific dependencies for cookie decryption: pywin32, pycryptodomex

## Architecture Overview

### Entry Point and Server Management
- **src/run.py**: Main entry point with dual-server architecture and hot-switching capability
- Implements multiprocessing for seamless switching between WebAI and g4f modes
- Handles graceful shutdown and server lifecycle management
- Supports runtime mode switching via keyboard input (1 for WebAI, 2 for g4f)

### Core Application Structure
```
src/
├── run.py                     # Entry point with server management
├── app/
│   ├── main.py               # FastAPI app creation and lifespan management
│   ├── config.py             # Configuration loading and management
│   ├── logger.py             # Centralized logging setup
│   ├── endpoints/            # API route handlers
│   │   ├── gemini.py         # Gemini-specific endpoints (/gemini, /gemini-chat)
│   │   ├── chat.py           # OpenAI-compatible endpoints (/v1/chat/completions, /translate)
│   │   └── google_generative.py # Google Generative AI API endpoints
│   ├── services/             # Business logic layer
│   │   ├── gemini_client.py  # Gemini client wrapper and initialization
│   │   └── session_manager.py # Chat session management
│   └── utils/
│       └── browser.py        # Browser cookie extraction utilities
├── models/
│   └── gemini.py             # Gemini client model wrapper
└── schemas/
    └── request.py            # Pydantic models for request validation
```

### Key Components

#### Configuration System
- **config.conf**: INI-format configuration file with sections for Browser, AI, Cookies, EnabledAI, and Proxy
- **app/config.py**: Handles configuration loading with UTF-8 encoding support and defaults
- Global CONFIG object accessible throughout the application

#### Gemini Client Integration
- **app/services/gemini_client.py**: Manages Gemini client lifecycle with authentication and error handling
- **models/gemini.py**: Wrapper around gemini-webapi library
- Supports both manual cookie configuration and automatic browser cookie extraction
- Handles AuthError exceptions for expired cookies or network issues

#### Session Management
- **app/services/session_manager.py**: Manages persistent chat sessions for context retention
- Separate managers for different endpoint types (chat vs translate)

#### API Endpoints
- **Primary WebAI endpoints**:
  - `/gemini`: Stateless content generation
  - `/gemini-chat`: Stateful chat with context
  - `/translate`: Translation service (alias for gemini-chat)
  - `/v1/chat/completions`: OpenAI-compatible endpoint
- **Google Generative AI endpoint**:
  - `/google-generatives`: Direct Google Generative API integration

### Server Mode Architecture
The application implements a unique dual-server design:

1. **WebAI Mode**: FastAPI server focused on Gemini integration with custom endpoints
2. **g4f Mode**: gpt4free library server providing access to multiple LLM providers

The server manager in `run.py` handles:
- Mode availability checking during startup
- Graceful server switching without connection drops
- Process lifecycle management with proper cleanup
- User input handling for mode selection

### Browser Integration
- **app/utils/browser.py**: Cross-platform browser cookie extraction
- Supports Chrome, Firefox, Brave, Edge, Safari
- Handles cookie decryption on Windows using platform-specific dependencies

## Development Notes

### Error Handling Patterns
- Specific exception handling for AuthError in Gemini client
- Comprehensive logging throughout the application
- Graceful degradation when services are unavailable

### Configuration Management
- UTF-8 encoding for configuration files to ensure cross-platform compatibility
- Automatic creation of default configuration when missing
- Hot-reloading support in development mode

### Session Management
- Stateful conversations using session managers
- Context retention between messages for chat endpoints
- Separate session handling for different endpoint types

### Testing and Development
- Use `--reload` flag for auto-reloading during development
- Server runs on localhost:6969 by default
- Comprehensive logging for debugging and monitoring
- Swagger documentation available at `/docs` endpoint