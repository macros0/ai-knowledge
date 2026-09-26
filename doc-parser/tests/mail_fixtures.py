# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT
"""Small deterministic CFB writer for synthetic MSG fixtures, never production.

Format: MS-CFB 2.2 header and 2.6 directory; 512-byte FAT, 64-byte mini FAT.
No third-party fixture/code is copied. Deliberately limited to small test files.
"""
from __future__ import annotations

import io
import struct

import olefile

FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FAT = 0xFFFFFFFD


def compound_bytes(streams: dict[str, bytes]) -> bytes:
    nodes = [{"name": "Root Entry", "kind": 5, "children": [], "data": b""}]
    paths = {"": 0}
    for path, data in sorted(streams.items()):
        parts = path.split("/")
        for index, name in enumerate(parts):
            key = "/".join(parts[:index + 1])
            if key in paths:
                continue
            parent = paths["/".join(parts[:index])]
            node_id = len(nodes)
            paths[key] = node_id
            nodes[parent]["children"].append(node_id)
            nodes.append({"name": name, "kind": 2 if index == len(parts) - 1 else 1,
                          "children": [], "data": data if index == len(parts) - 1 else b""})

    sectors: list[bytes] = []
    fat: list[int] = []

    def allocate(data: bytes) -> int:
        if not data:
            return END
        first = len(sectors)
        for offset in range(0, len(data), 512):
            sectors.append(data[offset:offset + 512].ljust(512, b"\0"))
            fat.append(len(sectors))
        fat[-1] = END
        return first

    mini = bytearray()
    mini_fat: list[int] = []
    for node in nodes[1:]:
        data = node["data"]
        node["start"] = END
        if node["kind"] != 2 or not data:
            continue
        if len(data) >= 4096:
            node["start"] = allocate(data)
        else:
            node["start"] = len(mini_fat)
            for offset in range(0, len(data), 64):
                mini.extend(data[offset:offset + 64].ljust(64, b"\0"))
                mini_fat.append(len(mini_fat) + 1)
            mini_fat[-1] = END
    nodes[0]["data"] = bytes(mini)
    nodes[0]["start"] = allocate(bytes(mini))
    mini_fat_bytes = b"".join(struct.pack("<I", value) for value in mini_fat)
    mini_fat_bytes += struct.pack("<I", FREE) * ((-len(mini_fat)) % 128)
    mini_fat_start = allocate(mini_fat_bytes)

    for node in nodes:
        node.update(left=FREE, right=FREE, child=FREE, color=1)

    def siblings(ids: list[int], level=0, bottom=0) -> int:
        if not ids:
            return FREE
        middle = len(ids) // 2
        node_id = ids[middle]
        nodes[node_id]["left"] = siblings(ids[:middle], level + 1, bottom)
        nodes[node_id]["right"] = siblings(ids[middle + 1:], level + 1, bottom)
        nodes[node_id]["color"] = 0 if level == bottom and level else 1
        return node_id

    for node in nodes:
        ids = sorted(node["children"], key=lambda idx: (len(nodes[idx]["name"]), nodes[idx]["name"].upper()))
        node["child"] = siblings(ids, bottom=len(ids).bit_length() - 1)

    directory = bytearray()
    for node in nodes:
        name = (node["name"] + "\0").encode("utf-16-le")
        assert len(name) <= 64
        entry = bytearray(128)
        entry[:len(name)] = name
        struct.pack_into("<HBBIII", entry, 64, len(name), node["kind"], node["color"],
                         node["left"], node["right"], node["child"])
        struct.pack_into("<IQ", entry, 116, node.get("start", END), len(node["data"]))
        directory.extend(entry)
    directory_start = allocate(bytes(directory))
    fat_count = 1
    while len(fat) + fat_count > fat_count * 128:
        fat_count += 1
    assert fat_count <= 109, "fixture exceeds the simple header DIFAT"
    fat_ids = list(range(len(sectors), len(sectors) + fat_count))
    fat.extend([FAT] * fat_count)
    fat.extend([FREE] * (fat_count * 128 - len(fat)))
    sectors.extend(struct.pack("<128I", *fat[offset:offset + 128]) for offset in range(0, len(fat), 128))
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<5H", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<9I", header, 40, 0, fat_count, directory_start, 0, 4096,
                     mini_fat_start, len(mini_fat_bytes) // 512, END, 0)
    struct.pack_into("<109I", header, 76, *(fat_ids + [FREE] * (109 - fat_count)))
    result = bytes(header) + b"".join(sectors)
    # An independent reader must recover every original stream byte-for-byte.
    with olefile.OleFileIO(io.BytesIO(result)) as ole:
        assert len(ole.listdir()) == len(streams)
        for path, data in streams.items():
            assert ole.openstream(path).read() == data
    return result


def unicode_msg(*, subject="Решение 3509", body="Согласован лимит в приложенной таблице.",
                message_class="IPM.Note", content_class="", sender="approver@example.test",
                attachments: dict[str, bytes] | None = None) -> bytes:
    """Synthetic Unicode MSG with real attachment storage and MAPI streams."""
    values = {
        "001A": message_class, "0037": subject, "1000": body,
        "007D": f"From: {sender}\r\nTo: team@example.test\r\nDate: Fri, 25 Sep 2026 10:30:00 +0300\r\n",
        "1035": "<decision-3509@example.test>",
    }
    if content_class:
        values["007D"] += f"Content-Class: {content_class}\r\n"
    streams = {f"__substg1.0_{tag}001F": (value + "\0").encode("utf-16-le") for tag, value in values.items()}
    # MSG property stream: reserved, next recipient/attachment, counts, reserved.
    streams["__properties_version1.0"] = struct.pack("<8sIIII8s", b"", 0, len(attachments or {}), 0, len(attachments or {}), b"")
    for index, (name, payload) in enumerate((attachments or {}).items()):
        prefix = f"__attach_version1.0_#{index:08X}/"
        streams[prefix + "__properties_version1.0"] = b"\0" * 8 + struct.pack("<IIQ", 0x37050003, 6, 1)
        streams[prefix + "__substg1.0_3707001F"] = (name + "\0").encode("utf-16-le")
        streams[prefix + "__substg1.0_37010102"] = payload
    return compound_bytes(streams)


def nested_msg(parent: bytes, child: bytes) -> bytes:
    def read_streams(payload):
        with olefile.OleFileIO(io.BytesIO(payload)) as ole:
            return {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}

    streams = read_streams(parent)
    streams["__properties_version1.0"] = struct.pack("<8sIIII8s", b"", 0, 1, 0, 1, b"")
    prefix = "__attach_version1.0_#00000000/"
    streams[prefix + "__properties_version1.0"] = bytes(8) + struct.pack("<IIQ", 0x37050003, 6, 5)
    streams[prefix + "__substg1.0_3707001F"] = "nested.msg\0".encode("utf-16-le")
    for path, data in read_streams(child).items():
        if path == "__properties_version1.0":
            data = data[:24] + data[32:]
        streams[prefix + "__substg1.0_3701000D/" + path] = data
    return compound_bytes(streams)


def rtf_only_msg(*, compressed=True) -> bytes:
    """Real RTF property, with neither a plain nor HTML alternative."""
    from compressed_rtf import compress

    with olefile.OleFileIO(io.BytesIO(unicode_msg(attachments={"proof.bin": b"proof\0bytes"}))) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}
    del streams["__substg1.0_1000001F"]
    streams["__substg1.0_10090102"] = compress(b"{\\rtf1\\ansi RTF ONLY BODY}", compressed=compressed)
    return compound_bytes(streams)
