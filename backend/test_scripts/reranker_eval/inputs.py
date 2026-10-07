"""Bounded scoring windows; canonical evidence is never edited."""
import re

PAIR_LIMIT = 2048
WINDOW_LIMIT = 1024
OVERLAP = 128
TEMPLATE_RESERVE = 256


def _encode(tokenizer, text):
    return tokenizer.encode(text, add_special_tokens=False)


def make_windows(query: str, block: dict, *, tokenizer, forms: tuple[str, ...]) -> list[str]:
    title = block.get('title', '')
    available = PAIR_LIMIT - TEMPLATE_RESERVE - len(_encode(tokenizer, query + title))
    if available <= OVERLAP:
        raise ValueError('unsupported input length')
    width = min(WINDOW_LIMIT, available)
    text = (block.get('mail_fragment') or {}).get('content') or block.get('content', '')
    ids = _encode(tokenizer, text)
    if len(ids) <= width:
        return [text]
    step = width - OVERLAP
    starts = list(range(0, max(1, len(ids) - width + 1), step))
    if starts[-1] + width < len(ids):
        starts.append(max(0, len(ids) - width))
    pieces = [tokenizer.decode(ids[i:i + width], skip_special_tokens=True) for i in starts]
    anchors = set(re.findall(r'\w+', query.lower())) | {f.lower() for f in forms if f}
    relevance = lambda i: sum(bool(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)',
                                           pieces[i].lower())) for a in anchors)
    selected = {0, len(pieces) - 1}
    selected.update(sorted(range(1, len(pieces) - 1), key=lambda i: (-relevance(i), i))[:2])
    output = []
    for i in sorted(selected):
        prefix_text = tokenizer.decode(ids[:starts[i]], skip_special_tokens=True)
        heading = ''
        table_header = ''
        previous = ''
        for line in prefix_text.splitlines():
            if line.startswith('#'):
                heading = line
                table_header = ''
            elif line.strip() and '|' not in line:
                table_header = ''
            if re.match(r'^\s*\|?[\s:|\-]+\|?\s*$', line) and '---' in line:
                table_header = previous + '\n' + line
            previous = line
        context = '\n'.join(p for p in (heading, table_header) if p)
        piece = pieces[i]
        if context and context not in piece:
            reserve = len(_encode(tokenizer, context + '\n'))
            if reserve >= available - OVERLAP:
                raise ValueError('unsupported input table header')
            piece_ids = _encode(tokenizer, piece)
            room = available - reserve
            # Retain the end when adding a header to the final window.
            piece_ids = piece_ids[-room:] if i == len(pieces) - 1 else piece_ids[:room]
            piece = context + '\n' + tokenizer.decode(piece_ids, skip_special_tokens=True)
        output.append(piece)
    return output
