"""Choose evidence for one-pass or exhaustive chat answers."""

from __future__ import annotations

from collections.abc import Callable

from app.services.context_builder import format_context


class EvidenceTooLarge(ValueError):
    """A source cannot be included whole within the selected context budget."""


def select_batches(
    blocks: list[dict],
    *,
    mode: str,
    max_context_chars: int,
    fits: Callable[[str], bool],
    render: Callable[[list[dict]], str] = format_context,
) -> list[list[dict]]:
    """Preserve global source numbers while packing ordered whole blocks."""
    if mode not in {"fast", "full"}:
        raise ValueError("Unsupported generation mode")
    batches: list[list[dict]] = []
    current: list[dict] = []

    def allowed(items: list[dict]) -> bool:
        context = render(items)
        return len(context) <= max_context_chars and fits(context)

    def split_complete_lines(block: dict, *, first_only: bool = False) -> list[dict]:
        if block.get("mail_fragment"):
            raise EvidenceTooLarge("Mail attribution fragment must remain intact")
        lines = block["content"].splitlines(keepends=True)
        if len(lines) < 2:
            raise EvidenceTooLarge(f"Source {block.get('_source_index')} exceeds context budget")
        prefix = []
        if lines[0].lstrip().startswith("|") and len(lines) > 1 and lines[1].lstrip().startswith("|"):
            prefix = lines[:2]
            lines = lines[2:]
        segments = []
        position = 0
        while position < len(lines):
            start = position
            content = "".join(prefix)
            # Character checks are cheap; ask the tokenizer only for each
            # candidate segment, then binary-search if its token count is high.
            while position < len(lines):
                candidate = content + lines[position]
                if len(render([{**block, "content": candidate, "partial": True}])) > max_context_chars:
                    break
                content = candidate
                position += 1
            if position == start:
                if first_only:
                    position += 1
                    continue
                raise EvidenceTooLarge(f"Source {block.get('_source_index')} has an oversized row")
            segment = {**block, "content": content, "partial": True}
            if not fits(render([segment])):
                low, high, best = 1, position - start, 0
                while low <= high:
                    middle = (low + high) // 2
                    candidate = {**block, "content": "".join([*prefix, *lines[start:start + middle]]),
                                 "partial": True}
                    if allowed([candidate]):
                        best = middle
                        low = middle + 1
                    else:
                        high = middle - 1
                if best == 0:
                    if first_only:
                        position = start + 1
                        continue
                    raise EvidenceTooLarge(f"Source {block.get('_source_index')} has an oversized row")
                position = start + best
                segment = {**block, "content": "".join([*prefix, *lines[start:position]]),
                           "partial": True}
            segments.append(segment)
            if first_only:
                break
        return segments

    for block in blocks:
        if mode == "fast":
            segments = [block] if allowed([block]) else []
            if not segments:
                try:
                    segments = split_complete_lines(block, first_only=True)
                except EvidenceTooLarge:
                    continue
        else:
            segments = [block] if allowed([block]) else split_complete_lines(block)
        for segment in segments:
            if allowed([*current, segment]):
                current.append(segment)
                continue
            if mode == "fast":
                continue
            if current:
                batches.append(current)
            current = [segment]
    if current:
        batches.append(current)
    return batches
