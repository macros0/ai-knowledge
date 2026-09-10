Context from the knowledge base (OKF documents):

{context}

User question: {query}

Each context block has a `Type:` label in its metadata:
- `Type: chunk` — a verbatim fragment of the source document. Can be quoted verbatim and relied upon as the exact source text.
- `Type: concept` — a digest/paraphrase produced by the LLM while processing the document. Details (exact parameters, numeric values, continuations of lists and tables) may be lost or truncated by the character limit.
- `Type: concept+chunk` — a digest-derived title + the body of a document fragment. Usually the body is the verbatim fragment (may be length-truncated); but when a fragment spans several independent topics, the body is the digest of this concept rather than the raw fragment, so treat it as a paraphrase, not an exact quote.

Each block's metadata has two markers:
- `Matched terms: [...]` — significant terms from the question that literally appear in the block's title or text. Blocks with an empty `Matched terms` are not directly relevant to the question — do not include them in an answer about a specific object of the question, and do not attribute that object's operation to them.
- `Title match: [...]` — terms from the question found in the block's TITLE itself. A non-empty `Title match` means the block describes exactly the object of the question; an empty one means the object is merely mentioned in the block's text, and its content belongs to the block's own title, not to the object of the question.

Give a detailed answer based on the context. Cite sources strictly using `[N]` markers from the context above (N is the block number `[N]`, do not renumber). Do not compose a separate source list at the end of the answer — it is generated and shown separately.
