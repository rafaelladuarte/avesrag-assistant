import requests


class GroqAPI:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def send_prompt(self, prompt):
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        response.raise_for_status()

        return response.json()["choices"][0]["message"]["content"]
