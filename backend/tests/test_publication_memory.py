"""Publication verification hashes the exact parsed bytes without duplicate raw buffers."""
import hashlib
import json
import sys
import tracemalloc

import pytest

from app.config import Settings
from app.services.generation_artifacts import GenerationIntegrityError, load_verified_publication
from app.services.generation_files import generation_paths

DOC = 'memory-publication'
GENERATION = 'a' * 32


def publication(tmp_path, payload, encoding='utf8'):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    paths = generation_paths(settings, DOC, GENERATION)
    paths.attachments.mkdir(parents=True)
    paths.bundle.mkdir(parents=True)
    value = {'doc_id': DOC, 'generation_id': GENERATION, 'artifacts': {},
             'sources': [], 'attachments': [], **payload}
    path = paths.uploads_root / 'publication.json'
    with path.open('w', encoding=encoding, newline='') as stream:
        json.dump(value, stream, ensure_ascii=False)
    return settings, path, value


def test_large_verified_publication_does_not_keep_raw_bytes_during_object_decode(tmp_path):
    value = 'Ж🌍' * 2_000_000
    settings, path, expected = publication(tmp_path, {'padding': value})
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    tracemalloc.start()
    try:
        result = load_verified_publication(settings, DOC, GENERATION, digest)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == expected
    # Two Unicode representations plus a generous allocator/parser allowance;
    # retaining the complete raw UTF-8 document as a third copy exceeds this.
    assert peak <= sys.getsizeof(value) * 2.4 + 1024 * 1024, peak


@pytest.mark.parametrize('encoding', ['utf8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32'])
def test_hash_matches_exact_parsed_encoding_and_crlf(tmp_path, encoding):
    settings, path, expected = publication(tmp_path, {'text': 'Ж🌍\r\n', 'zero': 0, 'false': False}, encoding)
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    assert load_verified_publication(settings, DOC, GENERATION, digest) == expected
    with pytest.raises(GenerationIntegrityError):
        load_verified_publication(settings, DOC, GENERATION, '0' * 64)
    assert path.read_bytes() == original
