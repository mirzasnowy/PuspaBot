# Document knowledge base (AST-02)

Upload documents and ask questions about them. Answers come from
the uploaded documents only, using local vector search, and every
answer cites its passages (source file + chunk number).

Pattern source: AstrBot's knowledge base (a local SQLite store
with a local vector index). Ideas and patterns only: no AstrBot
code was copied, and AstrBot is not a dependency.

## What it is

- **Upload**: plain text (`.txt`) and Markdown (`.md`, `.markdown`)
  files, up to 5 MB each. PDF is refused with a plain reason: no
  PDF parser exists in this repo, and this item adds no heavy new
  dependency for one. Upload the text or Markdown version instead.
- **Chunking**: text is split into sentences, then packed into
  chunks of at most 800 characters. Each chunk after the first
  re-opens with the previous chunk's last sentence, so an answer
  never loses the sentence a passage refers back to.
- **Embedding**: the repo's existing local embedder
  (`puspa.memory.embeddings`; the default is the free local
  `HashEmbedding`, the same one the episodic index uses). Pass any
  embedder with `.embed` / `.dim` (for example
  `FastEmbedEmbedding`) to use the real model instead.
- **Storage**: Postgres + pgvector, migration `0037`
  (`knowledge_documents` + `knowledge_chunks`), row-level
  security per user, same policy shape as the episodic table.
  Every row stores `embedder_id` + `dimension` (E-14): search
  only ever compares rows from the asking embedder, and an
  upload is refused when the user already has chunks from
  another embedder (delete the old documents first).
- **Asking**: vector search over the user's chunks, then an
  extractive answer built from the closest chunks word for word.
  The agent never invents a sentence the documents do not
  contain. When the base is empty the answer says so plainly.

## CLI

```sh
puspa knowledge upload notes.md --user <user-id>
puspa knowledge list --user <user-id>
puspa knowledge ask "What is the refund policy?" --user <user-id>
puspa knowledge delete <document-id> --user <user-id>
```

## PWA

- Screen: `/knowledge` (Jinja template
  `apps/pwa/templates/knowledge.html`; the screen fetches
  everything through the APIs below, so it needs no server
  bootstrap data).
- `GET /api/knowledge/documents`: the user's documents, newest first.
- `POST /api/knowledge/upload`: multipart `file` + optional `title`.
- `GET /api/knowledge/ask?q=<question>&limit=5`: answer + citations.
- `DELETE /api/knowledge/documents/{document_id}`: delete one document.

All routes need login (401 otherwise). A React screen for the
knowledge base does not exist yet: the Jinja screen plus the
APIs above are the PWA surface for this item.

## Limits (honest)

- Text and Markdown only; PDF is refused, not half-parsed.
- The default embedder is the hash stand-in, so similarity
  measures word overlap, not meaning; inject
  `FastEmbedEmbedding` for real semantic search.
- Answers are extractive quotes, not generated summaries: a
  question whose answer spans many passages gets the passages,
  not a synthesis.
- One user, one embedder: switching embedders means deleting
  the old documents (or a re-embed job, which does not exist yet).
