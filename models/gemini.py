from gemini_webapi import GeminiClient as WebGeminiClient

class MyGeminiClient:
    def __init__(self, secure_1psid, secure_1psidts):
        self.client = WebGeminiClient(secure_1psid, secure_1psidts)

    async def init(self):
        await self.client.init()

    async def generate_content(self, message, model, images=None):
        if images:
            response = await self.client.generate_content(message, model=model, images=images)
        else:
            response = await self.client.generate_content(message, model=model)
        return response

    async def close(self):
        await self.client.close()