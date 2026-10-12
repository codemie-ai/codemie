- Loading configuration...
✔ Connected to CodeMie
Summary:
Exchange toolkit: read transports — Microsoft Graph primary and EWS/NTLM required fallback — with four read operations

Description:
h1. Slice 2 of 3 — Exchange read transports

Delivers both mailbox transports behind one toolkit contract, plus the four read-only operations. Begins where Slice 1 ends: a validated, decrypted ExchangeToolConfig is available in memory for either path.

*Delivery chain:* *blocked by EPMCDME-15199* (Slice 1 — platform enablement). *Blocks* Slice 3 (Exchange write operations).

*Both transports ship in this one slice.* EWS/NTLM is client-required, not deferrable. A by-transport split (Graph first, EWS second) was explicitly considered and *rejected* — making the client-required EWS path a dependent follow-on is the mechanism by which a required transport becomes a deferred one.
h2. Complexity

*L (26/36)* — recommended flow sdlc-standard.
||Dimension||Score||
|Component scope|5 (XL)|
|Requirements clarity|3 (M)|
|Technical risk|5 (XL)|
|File change estimate|4 (L)|
|Dependencies|5 (XL)|
|Affected layers|4 (L)|

*26 is the top of the L band.* Three dimensions were held one tier below their mechanical reading; any one moving up returns this to XL and re-triggers a split. Re-assess before committing if scope grows.
h2. Story

*As an* automation engineer, *I want* read-only mailbox operations working over Microsoft Graph for Exchange Online and over EWS/NTLM for on-premises environments, behind one toolkit contract *so that* I can retrieve mail in assistants and workflows regardless of which Exchange estate the client runs, without handling credentials myself.
h2. Entry conditions — all must hold before the slice starts
 # *EPMCDME-15199 delivered* — ExchangeToolConfig validates both paths and credentials inject from the encrypted store.
 # *EPMCDME-11264 output contract confirmed.* enrich_body cannot be implemented while its return type is undecided (str vs FileObject). If still open at slice start, *move enrich_body to a follow-up rather than carrying it as an unresolved criterion.*
 # *Two live test Exchange environments provisioned* — an Exchange Online tenant with an App Registration holding consented Mail.Read application permission, and an on-premises Exchange server reachable over EWS with a service account holding delegate access to a shared mailbox. These block on two different client-side admins and are a delivery gate, not an implementation task.

h2. Release coupling

EPMCDME-15199 releases together with this slice (decided on EPMCDME-15199, 2026-09-25). It has no gating of its own: once merged, it registers a Microsoft Exchange toolkit with no tools, and the toolkit appears in the selector. EPMCDME-15199 must therefore not reach a release without this slice. This applies to both of its MRs: backend !4354 and the codemie-ui MR from branch EPMCDME-15199_add-ms-exchange-integration. How to hold them back (keep them unmerged, or merge both slices into the same release) is a release-management decision. It is recorded here so that the dependency is visible from this side.

h2. Scope

*Microsoft Graph — primary.* azure-identity ClientSecretCredential, built from oauth_tenant_id, oauth_client_id and oauth_client_secret, supplies the token; msgraph-sdk-python issues calls against /users/

{mailbox}

/... using application permissions. Apply Prefer: outlook.body-content-type="text" for predictable body extraction. Token acquisition, caching and refresh are azure-identity's responsibility — no manual JWT handling in tool code.

*EWS/NTLM — required fallback.* exchangelib with explicit Configuration(server=ews_url, ...), NTLM authentication, autodiscover=False, access_type=DELEGATE. Never silently attempt Autodiscover — client on-premises DNS and egress may not support it, and a fallback attempt turns a clear configuration error into a timeout.

*Two-identity model, no platform precedent.* One identity authenticates, a different identity owns the mailbox opened. Graph authenticates an App Registration with tenant-wide application permissions; EWS authenticates a domain service account with per-mailbox delegate access.

*Symmetric client modules.* The transports live in the codemie_tools/ms_exchange/ package that EPMCDME-15199 created (models.py, toolkit.py): graph_client.py and ews_client.py sit beside them, and the tool classes go in tools.py in the same package. ews_client.py mirrors graph_client.py, so neither transport is embedded in tools.py. Test fixtures patch each client module's connection builder (build_graph_client in graph_client.py, and its counterpart in ews_client.py). Note: unified spec §7's fixture example patches codemie_tools.core.exchange.tools.Account. That path predates the package layout and does not apply.

