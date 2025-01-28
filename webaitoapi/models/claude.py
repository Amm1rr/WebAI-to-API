import json
import time
import httpx
from curl_cffi import requests

class ClaudeClient:
    def __init__(self, cookie):
        self.cookie = self.fix_sessionkey(cookie)
        self.organization_id = self.get_organization_id()

    def fix_sessionkey(self, cookie: str) -> str:
        if isinstance(cookie, dict):
            return "; ".join([f"{key}={value}" for key, value in cookie.items()])
        return cookie

    def get_organization_id(self):
        url = "https://claude.ai/api/organizations"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
            'Cookie': self.cookie,
        }
        response = requests.get(url, headers=headers, impersonate="chrome110")
        if response.status_code == 200:
            return response.json()["data"][0]["id"]
        else:
            raise Exception("Error getting organization ID")

    def send_message(self, prompt, model):
        conversation_id = self.create_new_chat()
        url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"
        payload = json.dumps({
            "prompt": prompt,
            "model": model,
            "timezone": "Europe/London",
            "attachments": [],
        })
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
        }
        response = requests.post(url, headers=headers, data=payload, impersonate="chrome110")
        if response.status_code == 200:
            return response.json()["completion"]
        else:
            raise Exception("Error sending message to Claude")

    async def stream_message(self, prompt, model):
        conversation_id = self.create_new_chat()
        url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"
        payload = json.dumps({
            "prompt": prompt,
            "model": model,
            "timezone": "Europe/London",
            "attachments": [],
        })
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
        }
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", url, headers=headers, data=payload) as response:
                async for chunk in response.aiter_text():
                    yield json.dumps({
                        "id": f"chatcmpl-{int(time.time())}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "delta": {
                                    "content": chunk,
                                },
                                "index": 0,
                                "finish_reason": None,
                            }
                        ],
                    })

    def create_new_chat(self):
        url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations"
        payload = json.dumps({"name": ""})
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
            'Cookie': self.cookie,
            'Content-Type': 'application/json',
        }
        response = requests.post(url, headers=headers, data=payload, impersonate="chrome110")
        if response.status_code == 200:
            return response.json()["uuid"]
        else:
            raise Exception("Error creating new chat")