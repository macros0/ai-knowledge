"""Small measurement helpers; no application behavior or external state changes."""
from contextlib import contextmanager
from copy import deepcopy


def model_identity(body):
    """Keep provider model metadata, excluding the observed response clock."""
    if (not isinstance(body, dict) or not isinstance(body.get('data'), list)
            or not body['data'] or any(not isinstance(entry, dict)
            or not isinstance(entry.get('id'), str) or not entry['id'] for entry in body['data'])):
        raise ValueError('Provider model identity is unavailable')
    result = deepcopy(body)
    for entry in result['data']:
        entry.pop('created', None)
    return result


@contextmanager
def instrument_query_clients(clients, completed):
    """Count a shared client's successful call once; restore all aliases."""
    originals = {}
    try:
        for client in clients:
            if id(client) in originals:
                continue
            original = client.query_points
            originals[id(client)] = (client, original)
            def measured(*args, _original=original, **kwargs):
                result = _original(*args, **kwargs)
                completed()
                return result
            client.query_points = measured
        yield
    finally:
        for client, original in originals.values():
            client.query_points = original
