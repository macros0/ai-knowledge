"""Count private synchronous transport completions without altering application calls."""
from contextlib import contextmanager


@contextmanager
def instrument_transport_calls(targets, completed, *, completed_error=None):
    originals = {}
    try:
        for target, names in targets:
            for name in names:
                key = (id(target), name)
                if key in originals:
                    continue
                original = getattr(target, name)
                if not callable(original):
                    raise ValueError('Transport method is unavailable')
                originals[key] = (target, name, original)
                def measured(*args, _original=original, **kwargs):
                    try:
                        result = _original(*args, **kwargs)
                    except Exception as error:
                        if completed_error is not None and completed_error(error) is True:
                            completed()
                        raise
                    completed()
                    return result
                setattr(target, name, measured)
        yield
    finally:
        for target, name, original in originals.values():
            setattr(target, name, original)


def completed_ollama_http_error(error):
    """Only a fully received native HTTP rejection establishes a completion.

    Timeout/network failures or unread responses remain unclaimed. This is an
    own-call bracket, not independent proof of server exclusivity or CPU cost.
    """
    import httpx
    if not isinstance(error, httpx.HTTPStatusError):
        return False
    response = error.response
    request = error.request
    return (400 <= response.status_code <= 599
            and response.is_stream_consumed is True and response.is_closed is True
            and response.request is request and request.method == 'POST'
            and request.url.path == '/api/embed')


def qdrant_transport_targets(clients):
    """Capture every initialized synchronous gRPC pool member and REST send.

    Initial compatibility probes must be drained before the monitor boundary.
    Unsupported/new pools or async bypasses reconcile as foreign/unknown via
    independent server counters rather than being silently declared owned.
    """
    targets = []
    seen = set()
    for client in clients:
        if id(client) in seen:
            continue
        seen.add(id(client))
        remote = client._client
        targets.append((remote.openapi_client.client, ['send']))
        # Both accessors initialize their complete pool before selecting members.
        remote.grpc_points
        remote.grpc_collections
        for name in ('_grpc_points_client_pool', '_grpc_collections_client_pool'):
            pool = getattr(remote, name)
            if not isinstance(pool, list) or not pool:
                raise ValueError('Qdrant synchronous pool identity is unavailable')
            for stub in pool:
                methods = [key for key, value in vars(stub).items()
                           if key and key[0].isupper() and callable(value)]
                if not methods:
                    raise ValueError('Qdrant synchronous transport methods unavailable')
                targets.append((stub, methods))
    if not targets:
        raise ValueError('Qdrant transport clients are absent')
    return targets


def parse_model_connections(tcp, tcp6, *, own_peer, server_port=8080):
    """Read established peers for the selected model port; never export peers."""
    import ipaddress
    selected = ipaddress.ip_address(own_peer)
    own = other = 0
    for body, size in ((tcp, 4), (tcp6, 16)):
        if not isinstance(body, str) or not body.splitlines():
            raise ValueError('Model socket snapshot unavailable')
        for line in body.splitlines()[1:]:
            if not line.strip():
                continue
            fields = line.split()
            try:
                local_port = int(fields[1].rsplit(':', 1)[1], 16)
                if local_port != server_port or int(fields[3], 16) != 1:
                    continue
                raw, port = fields[2].rsplit(':', 1)
                if len(raw) != size * 2 or not 0 < int(port, 16) <= 65535:
                    raise ValueError()
                packed = bytes.fromhex(raw)
                packed = (packed[::-1] if size == 4 else b''.join(
                    packed[i:i+4][::-1] for i in range(0, 16, 4)))
                peer = ipaddress.ip_address(packed)
                if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
                    peer = peer.ipv4_mapped
                if peer == selected:
                    own += 1
                else:
                    other += 1
            except (IndexError, ValueError) as error:
                raise ValueError('Selected model socket identity unavailable') from error
    return {'own_established': own, 'other_established': other, 'known': True}


