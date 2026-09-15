#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 IMAGE" >&2
  exit 64
fi

image="$1"

docker run --rm --entrypoint sh "$image" -ceu '
  matches=$(find /usr/local/lib/python*/site-packages \
    \( -iname "*mupdf*" -o -iname "pymupdf*" -o -iname "fitz" -o -iname "fitz.py" \) \
    -print 2>/dev/null || true)
  if [ -n "$matches" ]; then
    printf "%s\\n" "Forbidden MuPDF artifacts found:" >&2
    printf "%s\\n" "$matches" >&2
    exit 1
  fi
'
