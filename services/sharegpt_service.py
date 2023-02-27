import json

import aiohttp


class ShareGPTService:
    def __init__(self):
        self.API_URL = "https://sharegpt.com/api/conversations"

    def format_conversation(
        self, conversation_history, avatar_url="https://i.imgur.com/SpuAF0v.png"
    ):
        # The format is { 'avatarUrl' : <url>, 'items': [ { 'from': 'human', 'text': <text> }, { 'from': 'bot', 'text': <text> } ] } "
        # The conversation history is not in this format, its just in simple alternating human and bot conversation snippets
        conversation = {"avatarUrl": avatar_url, "items": []}
        # The conversation history alternates between human and bot
        # So we need to add the human and bot items to the conversation
        for i in range(len(conversation_history)):
            if i % 2 == 0:
                conversation["items"].append(
                    {"from": "human", "value": conversation_history[i]}
                )
            else:
                conversation["items"].append(
                    {"from": "gpt", "value": conversation_history[i]}
                )

        return json.dumps(conversation)

    async def format_and_share(self, conversation_history, avatar_url=None):
        conversation = self.format_conversation(conversation_history, avatar_url)
        print(conversation)

        headers = {"Content-Type": "application/json"}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.API_URL, data=conversation, headers=headers
            ) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json["id"]
                else:
                    raise ValueError(
                        f"ShareGPT returned an invalid response: {await response.text()}"
                    )
