# Free Chatbot API

This project implements a Python web API that provides unified access to three leading AI chatbots - ChatGPT, Google Bard, and Claude - through a single interface. Key features:
- Unified API endpoints allow seamlessly querying ChatGPT, Bard, and Claude from one integration.
- Leverages the free web/browser versions of each AI service by managing cookies and sessions. Avoid costs of paid API access.
- Simple API for text completion, question answering, and conversational queries. Integrate AI capabilities easily.
- Lightweight Python server using FastAPI. Easily self-host locally or on your own infrastructure.
- Modular design makes adding new AI services simple as they become available.
- Open source for customization to your needs.
Rather than directly integrating multiple chatbot APIs, this project provides a simple Python web API to access ChatGPT, Bard, and Claude through one integration. By handling cookies and sessions, it provides free access to the web versions of these AI services for basic usage. This makes it easy to add powerful AI chatbot capabilities to your applications.

<br>

**NOTE:** This project is currently under active development.
- ✅ __*Google*__ Bard integration is fully complete and available now.
- ✅ __*Claude-2*__ integration is also fully implemented and available.
- ⏳ __*ChatGPT*__ integration is in progress and not available yet.

<br>

## Upcoming Tasks
- Implement API responses for ChatGPT.
- Use proxy
- Use multiple accounts



<br>

## Usage

#### How to Find Your Free Tokens
First you need to add your tokens to the **`Config.conf`** file (see **[Configuration](#configuration)** section).

<br>

**Google Bard**

[![Image](assets/Bard-Thumb.jpg)](assets/Bard.jpg)

*`Google Bard:`* Please obtain the cookies mentioned here from an authorized session on bard.google.com. The cookies can be used to
send POST requests to the /bard endpoint along with a message in a JSON payload. It is important that the session_id,
which is your __Secure-1PSID cookie, is included in the request. ([Screenshot](assets/Bard.jpg))


1. Login to [bard.google.com](https://bard.google.com)
2. Open `Developer Tools` (Press **F12**)
3. Go to `Application Tab`
4. Go to `Cookies Tab`
5. Copy the content of `__Secure-1PSID`
6. Set in **[Config.conf](#configuration)** file.

<br><hr><br>

**Claude 2**

[![Image](assets/Claude-Thumb.jpg)](assets/Claude.jpg)

*`Claude:`* You can get cookie from the browser's developer tools network tab ( see for any claude.ai requests check out cookie ,copy whole value ) or storage tab ( You can find cookie of claude.ai ,there will be four values ) ([Screenshot](assets/Claude.jpg))

1. Login to [claude.ai/chats](https://claude.ai/chats)
2. Open `Developer Tools` (Press **F12**)
3. Go to `Network Tab`
4. Select an ajax request (like step 3 in [picture](assets/Claude.jpg))
5. Copy the content of `Cookie`
6. Set in **[Config.conf](#configuration)** file.

<br><hr><br>

**ChatGPT**

*`ChatGPT:`* (Under active development)

<br><hr>

### Prerequisites

(NOTE: This guide is still in progress.)

Before using the APIs, sign up for free accounts to get access credentials if you don't already have them:

- ChatGPT: https://openai.com/api/ 
- Google Bard: https://bard.google.com/
- Claude: https://claude.ai/chat/

Then, add your token(s) to the **`Config.conf`** file. (see **[Configuration](#configuration)** section).

<br>

## Installation

<br>

### Step 1. Clone Repository
```
git clone https://github.com/Amm1rr/Free-Chatbot-API.git
```

<br>

### Step 2. Start Web Server
Navigate into the **`src`** directory clone, then run:

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
    "message": "Hi, Who are you?",
    "stream": True
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

Once you have launched the webserver using **[`uvicorn main:app --reload --port 8000`](#2-start-web-server)** , run this cURL command in a terminal window:

```
curl -X POST "http://127.0.0.1:8000/claude" -H "accept: application/json" -H "Content-Type: application/json" -d '{"session_id":"","message":"Who are you?"}'

```

**Note**: The **`session_id`** is configured in the **[Config.conf](#configconf)** file. If you send this variable empty, it will use the settings.

<br><br>

## Configuration
[How to Find Your Free Tokens](#usage)

#### Config File Path:
- *Linux* :
    - **~/.config/Free_Chatbot_API/Config.conf**
- *Windows* :
    - **.\Config.conf**

``` Config.conf
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

<br>

## Sources

[Google Bard API](https://github.com/ra83205/google-bard-api) | [Claude API](https://github.com/KoushikNavuluri/Claude-API)

<br>

## Licensing

This project is licensed under the MIT License. Feel free to use it however you like.

<br>

[![](https://visitcount.itsvg.in/api?id=amm1rr&label=V&color=0&icon=2&pretty=true)](https://github.com/Amm1rr/)
