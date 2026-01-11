# src/app/endpoints/anthropic.py
import time
import json
import re
import uuid
from typing import List, Optional, Union, Dict, Any, Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.logger import logger
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError

router = APIRouter()

class AnthropicToolInputSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: Optional[Dict[str, Any]] = None
    required: Optional[List[str]] = None

class AnthropicTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]

class AnthropicBlock(BaseModel):
    type: str
    text: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    tool_use_id: Optional[str] = None
    content: Optional[Union[str, List[Any]]] = None
    is_error: Optional[bool] = None

class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, List[AnthropicBlock]]

class AnthropicRequest(BaseModel):
    model: str
    messages: List[AnthropicMessage]
    max_tokens: Optional[int] = Field(default=4096)
    system: Optional[Union[str, List[AnthropicBlock]]] = None
    metadata: Optional[Dict[str, Any]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[AnthropicTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None

TOOL_INJECTION_PROMPT = """
[SYSTEM INSTRUCTION: TOOL USE]
You are acting as an AI assistant that has access to local tools.
The client (Claude Code) expects you to use these tools when necessary to complete tasks.

AVAILABLE TOOLS:
{tools_json}

INSTRUCTIONS FOR USING TOOLS:
1. When you need to read a file, execute a command, or perform an action provided by the tools above, you MUST output a tool call.
2. The tool call MUST be strict JSON wrapped in specific tags.
3. FORMAT:
   [[TOOL_CALL:
   {{
     "name": "<tool_name>",
     "input": {{ <arguments> }}
   }}
   ]]
4. Do NOT output the tool call inside Markdown code blocks (like ```json). Output it as raw text.
5. You can call multiple tools if needed, but usually one at a time is safer.
6. After emitting a tool call, STOP generating text. The system will execute it and return the result.
7. If the user provides a "tool_result" in the chat history, treats it as the output of your previous tool call.
"""

def parse_system_prompt(system: Union[str, List[AnthropicBlock], None]) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n".join([b.text for b in system if b.type == "text" and b.text])

def format_tools_for_prompt(tools: List[AnthropicTool]) -> str:
    if not tools:
        return ""
    
    tools_def = []
    for tool in tools:
        tools_def.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema
        })
    
    return json.dumps(tools_def, indent=2)

def convert_messages_to_prompt(messages: List[AnthropicMessage]) -> str:
    prompt_parts = []
    
    for msg in messages:
        role = msg.role.capitalize()
        
        if isinstance(msg.content, str):
            prompt_parts.append(f"{role}: {msg.content}")
        elif isinstance(msg.content, list):
            prompt_parts.append(f"{role}:")
            for block in msg.content:
                if block.type == "text":
                    prompt_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_json = json.dumps({"name": block.name, "input": block.input})
                    prompt_parts.append(f"[[TOOL_CALL: {tool_json}]]")
                elif block.type == "tool_result":
                    result_content = block.content
                    if isinstance(result_content, list):
                        result_text = " ".join([b.get('text', '') for b in result_content if isinstance(b, dict)])
                    else:
                        result_text = str(result_content)
                    
                    status = "ERROR" if block.is_error else "SUCCESS"
                    prompt_parts.append(f"[TOOL_RESULT ({status}) for ID {block.tool_use_id}]: {result_text}")
                elif block.type == "image":
                     prompt_parts.append("[User uploaded an image (not supported in text context)]")
        
        prompt_parts.append("")
        
    return "\n".join(prompt_parts)

@router.post("/v1/messages")
async def create_message(request: AnthropicRequest):
    request_id = f"req_{uuid.uuid4().hex[:8]}"
    logger.info(f"[Anthropic:{request_id}] New Request | Model: {request.model} | Stream: {request.stream}")
    
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        logger.error(f"[Anthropic:{request_id}] Client not init: {e}")
        raise HTTPException(status_code=503, detail="Gemini client not initialized")

    system_instruction = parse_system_prompt(request.system)
    
    # Inject Tool Instructions if tools are present
    if request.tools:
        logger.info(f"[Anthropic:{request_id}] Request contains {len(request.tools)} tools. Injecting shim.")
        tools_json = format_tools_for_prompt(request.tools)
        tool_prompt = TOOL_INJECTION_PROMPT.format(tools_json=tools_json)
        system_instruction += f"\n\n{tool_prompt}"
    
    full_prompt = ""
    if system_instruction:
        full_prompt += f"System: {system_instruction}\n\n"
    
    full_prompt += convert_messages_to_prompt(request.messages)
    
    logger.debug(f"[Anthropic:{request_id}] Prompt len: {len(full_prompt)}")
    
    target_model = "gemini-3.0-pro"
    
    try:
        start_t = time.time()
        response = await gemini_client.generate_content(
            message=full_prompt,
            model=target_model
        )
        duration = time.time() - start_t
        
        response_text = response.text
        logger.info(f"[Anthropic:{request_id}] Gemini Response in {duration:.2f}s | Len: {len(response_text)}")
        
        if request.stream:
            return StreamingResponse(
                stream_generator(response_text, request.model, request_id),
                media_type="text/event-stream"
            )
        else:
            return {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "model": request.model,
                "content": [{"type": "text", "text": response_text}],
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }

    except Exception as e:
        logger.error(f"[Anthropic:{request_id}] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def stream_generator(full_text: str, model_name: str, req_id: str):
    msg_id = f"msg_{uuid.uuid4().hex}"
    
    yield mk_event("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_name,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    })
    
    pattern = re.compile(r'\[\[TOOL_CALL:\s*(\{.*?\})\s*\]\]', re.DOTALL)
    
    last_pos = 0
    content_index = 0
    
    stop_reason = "end_turn"
    
    for match in pattern.finditer(full_text):
        text_chunk = full_text[last_pos:match.start()].strip()
        if text_chunk:
            async for chunk in yield_text_block(text_chunk, content_index):
                yield chunk
            content_index += 1
            
        tool_json_str = match.group(1)
        try:
            tool_data = json.loads(tool_json_str)
            tool_name = tool_data.get("name")
            tool_input = tool_data.get("input", {})
            tool_id = f"toolu_{uuid.uuid4().hex[:12]}"
            
            yield mk_event("content_block_start", {
                "type": "content_block_start",
                "index": content_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": {}
                }
            })
            
            yield mk_event("content_block_delta", {
                "type": "content_block_delta",
                "index": content_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input)
                }
            })
            
            yield mk_event("content_block_stop", {
                "type": "content_block_stop",
                "index": content_index
            })
            
            content_index += 1
            stop_reason = "tool_use"
            
        except json.JSONDecodeError:
            logger.warning(f"[Anthropic:{req_id}] Failed to parse tool JSON: {tool_json_str[:50]}...")
            raw_text = match.group(0)
            async for chunk in yield_text_block(raw_text, content_index):
                yield chunk
            content_index += 1
            
        last_pos = match.end()

    remaining_text = full_text[last_pos:].strip()
    if remaining_text:
        async for chunk in yield_text_block(remaining_text, content_index):
            yield chunk
        content_index += 1
    
    yield mk_event("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason, 
            "stop_sequence": None
        },
        "usage": {"output_tokens": len(full_text.split())}
    })
    
    yield mk_event("message_stop", {"type": "message_stop"})


async def yield_text_block(text: str, index: int):
    yield mk_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""}
    })
    
    yield mk_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text}
    })
    
    yield mk_event("content_block_stop", {
        "type": "content_block_stop",
        "index": index
    })

def mk_event(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
