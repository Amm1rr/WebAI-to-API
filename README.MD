# Free Chatbot API

This project implements a Python web server that provides a free unified API for accessing the AI assistants ChatGPT, Google Bard, and Claude.

This API can work with any software or app that needs access to OpenAI, Google Bard, and Claude-2 APIs.

I designed this web server API specifically for integration with [Shell GPT](https://github.com/TheR1D/shell_gpt/), but it can be used for any purpose. My goal with this project was to make AI access easy and seamless when using [shell-gpt](https://github.com/TheR1D/shell_gpt/).


<br>

**NOTE:** This project is currently under active development.

<br>

## Features

- API endpoints to query ChatGPT, Google Bard, and Claude
- Uses the web/browser versions of each AI service
- Uses cookies and sessions for free access

<br>

## Upcoming Tasks

- Implement streaming API responses to improve response time and interactivity.
    - ~~Claude~~    ✅ done
    - Bard
    - ChatGPT


<br>

## Usage

#### Get the tokens: (Free)
First you need to add your tokens to the `Config.conf` file (see [Configuration](#configuration) section).

<br>

**Google Bard**

[![Image](assets/Bard-Thumb.jpg)](assets/Bard.jpg)

`Google Bard:` Please obtain the cookies mentioned here from an authorized session on bard.google.com. The cookies can be used to
send POST requests to the /bard endpoint along with a message in a JSON payload. It is important that the session_id,
which is your __Secure-1PSID cookie, is included in the request. ([Screenshot](assets/Bard.jpg))


1. Login to [bard.google.com](https://bard.google.com)
2. Open `Developer Tools` (Press F12)
3. Go to `Application Tab`
4. Go to `Cookies Tab`
5. Copy the content of `__Secure-1PSID`

<br><hr><br>

**Claude 2**

[![Image](assets/Claude-Thumb.jpg)](assets/Claude.jpg)

`Claude:` You can get cookie from the browser's developer tools network tab ( see for any claude.ai requests check out cookie ,copy whole value ) or storage tab ( You can find cookie of claude.ai ,there will be four values ) ([Screenshot](assets/Claude.jpg))

1. Login to [claude.ai/chats](https://claude.ai/chats)
2. Open `Developer Tools` (Press F12)
3. Go to `Network Tab`
4. Select a ajax request (like step 3 in [picture](assets/Claude.jpg))
5. Copy the content of `Cookie`

<br><hr><br>

**ChatGPT**

`ChatGPT:` (Under active development)

<br><hr>

### Prerequisites

(NOTE: This guide is still in progress.)

Before using the APIs, sign up for free accounts to get access credentials if you don't already have them:

- ChatGPT: https://openai.com/api/ 
- Google Bard: https://bard.google.com/
- Claude: https://claude.ai/chat/

Then, add your token(s) to the `config.conf` file. (see [Configuration](#configuration) section).

<br>

## Installation

<br>

### Step 1. Clone Repository
```
git clone https://github.com/Amm1rr/Free-Chatbot-API.git
```

<br>

### Step 2. Start Web Server
Navigate into the `src` directory clone, then run:

```
uvicorn main:app --reload --port 8000

```

<br>

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
_____

    {
    "session_id": "your-session-id",
    "message": "Hi, Who are you?"
    }

--------------------
--------------------

Output:
_____

    String

```

<br>

## Example
try **Claude** with **cURL**

Once you have launched the webserver using [`uvicorn main:app --reload --port 8000`](#2-start-web-server) , run this cURL command in a terminal window:

```
curl -X POST "http://127.0.0.1:8000/claude" -H "accept: application/json" -H "Content-Type: application/json" -d '{"session_id":"","message":"Who are you?"}'

```

Note: The `session_id` is configured in the [Config.conf](#configconf) file. If you send this variable empty, it will use the settings.

<br><br>

## Configuration
[How to get Tokens](#usage)

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
