# Permanent memory and conversations

Conversations persist locally across restarts. Without a vector backend, long
conversations use summaries. Qdrant and Pinecone are optional alternatives for
retrieving older messages through OpenAI embeddings. Configure one backend only.

## Qdrant

Install `pip install 'gpt3discord[qdrant]'`. For embedded persistent storage:

```dotenv
QDRANT_PATH=/var/lib/gptdiscord/qdrant
```

The directory must be writable by the bot and used by only one running process.
No account, server, or API key is needed. Alternatively, use an existing server:

```dotenv
QDRANT_URL=https://your-qdrant-server.example
QDRANT_API_KEY=your-server-key
QDRANT_COLLECTION=conversation-embeddings
```

Use HTTPS for remote servers. Omit the key only if authentication is disabled.
`QDRANT_URL` and `QDRANT_PATH` are mutually exclusive. Back up embedded storage
while the bot is stopped, or use your remote server's snapshot facility.

The collection uses unnamed 1536-dimensional vectors and DOT distance. Startup
creates a missing collection and rejects an incompatible existing collection
without deleting it. Exact string thread IDs and embedding-model filters isolate
conversations and vector spaces. Historical third-party Qdrant data and local
summaries are not automatically imported.

## Pinecone

Install `pip install 'gpt3discord[pinecone]'` and configure:

```dotenv
PINECONE_TOKEN=your-pinecone-key
PINECONE_REGION=us-east-1
```

The bot creates a serverless `conversation-embeddings` index when missing, using
1536 dimensions and dot-product similarity. New records retain exact string
thread IDs. Legacy numeric IDs above `2**53` require re-ingestion because their
exact value cannot be recovered safely.

## Embeddings, retention, and corrections

`OPENAI_EMBEDDING_MODEL` defaults to `text-embedding-3-small`. Embedding requests
use the shared key or the conversation owner's personal key. Changing models
requires re-ingesting historical text; equal dimensions do not make vectors
compatible. Qdrant uses model payload filters; Pinecone uses model namespaces,
retaining the legacy namespace when `text-embedding-ada-002` is selected.

Vector-backed conversations require corrections as new messages. Retry and
message editing cannot replace already stored vectors. Ending a conversation
does not erase its vectors; manage retention through your backend's tools.

Preserve `SHARE_DIR` (falling back to `DATA_DIR`), its pickle files, SQLite
schemas/data, local indexes, and any configured Qdrant path when upgrading.
Installing an optional extra does not enable its backend.