*Four new production dependencies, all added here:* azure-identity, msgraph-sdk-python, exchangelib, requests-ntlm. All need Python 3.12 and existing-pin compatibility checks plus lockfile regeneration. Confirm the pinned msgraph-sdk-python major version before writing the client — v0.x exposes from msgraph.core import GraphClient, v1.x from msgraph import GraphServiceClient, with different APIs.

*Integration connection test.* This slice connects the Integrations screen Test button for CredentialTypes.MS_EXCHANGE to the existing SettingsTester path. Add an Exchange healthcheck that uses the selected Graph or EWS client and the configured mailbox. The check performs a small read request. A successful token request or server connection alone is insufficient. EPMCDME-15199 supplies configuration validation and credential storage; this slice supplies the live mailbox check.

*UI change (codemie-ui).* EPMCDME-15199 ships the Microsoft Exchange credential form with testable: false in CREDENTIAL_UI_MAPPING (src/utils/settingsUIConfig.ts), so the Test button is hidden. This slice sets testable: true once the backend healthcheck exists. It is delivered as a separate codemie-ui MR under this ticket. This slice therefore spans two repositories: codemie and codemie-ui.

h2. Acceptance criteria

*Connection — proven live on both transports*
 * Given the provisioned Exchange Online tenant with an App Registration (oauth_tenant_id, oauth_client_id, oauth_client_secret; admin consent granted for the Mail.Read application permission), when the toolkit authenticates via auth_type=oauth_azure and opens the shared mailbox, then it connects successfully against that live tenant and azure-identity manages the underlying JWT without manual token handling in tool code.
 * Given the provisioned on-premises Exchange server with an explicit EWS endpoint and autodiscovery disabled, when the toolkit authenticates via auth_type=ntlm with the service account holding delegate access to the shared mailbox, then it connects successfully against that live server using exchangelib and NTLM, with the service-account identity and the mailbox identity remaining distinct.

*Integration Test button — connection and Inbox access*
 * Given the Microsoft Exchange integration form in the UI, when it renders for either auth_type, then the Test button is shown, and selecting it sends the entered configuration to the settings test endpoint.
 * Given a new or existing Microsoft Exchange integration, when the user selects Test, then the platform validates the submitted configuration and checks the configured mailbox through the selected auth_type. Existing masked secrets are resolved through the standard settings test path.
 * Given auth_type=oauth_azure, when Test runs, then the Graph client acquires a token and requests at most one message ID from the configured mailbox Inbox through the Graph list-messages endpoint, with query parameters $top=1 and $select=id. The check does not follow further pages.
 * Given auth_type=ntlm, when Test runs, then the EWS client uses the configured endpoint, NTLM, autodiscover=False and access_type=DELEGATE. It executes FindItem against the configured mailbox Inbox with an ID-only response and a maximum of one result. The request must execute; constructing a client or a lazy query alone is insufficient.
 * Given an accessible empty Inbox, when the request succeeds with zero items, then Test succeeds. A mailbox need not contain a message to pass.
 * Given either transport, when Test completes, then it has not sent, moved, deleted or marked any message as read. It does not retrieve message bodies or attachments, or expose secrets or returned message IDs in the UI or logs.
 * Given invalid credentials, denied mailbox access, an unavailable mailbox or endpoint, a timeout, or rate limiting, when Test fails, then it returns a clear error. Report a specific cause such as expired credentials or missing consent only when the provider response supports that cause. Configure and document finite request timeouts and a bounded retry policy; do not retry rejected credentials.
 * Given a successful check, then the UI reports "Connection and Inbox access verified." This result verifies the checked Inbox access only. It does not establish full body or attachment access, access to every folder, or send/delete permissions; the operation tests cover those capabilities.

