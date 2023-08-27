#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, os, uuid
from curl_cffi import requests
import requests as req
import re
from datetime import datetime

class Client:

  def fix_sessionKey(self, cookie):
    if "sessionKey=" not in cookie:
        cookie = "sessionKey=" + cookie
    return cookie

  def __init__(self, cookie):
    self.cookie = self.fix_sessionKey(cookie)
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
        'Cookie': self.cookie
    }

    response = requests.get(url, headers=headers,impersonate="chrome110")
    res = json.loads(response.text)
    uuid = res[0]['uuid']

    return uuid


  def get_content_type(self, file_path):
    # Function to determine content type based on file extension
    extension = os.path.splitext(file_path)[-1].lower()
    if extension == '.pdf':
      return 'application/pdf'
    elif extension == '.txt':
      return 'text/plain'
    elif extension == '.csv':
      return 'text/csv'
    # Add more content types as needed for other file types
    else:
      return 'application/octet-stream'

  # Lists all the conversations you had with Claude
  def list_all_conversations(self):
    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations"

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
        'Cookie': self.cookie
    }

    response = requests.get(url, headers=headers,impersonate="chrome110")
    conversations = response.json()

    # Returns all conversation information in a list
    if response.status_code == 200:
      return conversations
    else:
      print(f"Error: {response.status_code} - {response.text}")

  # Send Message to Claude
  def send_message(self, prompt, conversation_id, attachment=None,timeout=120):
    url = "https://claude.ai/api/append_message"

    # Upload attachment if provided
    attachments = []
    if attachment:
      attachment_response = self.upload_attachment(attachment)
      if attachment_response:
        attachments = [attachment_response]
      else:
        return {"Error: Invalid file format. Please try again."}

    # Ensure attachments is an empty list when no attachment is provided
    if not attachment:
      attachments = []

    payload = json.dumps({
      "completion": {
        "prompt": f"{prompt}",
        "timezone": "Europe/London",
        "model": "claude-2"
      },
      "organization_uuid": f"{self.organization_id}",
      "conversation_uuid": f"{conversation_id}",
      "text": f"{prompt}",
      "attachments": attachments
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
      'Cookie': self.cookie,
      'Sec-Fetch-Dest': 'empty',
      'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Site': 'same-origin',
      'TE': 'trailers'
    }

    response = requests.post( url, headers=headers, data=payload,impersonate="chrome110",timeout=120)
    decoded_data = response.content.decode("utf-8")
    decoded_data = re.sub('\n+', '\n', decoded_data).strip()
    data_strings = decoded_data.split('\n')
    completions = []
    for data_string in data_strings:
      json_str = data_string[6:].strip()
      data = json.loads(json_str)
      if 'completion' in data:
        completions.append(data['completion'])

    answer = ''.join(completions)

    # Returns answer
    return answer
  
  # Send and Response Stream Message to Claude
  def stream_message(self, prompt, conversation_id, attachment=None,timeout=120):

    # for i in range(10):
        #     yield b'some fake data\n'
        #     time.sleep(0.5)
        # return
    
    url = "https://claude.ai/api/append_message"

    # Upload attachment if provided
    attachments = []
    if attachment:
      attachment_response = self.upload_attachment(attachment)
      if attachment_response:
        attachments = [attachment_response]
      else:
        return {"Error: Invalid file format. Please try again."}

    # Ensure attachments is an empty list when no attachment is provided
    if not attachment:
      attachments = []

    payload = json.dumps({
      "completion": {
        "prompt": f"{prompt}",
        "timezone": "Europe/London",
        "model": "claude-2"
      },
      "organization_uuid": f"{self.organization_id}",
      "conversation_uuid": f"{conversation_id}",
      "text": f"{prompt}",
      "attachments": attachments
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
      'Cookie': self.cookie,
      'Sec-Fetch-Dest': 'empty',
      'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Site': 'same-origin',
      'TE': 'trailers'
    }

    newChunk = ""
    oldChunk = ""
    seen_lines = set()

    with requests.post( url, headers=headers, data=payload,impersonate="chrome110",timeout=120)  as response:
        for line in response.iter_lines():
            if line:
                # decoded_data = line.content.decode("utf-8")
                # decoded_data = re.sub('\n+', '\n', decoded_data).strip()
                # data = decoded_data.strip().split('\n')

                data = line.lstrip(b"data: ").decode("utf-8")

                # print(data)
                stripped_line = str(data)

                # if stripped_line:
                if stripped_line not in seen_lines:
                    try:
                        decoded_line = json.loads(stripped_line)
                        if stop_reason := decoded_line.get("stop_reason"):
                            # yield '[DONE]'
                            yield ''
                            break
                        else:
                            if completion := decoded_line.get("completion"):
                                openai_response = (
                                    decoded_line
                                )

                                ### Clean Response
                                newChunk = completion
                                yield newChunk.replace(oldChunk, "", 1) if oldChunk in newChunk else oldChunk
                                oldChunk = completion
                            else:
                                errortype = decoded_line.get("error")["type"]
                                if errortype == "rate_limit_error":
                                    yield 'o_o: ' + decoded_line.get("error")["message"] + '\nGive me a few hours rest :)\nCame back at ' + str(datetime.fromtimestamp(decoded_line.get("error")["resets_at"])) + '\n'
                                    return

                    except json.JSONDecodeError as e:
                        print(
                            f"Error decoding JSON: \n{e}"
                        )  # Debug output
                        print(
                            f"Failed to decode line: \n{stripped_line}"
                        )  # Debug output
                    seen_lines.add(stripped_line)

  # Deletes the conversation
  def delete_conversation(self, conversation_id):
    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}"

    payload = json.dumps(f"{conversation_id}")
    headers = {
        'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Content-Length': '38',
        'Referer': 'https://claude.ai/chats',
        'Origin': 'https://claude.ai',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Connection': 'keep-alive',
        'Cookie': self.cookie,
        'TE': 'trailers'
    }

    response = requests.delete( url, headers=headers, data=payload,impersonate="chrome110")

    # Returns True if deleted or False if any error in deleting
    if response.status_code == 204:
      return True
    else:
      return False

  # Returns all the messages in conversation
  def chat_conversation_history(self, conversation_id):
    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}"

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
        'Cookie': self.cookie
    }

    response = requests.get( url, headers=headers,impersonate="chrome110")
    

    # List all the conversations in JSON
    return response.json()

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

    response = requests.post( url, headers=headers, data=payload,impersonate="chrome110")

    # Returns JSON of the newly created conversation information
    return response.json()

  # Resets all the conversations
  def reset_all(self):
    conversations = self.list_all_conversations()

    for conversation in conversations:
      conversation_id = conversation['uuid']
      delete_id = self.delete_conversation(conversation_id)

    return True

  def upload_attachment(self, file_path):
    if file_path.endswith('.txt'):
      file_name = os.path.basename(file_path)
      file_size = os.path.getsize(file_path)
      file_type = "text/plain"
      with open(file_path, 'r', encoding='utf-8') as file:
        file_content = file.read()

      return {
          "file_name": file_name,
          "file_type": file_type,
          "file_size": file_size,
          "extracted_content": file_content
      }
    url = 'https://claude.ai/api/convert_document'
    headers = {
        'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://claude.ai/chats',
        'Origin': 'https://claude.ai',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Connection': 'keep-alive',
        'Cookie': self.cookie,
        'TE': 'trailers'
    }

    file_name = os.path.basename(file_path)
    content_type = self.get_content_type(file_path)

    files = {
        'file': (file_name, open(file_path, 'rb'), content_type),
        'orgUuid': (None, self.organization_id)
    }

    response = req.post(url, headers=headers, files=files)
    if response.status_code == 200:
      return response.json()
    else:
      return False
      

    
  # Renames the chat conversation title
  def rename_chat(self, title, conversation_id):
    url = "https://claude.ai/api/rename_chat"

    payload = json.dumps({
        "organization_uuid": f"{self.organization_id}",
        "conversation_uuid": f"{conversation_id}",
        "title": f"{title}"
    })
    headers = {
        'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/json',
        'Referer': 'https://claude.ai/chats',
        'Origin': 'https://claude.ai',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Connection': 'keep-alive',
        'Cookie': self.cookie,
        'TE': 'trailers'
    }

    response = requests.post(url, headers=headers, data=payload,impersonate="chrome110")

    if response.status_code == 200:
      return True
    else:
      return False
      
