import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import requests
import configparser
import urllib.parse
import json
import time
import uuid

app = FastAPI()


class Message(BaseModel):
    session_id: str
    message: str

@app.post("/claude2")
def stream_handler():
  def generate():
    for i in range(10):
      print("Yielding", i)
      yield json.dumps({"data": i})

  return StreamingResponse(generate())

@app.post("/claude")
async def ask(request: Request, message: Message):

    cookie = os.environ.get('CLAUDE_COOKIE')
    if not cookie:
        config = configparser.ConfigParser()
        config.read("Config.conf")
        cookie = config.get('Claude', 'COOKIE', fallback=None)

    if not cookie:
        raise ValueError("Please set the 'cookie' environment variable.")

    claude = Client(cookie)
    conversation_id = None

    if not conversation_id:
        conversation = claude.create_new_chat()
        conversation_id = conversation['uuid']

    # responses = claude.send_message(message.message, conversation_id)
    # return responses

    responses = claude.stream_message(message.message, conversation_id)
    print(responses)
    return StreamingResponse(responses)


class Client:

    def __init__(self, cookie):
        self.cookie = cookie
        self.organization_id = self.get_organization_id()

    def get_organization_id(self):
        url = "https://claude.ai/api/organizations"

        headers = {
            'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://claude.ai/chats',
            'Content-Type': 'application/json',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Connection': 'keep-alive',
            'Cookie': f'{self.cookie}'
        }

        response = requests.request("GET", url, headers=headers)
        res = json.loads(response.text)
        uuid = res[0]['uuid']

        return uuid

    def send_message(self, prompt, conversation_id, attachment=None):
        url = "https://claude.ai/api/append_message"

        payload = json.dumps({
            "completion": {
                "prompt": f"{prompt}",
                "timezone": "Asia/Kolkata",
                "model": "claude-2"
            },
            "organization_uuid": f"{self.organization_id}",
            "conversation_uuid": f"{conversation_id}",
            "text": f"{prompt}",
            "attachments": []
        })

        headers = {
            'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'text/event-stream, text/event-stream',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://claude.ai/chats',
            'Content-Type': 'application/json',
            'Origin': 'https://claude.ai',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Cookie': f'{self.cookie}',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
        }

        response = requests.post(url, headers=headers,
                                 data=payload, stream=True)
        decoded_data = response.content.decode("utf-8")
        data = decoded_data.strip().split('\n')[-1]

        answer = {"answer": json.loads(data[6:])['completion']}['answer']

        # Returns answer
        return answer

    def generate_uuid(self):
        random_uuid = uuid.uuid4()
        random_uuid_str = str(random_uuid)
        formatted_uuid = f"{random_uuid_str[0:8]}-{random_uuid_str[9:13]}-{random_uuid_str[14:18]}-{random_uuid_str[19:23]}-{random_uuid_str[24:]}"
        return formatted_uuid

    def create_new_chat(self):
        url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations"
        uuid = self.generate_uuid()

        payload = json.dumps({"uuid": uuid, "name": ""})
        headers = {
            'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://claude.ai/chats',
            'Content-Type': 'application/json',
            'Origin': 'https://claude.ai',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Cookie': self.cookie,
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
        }

        response = requests.request("POST", url, headers=headers, data=payload)

        # Returns JSON of the newly created conversation information
        return response.json()

    def stream_message(self, prompt, conversation_id):

        url = "https://claude.ai/api/stream_message"

        payload = json.dumps({
            "completion": {
                "prompt": f"{prompt}",
                "timezone": "Asia/Kolkata",
                "model": "claude-2"
            },
            "organization_uuid": f"{self.organization_id}",
            "conversation_uuid": f"{conversation_id}",
            "text": f"{prompt}",
            "attachments": []
        })
        # payload = json.dumps({
        #     "conversation_uuid": conversation_id,
        #     "prompt": prompt
        # })

        headers = {
            'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'text/event-stream, text/event-stream',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://claude.ai/chats',
            'Content-Type': 'application/json',
            'Origin': 'https://claude.ai',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Cookie': f'{self.cookie}',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
        }
        
        with requests.post(url, headers=headers, data=payload, stream=True) as response:
    
            for line in response.iter_lines():
                if line:
                    data = json.loads(line.decode())
                    print(data)
                    # yield data
                    yield json.dumps(data)
            
        # with requests.post(url, headers=headers, data=payload, stream=True) as response:
        #     # for line in response.iter_lines():
        #     #     if line:
        #     #         data = json.loads(line)
        #     #         yield data
        #     for line in response.iter_lines():
        #         if line:
        #             try:
        #                 for obj in line.split(b"\n"):
        #                     json_response = json.loads(obj)
        #                     print(json_response)
        #             except json.JSONDecodeError:
        #                 print("Error")
