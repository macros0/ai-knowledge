# Controlled PyMuPDF return procedure

PyMuPDF is **not** a dependency, adapter, binary, or extra in the standard
source tree or standard image. The default provider is pypdf + pypdfium2.

If a future customer need requires PyMuPDF, create a separate provider module
and a separately reviewed dependency extra. Publish it only in a distinct image
tag with its own generated SBOM. Before release, run the complete PDF provider
contract suite and record the AGPL versus commercial-license decision, the
applicable notices, and the customer approval. Never add PyMuPDF back to the
standard image as a transitive or optional dependency.
