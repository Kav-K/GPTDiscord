# Feature validation — 2026-10-17

This audit compares the feature list and guides at `2bf8e5c` with the modernized
bot. The v13.1 follow-up restores gaps found after the original audit. A passing API check does not establish that Discord delivered a human event.
The v13.2 follow-up makes `/internet chat` use OpenAI's native web search by default,
with Google retained as an explicit provider. It does not require Google credentials.
The v13.2.1 follow-up connects saved indexes to document chats: an explicit
`user_index` wins, otherwise chat uses the active selection or the latest saved
user index after restart. Add/load inside an owned chat preserves its existing
documents and memory. Empty or unreadable PDFs cannot report a successful index.
Offline tests exercise application handlers and actual Pycord/LlamaIndex objects
with network boundaries simulated. Live checks use real OpenAI and Discord APIs,
isolated storage, bounded request budgets, and temporary Discord resources.

| Original feature | Verification and current status |
| --- | --- |
| Ask, private ask, text editing, message context actions | Live chat/edit API checks passed; SDK handler tests cover Ask GPT, Paraphrase, Elaborate, Summarize, acknowledgement timing, and personal keys. |
| Public/private conversations, minimal mode, openers, full-channel conversations | Live public/private thread creation, memory, Retry, and End passed again. Actual SDK tests cover cacheless full-channel startup, explicit enrollment, role checks, and duplicate suppression. Full-channel, index/search/code sessions now share bounded recovery; free mentions still need Gateway delivery. |
| Retry and message editing | Live conversation Retry and hybrid edit/replacement passed. Tests cover stale controls, busy turns, cancellation, ended conversations, rotated keys, and history replacement. REST recovery does not discover message edits. |
| Persistent conversations and instructions | Atomic private pickle writes, restoration, and failure cleanup are tested. Deployment preserves databases and saved conversations. Saves are periodic, not a four-file transaction. |
| Long conversations and summarization | Live summarization passed; handler tests cover summarization, history, and conversation limits. |
| User/channel instructions | Tests cover set/get/clear, pre-moderation, personal instruction priority, and propagation into conversation requests, including openers. |
| Per-user API keys and permissions | Live key validation passed. SDK tests cover setup modals, changed/missing keys, command and component ownership, role IDs, and denial before provider work. Production remains restricted to the exact Maintainer role. |
| Model settings, low-usage mode, usage estimates | Tests cover persistence, defaults, writable settings, model-specific requests, token counts, pricing, and concurrent usage updates. Retired `best_of`, `mode`, and `use_org` UI options are hidden; stored compatibility properties remain. |
| Image understanding and in-conversation drawing | Live vision and image-generation API checks passed. Tests cover multimodal request content, generated attachments, upload limits, and conversation images. |
| Image generation, optimization, Draw/Retry controls | Live generation and current GPT Image edits passed. Per-image Vary creates a source-guided variation; Retry retains its source. Tests cover ownership, current keys, concurrency, moderation, source-file containment, and signed-URL/base64 delivery. Retired DALL·E random sampling is replaced by the current edit equivalent. |
| Audio/video transcription | Live generated speech transcription passed. Tests cover uploaded files, MIME/extension handling, pagination, YouTube download isolation, and errors. Remote YouTube download was not live tested. |
| AI moderation and English-language checks | Live moderation and language detection passed. Tests simulate warn/delete/no-action, threshold updates including zero, worker lifetime, reconnects, alert-channel validation, and button acknowledgements. Real users were not kicked or timed out. Automatic server moderation requires Gateway events. |
| Internet search and chat | `/web` and default `/internet chat` use OpenAI native web search. A live two-turn internet chat returned an official-source citation and remembered a private codeword on its second turn, using two streaming Responses requests with no Google credentials. Duplicate delivery added no requests; End archived the thread and cleanup removed owned fixtures. Actual SDK tests also cover follow-ups, local tool execution, citation pagination, and complete-file fallback. `provider: google` retains the Google option; `/internet search` still needs Google credentials. Google/Wolfram integrations pass offline request/agent tests; their credentials are absent on the deployed server. Registered chats can use scoped recovery when their guild is absent from the Gateway. |
| Document indexing and Q&A | The actual `/index add` public PDF URL → saved storage → default `/index chat` sequence passed live, without injecting retrieval tools. Three questions checked four resume facts, including a follow-up and a fresh-handler reload. Real retrieval, Discord replies, duplicate suppression, End/archive, and temporary-resource cleanup passed with four embedding and nine bounded Responses requests. Offline workflows additionally cover explicit selection, add/load inside chats, owner isolation, colliding filenames, failed persistence, restart recency, invalid/blank PDFs, and Vector/Tree composition. Native image extraction and three-second audio transcription were embedded successfully. Real parsers cover TXT, Markdown, CSV, PDF, DOCX, PPTX, XLSX, EPUB, and RTF. Worker SDK requests are synchronous; a 121-document tree regression preserves complete multi-level summaries. |
| Media and specialized index readers | Image uploads extract text/visible information; supported audio/video uploads index speech, with 10/25 MiB limits. GitHub default-branch reading passed live. Current YouTube SDK objects, bounded requests, and URL forms are tested; transcript retrieval passed on the deployed host (61 caption segments). Public Google Docs links now reach text export; private Docs require exported uploads. Legacy Office/SVG/PSD need conversion. |
| Pinecone permanent memory | Offline tests cover namespaces, embedding compatibility, and exact conversation IDs. No Pinecone credentials are configured, so remote persistence was not live tested. Legacy numeric conversation IDs above `2**53` require re-ingestion because Pinecone cannot preserve their exact value. |
| Qdrant permanent memory | Implemented optional embedded/remote adapter. Real persistent-storage tests and a real authenticated Qdrant 1.19.2 HTTP server passed, including restart, idempotency, exact thread/model isolation, and wrong-key rejection. Real OpenAI embeddings also passed retrieval and local restart persistence. No Qdrant service or credentials were retained on the production host; installation alone does not enable it. |
| E2B code interpreter and artifacts | Current SDK imports and offline agent/upload/artifact tests pass. Startup failures and cancellation close created or late-arriving sandboxes, clear session state, and archive dead threads. Changed/deleted personal keys stop future provider work while End remains available. No E2B key is configured; remote execution was not live tested. Registered chats can use scoped recovery. |
| DeepL translation and language selection | Offline tests cover free/pro endpoints, authentication, language selection, provider errors, ownership, and SDK acknowledgements. Expanded source/results are fully paginated within Discord embed limits. No DeepL key is configured; remote translation was not live tested. |
| Replicate fallback image understanding | Current SDK import and offline integration checks pass. No Replicate key is configured; remote inference was not live tested. Native OpenAI vision passed live. |
| ShareGPT conversation sharing | The obsolete external API was removed. End now offers an owner-only **Download Conversation** button delivering a private TXT transcript. New conversations export their full transcript; legacy conversations export retained context, which may contain summaries. SDK tests cover ownership, cacheless interactions, and private delivery. |
| Concurrency and automatic API retries | Offline tests cover overlapping requests, blocked threads, bounded worker scheduling, duplicate delivery, cancellation, rate limits, quota errors, and retry limits. The historical claim of hundreds of simultaneous users was not established by a paid load test. |
| Pagination, tagging, diagnostics, health | Tests cover complete pagination, context actions, role checks, and diagnostic paths. Flask `/healthz` passed a local request; it is a liveness endpoint. Bot mentions require Gateway delivery. |
| Installation, packages, containers, CI | Base/full dependency consistency, imports, offline suites, wheel/sdist builds, installed entry points, and packaged prompts are checked. CI covers Python 3.11–3.13, Windows/macOS, optional integrations, both Docker variants, and tests extracted source distributions. |

