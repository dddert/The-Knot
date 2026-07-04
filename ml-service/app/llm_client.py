from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings

YANDEX_COMPLETION_URL = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or '').strip()
    raw = re.sub(r'^```json\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start, end = raw.find('{'), raw.rfind('}')
    candidate = raw[start:end + 1] if start >= 0 and end > start else raw
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    try:
        from json_repair import repair_json
        repaired = repair_json(candidate)
        data = json.loads(repaired)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        raise ValueError(f'Could not parse LLM JSON: {exc}') from exc
    raise ValueError('Could not parse LLM JSON object')


class LLMClient:
    @property
    def available(self) -> bool:
        provider = settings.llm_provider.lower().strip()
        if provider == 'yandex':
            return bool(settings.yandex_api_key and settings.yandex_folder_id)
        if provider in {'openai_compatible', 'openai-compatible', 'local'}:
            return bool(settings.openai_compatible_base_url)
        return False

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int = 2200,
        json_mode: bool = False,
    ) -> str:
        provider = settings.llm_provider.lower().strip()
        if provider == 'yandex':
            return await self._complete_yandex(system, user, temperature, max_tokens, json_mode)
        if provider in {'openai_compatible', 'openai-compatible', 'local'}:
            return await self._complete_openai_compatible(system, user, temperature, max_tokens, json_mode)
        raise RuntimeError(f'Unsupported LLM provider: {settings.llm_provider}')

    async def complete_json(self, *, system: str, user: str, max_tokens: int = 2200) -> dict[str, Any]:
        text = await self.complete(system=system, user=user, temperature=0.1, max_tokens=max_tokens, json_mode=True)
        return extract_json_object(text)

    async def _complete_yandex(self, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        if not settings.yandex_api_key or not settings.yandex_folder_id:
            raise RuntimeError('YANDEX_API_KEY and YANDEX_FOLDER_ID are required')
        model = settings.yandex_model.strip()
        if model.startswith('gpt://'):
            model_uri = model
        elif '/' in model:
            model_uri = f'gpt://{settings.yandex_folder_id}/{model}'
        else:
            model_uri = f'gpt://{settings.yandex_folder_id}/{model}/latest'

        payload: dict[str, Any] = {
            'modelUri': model_uri,
            'completionOptions': {
                'stream': False,
                'temperature': temperature,
                'maxTokens': str(max_tokens),
            },
            'messages': [
                {'role': 'system', 'text': system},
                {'role': 'user', 'text': user},
            ],
        }
        if json_mode:
            payload['jsonObject'] = True
        headers = {
            'Authorization': f'Api-Key {settings.yandex_api_key}',
            'Content-Type': 'application/json',
            'x-folder-id': settings.yandex_folder_id,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(YANDEX_COMPLETION_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f'Yandex API error {response.status_code}: {response.text[:2000]}')
        data = response.json()
        alternatives = data.get('alternatives') or (data.get('result') or {}).get('alternatives')
        if not alternatives:
            raise RuntimeError(f'Unexpected Yandex response: {json.dumps(data, ensure_ascii=False)[:2000]}')
        return alternatives[0]['message']['text']

    async def _complete_openai_compatible(self, system: str, user: str, temperature: float, max_tokens: int, json_mode: bool) -> str:
        url = settings.openai_compatible_base_url.rstrip('/') + '/chat/completions'
        payload: dict[str, Any] = {
            'model': settings.openai_compatible_model,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        headers = {
            'Authorization': f'Bearer {settings.openai_compatible_api_key}',
            'Content-Type': 'application/json',
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f'OpenAI-compatible API error {response.status_code}: {response.text[:2000]}')
        data = response.json()
        return data['choices'][0]['message']['content']
