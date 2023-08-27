#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Reverse engineering of Google Bard
"""
import argparse
import json
import random
import re
import string
import os
import time

import requests
from prompt_toolkit import prompt
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def load_proxies():
    proxy_enabled = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    if proxy_enabled:
        return {
            "http": os.getenv("PROXY_HTTP", ""),
            "https": os.getenv("PROXY_HTTPS", ""),
        }
    else:
        return {}


def __create_session() -> PromptSession:
    return PromptSession(history=InMemoryHistory())


def __create_completer(commands: list, pattern_str: str = "$") -> WordCompleter:
    return WordCompleter(words=commands, pattern=re.compile(pattern_str))


def __get_input(
    session: PromptSession = None,
    completer: WordCompleter = None,
    key_bindings: KeyBindings = None,
) -> str:
    """
    Multiline input function.
    """
    return (
        session.prompt(
            completer=completer,
            multiline=True,
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=key_bindings,
        )
        if session
        else prompt(multiline=True)
    )


class ChatbotBard:
    """
    A class to interact with Google Bard.
    Parameters
        session_id: str
            The __Secure-1PSID cookie.
        session_idTS: str
            The __Secure-1PSIDTS cookie.
    """

    __slots__ = [
        "headers",
        "_reqid",
        "SNlM0e",
        "conversation_id",
        "response_id",
        "choice_id",
        "session",
    ]

    def __init__(self, cookies: dict = None, session_id:str = None, session_idTS:str = None, session_idCC:str = None):
        """
        Initialize the ChatbotBard instance.
        
        Note: you should set either the "cookies" parameter or the sessions.
        
        Parameters:
            session_id (str): The __Secure-1PSID cookie value.
            session_idTS (str): The __Secure-1PSIDTS cookie value.
            
        Sets up the request session with headers, cookies, and proxies.
        Configures a pool of 100 connections and 100 max pool size.
        Sets up retry with 5 total retries and exponential backoff.
        Generates a random request ID and initializes the conversation ID, 
        response ID, and choice ID to empty strings. Retrieves the SNlM0e
        value from the Bard website.
        """
        max_retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504]) 
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=max_retries)
        
        self.session = Session()
        self.session.headers = self._get_headers()
        
        if not cookies:
          self.session.cookies.set("__Secure-1PSID", session_id) 
          self.session.cookies.set("__Secure-1PSIDTS", session_idTS)
          self.session.cookies.set("__Secure-1PSIDCC", session_idCC)
        else:
          self.session.cookies.update(cookies)
        
        self.session.proxies = load_proxies()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._reqid = int("".join(random.choices(string.digits, k=4)))
        self.conversation_id = ""
        self.response_id = ""
        self.choice_id = ""
        self.SNlM0e = self.__get_snlm0e()
    
    def _get_headers(self):
        return {
            'authority': 'bard.google.com',
            "Host": "bard.google.com",
            "X-Same-Domain": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://bard.google.com",
            "Referer": "https://bard.google.com/",
            'x-same-domain': '1'
        }
    def __get_snlm0e(self):
        try:
            resp = self.session.get(url="https://bard.google.com/", timeout=10)
        except Exception as e:
            print(f"ERROR: Unable to access the Google Bard website. Please check your internet connection.\n\n{str(e)}")
            return None

        # Find "SNlM0e":"<ID>"
        if resp.status_code != 200:
            print("Error: Failed to retrieve the Google Bard website.")
            # raise Exception("Error: Failed to retrieve the Google Bard website.")
        try:
            # SNlM0e = re.search(r"SNlM0e\":\"(.*?)\"", resp.text).group(1)
            # - OR
            pattern = r"SNlM0e\":\"(.*?)\""
            if match := re.search(pattern, resp.text):
                return match[1]
            print("Error: Session not found.")
            # raise ValueError("Error: Session not found.")
            return None

        except Exception as e:
            # raise ValueError("Maybe it's because of 'SESSION_ID' environment variable for [Bard] key in Config.conf file.")
            print(f"Error: Session error:\n\n{e}")
            return None

    def ask(self, message: str) -> dict:
        """
        Send a message to Google Bard and return the response.
        :param message: The message to send to Google Bard.
        :return: A dict containing the response from Google Bard.
        """
        # url params
        params = {
            "bl": "boq_assistant-bard-web-server_20230326.21_p0",
            "_reqid": str(self._reqid),
            "rt": "c",
        }

        # message arr -> data["f.req"]. Message is double json stringified
        message_struct = [
            [message],
            None,
            [self.conversation_id, self.response_id, self.choice_id],
        ]

        data = {
            "f.req": json.dumps([None, json.dumps(message_struct)]),
            "at": self.SNlM0e,
        }

        # Question
        # print(message)

        # do the request!
        resp = self.session.post(
            "https://bard.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
            params=params,
            data=data,
            timeout=120,
        )

        # Answer
        # print(resp)

        chat_data = json.loads(resp.content.splitlines()[3])[0][2]
        if not chat_data:
            return {"content": f"Google Bard encountered an error: {resp.content}."}
        json_chat_data = json.loads(chat_data)

        results = {
            "content": json_chat_data[5][2],
            "conversation_id": json_chat_data[1][0],
            "response_id": json_chat_data[1][1],
            "factualityQueries": json_chat_data[3],
            "textQuery": json_chat_data[2][0] if json_chat_data[2] is not None else "",
            "choices": [{"id": i[0], "content": i[1]} for i in json_chat_data[4]],
        }
        self.conversation_id = results["conversation_id"]
        self.response_id = results["response_id"]
        self.choice_id = results["choices"][0]["id"]
        self._reqid += 100000
        return results

    def ask_bard(self, message: str) -> dict:
        """
        Send a message to Google Bard and return the response. (FastAPI)
        :param message: The message to send to Google Bard.
        :return: A dict containing the response from Google Bard.
        """
        # url params
        params = {
            "bl": "boq_assistant-bard-web-server_20230326.21_p0",
            "_reqid": str(self._reqid),
            "rt": "c",
        }

        # message arr -> data["f.req"]. Message is double json stringified
        message_struct = [
            [message],
            None,
            [self.conversation_id, self.response_id, self.choice_id],
        ]

        data = {
            "f.req": json.dumps([None, json.dumps(message_struct)]),
            "at": self.SNlM0e,
        }

        # Question
        # print(message)

        # do the request!
        resp = self.session.post(
            "https://bard.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
            params=params,
            data=data,
            timeout=120,
        )

        # Answer
        # print(resp.text)

        chat_data = json.loads(resp.content.splitlines()[3])[0][2]
        if not chat_data:
            return {"content": f"Google Bard encountered an error: {resp.content}."}
        json_chat_data = json.loads(chat_data)

        results = {
            "content": json_chat_data[5][2],
            "conversation_id": json_chat_data[1][0],
            "response_id": json_chat_data[1][1],
            "factualityQueries": json_chat_data[3],
            "textQuery": json_chat_data[2][0] if json_chat_data[2] is not None else "",
            "choices": [{"id": i[0], "content": i[1]} for i in json_chat_data[4]],
        }
        self.conversation_id = results["conversation_id"]
        self.response_id = results["response_id"]
        self.choice_id = results["choices"][0]["id"]
        self._reqid += 100000

        return {
            "choices": [{"message": {"content": results["choices"][0]["content"]}}]
        }

    def ask_bardStream(self, message: str) -> dict:
        """
        Send a message to Google Bard and return the response.
        :param message: The message to send to Google Bard.
        :return: A dict containing the response from Google Bard.
        """

        # for i in range(10):
        #     yield b'some fake data\n'
        #     print(b'some fake data\n')
        #     time.sleep(0.5)
        # return

        # url params
        params = {
            "bl": "boq_assistant-bard-web-server_20230326.21_p0",
            "_reqid": str(self._reqid),
            "rt": "c",
        }

        url = "https://bard.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"

        # message arr -> data["f.req"]. Message is double json stringified
        message_struct = [
            [message],
            None,
            [self.conversation_id, self.response_id, self.choice_id],
        ]

        data = {
            "f.req": json.dumps([None, json.dumps(message_struct)]),
            "at": self.SNlM0e,
        }

        # Question
        # print(message)

        with self.session.post(
                url, params=params, data=data, timeout=120, stream=True
            ) as response:
            chat_data = json.loads(response.content.splitlines()[3])[0][2]
            if not chat_data:
                return {
                    "content": f"Google Bard encountered an error: {response.content}."
                }
            json_chat_data = json.loads(chat_data)

            results = {
                "content": json_chat_data[5][2],
                "conversation_id": json_chat_data[1][0],
                "response_id": json_chat_data[1][1],
                "factualityQueries": json_chat_data[3],
                "textQuery": json_chat_data[2][0]
                if json_chat_data[2] is not None
                else "",
                "choices": [{"id": i[0], "content": i[1]} for i in json_chat_data[4]],
            }
            self.conversation_id = results["conversation_id"]
            self.response_id = results["response_id"]
            self.choice_id = results["choices"][0]["id"]
            self._reqid += 100000

            json_data = {
                "choices": [{"message": {"content": results["choices"][0]["content"]}}]
            }

            yield json_data["choices"][0]["message"]["content"][0]


# if __name__ == "__main__":
#     print(
#         """
#         Bard - A command-line interface to Google's Bard (https://bard.google.com/)
#         Repo: github.com/acheong08/Bard
#         Enter `alt+enter` or `esc+enter` to send a message.
#         """,
#     )
#     parser = argparse.ArgumentParser()
#     parser.add_argument(
#         "--session",
#         help="__Secure-1PSID cookie.",
#         type=str,
#         required=True,
#     )
#     args = parser.parse_args()

#     chatbot = ChatbotBard(args.session)
#     prompt_session = __create_session()
#     completions = __create_completer(["!exit", "!reset"])
#     console = Console()
#     try:
#         while True:
#             console.print("You:")
#             user_prompt = __get_input(session=prompt_session, completer=completions)
#             console.print()
#             if user_prompt == "!exit":
#                 break
#             elif user_prompt == "!reset":
#                 chatbot.conversation_id = ""
#                 chatbot.response_id = ""
#                 chatbot.choice_id = ""
#                 continue
#             print("Google Bard:")
#             response = chatbot.ask(user_prompt)
#             console.print(Markdown(response["content"]))
#             print()
#     except KeyboardInterrupt:
#         print("Exiting...")
