from app.services import processing_scope_labels as labels


def test_label_cache_is_bounded_and_returns_detached_metadata():
    cache = labels.LabelCache(max_items=2, max_bytes=4096)
    cache.put('a', {'one': 'Original.pdf'})
    cached = cache.get('a')
    cached['one'] = 'Changed.pdf'
    assert cache.get('a') == {'one': 'Original.pdf'}
    cache.put('b', {})
    cache.get('a')
    cache.put('c', {'three': 'Other.msg'})
    assert cache.get('b') is None
    assert cache.get('a') == {'one': 'Original.pdf'}
    cache.put('huge', {'large': 'x' * 5000})
    assert cache.get('huge') is None
    assert len(cache.items) <= 2


def test_label_cache_never_shares_different_content_hashes():
    cache = labels.LabelCache()
    cache.put('hash-a', {'same-locator': 'Source A.pdf'})
    assert cache.get('hash-b') is None