*Read operations — each verified on both transports*
 * Given a connected mailbox on either auth path, when retrieve_folders is called, then it returns the folder list for the shared mailbox and changes no mailbox state.
 * Given a connected mailbox on either auth path and a specified window (count, folder, start), when read is called, then it returns no more than the requested window, does not continue past it, and does not mark any message as read.
 * Given two identical read calls on either auth path, when both run, then both return the same result and neither changes mailbox state.
 * Given a message identifier on either auth path, when retrieve_by_id is called, then it returns the matching message, or a diagnosable not-found error when the identifier does not resolve.
 * Given entry condition 2 is satisfied and a message with attachments on either auth path, when enrich_body is called, then it returns the message body plus extracted attachment content in the return shape confirmed by EPMCDME-11264, and triggers no downstream ingestion of its own.

*Failure behaviour — each separately diagnosable, never a generic tool failure*
 * Given a Graph 429 Too Many Requests response while reading or enriching, when the bounded retry policy is exhausted, then the toolkit returns a diagnosable rate-limit error and does not alter the mailbox.
 * Given a Graph 401 Unauthorized caused by an expired oauth_client_secret, when a read is attempted, then the toolkit surfaces a diagnosable credential-expiry error rather than a generic authentication failure.
 * Given missing or revoked admin consent on the Graph path, when a read is attempted, then the toolkit surfaces an authorization error naming consent as the cause.
 * Given an authentication failure on the EWS/NTLM path, when a read is attempted, then the toolkit distinguishes rejected service-account credentials from a service account whose delegate access to the shared mailbox has been revoked, and alters no mailbox state.
 * Given an unreachable mailbox or endpoint on either path, when the toolkit retries and the call still fails, then it surfaces a diagnosable error naming the specific failure mode and does not mark, move or alter any message.

*Tests*
 * Given read-path tests for both transports, when run with mocked Graph and exchangelib clients, then both paths cover successful and failed connection, bounded read, no-mark-as-read, retrieval by identifier, folder listing, body enrichment, and each enumerated failure mode — using fixture-only credentials with no literal production values.
 * Given the EWS test fixtures, when they patch the transport, then they assert the explicit endpoint is used, autodiscovery is disabled, and access type is delegate.

 * Given automated tests for the MS_EXCHANGE SettingsTester handler, then both authentication paths cover success, an empty Inbox, invalid configuration, authentication and access failures, timeout and rate-limit handling, and saved masked credentials. Assert the one-result limit, no further pages, no mailbox changes, and no secret or message content disclosure. Use fixture-only credentials.
 * Given the codemie-ui unit tests, when they run, then they assert that the msexchange entry is testable and that the Test button renders on the Microsoft Exchange form.
 * Given the two provisioned live environments, when the Integrations screen Test button is used for each transport, then record the displayed success or failure and verify that the target mailbox remains unchanged. This verifies the UI-to-backend connection in addition to the live transport checks above.

Mocked clients cover CI and the failure taxonomy; they do *not* substitute for the live connection proof, because NTLM handshake behaviour, delegate-access enforcement and admin-consent propagation are unobservable through a patched client.
h2. Out of scope
 * Credential type, ToolSet member, ExchangeToolConfig, the Alembic migration, toolkit registration, the feature flag, the connector-pattern documentation and the Microsoft Exchange credential form in codemie-ui — all delivered by EPMCDME-15199. This slice changes only the form's testable switch.
 * Mutating operations (send, forward, move, tag, delete) — Slice 3.
 * Polling cadence, batching and deduplication — calling flow.
 * IMAP and POP3 — explicitly out of scope. Exchange only.
 * Certificate-based OAuth (OAUTH_AZURE_CERT); Exchange Autodiscover.
 * Exchange Application Access Policy setup — client Azure admin action via Exchange Online PowerShell.
 * Business logic deciding what to do with a retrieved message.

