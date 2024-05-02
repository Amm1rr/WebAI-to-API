#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, os, uuid
from curl_cffi import requests
import re
from datetime import datetime
import httpx
import asyncio

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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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

    def parse_text(text):

      try:
        # print(text)
        parsed_response = json.loads(text)
        if 'error' in parsed_response:
            error_message = parsed_response['error']['message']
            print("Error Message:", error_message)
            
            return json.dumps(error_message)
            

      except json.JSONDecodeError:
          # print("Invalid JSON format:", response)
          events = []
          lines = text.split('\n')
          for line in lines:
              line = line.strip()
              # print(line)
              if line:
                  parts = line.split(': ')
                  if len(parts) == 2:
                      event_type, data = parts
                      if data != 'completion' and data != 'ping':
                        event_data = json.loads(data)
                        events.append(event_data['completion'])

          return events
    
    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion" 

    payload = json.dumps({
        "prompt": prompt,
        "timezone": "Europe/London",
        # "model": f"claude-{self.model_version}",
        "model": "claude-3-sonnet-20240229",
        # claude-3-haiku-20240307
        # claude-3-opus-20240229
        "attachments": [],
        "files": []
    })


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

    headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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

    # response = requests.post( url, headers=headers, data=payload,impersonate="chrome110",timeout=120)
    response = httpx.post( url, headers=headers, data=payload, timeout=120)

    response_parse_text = parse_text(response.content.decode('utf-8'))
    
    text_res = ""
    if response_parse_text:
      for text in response_parse_text:
        text_res += text
    
    answer = ''.join(text_res).strip()
    # print(answer)
    return answer

  # Send and Response Stream Message to Claude
  async def stream_message(self, prompt, conversation_id, attachment=None, timeout=120):

    async def parse_text(text):
        try:
            parsed_response = json.loads(text)
            if 'error' in parsed_response:
                error_message = parsed_response['error']['message']
                print("Error Message:", error_message)
                return error_message
        except json.JSONDecodeError:
            events = []
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    parts = line.split(': ')
                    if len(parts) == 2:
                        event_type, data = parts
                        if data != 'completion' and data != 'ping':
                          try:
                            event_data = json.loads(data)
                            events.append(event_data['completion'])
                          except json.JSONDecodeError:
                            # print("CLAUDE STREAM EXCEPT: ", data)
                            pass
                          
            # print(events)
            return events

    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}/completion"

    # Upload attachment if provided
    attachments = []
    if attachment:
      attachment_response = self.upload_attachment(attachment)
      if attachment_response:
        attachments = [attachment_response]
      else:
        yield {"Error: Invalid file format. Please try again."}


    # Ensure attachments is an empty list when no attachment is provided
    if not attachment:
      attachments = []

    payload = json.dumps({
      "attachments": attachments,
      "files": [],
      "model": "claude-3-sonnet-20240229",
      "timezone": "Europe/London",
      "prompt": f"{prompt}"
    })

    headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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

    answer = ""
    with httpx.stream("POST", url, headers=headers, data=payload) as r:
      for text in r.iter_text():
        response_parse_text = await parse_text(text)

        text_res = ""
        if response_parse_text:
            for text in response_parse_text:
                text_res += text
                yield text

        # answer = ''.join(text_res)
        # print(answer)
    
        # yield answer
        await asyncio.sleep(0)

  # Deletes the conversation
  def delete_conversation(self, conversation_id):
    url = f"https://claude.ai/api/organizations/{self.organization_id}/chat_conversations/{conversation_id}"

    payload = json.dumps(f"{conversation_id}")
    headers = {
        'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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

    # response = requests.post( url, headers=headers, data=payload,impersonate="chrome110")
    response = httpx.post( url, headers=headers, data=payload)

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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/124.0',
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
      