def model_other_busy(slots, owned_before, owned_after, connections):
    """Exclude a busy slot only within own call bracket and own peer evidence.

    Idle sockets do not count as work. Unknown/stale/competing peers while the
    slot is busy fail closed. Sampling cannot exclude unsampled transient or
    already-disconnected clients; no universal client exclusivity is claimed.
    """
    import math
    if (not isinstance(slots, list) or not slots or any(
            not isinstance(row, dict) or type(row.get('is_processing')) is not bool
            for row in slots) or any(type(n) is not int or n < 0
                                    for n in (owned_before, owned_after))):
        raise ValueError('Model slot/call ownership unknown')
    active = sum(row['is_processing'] for row in slots)
    if active == 0:
        return 0
    own = max(owned_before, owned_after)
    if active > own:
        return active - own
    if not isinstance(connections, dict):
        raise ValueError('Busy model peer ownership unavailable')
    age = connections.get('age_s')
    if (connections.get('known') is not True or type(age) not in (float, int)
            or not math.isfinite(age) or not 0 <= age <= 1.25
            or type(connections.get('own_established')) is not int
            or connections['own_established'] < active
            or type(connections.get('other_established')) is not int
            or connections['other_established'] != 0):
        raise ValueError('Busy model peer ownership missing, competing or stale')
    return 0


def sample_model_ownership(probe, *, clock=None, wait=None):
    """Acquire a current slot/peer pair across the periodic observer boundary.

    Only a missing owned connection may be sampled again, for at most 0.5 s.
    Each probe must itself have bounded IO. Competing, stale or unknown evidence
    fails immediately; callers must timestamp the final probe, not the old one.
    """
    import math
    import time
    clock = clock or time.monotonic
    wait = wait or time.sleep
    deadline = clock() + 0.5
    retried = False
    while True:
        slots, before, after, peers = probe()
        if retried and clock() > deadline:
            raise ValueError('Model peer acquisition deadline exceeded')
        try:
            return model_other_busy(slots, before, after, peers)
        except ValueError:
            active = (sum(row['is_processing'] for row in slots)
                      if isinstance(slots, list) and slots and all(
                          isinstance(row, dict) and type(row.get('is_processing')) is bool
                          for row in slots) else None)
            age = peers.get('age_s') if isinstance(peers, dict) else None
            missing_owned_peer = (
                active is not None and active > 0
                and all(type(n) is int and n >= 0 for n in (before, after))
                and active <= max(before, after)
                and isinstance(peers, dict) and peers.get('known') is True
                and type(age) in (int, float) and math.isfinite(age) and 0 <= age <= 1.25
                and type(peers.get('own_established')) is int
                and 0 <= peers['own_established'] < active
                and type(peers.get('other_established')) is int
                and peers['other_established'] == 0)
            remaining = deadline - clock()
            if not missing_owned_peer or remaining <= 0:
                raise
            wait(min(0.05, remaining))
            retried = True


class CompletionBoundary:
    """Freeze completed-service snapshots before later storage verification.

    Readers are sequential, not an atomic global snapshot. This boundary does
    not establish client exclusivity or prove unfinished/unsampled requests.
    Failed closure remains unknown and is never retried to manufacture coverage.
    """
    def __init__(self, read_qdrant, read_embedding):
        self.read_qdrant = read_qdrant
        self.read_embedding = read_embedding
        self.attempted = False
        self.saved = None

    def close(self):
        from copy import deepcopy
        if self.attempted:
            raise ValueError('Service boundary was already attempted')
        self.attempted = True
        qdrant = deepcopy(self.read_qdrant())
        embedding = deepcopy(self.read_embedding())
        if not isinstance(qdrant, dict) or not qdrant or not isinstance(embedding, dict) or not embedding:
            raise ValueError('Completed-service snapshots unavailable')
        self.saved = (qdrant, embedding)

    def snapshots(self):
        from copy import deepcopy
        if self.saved is None:
            raise ValueError('Service completion window is not closed')
        return deepcopy(self.saved)
