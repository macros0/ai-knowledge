"""Build-time installation of the pinned official 7-Zip RAR-capable helper."""

import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

VERSION = "26.04"
ARTIFACTS = {
    "x86_64": ("x64", "fc0327ba27e89bd086cf426dff17d77de582953cdbbc10a6576540a06853ffcd"),
    "aarch64": ("arm64", "5b0ac3aa91c1e3499f011d6af69d7f6b4de3ef2f011d9e2ff44c58ebd04aa388"),
}


def install(destination=Path("/opt/7zip")):
    arch, expected = ARTIFACTS[platform.machine()]
    url = f"https://github.com/ip7z/7zip/releases/download/{VERSION}/7z2604-linux-{arch}.tar.xz"
    with urlopen(url, timeout=60) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("7-Zip distribution hash mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        for member in archive.getmembers():
            # Upstream names are data; never extract directories, links or paths.
            if not member.isfile():
                continue
            if (
                member.name == "7zz"
                or member.name.startswith("readme")
                or member.name.endswith((".txt", ".htm", ".html"))
            ):
                target = destination / Path(member.name).name
                with archive.extractfile(member) as source:
                    target.write_bytes(source.read())
                target.chmod(0o755 if member.name == "7zz" else 0o644)
    if not (destination / "7zz").is_file():
        raise RuntimeError("Official helper missing")
    (destination / "distribution.txt").write_text(
        f"7-Zip {VERSION}\n{url}\nsha256 {expected}\n"
        f"Source: https://github.com/ip7z/7zip/releases/tag/{VERSION}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    install()
