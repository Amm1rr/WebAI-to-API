#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Reverse engineering of Google Gemini
"""
import argparse
import json
import random
import re
import string
import os
import time

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

import utility

def GeminiInit():
    # Execute code without authenticating the resource
    session_id = None #message.session_id
    session_idTS = None #message.session_idTS
    session_idCC = None #message.session_idCC
    # if not utility.IsSession(session_id):
    #     session_id = os.getenv("SESSION_ID")
    #     # print("Session: " + str(session_id) if session_id is not None else "Session ID is not available.")
    COOKIE_GEMINI = None
    
    gemini = None
    if not (session_id or session_idTS or session_idCC):
        cookies = ChatbotGemini.get_session_id_Gemini()
        if type(cookies) == dict:
            gemini = ChatbotGemini(cookies)
        else:
            gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
        
    else:
        gemini = ChatbotGemini(session_id=session_id, session_idTS=session_idTS, session_idCC=session_idCC)
    
    return gemini

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


class ChatbotGemini:
    """
    A class to interact with Google Gemini.
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
        Initialize the ChatbotGemini instance.
        
        Note: you should set either the "cookies" parameter or the sessions.
        
        Parameters:
            session_id (str): The __Secure-1PSID cookie value.
            session_idTS (str): The __Secure-1PSIDTS cookie value.
            
        Sets up the request session with headers, cookies, and proxies.
        Configures a pool of 100 connections and 100 max pool size.
        Sets up retry with 5 total retries and exponential backoff.
        Generates a random request ID and initializes the conversation ID, 
        response ID, and choice ID to empty strings. Retrieves the SNlM0e
        value from the Gemini website.
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
            'authority': 'gemini.google.com',
            "Host": "gemini.google.com",
            "X-Same-Domain": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            'x-same-domain': '1'
        }
    def __get_snlm0e(self):
        try:
            resp = self.session.get(url="https://gemini.google.com/", timeout=10)
        except Exception as e:
            print(f"ERROR: Unable to access the Google Gemini website. Please check your internet connection.\n\n{str(e)}")
            return None

        # Find "SNlM0e":"<ID>"
        if resp.status_code != 200:
            print("Error: Failed to retrieve the Google Gemini website.")
            # raise Exception("Error: Failed to retrieve the Google Gemini website.")
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
            # raise ValueError("Maybe it's because of 'SESSION_ID' environment variable for [Gemini] key in Config.conf file.")
            print(f"Error: Session error:\n\n{e}")
            return None
    
    def get_session_id_Gemini(sessionId: str = "SESSION_ID"):
        """Get the session ID for Gemini.

        Args:
            sessionId (str, optional): The session ID to get. Defaults to "SESSION_ID".

        Returns:
            str: The session ID.
        """
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE_PATH)
            sess_id = config.get("Germini", sessionId)

        except Exception as e:
            # print(e)
            sess_id = None

        if not sess_id:
            sessions = utility.get_cookies("google.com")
            return sessions
        else:
            session_name = "Bard" if sessionId == "SESSION_ID" else ("BardTS" if sessionId == "SESSION_DTS" else "BardCC")
            sess_id =  utility.get_Cookie(session_name)
                
            if not IsSession(sess_id):
                print(f"You should set {sessionId} for Gemini in {CONFIG_FILE_NAME}")

            return sess_id

    def ask_gemini(self, message: str) -> dict:
        """
        Send a message to Google Gemini and return the response. (FastAPI)
        :param message: The message to send to Google Gemini.
        :return: A dict containing the response from Google Gemini.
        """
        # url params
        params = {
            "bl": "boq_assistant-bard-web-server_20240403.10_p0",
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
            "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
            params=params,
            data=data,
            timeout=120,
        )

        # Answer
        # print(resp.text)

        chat_data = json.loads(resp.content.splitlines()[3])[0][2]
        if not chat_data:
            return {"content": f"Google Gemini encountered an error: {resp.content}."}
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

        # return {
        #     "choices": [{"message": {"content": results["choices"][0]["content"]}}]
        # }
        print(results["choices"][0]["content"][0])
        return results["choices"][0]["content"][0]

    async def ask_geminiStream(self, message: str) -> dict:
        """
        Send a message to Google Gemini and return the response.
        :param message: The message to send to Google Gemini.
        :return: A dict containing the response from Google Gemini.
        """

        # for i in range(10):
        #     yield b'some fake data\n'
        #     print(b'some fake data\n')
        #     time.sleep(0.5)
        # return

        # url params
        params = {
            "bl": "boq_assistant-bard-web-server_20240403.10_p0",
            "_reqid": str(self._reqid),
            "rt": "c",
        }

        url = "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"

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

        answer = ""
        with await httpx.stream("POST", url, params=params, data=data) as r:
            for text in r.iter_text():
                response_parse_text = text

                text_res = ""
                if response_parse_text:
                    for text in response_parse_text:
                        text_res += text

                answer = ''.join(text_res)
                print(answer)
            
                yield answer

        # with self.session.post(
        #         url, params=params, data=data, timeout=120, stream=True
        #     ) as response:
        #     chat_data = json.loads(response.content.splitlines()[3])[0][2]
        #     if not chat_data:
        #         return {
        #             "content": f"Google Gemini encountered an error: {response.content}."
        #         }
        #     json_chat_data = json.loads(chat_data)

        #     results = {
        #         "content": json_chat_data[5][2],
        #         "conversation_id": json_chat_data[1][0],
        #         "response_id": json_chat_data[1][1],
        #         "factualityQueries": json_chat_data[3],
        #         "textQuery": json_chat_data[2][0]
        #         if json_chat_data[2] is not None
        #         else "",
        #         "choices": [{"id": i[0], "content": i[1]} for i in json_chat_data[4]],
        #     }
        #     self.conversation_id = results["conversation_id"]
        #     self.response_id = results["response_id"]
        #     self.choice_id = results["choices"][0]["id"]
        #     self._reqid += 100000

        #     json_data = {
        #         "choices": [{"message": {"content": results["choices"][0]["content"]}}]
        #     }

        #     print(json_data["choices"][0]["message"]["content"][0])
        #     yield json_data["choices"][0]["message"]["content"][0]


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument(
#         "--session",
#         help="__Secure-1PSID cookie.",
#         type=str,
#         required=True,
#     )
#     args = parser.parse_args()

#     chatbot = ChatbotGemini(args.session)
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
#             print("Google Gemini:")
#             response = chatbot.ask(user_prompt)
#             console.print(Markdown(response["content"]))
#             print()
#     except KeyboardInterrupt:
#         print("Exiting...")
