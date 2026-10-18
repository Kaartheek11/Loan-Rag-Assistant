# Loan File RAG Assistant: Extension (Chat UI, Admin Uploads, Large Loan Files)

## 0. How to Use This Document

This is the only change document for version 2 of the project. Version 1 is already built from `task.md` v2.1, and **`task.md` is not modified**. Everything new is described here, as additions or changes to that built system:

1. **Large loan files** (section 17): synthetic loan files grow from 3 to 12 pages to 50 to 100 pages, with the ingestion, database, and evaluation changes that requires.
2. **Chat UI** (sections 1 to 16): a web application with sign-in, a dashboard, and a chat window, replacing `curl` and the API docs page for end users.
3. **Admin uploads** (section 8.9 and section 6.1): admins upload loan PDFs and set who can read them, from the app.

When this document says "`task.md` section X", it means the existing, built spec. When it says "section X" alone, it means this document.

- Build in the order given in section 13, starting with Phase 0 (large loan files). Each phase ends with its tests passing.
- Requirements have IDs: `UI-F` (feature), `UI-S` (security), `UI-T` (test), `BE` (backend change). Use them in commit messages.
- **MUST**, **MUST NOT**, and **SHOULD** have their usual meaning.
- The product name used here is **LoanLens**. Rename it freely; change it in one place (`src/config/brand.ts`).

---

## 1. Purpose

Give loan staff a real application for asking questions about loan files. A user opens the app in a browser, signs in, lands on a dashboard, and talks to the assistant in a chat window. Answers come with citations that open the exact source page, with the cited passage highlighted.

No terminal, no `curl`, no API docs page, no JSON anywhere in the user's view.

## 2. The Rule That Overrides Everything Else

**The browser is not a security boundary. All permission decisions happen in the backend.**

- The frontend MUST NOT decide, filter, or hide anything based on permissions. It shows exactly what the backend returns and nothing more.
- The frontend MUST NOT receive data the user is not allowed to see and then hide it. If it is in a network response, assume the user can read it.
- Group names and roles shown in the UI are for display only. The UI never uses them to decide what to request or what to show.
- Every API call carries the user's token, and the backend checks permissions on every call, exactly as `task.md` section 6 specifies.

## 3. Goals

- **UI-G1. Feels like a product.** Sign in, dashboard, chat. Nothing technical on screen.
- **UI-G2. Access is resolved at sign-in.** Immediately after sign-in, the app calls the backend, which verifies the user and resolves their access from Postgres. The dashboard shows who they are and what they can reach before they type anything.
- **UI-G3. Access stays current.** Permission changes made by an admin show up without the user signing out.
- **UI-G4. Trustworthy answers are visible.** Citations are one click from the source page with the cited text highlighted.
- **UI-G5. Lively but calm.** A distinct visual identity with color, motion, and personality, without getting in the way of reading answers.
- **UI-G6. Admins add files without a terminal.** An admin uploads loan PDFs, chooses who can read them, and watches them become searchable, all from the app.

## 4. Non-Goals

- No user or group management screens. Users and their groups are still managed in Postgres and Keycloak. Admins can upload files and set each file's access in the app (section 8.9), but cannot create users or groups there.
- No saved chat history across sessions. Chats live in memory and disappear on sign-out or reload (UI-S4).
- No conversation memory in answers. The backend answers each question on its own; the UI says so (UI-F9).
- No streaming responses. The backend returns complete answers.
- No native mobile app. The web app is responsive down to phone width.

---

## 5. User Flow

```
  Open https://localhost
          |
          v
  +----------------+   not signed in    +-------------------------+
  | Welcome screen | -----------------> | Keycloak sign-in page   |
  | "Sign in"      |                    | (username + password)   |
  +----------------+ <---- redirect ----+-------------------------+
          |                    back with auth code (PKCE)
          v
  +-----------------------------+
  | "Checking your access..."   |  calls GET /v1/me
  +-----------------------------+
      |                    |
      | 200                | 401 (not provisioned or disabled)
      v                    v
  +-----------+      +---------------------+
  | Dashboard |      | No-access screen    |
  | + chat    |      | "Sign out" button   |
  +-----------+      +---------------------+
      |
      | ask question -> POST /v1/query
      | click citation -> GET /v1/documents/{id}/pages/{n}
      | sign out -> Keycloak logout -> Welcome screen
```

### 5.1 Step by step

1. **Welcome screen.** The user opens the app. `keycloak-js` runs a silent check (`check-sso`). If the user already has a session, skip straight to step 4.
2. **Sign in.** The user clicks "Sign in" and is sent to Keycloak's sign-in page.
3. **Return.** Keycloak redirects back with an authorization code. `keycloak-js` exchanges it for tokens using PKCE (S256). Tokens stay in memory.
4. **Access check (happens immediately).** The app shows a short "Checking your access" state and calls `GET /v1/me` (BE-1). The backend:
   - verifies the token (signature, expiry, issuer, audience),
   - loads the user's groups and roles from Postgres,
   - counts the documents the user can read, using the permission module,
   - writes a `session_start` audit event (BE-4),
   - returns the result.
5. **Dashboard.** On 200, the dashboard opens with the user's name, their access groups, and their readable document count. The chat is ready.
6. **No access.** On 401 from `/v1/me` (valid Keycloak account but no row in `users`, or disabled), show the no-access screen. The user can only sign out.
7. **Asking.** Each question goes to `POST /v1/query`. The backend runs its full permission-filtered retrieval for that request.
8. **Sources.** Clicking a citation calls the page endpoint. The backend re-checks access before returning the page.
9. **Sign out.** Clears the in-memory chat and tokens, ends the Keycloak session, and returns to the welcome screen.

### 5.2 Why access is checked at sign-in and again on every request

The sign-in check (`/v1/me`) gives the user an immediate, accurate picture of their access and catches unprovisioned accounts before they reach the chat. It does **not** replace the per-request checks. If an admin removes Alice from a group five minutes after she signs in, her next question must already reflect that. The backend's per-request checks guarantee this; the UI refreshing `/v1/me` (UI-F4) only keeps the sidebar display current.

---

## 6. Backend Changes Required

These are small additions to the existing backend. All permission logic stays in `app/security/permissions.py`.

