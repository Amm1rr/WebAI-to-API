     # # # # # # # # # # # # # # # # # # # # # #
    #                                         #
   #    This file is for development and     #
  #         debugging purposes only.        #
 #                                         #
# # # # # # # # # # # # # # # # # # # # # #

import requests
import sys
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# url = "http://127.0.0.1:8000/chatgpt"
# url = "http://localhost:8000/claude"
# url = "http://localhost:8000/bard"
ai = "chatgpt"
ai = "v1/chat/completions"
url = f"http://localhost:8000/{ai}"


## Argument for stream if available
#
stream = True
if len(sys.argv) > 1:
    arg1 = sys.argv[1]  # The first argument
    stream = arg1.upper() == "TRUE"
data = {
    "messages": "I'm David, What is your name?",
    "message": "I'm David, What is your name?",
    "model": "gpt-3.5-turbo-030",
    "temperature": 0.5,
    "top_p": 0.5,
    "stream": stream,
    "session_id": "",
}
endpoint = url


def is_ValidJSON(jsondata=any) -> bool:
    try:
        json.loads(jsondata)
        return True
    except:
        return False


response = requests.post(
    endpoint,
    headers={"Content-Type": "application/json", "Authorization": "Bearer "},
    json=data,
    timeout=360,
    stream=stream,
)
response.raise_for_status()

if not stream:
    data = response.json()
    print(data)
    # print(data["choices"][0]["message"]["content"])  # type: ignore
    exit()
for line in response.iter_lines():
    # data = line.lstrip(b"data: ").decode("utf-8")
    # if data == "[DONE]":  # type: ignore
    #     break
    # if not data:
    #     continue
    # data = json.loads(data)  # type: ignore
    # delta = data["choices"][0]["delta"]  # type: ignore
    # if "content" not in delta:
    #     continue
    # print(delta["content"])
    data = line.lstrip(b"data: ").decode("utf-8")
    if data == "[DONE]":  # type: ignore
        break
    if not data:
        continue
    data = json.loads(data)  # type: ignore
    print(data)
    delta = data["choices"][0]["delta"]  # type: ignore
    if "content" not in delta:
        continue
    # print(delta["content"])

exit()

# application/json
with requests.post(endpoint, headers={"Content-Type": "application/json", "Authorization": "Bearer "}, json=data, timeout=120, stream=stream) as response:
    save = ""
    for line in response.iter_lines():
        # data = response.json
        data = line.lstrip(b"data: ").decode("utf-8")
        # print(data)
        if data == "[DONE]":  # type: ignore
            break
        if not data:
            continue
        data = json.dumps(data)  # type: ignore
        print(type(data))
        print(data)
        try:
            delta = data["choices"][0]["delta"]  # type: ignore
        except:
            delta = data["choices"][0]["message"]["content"]
        if "content" not in delta:
            continue
        print(delta["content"])
        continue

            # try:
            #     data = line.lstrip(b"data: ").decode("utf-8")
            #     jsondata = json.dumps(data)
            # except:
            #     save += line
            #     # print(save + "\n\n")
            #     if is_ValidJSON(save):
            #         jsstring = json.loads(save)
            #         content = jsstring["choices"][0]["delta"]["content"]
            #         print(f"Save: {content}")
            #         save = ""
            #     else:
            #         # print("Invalid JSON format in line:", line)
            #         pass

    #     for line in response.iter_lines(decode_unicode=True):
    #         if line:
    #             try:
    #                 jsstring = json.loads(line)
    #                 print(f"Valid: {jsstring}")
    #             except:
    #                 save+=line
    #                 # print(save + "\n\n")
    #                 if is_ValidJSON(save):
    #                      jsstring = json.loads(save)
    #                      content = jsstring["choices"][0]["delta"]["content"]
    #                      print(f"Save: {content}")
    #                      save=""
    #                 # print("Invalid JSON format in line:", line)
    # # except requests.exceptions.RequestException as e:
    # #     print(f"Error: {e}")
    # # except:
    # #     print(f"Error: !")


# for line in response.iter_lines():
#     data = line.lstrip(b"data: ").decode("utf-8")
#     jsstring = json.dumps(data, indent=2)
#     jso = json.loads(jsstring)
#     jm = response.json
#     print(jso)
# print(jsstring)

# jso = json.loads(js)
# print(jso["id"])
# print(jso["choices"][0]["delta"]["content"])

# jso = json.loads(js)
# print(jso["id"])

# # response.raise_for_status()


# # TODO: Optimise.
# # https://github.com/openai/openai-python/blob/237448dc072a2c062698da3f9f512fae38300c1c/openai/api_requestor.py#L98
# if not stream:
#     data = response.json()
#     print(data["choices"][0]["delta"]["content"])  # type: ignore
#     exit
# for line in response.iter_lines():

#     print(line)

#     data = line.lstrip(b"data: ").decode("utf-8")
#     if not data:
#         continue
#     if data == "[DONE]":  # type: ignore
#         break
#     try:
#         pass
#         # print(data)
#         # data = json.loads(data)  # type: ignore
#         # print(f"{data}\n\n")
#         # delta = data["choices"][0]["delta"]  # type: ignore
#         # if "content" not in delta:
#         #     continue
#         # print(delta["content"])
#     except TypeError as e:
#         pass

# exit

# ###
# ###

# data = {
#     "message": "3+6= ?",
#     "stream": True,
#     "session_id": "",
# }
# if data["stream"] == True:
#     with requests.post(url, json=data, stream=True) as response:
#         try:
#             for line in response.iter_lines(decode_unicode=True):
#                 if line:
#                     try:
#                         print(line)
#                     except json.JSONDecodeError:
#                         print("Invalid JSON format in line:", line)
#         except requests.exceptions.RequestException as e:
#             print(f"Error: {e}")
#         except:
#             print(f"Error: !")

# else:
#     try:
#         response = requests.post(url, json=data, stream=False)
#         # response.raise_for_status()  # Raise an exception if the request was not successful

#         result = response.json()
#         print(result)
#         # return result["response"]

#         # Check if the response is successful (status code 200)
#         # if response.status_code == 200:
#         #     pass
#         # else:
#         #     print(f"Error: {response.status_code} - {response.text}")

#     except requests.exceptions.RequestException as e:
#         print(f"Error occurred: {e}")

#     # result = response.json()
#     # print(result["response"])


# # import requests
# # import sseclient

# # # Replace these values with the actual session_id and message you want to send
# # session_id = "your_session_id"
# # message_text = "Hello, Claude!"

# # # Assuming the API is running locally at http://127.0.0.1:8000
# # base_url = "http://127.0.0.1:8000"
# # endpoint = "/claude"

# # url = f"{base_url}{endpoint}"

# # # Prepare the request payload
# # payload = {
# #     "session_id": session_id,
# #     "message": message_text
# # }

# # # Make the POST request to the /claude endpoint with stream=True
# # response = requests.post(url, json=payload, stream=True)

# # # Check if the response is successful (status code 200)
# # if response.status_code == 200:
# #     # Use the sseclient library to parse the stream of events
# #     client = sseclient.SSEClient(response.iter_lines())
# #     for event in client.events():
# #         # Parse the SSE event data (JSON) and handle it as needed
# #         data = event.data
# #         print("Received SSE event:", data)
# # else:
# #     print(f"Error: {response.status_code} - {response.text}")
