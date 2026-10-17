# Feature validation — 2026-10-17

This audit compares the feature list and guides at `2bf8e5c` with the modernized
bot. A passing API check does not establish that Discord delivered a human event.
Offline tests exercise application handlers and actual Pycord/LlamaIndex objects
with network boundaries simulated. Live checks use real OpenAI and Discord APIs,
isolated storage, bounded request budgets, and temporary Discord resources.

| Original feature | Verification and current status |
| --- | --- |
| Ask, private ask, text editing, message context actions | Live chat/edit API checks passed; SDK handler tests cover Ask GPT, Paraphrase, Elaborate, Summarize, acknowledgement timing, and personal keys. |
| Public/private conversations, minimal mode, openers, full-channel conversations | Live public/private thread creation, memory, Retry, and End passed. SDK tests cover opener ownership, instructions, settings, private membership, full-channel mode, and failures. Full-channel delivery requires a healthy Gateway. |
| Retry and message editing | Live conversation Retry and hybrid edit/replacement passed. Tests cover stale controls, busy turns, cancellation, ended conversations, rotated keys, and history replacement. REST recovery does not discover message edits. |
| Persistent conversations and instructions | Atomic private pickle writes, restoration, and failure cleanup are tested. Deployment preserves databases and saved conversations. Saves are periodic, not a four-file transaction. |
| Long conversations and summarization | Live summarization passed; handler tests cover summarization, history, and conversation limits. |
| User/channel instructions | Tests cover set/get/clear, pre-moderation, personal instruction priority, and propagation into conversation requests, including openers. |
| Per-user API keys and permissions | Live key validation passed. SDK tests cover setup modals, changed/missing keys, command and component ownership, role IDs, and denial before provider work. Production remains restricted to the exact Maintainer role. |
| Model settings, low-usage mode, usage estimates | Tests cover persistence, defaults, writable settings, model-specific requests, token counts, pricing, and concurrent usage updates. Retired `best_of`, `mode`, and `use_org` UI options are hidden; stored compatibility properties remain. |
| Image understanding and in-conversation drawing | Live vision and image-generation API checks passed. Tests cover multimodal request content, generated attachments, upload limits, and conversation images. |
| Image generation, optimization, Draw/Retry controls | Live current-image generation passed. SDK tests cover optimizer ownership, fresh keys, acknowledgement deadlines, image delivery, and replacement attachments. `/dalle draw_old` uses current generation. Retired DALL·E variations are unavailable. |
| Audio/video transcription | Live generated speech transcription passed. Tests cover uploaded files, MIME/extension handling, pagination, YouTube download isolation, and errors. Remote YouTube download was not live tested. |
| AI moderation and English-language checks | Live moderation and language detection passed. Tests simulate warn/delete/no-action, threshold updates including zero, worker lifetime, reconnects, alert-channel validation, and button acknowledgements. Real users were not kicked or timed out. Automatic server moderation requires Gateway events. |
| Internet search and chat | Native `/web` live search returned a citation. Google/Wolfram and crawler integrations pass offline request/agent tests; those provider credentials are absent on the deployed server. Their ongoing chats require Gateway events. |
| Document indexing and Q&A | Live create → persist → reload → query passed, using two embedding calls and one answer. Real parser fixtures cover TXT, Markdown, CSV, PDF, DOCX, PPTX, XLSX, EPUB, and RTF. Offline tests cover composition, deep/multistep query paths, Discord backups, embedding compatibility, and personal-key isolation. Basic Q&A and agent memory tolerate unavailable vocabulary downloads; deep tree/router modes still require a cached tokenizer vocabulary. Indexing is disabled on the deployed server. |
| Media and specialized index readers | Binary image/audio/video/legacy Office indexing is rejected with guidance to extract text first. GitHub and YouTube links need their separate reader packages and any required credentials; they were not live tested. The Google Docs reader helper is not exposed by a command. Document-chat event delivery requires Gateway events. |
| Pinecone permanent memory | Offline tests cover namespaces, embedding compatibility, and exact conversation IDs. No Pinecone credentials are configured, so remote persistence was not live tested. Legacy numeric conversation IDs above `2**53` require re-ingestion because Pinecone cannot preserve their exact value. |
| E2B code interpreter and artifacts | Current SDK imports and offline agent/upload/artifact tests pass, including failure cleanup and concurrent requests. No E2B key is configured; remote execution was not live tested. Chats require Gateway events. |
| DeepL translation and language selection | Offline tests cover free/pro endpoints, authentication, request bodies, provider errors, ownership, and SDK acknowledgements. No DeepL key is configured; remote translation was not live tested. |
| Replicate fallback image understanding | Current SDK import and offline integration checks pass. No Replicate key is configured; remote inference was not live tested. Native OpenAI vision passed live. |
| ShareGPT conversation sharing | The obsolete external API was removed. End now offers an owner-only **Download Conversation** button delivering a private TXT transcript. New conversations export their full transcript; legacy conversations export retained context, which may contain summaries. SDK tests cover ownership, cacheless interactions, and private delivery. |
| Concurrency and automatic API retries | Offline tests cover overlapping requests, blocked threads, bounded worker scheduling, duplicate delivery, cancellation, rate limits, quota errors, and retry limits. The historical claim of hundreds of simultaneous users was not established by a paid load test. |
| Pagination, tagging, diagnostics, health | Tests cover complete pagination, context actions, role checks, and diagnostic paths. Flask `/healthz` passed a local request; it is a liveness endpoint. Bot mentions require Gateway delivery. |
| Installation, packages, containers, CI | Base/full dependency consistency, imports, offline suites, wheel/sdist builds, installed entry points, and packaged prompts are checked. CI covers Python 3.11–3.13, Windows/macOS, optional integrations, both Docker variants, and tests extracted source distributions. |

The final local offline runs passed **578 tests** with the base installation
(9 optional skips) and **670 tests** with all extras installed. Four opt-in paid
pytest cases were deselected; the explicit live runners below exercised their API
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
The installation is present. Scoped REST recovery covers registered bot-owned
conversation threads; it does not recover mentions, full-channel conversations,
automatic moderation, or optional integration chats. These cannot be claimed to
work end to end in that server until Discord supplies their events.

Optional providers are unconfigured, not live-verified successes. No credentials,
prompts, generated responses, or raw provider exceptions are included in reports.

## Keeping the change small

The review removes dead retired-API code, duplicate transcription/optimizer paths,
and the unused ShareGPT client. Common API response validation is shared, while
provider and conversation behavior retains focused regression tests. Original
line endings are retained in pre-existing CRLF files to avoid whole-file cosmetic
diffs. Recovery, role checks, and state preservation remain because live failures
and deterministic regressions demonstrate why they are needed.