| ID | Change | Details |
|---|---|---|
| BE-1 | `GET /v1/me` | Returns `{ "user_id", "display_name", "groups": [...], "roles": [...], "readable_document_count": int }`. Uses the existing identity resolution and the permission module to count readable documents (indexed, not deleted, ACL match). 401 for unknown or disabled users. |
| BE-2 | `GET /v1/me/documents?cursor=&limit=` | Paginated list of documents the caller can read: `{ "items": [{ "doc_id", "filename", "page_count", "ingested_at" }], "next_cursor" }`. `limit` 1 to 100, default 50. Sorted by filename. The permission predicate is applied in SQL. |
| BE-3 | `excerpt` in citations | Add `"excerpt": "<cited chunk text>"` to each object in the `citations` array of `POST /v1/query`. The chunk already passed the permission check for this request, so returning its text exposes nothing new. The UI uses it to highlight the passage on the page. |
| BE-4 | `session_start` audit event | Add an `event` column to `audit_log` (`query` or `session_start`, default `query`). `/v1/me` writes one row with `event = 'session_start'`, no question hash, and empty chunk arrays. Make `query_hmac` nullable for this event type. Write it at most once per token (track by the token's `jti` in memory with a short TTL), so window-focus refreshes do not flood the log. |
| BE-5 | Keycloak web client | Add a second client `loanrag-web` to the realm file: public, Standard Flow only, PKCE required with S256, Direct Access Grants **off**, exact redirect URI `https://localhost/*` (and the production origin), exact web origin `https://localhost`. Add an Audience mapper that puts `loanrag-api` in `aud`. Keep the existing `loanrag-api` client for development `curl`. |
| BE-6 | Keycloak behind Caddy | Run Keycloak with `KC_HTTP_RELATIVE_PATH=/auth`, `KC_PROXY_HEADERS=xforwarded`, and `KC_HOSTNAME=https://localhost/auth` so the token issuer is always `https://localhost/auth/realms/loanrag` whether reached from the browser or the API container. Update `JWT_ISSUER` to match. The API keeps fetching keys over the internal network: `JWKS_URL=http://keycloak:8080/auth/realms/loanrag/protocol/openid-connect/certs`. |
| BE-7 | Caddy routes | Serve the built frontend and route everything through one origin, so no CORS configuration is needed (section 11.3). |
| BE-8 | `POST /v1/admin/uploads` | Upload **one** PDF per request (the UI sends several requests for several files) as `multipart/form-data` with fields `file`, `groups[]`, `users[]`. Returns 202 with `{ "job_id", "doc_id", "filename", "status": "queued" }`. Validation in section 6.1. Admin role required. |
| BE-9 | Upload worker | New `worker` service in Docker Compose (same image, command `python -m app.worker`) that turns queued uploads into indexed documents. Details in section 6.1. |
| BE-10 | Upload status | `GET /v1/admin/uploads?status=&cursor=` lists jobs; `GET /v1/admin/uploads/{job_id}` returns one: `{ "job_id", "doc_id", "filename", "status", "pages_done", "pages_total", "failure_reason", "created_at", "uploaded_by" }`. `POST /v1/admin/uploads/{job_id}/retry` re-queues a failed job. |
| BE-11 | Pickers for access | `GET /v1/admin/groups` returns known group names (distinct values from `user_groups`). `GET /v1/admin/users?q=` returns up to 20 matching users `{ "user_id", "display" }`. Both admin only. |
| BE-12 | Admin file list | Extend `GET /v1/admin/documents` with paging (`cursor`, `limit`), `q` (filename search), `status` filter, and these fields per item: `doc_id`, `filename`, `page_count`, `status`, `groups`, `users`, `uploaded_by`, `ingested_at`. Metadata only, never page or chunk text. |
| BE-13 | Admin audit | Add audit events `upload`, `acl_change`, and `delete` with the admin's user ID, the `doc_id`, and a timestamp. No document content. |

Tests for BE-1 to BE-4 go in the backend test suite. Add these security tests:

- `/v1/me` for Bob returns a document count that excludes documents only Alice can read.
- `/v1/me/documents` never lists a document the caller cannot read, including `pending`, soft-deleted, and empty-ACL documents.
- After removing a user from a group, `/v1/me` and `/v1/me/documents` reflect it on the next call with the same token.
- Citation `excerpt` values always equal the text of a chunk in the request's permitted set.

### 6.1 Upload pipeline (BE-8 and BE-9)

**Validation at upload time (in the API, before anything is stored permanently):**

1. Caller has the `admin` role, otherwise 403.
2. Exactly one file, at most **50 MB**. Caddy enforces a 55 MB body limit on this route only (11.3); the API checks again and returns 413.
3. The file starts with `%PDF-` (checked from the bytes, not the extension or the browser's content type) and opens with pypdf. Otherwise 422 `{"error": "not_a_pdf"}`.
4. Not encrypted or password protected. Otherwise 422 `{"error": "encrypted_pdf"}`.
5. Between 1 and 500 pages. Otherwise 422 `{"error": "too_many_pages"}`.
6. At least one group or one user. Every group must exist in `GET /v1/admin/groups` and every user in `users`. Otherwise 422 `{"error": "invalid_access"}`. An upload with no access is rejected, never stored as default deny.
7. SHA-256 not already present (excluding soft-deleted documents). Otherwise 409 `{"error": "duplicate", "existing_filename": "..."}`.
8. Rate limit: 120 uploads per hour per admin.

**Storing:**

- Stream the upload to a temporary file, validate, then move it into encrypted document storage under a generated name (`{doc_id}.pdf`). Never use the uploaded filename as a path.
- Keep the original filename for display only, after removing any path parts and control characters, and trimming to 200 characters.
- In one transaction, insert the `documents` row (status `pending`, ACL from the request) and an `upload_jobs` row (status `queued`), and write the `upload` audit event. Return 202.

**New table:**

```sql
CREATE TABLE upload_jobs (
    job_id          UUID PRIMARY KEY,
    doc_id          UUID NOT NULL REFERENCES documents(doc_id),
    uploaded_by     TEXT NOT NULL REFERENCES users(user_id),
    status          TEXT NOT NULL CHECK (status IN ('queued','processing','indexed','failed')),
    pages_done      INTEGER NOT NULL DEFAULT 0,
    pages_total     INTEGER NOT NULL,
    failure_reason  TEXT CHECK (failure_reason IN
                      ('unreadable_pdf','ocr_failed','no_text_found','internal_error')),
    heartbeat_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
CREATE INDEX upload_jobs_status_idx ON upload_jobs (status, created_at);
```

**Worker loop:**

1. Claim up to 5 queued jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, set them to `processing`.
2. Take the index writer lock from `task.md` section 5.5. If the CLI is ingesting, wait and retry; never write the index without the lock.
3. Run the existing pipeline steps (extract, OCR, clean, chunk with the context prefix from section 17.2, embed). Refactor the pipeline so the ACL is passed in: the CLI gets it from `acl_seed.yaml`, uploads get it from the `documents` row.
4. Update `pages_done` and `heartbeat_at` every 5 pages so the UI can show progress on long files.
5. Add vectors, save the index atomically once for the whole batch, mark documents `indexed` and jobs `indexed`, and bump `index_state.version`, all as `task.md` section 5.4 describes.
6. On failure, set the job to `failed` with one of the fixed reasons, set the document to `failed`, keep the stored file for retry, and log the details server-side only.
7. On start, and every minute, reset `processing` jobs whose heartbeat is older than 5 minutes back to `queued` (crash recovery).

An uploaded document is invisible to everyone until it is `indexed`, because the permission predicate already requires that status.

**Backend tests for uploads:**

- A non-admin gets 403 on every admin endpoint.
- A text file renamed to `.pdf` is rejected; an encrypted PDF is rejected; a 51 MB file gets 413.
- An upload with no groups and no users, or with an unknown group, gets 422.
- A duplicate upload gets 409.
- A queued or processing document never appears in `/v1/query`, `/v1/me/documents`, or the page endpoint.
- After indexing, the document is visible to members of its groups only, and **not** to the uploading admin unless the admin is in one of those groups.
- Killing the worker mid-job leaves the job recoverable; it finishes after restart with no duplicate chunks or vectors.
- CLI ingestion and the worker running at the same time never corrupt the index (writer lock).
- A path-traversal filename like `../../etc/passwd.pdf` is stored safely and displayed sanitized.

---

## 7. Technology Stack

| Area | Choice | Notes |
|---|---|---|
| Build | Vite | Static build output served by Caddy. |
| Framework | React 18 with TypeScript (`strict: true`) | |
| Routing | React Router | Routes in section 8. |
| Server state | TanStack Query | Caching, retries, refetch on window focus. |
| Auth | `keycloak-js`, same major version as the Keycloak server | PKCE S256, `check-sso`, silent token refresh. |
| Styling | Tailwind CSS with design tokens as CSS variables | Tokens in section 9. |
| Accessible primitives | Radix UI (Dialog, Tooltip, ScrollArea, DropdownMenu) | Handles focus management and ARIA. |
| Motion | Framer Motion | Respects `prefers-reduced-motion`. |
| Icons | lucide-react | |
| Fonts | Self-hosted via `@fontsource` packages | No calls to external font servers, which keeps the content security policy strict. |
| Unit and component tests | Vitest, React Testing Library, MSW (mock API) | |
| End-to-end tests | Playwright against the real Docker Compose stack | |
| Accessibility checks | `@axe-core/playwright` | |
| Lint and format | ESLint (typescript-eslint, jsx-a11y), Prettier | |

Pin every dependency with a lockfile.

### 7.1 Folder structure

```
frontend/
├── index.html
├── public/
│   └── silent-check-sso.html      # used by keycloak-js check-sso
├── src/
│   ├── main.tsx
│   ├── App.tsx                    # routes, providers
│   ├── config/
│   │   ├── brand.ts               # product name, tagline
│   │   └── env.ts                 # reads VITE_* settings
│   ├── auth/
│   │   ├── keycloak.ts            # keycloak-js instance and init
│   │   ├── AuthProvider.tsx       # signed-in state, token refresh, sign out
│   │   └── RequireAuth.tsx        # route guard
│   ├── api/
│   │   ├── client.ts              # fetch wrapper: token, refresh, error mapping
│   │   ├── types.ts               # mirrors backend response models
│   │   ├── me.ts                  # useMe, useMyDocuments
│   │   ├── query.ts               # useAskQuestion
│   │   ├── pages.ts               # usePage
│   │   └── admin.ts               # uploads, jobs, documents, groups, users
│   ├── features/
│   │   ├── welcome/WelcomeScreen.tsx
│   │   ├── access/CheckingAccess.tsx
│   │   ├── access/NoAccessScreen.tsx
│   │   ├── dashboard/DashboardLayout.tsx
│   │   ├── sidebar/Sidebar.tsx
│   │   ├── sidebar/AccessCard.tsx
│   │   ├── sidebar/DocumentList.tsx
│   │   ├── chat/ChatWindow.tsx
│   │   ├── chat/MessageList.tsx
│   │   ├── chat/UserMessage.tsx
│   │   ├── chat/AssistantMessage.tsx
│   │   ├── chat/InsufficientMessage.tsx
│   │   ├── chat/ErrorMessage.tsx
│   │   ├── chat/ThinkingIndicator.tsx
│   │   ├── chat/Composer.tsx
│   │   ├── chat/StarterPrompts.tsx
│   │   ├── chat/citations.ts      # parse [n] markers into segments
│   │   ├── source/SourcePanel.tsx # page viewer with highlight
│   │   ├── admin/FilesPage.tsx    # table, filters, summary chips
│   │   ├── admin/UploadDialog.tsx # drop zone, access pickers
│   │   ├── admin/UploadTray.tsx   # background progress
│   │   ├── admin/EditAccessDialog.tsx
│   │   └── admin/uploadQueue.ts   # XHR uploads, 2 at a time, job polling
│   ├── components/                # Button, Avatar, Chip, Toast, ThemeToggle
│   ├── styles/
│   │   ├── tokens.css             # color and type tokens, light and dark
│   │   └── index.css
│   └── lib/
│       ├── highlight.ts           # find excerpt in page text, split into segments
│       └── greeting.ts            # time-of-day greeting
├── tests/
│   ├── unit/
│   ├── component/
│   └── e2e/
├── package.json
├── tailwind.config.ts
├── vite.config.ts
└── playwright.config.ts
```

---

## 8. Screens and Features

### 8.1 Routes

| Route | Screen | Access |
|---|---|---|
| `/` | Welcome screen | Public. Redirects to `/dashboard` if a session exists. |
| `/dashboard` | Dashboard with chat | Signed in and `/v1/me` returned 200. |
| `/no-access` | No-access screen | Signed in but `/v1/me` returned 401. |
| `/admin/files` | Admin file management and upload | Signed in. The backend returns 403 to non-admins; the page then shows "This page is for administrators." |
| any other | Redirect to `/` | |

The route guard only controls navigation and display. It is not a security control (section 2).

### 8.2 Welcome screen (UI-F1)

- Product name, one plain sentence about what it does ("Ask questions about the loan files you work with, and see exactly where each answer comes from."), and a "Sign in" button.
- A small animated preview on one side: a short, looping example exchange (a question, then an answer with a citation chip that gets highlighted). Built from static demo text, never real data. This is the welcome screen's single motion moment.
- No sign-up link. Accounts are created by admins.

### 8.3 Checking access (UI-F2)

- Shown while `/v1/me` is in flight after sign-in. Centered logo mark with a subtle pulse and the text "Checking your access".
- If it takes longer than 8 seconds, add "This is taking longer than usual" and a "Try again" button.
- On a network failure, show a clear message and "Try again". Do not show the dashboard without a successful `/v1/me`.

### 8.4 No-access screen (UI-F3)

- Message: "Your account is signed in, but it hasn't been set up for LoanLens yet. Ask your administrator to add you."
- "Sign out" button. Nothing else.

### 8.5 Dashboard layout (UI-F4)

Desktop (1024px and wider):

```
+--------------------+----------------------------------------+------------------+
| SIDEBAR (280px)    | CHAT WINDOW (fluid, max 760px content) | SOURCE PANEL     |
|                    |                                        | (420px, slides   |
| LoanLens logo      | Good afternoon, Alice                  |  in on citation  |
|                    | Ask about any loan file you can open.  |  click)          |
| [AK] Alice Kim     |                                        |                  |
| Underwriting       |  [starter prompt] [starter prompt]     | loan_004512.pdf  |
| Western region     |  [starter prompt]                      | Page 63 of 87    |
|                    |                                        | < prev   next >  |
| 642 loan files     |  ... messages ...                      |                  |
|                    |                                        | ...page text...  |
| Your files         |                                        | [highlighted     |
| [search box]       |                                        |  cited passage]  |
| loan_000123.pdf    | +------------------------------------+ |                  |
| loan_000124.pdf    | | Ask about a loan file...      [->] | | [Close]          |
| ...                | +------------------------------------+ |                  |
|                    | Each question is answered on its own.  |                  |
| [+ New chat]       |                                        |                  |
| [theme] [Sign out] |                                        |                  |
+--------------------+----------------------------------------+------------------+
```

Tablet (768px to 1023px): sidebar collapses to an icon rail that expands as an overlay; source panel overlays the chat from the right.

Phone (below 768px): sidebar becomes a drawer opened from a header button; source panel opens as a full-screen sheet with a clear close button.

**Sidebar contents:**

- **User card:** initials avatar (color derived from user ID), display name.
- **Access card:** the groups returned by `/v1/me`, shown as friendly labels (map `western_region` to "Western region" with a simple formatter, no hard-coded list) and the readable file count ("642 loan files"). Display only.
- **Your files:** list from `GET /v1/me/documents`, loaded page by page as the user scrolls. A search box filters the loaded items by filename on the client. Clicking a file opens page 1 in the source panel. Empty state: "No loan files are shared with you yet."
- **New chat** button: clears the conversation (with a confirm if it has more than 2 messages).
- **Manage files** link (folder icon), shown only when `/v1/me` includes the `admin` role. Hiding it for others is cosmetic; the backend enforces the role on every admin endpoint.
- **Theme toggle** (light, dark, system) and **Sign out**.

**Keeping access current:** `/v1/me` and the first page of `/v1/me/documents` refetch when the window regains focus and every 5 minutes. If the groups change, the access card updates with a brief, gentle highlight on the changed area and a toast: "Your access was updated." No sign-out needed.

### 8.6 Chat window (UI-F5 to UI-F12)

**UI-F5. Empty state.** Time-of-day greeting with the user's first name ("Good morning, Alice"), one line ("Ask about any loan file you can open."), and 3 or 4 starter prompts as chips. Starter prompts are fixed templates, never generated from document content:

- "What's the interest rate on loan ___?"
- "Summarize the conditions of approval for loan ___."
- "When is the next payment due on loan ___?"
- "Who is the borrower on loan ___?"

Clicking a chip fills the composer and places the cursor at the blank. It does not send.

**UI-F6. Composer.**

- Auto-growing text area, 1 to 6 lines, placeholder "Ask about a loan file...".
- Enter sends, Shift+Enter adds a line. Send button with an icon and an accessible label "Send".
- Character counter appears after 800 characters; sending is blocked above 1,000 with the message "Questions can be up to 1,000 characters." (matches backend F14).
- Empty or whitespace-only input cannot be sent.
- One question at a time: while an answer is pending, the send button shows a spinner and is disabled, but the user can keep typing the next question.

**UI-F7. Messages.**

- User messages: right-aligned bubbles in the primary color with light text.
- Assistant messages: left-aligned, no heavy bubble, a small LoanLens mark beside them, comfortable reading width and line height. Text rendered as plain text with line breaks preserved. Never render HTML or Markdown from the response.
- New messages animate in with a short fade and rise (150 to 200 ms). This is user-triggered motion, so it is allowed; nothing else on the page animates on its own.
- Each assistant message has a hover or focus toolbar with "Copy answer".
- The list auto-scrolls to the newest message unless the user has scrolled up; in that case show a "Jump to latest" pill.

**UI-F8. Thinking state.** While waiting, show an assistant placeholder with three bouncing dots and a status line that rotates on a timer: "Searching your files", then "Reading the sources", then "Writing the answer". These are cosmetic timers and MUST NOT reflect backend internals such as retrieval rounds (those could hint at how much of the corpus the user can see).

**UI-F9. Independent questions hint.** Under the composer, in small muted text: "Each question is answered on its own, so include the loan number or borrower name." This sets expectations honestly, since the backend has no conversation memory.

**UI-F10. Citations.**

- Parse `[1]`, `[2]` markers in the answer into inline citation chips (small rounded tags with the number). Text that looks like a marker but has no matching citation is shown as plain text.
- Below the answer, a "Sources" row lists each citation once as a chip: file icon, filename, "p. 1".
- Clicking either chip type opens the source panel at that page with that citation's excerpt highlighted.
- Chips have accessible names: "Source 1: loan_004512.pdf, page 1".

**UI-F11. Insufficient answers.** When `status` is `insufficient`, show the backend's message in a calm, neutral card (soft background, info icon), not an error style. Add one helpful line: "Try rephrasing, or include the loan number." Do not add anything that suggests hidden files exist, such as "you may not have access" or "ask for permission".

**UI-F12. Errors.** Shown inline in the conversation as a compact card with a "Try again" button that resends the same question.

| Situation | Message |
|---|---|
| 429 | "You're asking questions quickly. Wait a minute, then try again." |
| 503 | "The assistant isn't available right now. Try again in a moment." |
| Network failure | "Couldn't reach LoanLens. Check your connection and try again." |
| 422 | "That question couldn't be sent. Keep it under 1,000 characters." |
| 401 after refresh fails | Not shown in chat. Go to sign-in (section 10.2). |
| Anything else | "Something went wrong while answering. Try again." |

Never show status codes, request IDs, stack traces, or raw JSON. The request ID is attached to the error card as a hidden data attribute so it can be read in developer tools during debugging.

### 8.7 Source panel (UI-F13)

- Header: filename, "Page 63 of 87", previous and next page buttons, a small "Go to page" number field (files run 50 to 100 pages), and a close button.
- Show the page's section name (for example, "Bank statements") under the filename when the backend provides it, so the user knows where they are in a long file. This needs the page endpoint to include the section of the page's first chunk; add it alongside BE-3.
- Body: the page text from the page endpoint, in a readable serif-free text style with preserved line breaks.
- **Highlight:** find the citation's `excerpt` within the page text (`lib/highlight.ts`), normalizing whitespace on both sides for matching. Wrap the matched range in a `<mark>` styled with the highlighter token, scroll it into view, and play a one-time highlighter sweep (the mark's background grows from left to right over 400 ms). If the excerpt isn't found, show the page without a highlight and a small note: "Couldn't locate the exact passage on this page."
- Highlighting MUST be built by splitting text into React elements, never with `dangerouslySetInnerHTML`.
- Previous and next pages call the page endpoint for those page numbers. Highlights only apply to the cited page.
- A 404 (for example, access was removed after the answer appeared) shows: "This page isn't available." with a close button.
- Keyboard: Escape closes; focus returns to the chip that opened it.

### 8.8 Sign out (UI-F14)

Clears all TanStack Query caches and chat state, then calls `keycloak.logout({ redirectUri: origin })`. After sign-out, the browser's back button must not show the previous conversation (UI-T6).

### 8.9 Admin: files and uploads (UI-F15 to UI-F20)

**UI-F15. Files page (`/admin/files`).**

```
+--------------------+---------------------------------------------------------+
| SIDEBAR            | Loan files                          [ Upload files ]    |
| ...                |                                                         |
| > Manage files     | 1,000 files   3 processing   1 needs attention          |
|                    |                                                         |
|                    | [search by filename]   [status: all v]                  |
|                    |                                                         |
|                    | File              Pages  Access          Status   ...   |
|                    | loan_004512.pdf     87   Underwriting    Ready    [...] |
|                    | loan_004513.pdf     62   Servicing (+1)  Processing 40% |
|                    | loan_004514.pdf      -   Compliance      Failed   [...] |
+--------------------+---------------------------------------------------------+
```

- Header with the title and a prominent **Upload files** button (spruce, upload icon).
- Summary chips: total files, processing, and failed ("needs attention"). Clicking a chip sets the status filter.
- Table columns: file name, pages, access (group chips, then "+N" for more), status badge, uploaded date, uploaded by, and an actions menu with **Edit access**, **Retry** (failed only), and **Delete**.
- Search by filename and filter by status, both sent to the backend (BE-12). Load more pages on scroll.
- The admin page shows metadata only. There is no "open" action and no document text, because being an admin does not grant read access (`task.md` section 6.2).
- Empty state: "No loan files yet. Upload PDFs to make them searchable."

**UI-F16. Upload dialog.**

- Opened by **Upload files**. A large drop zone: "Drop loan PDFs here, or browse your computer." On drag-over, the border turns marigold and the zone lifts slightly.
- Selected files appear in a list with name, size, and a remove button. Up to 20 files per batch.
- Quick checks in the browser for convenience only (the backend decides): `.pdf` extension and 50 MB size. Files that fail show the reason inline and are left out.
- **Who can read these files** section, required:
  - Groups: multi-select from `GET /v1/admin/groups`, shown with friendly labels.
  - People (optional): searchable picker using `GET /v1/admin/users?q=`.
  - The upload button stays disabled until at least one group or person is chosen.
  - A note under the pickers: "Only people in these groups can find these files in answers. That includes you: being an admin doesn't let you read files."
- Primary button text matches the count: "Upload 3 files".

**UI-F17. Upload progress.**

- Files upload two at a time. Use `XMLHttpRequest` (or an upload library built on it) because `fetch` does not report upload progress.
- Each file shows a status line and progress bar that moves through: **Uploading** (bytes sent) -> **Waiting** (queued) -> **Processing** (pages done of total, for example "Processing, page 40 of 87") -> **Ready** or **Failed**.
- Poll `GET /v1/admin/uploads/{job_id}` every 2 seconds while a job is queued or processing; stop when it finishes.
- The dialog can be closed at any time. Progress continues in the files table and in a small **Uploads** tray in the bottom corner, which collapses to a pill ("2 processing") and disappears when everything is done.
- When a batch finishes, show a toast: "3 files are ready" or "2 files are ready, 1 needs attention".

**UI-F18. Failure messages** (shown on the file row and in the tray):

| Reason | Message |
|---|---|
| `not_a_pdf` | "This file isn't a PDF." |
| `encrypted_pdf` | "This PDF is password protected. Remove the password and upload it again." |
| `too_many_pages` | "This PDF has more than 500 pages." |
| 413 | "This file is larger than 50 MB." |
| `duplicate` | "This file was already uploaded as loan_000123.pdf." |
| `invalid_access` | "Choose at least one group or person who can read this file." |
| `unreadable_pdf` | "This PDF couldn't be read. Try exporting it again and re-uploading." |
| `ocr_failed` / `no_text_found` | "No readable text was found in this PDF." |
| `internal_error` | "Processing failed. Try again." (with **Retry**) |
| 429 | "Too many uploads in the last hour. Try again later." |

**UI-F19. Edit access.** A dialog with the same group and people pickers, prefilled. Saving calls `PUT /v1/admin/documents/{doc_id}/acl`, then shows "Access updated". The same at-least-one rule applies.

**UI-F20. Delete.** A confirm dialog: "Delete loan_004512.pdf? It will stop appearing in answers for everyone." Buttons: "Delete file" (coral) and "Cancel". On success, the row fades out and a toast says "File deleted".

---

## 9. Visual Design

### 9.1 Direction

The subject is loan paperwork, and the people using it spend their day reading files and checking figures. The identity borrows from that world: the deep green of banking ledgers, the warm yellow of a highlighter pen on a printed page, and clean paper-like reading surfaces. It should feel energetic and friendly, not like a grey enterprise tool, while keeping answers easy to read.

**The one memorable thing:** the highlighter. When a citation opens, the cited passage gets a marigold highlighter sweep, the same marigold that marks citation chips in answers. It ties every answer visibly to its evidence, which is the product's whole promise. Everything else stays quieter so this moment stands out.

### 9.2 Color tokens

Defined as CSS variables in `tokens.css`, with dark values under `@media (prefers-color-scheme: dark)` and a `[data-theme]` override for the manual toggle.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--spruce` | `#0E5A4B` | `#3FB59A` | Primary: user bubbles, send button, links, focus rings |
| `--spruce-deep` | `#083D33` | `#0F2A25` | Sidebar background (light text on it in both themes) |
| `--marigold` | `#F4B13E` | `#E9A82E` | Citation chips, highlighter, small accents |
| `--highlight` | `#FBE3A6` | `#5C4613` | Highlighted passage background |
| `--paper` | `#F6F8F6` | `#121816` | Page background |
| `--surface` | `#FFFFFF` | `#1A2220` | Chat area, source panel, cards |
| `--ink` | `#1A2630` | `#E4ECE9` | Body text |
| `--ink-muted` | `#5B6B70` | `#97A8A3` | Hints, timestamps, secondary text |
| `--coral` | `#D9584A` | `#F07A6C` | Errors only |
| `--sky` | `#E3F1F7` | `#1C2E35` | Insufficient-answer card background |

All text and icon combinations MUST meet WCAG AA contrast (4.5:1 for body text, 3:1 for large text and UI components). Check each pair and adjust values if needed.

### 9.3 Typography

- **Display:** Bricolage Grotesque (600 and 700) for the product name, greeting, and panel titles. Its slightly quirky shapes give the app personality.
- **Body and UI:** IBM Plex Sans (400, 500, 600) for messages, controls, and page text. Plex has a financial, document-like character and clear figures.
- Use tabular figures (`font-variant-numeric: tabular-nums`) in the source panel and anywhere numbers line up.
- Scale (rem): 0.8125 (meta), 0.9375 (UI), 1 (messages), 1.25 (panel title), 1.75 (greeting), 2.5 (welcome headline). Message line height 1.6. Message width capped near 70 characters.
- Sentence case everywhere. No all-caps labels.

### 9.4 Shape and depth

- Radius by role, not one value everywhere: message bubbles 18px with a 6px corner on the speaker's side, chips fully rounded, composer 16px, panels 0 on the attached edge.
- Depth comes from color layering (dark sidebar, paper background, white chat surface), not from shadows on every element. Use one soft shadow only on floating elements: the composer and the source panel.
- The sidebar's deep spruce gives the screen a strong anchor; the paper and white surfaces keep the reading area light.

### 9.5 Lively details (use all, keep each small)

- Initials avatars in colors from a fixed set of 6 harmonious hues, picked by hashing the user ID.
- The greeting changes with time of day.
- Starter prompt chips have a marigold outline that fills on hover.
- Send button icon nudges forward slightly when pressed.
- The highlighter sweep (section 8.7).
- A subtle grain or faint ruled-paper texture on the welcome screen background only.

### 9.6 Motion rules

- Only one automatic, non-user-triggered animation per screen: the welcome preview loop, or the dashboard greeting fading in once on first load.
- All other motion responds to the user: messages appearing, panels sliding, chips filling, the highlighter sweep.
- Durations 150 to 400 ms, ease-out.
- With `prefers-reduced-motion: reduce`, replace all motion with instant changes or simple fades, and stop the welcome preview loop on its first frame.

### 9.7 Writing style

- Plain, specific, active voice. Buttons say what they do: "Sign in", "Send", "New chat", "Sign out", "Copy answer", "Try again".
- Errors explain what happened and what to do next. No apologies, no vague "Oops".
- Empty states invite action ("No loan files are shared with you yet." in the file list; the greeting and starter prompts in the chat).

---

## 10. Authentication Details

### 10.1 keycloak-js setup

```ts
const keycloak = new Keycloak({
  url: env.keycloakUrl,        // "/auth" (same origin via Caddy)
  realm: env.keycloakRealm,    // "loanrag"
  clientId: env.keycloakClient // "loanrag-web"
});

await keycloak.init({
  onLoad: "check-sso",
  pkceMethod: "S256",
  silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
  checkLoginIframe: false
});
```

- Tokens stay in `keycloak-js` memory. The app MUST NOT copy tokens into `localStorage`, `sessionStorage`, cookies, or URLs.

### 10.2 Token handling in the API client

- Before every request: `await keycloak.updateToken(30)` to refresh if the token expires within 30 seconds.
- Attach `Authorization: Bearer <token>`.
- On a 401 response: refresh once and retry once. If that fails, clear app state and call `keycloak.login()`. Show a toast after return: "Your session ended. Sign in again to continue."
- Never retry a 401 more than once.

---

## 11. Frontend Security Requirements

| ID | Requirement |
|---|---|
| UI-S1 | No permission logic in the frontend (section 2). Code review checks that no component filters or hides results by group or role. |
| UI-S2 | Render all API text (answers, excerpts, page text, filenames) as text nodes. `dangerouslySetInnerHTML` is banned by an ESLint rule. |
| UI-S3 | Tokens only in `keycloak-js` memory (section 10.1). |
| UI-S4 | Questions, answers, excerpts, and page text are never written to `localStorage`, `sessionStorage`, IndexedDB, or cookies. Chat lives in React state and the TanStack Query cache, cleared on sign-out. |
| UI-S5 | No secrets in the frontend bundle. Only the public client ID, realm, and URLs are configured. |
| UI-S6 | `loanrag-web` uses Standard Flow with PKCE S256 only, exact redirect URIs, exact web origins, Direct Access Grants off (BE-5). |
| UI-S7 | Strict content security policy from Caddy (section 11.2). |
| UI-S8 | Do not log questions, answers, or page text to the browser console. Strip `console.*` calls from production builds. |
| UI-S9 | Disable caching of API responses at the HTTP level: the API sends `Cache-Control: no-store` on all `/v1/*` responses (small backend header change). |

### 11.1 What the UI never shows

- Raw JSON, status codes, stack traces, chunk IDs, or document IDs in visible text.
- Retrieval rounds, latency per stage, or debug headers.
- Any hint that hidden documents exist.

### 11.2 Content security policy

Set by Caddy on the frontend routes:

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
font-src 'self';
img-src 'self' data:;
connect-src 'self';
frame-src 'self';
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```

`frame-src 'self'` is needed for the silent SSO check, which works because Keycloak is served from the same origin under `/auth`. `'unsafe-inline'` for styles is needed by Framer Motion's inline style updates; scripts stay strict.

Also set `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, and `Permissions-Policy: camera=(), microphone=(), geolocation=()`.

### 11.3 Caddy routing (single origin)

| Path | Goes to |
|---|---|
| `/v1/admin/uploads` (POST) | `api:8000`, with a 55 MB request body limit |
| `/v1/*`, `/health` | `api:8000`, with a 1 MB request body limit |
| `/auth/admin/*` | Blocked (404). The Keycloak admin console is never exposed (S15). |
| `/auth/*` | `keycloak:8080` |
| everything else | Built frontend files, with fallback to `index.html` for client-side routes |

Serving the frontend, API, and Keycloak from one origin means no CORS setup and a simpler, stricter CSP.

---

## 12. Testing

### 12.1 Unit and component tests (Vitest, React Testing Library, MSW)

- `citations.ts`: splits answers into text and chip segments; ignores markers with no matching citation; handles markers at the start, end, and next to punctuation.
- `highlight.ts`: finds an excerpt despite whitespace differences; returns no highlight when not found; handles excerpts that appear twice (highlight the first).
- Composer: Enter sends, Shift+Enter adds a line, 1,000-character limit, blank input blocked, disabled while pending.
- Each error code maps to the right message (UI-F12).
- Insufficient answers render in the neutral style with no access-related wording.
- Access card updates and shows the toast when `/v1/me` returns different groups.
- API client: refreshes before requests, retries a 401 once, then triggers sign-in.

### 12.2 End-to-end tests (Playwright, real stack)

Run against `docker compose up` with the synthetic corpus and seeded users. Use the Keycloak sign-in page for real logins.

| ID | Test | Expected result |
|---|---|---|
| UI-T1 | Alice signs in. | "Checking your access" appears, then the dashboard with her name, her groups, and her file count. |
| UI-T2 | Alice asks about loan LN-2024-004512. | Answer appears with citation chips. Clicking one opens the source panel on the right page with the passage highlighted. |
| UI-T3 | Bob signs in and asks the same question. | Neutral insufficient card. The hidden filename does not appear anywhere in the DOM or in any network response body captured during the test. |
| UI-T4 | While Alice is signed in, remove her from `underwriting` in Postgres, then refocus the window. | Access card updates with the toast. Her next question about the loan gets the insufficient card. Clicking her earlier citation now shows "This page isn't available." |
| UI-T5 | A user exists in Keycloak but not in Postgres. | No-access screen. No chat is shown. |
| UI-T6 | Alice signs out, then presses the browser back button. | Welcome screen or sign-in, never the old conversation. |
| UI-T7 | After a full session, inspect browser storage. | No tokens, questions, answers, or page text in `localStorage`, `sessionStorage`, IndexedDB, or cookies. |
| UI-T8 | Access token lifespan set to 1 minute in a test realm; Alice keeps chatting for 3 minutes. | Silent refresh keeps working with no sign-in prompt. |
| UI-T9 | Send 31 questions within a minute. | The 31st shows the rate-limit card with "Try again". |
| UI-T10 | Run axe on the welcome screen, dashboard, and open source panel, in light and dark themes. | No serious or critical violations. |
| UI-T11 | Repeat UI-T1 and UI-T2 at 390px width. | Drawer sidebar and full-screen source sheet work; nothing scrolls sideways. |
| UI-T12 | Keyboard only: sign in, ask, open a citation, close it, sign out. | Every step works with visible focus; focus returns to the chip after closing. |
| UI-T13 | A page contains text like `<img src=x onerror=alert(1)>`. | Shown as plain text; no script runs. |
| UI-T14 | Admin uploads two 80-page PDFs with the `underwriting` group. | Upload bars, then "Processing, page X of 80", then Ready, and a toast. Alice (in `underwriting`) can then ask about them and get cited answers. |
| UI-T15 | Bob (not an admin) signs in. | No Manage files link. Visiting `/admin/files` directly shows "This page is for administrators." |
| UI-T16 | Admin drops a renamed text file, an encrypted PDF, and a duplicate. | Each shows its own message from UI-F18; valid files in the same batch still upload. |
| UI-T17 | Admin removes `underwriting` from a file's access. | "Access updated". Alice's next question about it gets the insufficient card. |
| UI-T18 | Admin deletes a file. | Row disappears; the file no longer appears in Alice's file list or answers. |
| UI-T19 | Admin with no document groups uploads a file to `underwriting`, then asks about it in chat. | Insufficient card. Admin role does not grant read access. |
| UI-T20 | Inspect the admin page's DOM and network responses. | Only metadata; no page text or chunk text anywhere. |
| UI-T21 | Close the upload dialog mid-batch, then reload the page. | In-progress jobs still show in the table with live status (job status comes from the backend, not browser memory). |
| UI-T22 | Open a citation on page 74 of an 87-page file; use "Go to page" to jump to 10, then back. | Correct pages load; the highlight shows only on the cited page. |

---

## 13. Implementation Phases

### Phase 0: Large loan files (section 17)
- [ ] Database migration from section 17.3.
- [ ] Ingestion changes from section 17.2 (document fields, context prefix).
- [ ] Corpus generator changes from section 17.4; regenerate the corpus and evaluation questions.
- [ ] Re-ingest from scratch, rerun the existing test suite, and add the tests in section 17.7.
- [ ] Rerun the evaluation with the new metrics from section 17.5 and replace the numbers in `docs/evaluation.md`.

### Phase A: Backend additions
- [ ] BE-1 `/v1/me`, BE-2 `/v1/me/documents`, BE-3 citation excerpts, BE-4 session audit event, UI-S9 `no-store` header.
- [ ] Backend security tests from section 6.

### Phase B: Keycloak and Caddy
- [ ] BE-5 `loanrag-web` client with audience mapper in the realm file.
- [ ] BE-6 Keycloak under `/auth` with fixed hostname; update `JWT_ISSUER`.
- [ ] BE-7 Caddy routes and security headers (sections 11.2 and 11.3).
- [ ] Manual check: browser sign-in reaches a placeholder page and `/v1/me` returns 200.

### Phase C: Frontend foundation
- [ ] Scaffold Vite, React, TypeScript, Tailwind, ESLint (with the `dangerouslySetInnerHTML` ban), Prettier, Vitest.
- [ ] Design tokens, fonts, light and dark themes, theme toggle.
- [ ] Base components: Button, Chip, Avatar, Toast.

### Phase D: Auth and access
- [ ] keycloak-js setup, AuthProvider, API client with refresh and 401 handling.
- [ ] Welcome, Checking access, No-access screens; route guard.
- [ ] Tests: API client unit tests; UI-T1, UI-T5.

### Phase E: Dashboard and sidebar
- [ ] Dashboard layout for desktop, tablet, and phone.
- [ ] User card, access card with refresh on focus, file list with paging and search, New chat, Sign out.
- [ ] Tests: access card update; UI-T6, UI-T11 (layout part).

### Phase F: Chat
- [ ] Composer, messages, thinking state, starter prompts, independent-questions hint.
- [ ] Citation parsing and chips, Sources row, insufficient card, error cards, copy answer, jump to latest.
- [ ] Tests: section 12.1 chat tests; UI-T2 (answer part), UI-T3, UI-T9, UI-T13.

### Phase G: Source panel
- [ ] Panel with page navigation, highlight matching, highlighter sweep, 404 state, keyboard handling.
- [ ] "Go to page" field and section label for long files.
- [ ] Tests: `highlight.ts` unit tests; UI-T2 (panel part), UI-T4, UI-T12, UI-T22.

### Phase G2: Admin uploads
- [ ] Backend: BE-8 to BE-13, the `upload_jobs` migration, the worker service, and the pipeline refactor that passes the ACL in.
- [ ] Backend upload tests from section 6.1.
- [ ] Frontend: Files page, Upload dialog with access pickers, upload queue with progress, Uploads tray, Edit access, Delete.
- [ ] Tests: UI-T14 to UI-T21.

### Phase H: Polish and hardening
- [ ] Welcome preview animation, greeting entrance, reduced-motion handling.
- [ ] Contrast check of every token pair.
- [ ] Tests: UI-T7, UI-T8, UI-T10, full UI-T11.
- [ ] Production build served by Caddy; frontend build stage added to the Dockerfile.

### Phase I: Docs and demo
- [ ] README section: opening the app, seeded demo users and their access.
- [ ] Screenshots of light and dark themes for the README.
- [ ] Demo video (about 2 to 3 minutes): an admin uploads a few loan files for the underwriting group and watches them process; Alice signs in, asks about one, and opens a highlighted citation deep in the file; Bob asks the same question and gets the insufficient card; the admin removes Alice's access and her sidebar updates live.

---

## 14. Configuration

Frontend build settings (Vite, public values only):

| Variable | Example |
|---|---|
| `VITE_KEYCLOAK_URL` | `/auth` |
| `VITE_KEYCLOAK_REALM` | `loanrag` |
| `VITE_KEYCLOAK_CLIENT_ID` | `loanrag-web` |
| `VITE_API_BASE` | `/v1` |

Backend and Keycloak changes:

| Variable | New value |
|---|---|
| `JWT_ISSUER` | `https://localhost/auth/realms/loanrag` |
| `JWKS_URL` | `http://keycloak:8080/auth/realms/loanrag/protocol/openid-connect/certs` |
| `KC_HTTP_RELATIVE_PATH` | `/auth` |
| `KC_HOSTNAME` | `https://localhost/auth` |
| `KC_PROXY_HEADERS` | `xforwarded` |

---

## 15. Definition of Done

- [ ] A user can open the app, sign in, see their access on the dashboard, chat, open highlighted sources, and sign out, without ever seeing a terminal, JSON, or technical detail.
- [ ] Access is resolved by the backend right after sign-in and checked again on every request.
- [ ] Access changes appear in the sidebar on window focus, and in answers on the next question, without signing out.
- [ ] All backend security tests from section 6 and all UI tests in section 12 pass.
- [ ] No tokens or document content in browser storage (UI-T7).
- [ ] No serious accessibility violations in light or dark theme (UI-T10).
- [ ] Works at phone, tablet, and desktop widths.
- [ ] An admin can upload loan PDFs, set their access, and see them become searchable without using a terminal.
- [ ] The corpus is 1,000 files of 50 to 100 pages, and `docs/evaluation.md` reports recall by page depth and with and without the context prefix.
- [ ] README updated with screenshots and the demo video.

---

## 16. Open Decisions

1. **User and group management.** Uploads, access editing, and deletion are now in the app (section 8.9). Creating users and groups still happens in Keycloak and Postgres. A later version could add this, but it needs care: it must keep Keycloak user IDs and Postgres rows in sync.
2. **Custom Keycloak sign-in theme.** The default Keycloak page works. A custom theme using the same tokens and fonts would make sign-in feel part of the product; add it in Phase H if time allows.
3. **Chat history.** v1 keeps chats in memory only. Saving history would need a backend store with the same permission rules (a saved answer must disappear if access to its source is removed), so it is deliberately left out.

---

## 17. Large Loan Files

### 17.1 What changes and why

Real loan files run to dozens of pages. Version 1 used 3 to 12 page synthetic files, which made retrieval too easy. Version 2 uses 50 to 100 pages per file, about 75,000 pages across the 1,000-file corpus. This changes four things in the built system:

| Area | Version 1 (`task.md` v2.1) | Version 2 |
|---|---|---|
| Corpus | 3 to 12 pages per file | 50 to 100 pages, structured like real loan files (17.4) |
| Chunk context | Chunk text only | Document context prefix for search (17.2) |
| Index size | About 15K to 30K chunks | About 110K to 160K chunks (17.6) |
| Evaluation | No depth breakdown | Recall by page depth and context-dependent questions (17.5) |

### 17.2 Document context for chunks (required)

In a 90-page loan file, most pages never mention the loan number or borrower name. Those appear on the first pages and in a repeating page header that cleaning removes (`task.md` section 5.4, step 5). A chunk from a bank statement on page 63 would then never match a question like "What was the ending balance on the April bank statement for loan LN-2024-004512?"

Fix this by giving every chunk its document's context:

- **Extract document fields.** At ingestion, read `loan_number` and `borrower_name` from the first 5 pages with regular expressions and store them on the `documents` row. If either is not found, leave it null and list the file as a warning in the ingestion summary.
- **Build a prefix.** For each chunk: `"Loan LN-2024-004512 | Borrower Jane Doe | Section: Bank statements"`, using the chunk's detected section.
- **Embed with the prefix.** Embed `context_prefix + "\n" + text`, not the text alone.
- **Keyword search with the prefix.** The `tsv` column is generated from `context_prefix || ' ' || text` (17.3).
- **Identifier search.** Every chunk's `identifiers` array includes its document's loan number, in addition to identifiers found in the chunk's own text.
- **Never display the prefix as document content.** Citations, the `excerpt` field (BE-3), the page endpoint, and the prompt's source blocks use the raw `text` only. The prompt already carries the filename and page in each source tag (`task.md` section 8.5).
- **Token budget.** The 400-token chunk target (`task.md` section 8.1) includes the prefix, so the body is about 400 tokens minus the prefix length.
- **Uploads use the same code.** The upload worker (section 6.1) runs the same extraction and prefix steps.

### 17.3 Database migration

Add an Alembic migration:

```sql
ALTER TABLE documents
    ADD COLUMN loan_number   TEXT,
    ADD COLUMN borrower_name TEXT;

ALTER TABLE chunks
    ADD COLUMN context_prefix TEXT NOT NULL DEFAULT '';

-- Redefine the generated full-text column to include the prefix.
-- Dropping the column also drops its index, so recreate both.
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks
    ADD COLUMN tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('simple', context_prefix || ' ' || text)) STORED;
CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
```

Because the corpus is regenerated and re-ingested anyway (17.8), no data backfill is needed.

### 17.4 Synthetic corpus

These rules replace the size, content, planted-fact, and scanned-page rules in `task.md` section 11.1. Everything else in that section (near-duplicate borrowers, prompt-injection documents, ACL groups and users, the `--seed` flag) stays as built.

- **Size:** 1,000 documents. Each document's length is drawn uniformly from **50 to 100 pages**, about 75,000 pages in total.
- **Flags:** `--count` (default 1000), `--min-pages` (default 50), `--max-pages` (default 100), `--seed`. Use `--count 50` for a quick development corpus and the full size for evaluation.
- **Structure:** each document is a realistic loan file made of the sections below, in this order. Draw the total page count, then split it across sections in roughly these proportions, giving every section at least one page. Each page has a header with the loan number and "Page X of Y", as real files do.

  | Section | Approx. share of pages | Content |
  |---|---|---|
  | Loan application | 6% | Borrower details, employment, assets, liabilities, declarations |
  | Credit report | 7% | Scores, tradelines table, inquiries, public records summary |
  | Income documents | 15% | Pay stubs, W-2s, two years of tax return pages |
  | Bank statements | 12% | Two or three months across one or two accounts, with transaction tables and ending balances |
  | Verification of employment | 2% | Employer, title, start date, salary |
  | Purchase agreement | 8% | Price, earnest money, contingencies, closing date |
  | Appraisal report | 12% | Property description, comparable sales table, appraised value |
  | Title commitment | 5% | Legal description, exceptions, requirements |
  | Insurance | 3% | Hazard policy, premium, coverage amounts |
  | Underwriting worksheet and notes | 5% | Ratios (DTI, LTV), risk notes, exceptions granted |
  | Conditions of approval | 3% | Prior-to-close and prior-to-funding conditions |
  | Loan estimate and closing disclosure | 7% | Fee tables, cash to close, rate and payment |
  | Promissory note | 4% | Principal, rate, term, payment, late charge |
  | Deed of trust | 6% | Mostly standard legal text with the borrower and property filled in |
  | Standard disclosures | 3% | Boilerplate notices, nearly identical across all files |
  | Letters of explanation and correspondence | 1% | Short borrower letters and emails |
  | Payment history | 1% | Servicing table of payments after closing |

- **Boilerplate on purpose:** the deed of trust and standard disclosures reuse the same template text across files with names swapped. This is realistic and makes retrieval harder, because many near-identical chunks compete. The context prefix (17.2) is what lets the right file win.
- **Core fields:** borrower name, loan number (`LN-YYYY-NNNNNN`), principal, interest rate, term, and property address appear consistently in each file's application, note, and closing disclosure.
- **Planted facts:** 5 to 8 unique facts per document, spread across sections and depths, with at least two in the last third of the file (for example, a specific bank statement ending balance on page 70, or an unusual appraisal adjustment). Record each in `eval/synth/facts.jsonl` as `{doc_filename, page, section, fact_text, fact_type}`. The `section` field is new.
- **Scanned pages:** in about 20% of documents, render 3 to 8 pages as images with no text layer (signed pages, a copied pay stub, a scanned letter). That is about 1,000 OCR pages in total, enough to exercise OCR without making ingestion unbearably slow. This replaces version 1's rule of scanning whole documents.
- **Hand check:** open a few generated files and confirm they read like real loan files and that planted facts are where `facts.jsonl` says.

**Expected build cost** (record actual numbers in `docs/evaluation.md`): generating the corpus takes minutes. Ingestion is dominated by embedding (about 110K to 160K chunks; tens of minutes on a laptop CPU, a few minutes on a GPU) and OCR (about 20 to 40 minutes for about 1,000 pages). Ingestion MUST print progress (documents done, pages per second, estimated time left), and stays resumable through the idempotency rules in `task.md` section 5.4.

### 17.5 Evaluation changes

Additions to `task.md` sections 11.2 and 11.3:

- **Depth spread.** Of the at least 150 answerable questions, about a third have gold facts on pages 1 to 10, a third on pages 11 to 50, and a third on page 51 or later.
- **Context-dependent questions.** At least 30 questions whose answer chunk does not itself contain the loan number or borrower name (for example, bank statement or appraisal facts). These test section 17.2 directly.
- **New metric, recall by depth.** Recall@10 for each depth bucket and for the context-dependent questions, reported both with and without the context prefix. To measure "without", build a second index from the same chunks without prefixes, using a `--no-context-prefix` flag on `rebuild-index` that writes to a separate index path. The comparison shows what the prefix contributes.

### 17.6 Index sizing

The flat FAISS index from `task.md` section 7 stays. At about 110K to 160K chunks it holds roughly 250 MB of vectors, and exact search still takes tens of milliseconds per round on a laptop CPU. Measure p95 retrieval latency for the user with the narrowest access, since they trigger the most over-fetch rounds. If it becomes a problem, switch to `IndexIVFFlat`, which also supports `remove_ids`. Do not use HNSW, which does not support removal.

### 17.7 Tests for large files

- A context-dependent question (answer on page 60 or later, chunk text without the loan number) retrieves the right chunk in the top 10.
- The context prefix never appears in citation excerpts, page text, or the source blocks of the prompt.
- A file whose first 5 pages have no recognizable loan number is ingested with a warning and null fields, and its chunks still get a prefix with the fields that were found.
- Re-running ingestion on the large corpus creates no duplicate chunks or vectors.
- Ingestion progress output appears at least every 30 seconds during a full run.

### 17.8 Rollout order

Apply this part before the UI work (Phase 0 in section 13):

1. Run the migration in 17.3.
2. Deploy the ingestion changes in 17.2.
3. Regenerate the corpus (17.4) and the evaluation questions (17.5).
4. Delete the old index and corpus data, re-ingest, and rerun the full existing test suite plus 17.7.
5. Rerun the evaluation and replace the numbers in `docs/evaluation.md`.