The final local offline runs passed **637 tests** with the base installation
(49 skips) and **904 tests** with all extras installed. Four opt-in paid
pytest cases were skipped; the explicit live runners below exercised their API
routes and broader features. Dependency and Ruff checks passed.

## Live checks and limits

All twelve provider checks passed: key validation, chat, edit, summary, language,
JSON, vision, embeddings, moderation, image generation, transcription, and web.
Text output was capped at 512 tokens, images at one low-quality 1024px image,
web at one tool call, and transcription at a three-second speech fixture.

Conversation checks made six bounded provider attempts for public/private
conversation controls and worker lifetime, plus three for the hybrid edit flow.
Every owned temporary thread and starter was deleted. Human authorship and
interaction acknowledgements are simulated; provider requests and Discord
creation, replies, edits, membership checks, archival, and deletion are real.

LLM Hub remains available through Discord REST while absent from Gateway READY.
The installation is present. Scoped REST recovery now covers registered bot-owned
conversation and optional chat threads, plus deliberately started full-channel
conversations. Free mentions, general automatic moderation, and undelivered edits
still require Gateway events. The cleanup command can list accessible active
bot-owned conversation threads through REST; it does not enumerate archived threads.

DeepL, Google/Wolfram, E2B, Pinecone, and Replicate remain unconfigured, not
live-verified successes. GitHub and Qdrant were verified separately as described above. No credentials,
prompts, generated responses, or raw provider exceptions are included in reports.

## Keeping the change small

The review removes dead retired-API code, duplicate transcription/optimizer paths,
and the unused ShareGPT client. Common API response validation is shared, while
provider and conversation behavior retains focused regression tests. Original
line endings are retained in pre-existing CRLF files to avoid whole-file cosmetic
diffs. Recovery, role checks, and state preservation remain because live failures
and deterministic regressions demonstrate why they are needed.
