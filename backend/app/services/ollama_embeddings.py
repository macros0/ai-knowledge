"""Native Ollama embedding calls with a per-request total deadline.

The synchronous API is also used from asynchronous routes. Each call owns and
closes its HTTP client; no shared LiteLLM transport or event loop is modified.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import math

import httpx


async def _request(url, model, inputs, timeout_seconds, dimensions, num_batch=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    async with asyncio.timeout(timeout_seconds):
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            payload = {'model': model, 'input': inputs, 'truncate': False}
            if num_batch is not None:
                payload['options'] = {'num_batch': num_batch}
            response = await client.post(url, json=payload)
            response.raise_for_status()
            vectors = response.json().get('embeddings')
            if (not isinstance(vectors, list) or len(vectors) != len(inputs)
                    or any(not isinstance(vector, list) or len(vector) != dimensions
                           or any(type(value) not in (int, float) or not math.isfinite(value)
                                  for value in vector) for vector in vectors)):
                raise ValueError('Ollama embedding shape or values are invalid')
    # JSON decoding is synchronous: reject a late success even if its work
    # prevented the event loop from delivering the cancellation callback.
    if loop.time() > deadline:
        raise TimeoutError('Ollama embedding total deadline exceeded')
    return vectors


def embed_ollama(*, api_base, model, inputs, timeout_seconds, dimensions, num_batch=None):
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0):
        raise ValueError('Ollama embedding deadline must be positive and finite')
    if num_batch is not None and (type(num_batch) is not int or not 0 < num_batch <= 32768):
        raise ValueError('Ollama physical batch must be a positive integer up to 32768')
    url = api_base.rstrip('/')
    if not url.endswith('/api/embed'):
        url += '/api/embed'
    native_model = model.removeprefix('ollama/')
    def invoke():
        return asyncio.run(_request(url, native_model, inputs, timeout_seconds, dimensions, num_batch))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return invoke()
    # A synchronous call inside an async route cannot nest asyncio.run(). The
    # worker performs bounded async IO and is joined before returning/raising.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ollama-embedding') as worker:
        return worker.submit(invoke).result()
