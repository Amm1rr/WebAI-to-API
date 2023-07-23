# AI Assistant API

This project implements a Python web server that provides a unified API for accessing the AI assistants ChatGPT, Google Bard, and Claude.

I designed this web server API specifically for integration with [Shell GPT](https://github.com/TheR1D/shell_gpt/), but it can be used for any purpose. My goal with this project was to make AI access easy and seamless when using [Shell GPT](https://github.com/TheR1D/shell_gpt/).

This API can work with any software or app that needs access to OpenAI, Google Bard, and Claude APIs.

<br>

**NOTE:** This project and guide are under active development.



## Features

- REST API endpoints for querying each AI assistant ChatGPT/Google Bard/Claude
- Leverages the web/browser-based versions of each service
- Relies on cookies/sessions to provide free access

<br>

## Usage

First you need to add your tokens to the `config.conf` file (see [Configuration](#Configuration) section).

**Google Bard:** Please obtain the cookies mentioned here from an authorized session on bard.google.com. The cookies can be used to
send POST requests to the /bard endpoint along with a message in a JSON payload. It is important that the session_id,
which is your __Secure-1PSID cookie, is included in the request.

**Claude:** You can get cookie from the browser's developer tools network tab ( see for any claude.ai requests check out cookie ,copy whole value ) or storage tab ( You can find cookie of claude.ai ,there will be four values ) [(Screenshot)](assets/Claude-Cookie.png)

**ChatGPT:** (Under active development)

<br>

### 0. Prerequirments

(NOTE: This guide is still in progress.)

Before using the APIs, signup to get free access credentials for each one you want to use:

- ChatGPT: https://openai.com/api/ 
- Google Bard: https://bard.google.com/
- Claude: https://claude.ai/chat/

Then, add your tokens to the `config.conf` file (see [Configuration](#Configuration) section).


### 1. Clone Repository
```
git clone https://github.com/Amm1rr/AI-Assistant-API.git
```

### 2. Start Web Server
Navigate into the `src` directory clone, then run:

```
uvicorn main:app --reload --port 8000

```

Available **Endpoints:**
```
Google Bard
http://128.0.0.1:8000/bard


Claude
http://128.0.0.1:8000/claude


ChatGPT
http://128.0.0.1:8000/chatgpt # (Under active development)

```


**Input / Output**
```
Input:
    {
    "session_id": "your-session-id",
    "message": "Hi, Who are you?"
    }

Output: String
```

<br>

## Example
try **Claude** with **cURL**

Once you have launched the webserver using `uvicorn main:app --reload --port 8000` , run this cURL command in a terminal window:
```
curl -X POST "http://127.0.0.1:8000/claude" -H "accept: application/json" -H "Content-Type: application/json" -d '{"session_id":"[Your_Session_ID]","message":"Who are you?"}'

```

<br><br>

## Configuration

#### Config.conf
```
# Case-Sensative

[Claude]
COOKIE=[YOURS]

[ChatGPT]
ACCESS_TOKEN=[YOURS]

[Bard]
SESSION_ID=[YOURS]

```

<br>

## Implementation

(NOTE: This guide is still in progress.)


## Sources

[Google Bard API](https://github.com/ra83205/google-bard-api) | [Claude API](https://github.com/KoushikNavuluri/Claude-API)


## Licensing

This project is licensed under the MIT License. Feel free to use it however you like.

[![](https://visitcount.itsvg.in/api?id=amm1rr&label=V&color=0&icon=2&pretty=true)](https://github.com/Amm1rr/)
