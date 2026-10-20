"""Standalone bounded DNS IPC worker; never import application settings or DB."""
import ipaddress
import json
import socket
import sys
import threading

MAX_HOSTS = 8
MAX_ADDRESSES = 8
MAX_INPUT = 4096


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    names = json.loads(raw)
    if len(raw) > MAX_INPUT or not isinstance(names, list) or len(names) > MAX_HOSTS:
        raise ValueError('Invalid resolver input')
    if any(not isinstance(name, str) or not 0 < len(name) <= 254 for name in names):
        raise ValueError('Invalid resolver name')
    lock = threading.Lock()

    def resolve(name):
        addresses = []
        try:
            for item in socket.getaddrinfo(name, None, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP):
                address = str(ipaddress.ip_address(item[4][0]))
                if address not in addresses:
                    addresses.append(address)
                if len(addresses) == MAX_ADDRESSES:
                    break
        except OSError:
            pass
        with lock:
            print(json.dumps([name, addresses]), flush=True)

    threads = [threading.Thread(target=resolve, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == '__main__':
    main()