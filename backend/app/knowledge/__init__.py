"""M6 knowledge store: documents, immutable versions, processing, publication, feedback proposals and the audit trail.

It sits IN FRONT of the existing RAG index and never beside it: publishing writes the live `documents` / `chunks` rows that app.rag.retrieval already
searches (with its tenant, audience and embedding-model filters), and nothing else is ever searched."""
