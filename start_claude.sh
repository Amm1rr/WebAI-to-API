#!/bin/bash
# Claude Code Startup Script for WebAI-to-API with Gemini
# This script configures Claude Code to use the local Gemini API

echo "🚀 Starting Claude Code with Gemini API Backend..."
echo ""
echo "Configuration:"
echo "  - API Base URL: http://localhost:6969"
echo "  - Model: gemini-2.0-flash-thinking-exp"
echo "  - Backend: WebAI-to-API (Gemini)"
echo ""
echo "Make sure the WebAI-to-API server is running on port 6969!"
echo ""

# Export environment variables to point Claude Code to our API
export ANTHROPIC_BASE_URL=http://localhost:6969
export ANTHROPIC_AUTH_TOKEN=dummy
export ANTHROPIC_MODEL=gemini-3.0-pro
export ANTHROPIC_DEFAULT_SONNET_MODEL=gemini-3.0-pro
export ANTHROPIC_SMALL_FAST_MODEL=gemini-3.0-pro
export ANTHROPIC_DEFAULT_HAIKU_MODEL=gemini-3.0-pro
export DISABLE_NON_ESSENTIAL_MODEL_CALLS=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# Launch Claude Code
echo "Launching Claude Code..."
claude
