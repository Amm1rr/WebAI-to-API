import browser_cookie3
import time
import configparser
import json
from typing import Literal

_cookies = {}
def get_cookies(cookie_domain: str) -> dict: 
    if cookie_domain not in _cookies: 
        _cookies[cookie_domain] = {} 
        for cookie in browser_cookie3.load(cookie_domain): 
            _cookies[cookie_domain][cookie.name] = cookie.value 
    return _cookies[cookie_domain]

def get_Cookie(service_Name: Literal["Bard", "BardTS", "BardCC", "Claude"]) -> str:
    """
    Retrieve and return the session cookie value for the specified service.

    This function takes a service name as input, either 'Bard', 'BardTS', or 'Claude',
    and retrieves the corresponding session cookie value from the browser's stored cookies.
    The cookie value is then returned.

    Note: This function requires the 'browser_cookie3' library to be installed.

    Args:
        service_name (Literal["Bard", "BardTS", "Claude"]): The name of the service
            for which to retrieve the session cookie.

    Returns:
        str: The session cookie value for the specified service, or None if no matching
            cookie is found.
    """

    domains = {
        "Bard": "google",
        "BardTS": "google",
        "BardCC": "google",
        "Claude": "claude",
    }
    domain = domains[service_Name]

    if service_Name.lower() == "bardts":
        geminiSessionName = "__Secure-1PSIDTS"
    elif service_Name.lower() == "bardcc":
        geminiSessionName = "__Secure-1PSIDCC"
    else:
        geminiSessionName = "__Secure-1PSID"

    sessName = {
        "claude": "sessionKey",
        "google": geminiSessionName,
    }
    sessionName = sessName[domain]

    cookies = browser_cookie3.load(domain_name=domain)

    return (
        filtered_cookies[-1].value
        if (
            filtered_cookies := [
                cookie
                for cookie in cookies
                if sessionName == cookie.name
            ]
        )
        else None
    )


def find_all_cookie_values_for_sessions():
    domains = ["google", "claude"]
    sessions = ["__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC", "sessionKey"]

    found_items = []
    for domainname in domains:
        cookies = browser_cookie3.load(domain_name=domainname)

        for cookie in cookies:
            for session in sessions:
                if cookie.name == session:
                    found_items.append((cookie.name, cookie.value))

    # print("Found Items: ", found_items)
    json_found_items = json.dumps(found_items)
    return json_found_items

def getCookie_Gemini(configfilepath: str, configfilename: str):
    try:
        cookie = get_Cookie("google")
        if not cookie:
            raise Exception()
        return cookie
    except Exception as _:
        domain = ".google"
        sessions = ["__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC"]

        found_items = []
        cookies = browser_cookie3.load(domain_name=domain)

        if not cookies:
            return  {
                "Error": f"Looks like you're not logged in to Gemini. Please either set the Gemini cookie manually on '{configfilename}' or log in to your gemini.google.com account through your web browser."
            }
        
        for cookie in cookies:
            for session in sessions:
                if cookie.name == session:
                    found_items.append((cookie.name, cookie.value))

        # print("Found Items: ", found_items)
        json_found_items = json.dumps(found_items)
        return json_found_items

def getCookie_Claude(configfilepath: str, configfilename: str):
    # if error by system(permission denided)
    try:
        cookie = get_Cookie("Claude")
        if not cookie:
            raise Exception()
        return cookie
    except Exception as _:
        config = configparser.ConfigParser()
        config.read(filenames=configfilepath)
        cookie = config.get("Claude", "COOKIE")
        if not cookie:
            response_error = {
                "Error": f"You should set 'COOKIE' in '{configfilename}' file for the Claude or login with a browser to Claude.ai account."
            }

            print(response_error)
            return response_error
                        # raise ValueError(
                        #     f"You should set 'COOKIE' in '{CONFIG_FILE_NAME}' file for the Claude or login with a browser to Claude.ai account."
                        # )
        else:
            return cookie

def ConvertToChatGPT(message: str, model: str):
    """Convert response to ChatGPT JSON format.

    Args:
        message (String): Response string.
        model (String): Model name string.

    Yields:
        str: JSON response chunks.
    """

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": message,
                },
                "index": 0,
                "finish_reason": "Stop",
            }
        ],
    }

    # openairesp = {
    # "id": f"chatcmpl-{str(time.time())}",
    # "object": "chat.completion.chunk",
    # "created": int(time.time()),
    # "model": "gpt-3.5-turbo",
    # "choices": [
    #     {
    #         "message": {
    #             "role": "assistant",
    #             "content": resp,
    #         },
    #         "index": 0,
    #         "finish_reason": "stop",
    #     }
    # ],

    # jsonresp = json.dumps(OpenAIResp)

    return f"{OpenAIResp}"
    # return json.dumps(OpenAIResp)

