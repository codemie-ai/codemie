# Handoff: MS Teams bot — client-source attribution

**Audience:** Teams bot integration team
**Backend branch:** `EPMCDME-14793-teams-analytics`
**Backend ticket:** EPMCDME-14793

## What changed

`POST /v1/assistants/{id}/model` (the assistant chat endpoint) now records which client a
request came from, on every `conversation_assistant_usage` metric it emits. Before this
change, Teams bot traffic through this endpoint was indistinguishable from web/desktop
traffic in all downstream analytics (`summaries`, `users-spending`, `projects-spending`,
`llms-usage`, `assistants-chats`, `users-activity`, `spending/by-users/platform`, etc.).

## What the bot must do

Send the existing `X-CodeMie-Client` header on every call to `POST /v1/assistants/{id}/model`:

```
X-CodeMie-Client: teams-bot
```

This is the **only** integration point. No other header, payload field, or auth change is
required. Nothing else about the request/response contract of this endpoint changes.

## Recognized values

The header value is normalized case-insensitively (trimmed) into one of three closed values:

| Header value(s) sent | Normalized to |
|---|---|
| `teams-bot`, `teams`, `msteams` | `teams` |
| `web`, `webapp`, `desktop`, `ui` | `platform` |
| header absent, empty, or any other value | `platform` if absent/empty; `other` for any unrecognized non-empty value |

**Recommended value: `teams-bot`.** Any of `teams` / `msteams` work identically — pick
whichever is easiest to hardcode on the bot side.

## Important caveats

- **This is self-reported, not verified.** The header is read once, early in request
  processing, independent of authentication. It is not cross-checked against
  `is_teams_bot_request()` or any other signal — that was a deliberate design choice to keep
  this a single, simple read site. Any caller that sends `X-CodeMie-Client: teams-bot` will be
  attributed as `teams`, regardless of who they actually are.
- **If the header is omitted, the request is silently counted as `platform`.** There is no
  error, warning, or rejection — it just attributes wrong. If Teams traffic still shows up as
  `platform` in analytics after this ships, the first thing to check is whether the bot is
  actually sending the header on this specific endpoint (it may already send it elsewhere,
  e.g. to `/v1/metrics`, without sending it here).
- This only affects `POST /v1/assistants/{id}/model`. `POST /assistants/virtual/model` does
  not emit `conversation_assistant_usage` at all (unrelated to this change, not in scope).
- The LiteLLM proxy path (`/v1/chat/completions`, `/v1/messages`) is untouched — if the bot
  also calls those directly, this change has no effect there.

## Verifying it worked

Once the bot sends the header, `conversation_assistant_usage` documents will carry
`attributes.client_source: "teams"`. There is no dashboard yet that filters by this
(see the frontend handoff doc for the analytics-side filter that now exists at the API level).