h2. Durable architectural constraints
 * *Graph/OAuth is the primary path for Exchange Online; EWS/NTLM is a client-required fallback, not deferred scope.* Microsoft has deprecated EWS for Exchange Online, so Graph is correct where Azure AD is reachable. Where the environment is air-gapped or policy blocks Azure AD, EWS/NTLM is the only transport that works at all. Both are confirmed client deployment scenarios.
 * *The operator explicitly selects auth_type; there is no unsafe transport default.*
 * *Credentials remain exclusively in encrypted platform storage* — never in prompts, model context or logs. Only oauth_client_secret and service_account_password are secrets; mailbox is non-sensitive configuration and appears in Graph request paths by necessity.
 * *Retry on a read must never change mailbox state.* The only repetition the toolkit performs is a retry on a failed transport call. The toolkit is stateless — cadence, batching and deduplication belong to the calling flow.
 * Application-level Mail.Read grants tenant-wide mailbox access; the mailbox config field restricts what the toolkit opens, but there is no Azure-AD-level enforcement. Hard per-mailbox scoping requires an Exchange Application Access Policy (client admin action, out of scope).

h2. Relationship to EPMCDME-12864

EPMCDME-12864 ("Assistants and Workflows can read Mailboxes") is the existing mailbox-reading umbrella/reference item and is linked as relates to. It is deliberately *not* the implementation parent, and its scope is *not* assumed delivered.

EPMCDME-12864 currently describes an IMAP-oriented direction ("likely IMAP is only one we need"; "MS Outlook is a must; other IMAP-compatible mailboxes are desirable"). IMAP and POP3 remain explicitly out of scope for this Exchange story. Its status is also ambiguous — "Ready for Review" coexists with a PO comment from 2026-06-30 recording "defer for investigation… requires architecture/security investigation before delivery planning". That investigation is what the unified spec above provides.

This Story delivers the Bank-required Exchange-specific implementation: Microsoft Graph for Exchange Online and EWS/NTLM for on-premises or Azure-AD-restricted environments. The related link preserves traceability without treating EPMCDME-12864 as implementation parent.
h2. Dependencies and references
 * EPMCDME-15199 — Slice 1, blocks this Story and ships in the same release as this Story (see Release coupling).
 * EPMCDME-11264 (Automate Transfer and Processing of Attachments, Documentation preparation) — entry condition for enrich_body specifically, with a defined fallback (drop enrich_body to a follow-up). Not a blocker for the rest of the slice.
 * EPMCDME-9514 (UNS vs AppRegistration for Email tool, Ready for dev, Unassigned) — gates Graph production rollout only. The EWS path can reach production independently. This gate must not be allowed to stall the EWS half of this slice. Needs an assignee.
 * EPMCDME-14450 (workflow nodes returning handled errors as structured results, Open) — relevant to the value of this slice's failure taxonomy. Per its author: "At present, a tool failure stops the workflow execution, including when the tool is invoked for one item inside an iterator." Our diagnosable errors are not actionable by a calling workflow until this lands.
 * EPMCDME-11609 (Sub-workflow Node Support, Documentation preparation) — the iterator pattern for per-email processing on the caller side.
 * EPMCDME-12864 — related mailbox-reading umbrella item (see above).

h2. Open questions
 * *One tool or four?* The ServiceNow and Jira house pattern favours a single generic tool; four read operations cut against it. Team decision at implementation start; not a blocker. Contained: the dispatch pattern is per-tool-class, so the fork changes only the class count inside tools.py.
 * msgraph-sdk-python major version — confirm the pin before writing the client.
 * Graph body format — confirm Prefer: outlook.body-content-type="text" is applied on the Graph read and enrich paths.
h2.  

_Duplicate check: direct read-only JQL review across EPMCDME (summaries, descriptions and comments) completed 2026-09-21. Searching exchangelib, NTLM, EWS, Autodiscover, shared mailbox and on-premises Exchange across all of EPMCDME returned only false positives — __the EWS/NTLM transport has no existing Jira coverage whatsoever.__ The knowledge-base semantic check (codemie-jira-duplicates, codemie-jira-epics) could not be run — it requires Bash permission for codemie assistants chat._

Acceptance Criteria:
null

Linked Issues:
blocks EPMCDME-15201 — Exchange toolkit: mutating mailbox operations (send, forward, move, tag, delete) on both proven transports
is_blocked_by EPMCDME-15199 — Exchange toolkit: register MS_EXCHANGE credential type, dual-path config, reversible migration and polling feature flag
is a part of JBBMAFR-13 — 3.1 Enterprise & On-Prem Connectivity – secured SaaS connectors via MCP
relates to EPMCDME-12864 — Assistants and Workflows can read Mailboxes