async def ConvertToChatGPTStream(message: str, model: str):
    """Convert response to ChatGPT JSON format.

    Args:
        message (String): Response string.
        model (String): Model name string.

    Yields:
        str: JSON response chunks.
    """

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": message,
                },
                "index": 0,
                "finish_reason": "Stop",
            }
        ],
    }

    # openairesp = {
    # "id": f"chatcmpl-{str(time.time())}",
    # "object": "chat.completion.chunk",
    # "created": int(time.time()),
    # "model": "gpt-3.5-turbo",
    # "choices": [
    #     {
    #         "message": {
    #             "role": "assistant",
    #             "content": resp,
    #         },
    #         "index": 0,
    #         "finish_reason": "stop",
    #     }
    # ],

    # jsonresp = json.dumps(OpenAIResp)

    yield f"{OpenAIResp}"
    # yield json.dumps(OpenAIResp)
    

async def claudeToChatGPTStream(message: str, model: str):
    """Convert response to ChatGPT JSON format.

    Args:
        message (String): Response string.
        model (String): Model name string.

    Yields:
        str: JSON response chunks.
    """

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": message,
                },
                "index": 0,
                "finish_reason": "Stop",
            }
        ],
    }

    # openairesp = {
    # "id": f"chatcmpl-{str(time.time())}",
    # "object": "chat.completion.chunk",
    # "created": int(time.time()),
    # "model": "gpt-3.5-turbo",
    # "choices": [
    #     {
    #         "message": {
    #             "role": "assistant",
    #             "content": resp,
    #         },
    #         "index": 0,
    #         "finish_reason": "stop",
    #     }
    # ],

    # jsonresp = json.dumps(OpenAIResp)

    yield f"{OpenAIResp}"
    # yield json.dumps(OpenAIResp)

async def geminiToChatGPTStream(message: str, model: str):
    """Convert response to ChatGPT JSON format.

    Args:
        message (String): Response string.
        model (String): Model name string.

    Yields:
        str: JSON response chunks.
    """

    OpenAIResp = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": message,
                },
                "index": 0,
                "finish_reason": "Stop",
            }
        ],
    }

    # openairesp = {
    # "id": f"chatcmpl-{str(time.time())}",
    # "object": "chat.completion.chunk",
    # "created": int(time.time()),
    # "model": "gpt-3.5-turbo",
    # "choices": [
    #     {
    #         "message": {
    #             "role": "assistant",
    #             "content": resp,
    #         },
    #         "index": 0,
    #         "finish_reason": "stop",
    #     }
    # ],

    # jsonresp = json.dumps(OpenAIResp)

    yield f"{OpenAIResp}"
    # yield json.dumps(OpenAIResp)


def IsSession(session_id: str) -> bool:
    """Checks if a valid session ID is provided.

    Args:
        session_id (str): The session ID to check

    Returns:
        bool: True if session ID is valid, False otherwise
    """

    # if session_id is None or not session_id or session_id.lower() == "none":
    if session_id is None:
        return False
    return False if not session_id else session_id.lower() != "none"

def ConfigINI_to_Dict(filepath:str):
    config_object = configparser.ConfigParser()
    file =open(filepath,"r")
    config_object.read_file(file)
    file.close()
    output_dict=dict()
    sections=config_object.sections()
    for section in sections:
        items=config_object.items(section)
        output_dict[section]=dict(items)

    return output_dict

#############################################
####                                     ####
#####        Develope Functions         #####
####                                     ####

# print("".join(response))


def fake_data_streamer_OLD():
    for _ in range(10):
        yield b"some fake data\n"
        time.sleep(0.5)


def fake_data_streamer():
    openai_response = {
        "id": f"chatcmpl-{str(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-3.5-turbo",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 100,
            "total_tokens": 100,
        },
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "content": "Yes",
                },
                "index": 0,
                "finish_reason": "[DONE]",
            }
        ],
    }
    for _ in range(10):
        yield f"{openai_response}\n"
        # yield b"some fake data\n"
        time.sleep(0.5)

