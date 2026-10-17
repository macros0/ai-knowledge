"""Bounded reuse of a derived dedup signature for exact published text."""
from copy import deepcopy
import hashlib
import json

from app.services.sparse import TOKEN_RE

MAX_ENTRIES = 8
# Bump if token normalization or the signature algorithm changes independently
# of TOKEN_RE/configuration. Cache misses always use the canonical implementation.
SIGNATURE_CACHE_VERSION = 1


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def prepare_scope_signature(text, previous, settings, mail_fingerprint=None):
    from app.services import deduplication as dedup
    parameters=[settings.dedup_minhash_k,settings.dedup_shingle_n,dedup.banding_schemes(settings)]
    key=_hash([SIGNATURE_CACHE_VERSION,TOKEN_RE.pattern,TOKEN_RE.flags,parameters,mail_fingerprint,
               hashlib.sha256(text.encode("utf8")).hexdigest()])
    entries=(previous or {}).get("entries",[]) if isinstance(previous,dict) else []
    if not isinstance(entries,list):entries=[]
    entries=[e for e in entries[:MAX_ENTRIES] if isinstance(e,dict) and isinstance(e.get("key"),str)]
    signature=None
    for entry in entries:
        candidate=entry.get("signature")
        if entry["key"]==key and isinstance(candidate,dict) and entry.get("signature_hash")==_hash(candidate):
            values=candidate.get("minhash")
            if (candidate.get("content_hash")==dedup.content_hash(text)
                    and candidate.get("mail_fingerprint")==mail_fingerprint
                    and (values is None or (isinstance(values,list) and len(values)==settings.dedup_minhash_k
                         and all(type(v) is int and 0<=v<=0xFFFFFFFF for v in values)))
                    and isinstance(candidate.get("buckets"),list)):
                signature=deepcopy(candidate)
                break
    if signature is None:
        signature=dedup.prepare_document_signature(text,mail_fingerprint,settings=settings)
    entry={"key":key,"signature":deepcopy(signature),"signature_hash":_hash(signature)}
    cache={"schema_version":SIGNATURE_CACHE_VERSION,"entries":[entry,*[e for e in entries if e["key"]!=key]][:MAX_ENTRIES]}
    return signature,cache
