```
h3. Environment

Environment: Preview ([https://codemie-preview.lab.epam.com]), Analytics → CLI Analytics → Sessions
UI version: {{2.49.0-SNAPSHOT.229}}
API version: {{2.49.0-SNAPSHOT.235}}
Client: Claude Code Desktop, model {{claude-haiku-5-5}}
Repository / branch of the sessions: {{codemie-ai/codemie-code}} / {{main}}
Factory under test: STLC Factory ([https://gitbud.epam.com/epm-cdme/codemie-public-skills/-/tree/feat/stlc-factory/ai-packages/stlc-factory])
Developer: Dzmitry Lopukh
Test date: 27 Oct 2026 (sessions started 11:45–12:05, times as shown in the Sessions table)
h2. Description

A session started by launching a factory must be marked as a factory session in CLI Analytics: the *Framework* column of the Sessions table shows the factory name instead of "Pure chat". This works for the SDLC Factory ("CodeMie AI Factory") and for Superpowers, but *not for the QA factory (STLC Factory)*: every session started by an STLC Factory skill is shown as *Pure chat*, the same as an ordinary chat without any factory.

In the test, 4 sessions were started by STLC Factory skills ({{/stlc-factory:exploratory}}, {{/stlc-feature}}, {{/stlc-factory:stlc-foundation}}, {{/stlc-factory:suite-health}}). All 4 are shown as "Pure chat", both in the Sessions table and in the session details. In the same period, sessions started by SDLC Factory skills ({{/sdlc-factory:code-review}}, {{/sdlc-factory:feature-verification}}) are shown as "CodeMie AI Factory", and sessions started by {{/superpowers:brainstorming}} as "Superpowers".

So the fact that the QA factory was launched is not visible in the analytics at all: the session looks like an ordinary chat, and its skill usage is the only trace of the launch (the details of {{/stlc-factory:suite-health}} show Skills = 1 while the header says "Pure chat").
h2. Steps to reproduce
 # Install the STLC Factory package ([https://gitbud.epam.com/epm-cdme/codemie-public-skills/-/tree/feat/stlc-factory/ai-packages/stlc-factory]) in Claude Code Desktop and connect CLI Analytics ({{codemie proxy connect --claude-code-otlp}}) with the Preview profile.
 # In a repository ({{codemie-ai/codemie-code}}, branch {{main}}) start a new session and run one STLC Factory skill as the first prompt, e.g. {{/stlc-factory:suite-health}}. Repeat in separate new sessions for {{/stlc-factory:exploratory}}, {{/stlc-feature <Jira ticket URL>}}, {{/stlc-factory:stlc-foundation <ticket>}}.
 # For comparison, start a new session with an SDLC Factory skill (e.g. {{/sdlc-factory:feature-verification <URL>}}) and one with {{/superpowers:brainstorming <text>}}.
 # Wait a few minutes until the sessions are delivered, then open [https://codemie-preview.lab.epam.com] → Analytics → CLI Analytics → Sessions.
 # Look at the *Framework* column of the sessions started in steps 2–3.
 # Click an STLC Factory session row and look at the header of the session details modal.
h2. Actual vs expected

||Date||Prompt||Repository||Branch||Turns||Output||Framework (actual)||Framework (expected)||
|Oct 27, 12:07|/superpowers:brainstorming how update…|codemie-ai/codemie-code|main|1|2,589|Superpowers|Superpowers|
|Oct 27, 12:05|/stlc-factory:suite-health|codemie-ai/codemie-code|main|1|1,050|*Pure chat*|STLC Factory (QA factory)|
|Oct 27, 12:04|/sdlc-factory:feature-verification ht…|codemie-ai/codemie-code|main|1|3,656|CodeMie AI Factory|CodeMie AI Factory|
|Oct 27, 11:52|/stlc-factory:stlc-foundation EPMCDME…|codemie-ai/codemie-code|main|0|960|*Pure chat*|STLC Factory (QA factory)|
|Oct 27, 11:46|/stlc-feature https://jiraeu.epam.com…|codemie-ai/codemie-code|main|1|388|*Pure chat*|STLC Factory (QA factory)|
|Oct 27, 11:45|/stlc-factory:exploratory|codemie-ai/codemie-code|main|1|236|*Pure chat*|STLC Factory (QA factory)|
|Oct 27, 11:35|/sdlc-factory:code-review https://git…|epm-cdme/hyperfactory/cont…|main|8|66,314 (input 212)|CodeMie AI Factory|CodeMie AI Factory|
|Oct 27, 11:24|/sdlc-factory:code-review|epm-cdme/hyperfactory/cont…|EPMCDME-15297|6|74,766 (input 315,037)|CodeMie AI Factory|CodeMie AI Factory|

Session details of {{/stlc-factory:suite-health}} (Oct 27, 12:05):

||Field||Actual||Expected||
|Header|{{/stlc-factory:suite-health}} — Claude Code Desktop · claude-haiku-5-5 · codemie-ai/codemie-code · main · *Pure chat*|… · STLC Factory (QA factory)|
|Cost|$0.0094 (API-equivalent), cache-read $0.0025|same|
|Duration / Active|2m 36s / 2m 31s|same|
|Tokens|input 6, output 1.1K, cache read 229.3K, cache create 33.0K, total 263.4K|same|
|Turns / Tool calls|1 / 3 (100% ok)|same|
|Agents / Skills|0 / 1|same|
h2. Expected result

A session started by an STLC Factory skill is marked as a factory session in the *Framework* column of the Sessions table and in the header of the session details (the QA factory name instead of "Pure chat"), the same way the SDLC Factory sessions are marked "CodeMie AI Factory" and Superpowers sessions "Superpowers". Only sessions that were not started by any factory remain "Pure chat".
h2. Impact
 * The use of the QA factory cannot be seen in CLI Analytics: all its sessions are counted as plain chat.
 * Factory adoption and cost cannot be reported for QA teams (sessions of the QA factory are mixed with ordinary chat sessions in any grouping or filtering by framework).
 * The same user, in the same repository and on the same day, gets different labels for SDLC and QA factory launches, which makes the Framework column unreliable.
h2. Acceptance criteria
 # Each of the 4 skills {{/stlc-factory:exploratory}}, {{/stlc-feature}}, {{/stlc-factory:stlc-foundation}}, {{/stlc-factory:suite-health}}, run as the first prompt of a new session, produces a session with the STLC Factory label in the Framework column of the Sessions table.
 # The session details header shows the same label.
 # Sessions started by SDLC Factory skills still show "CodeMie AI Factory", Superpowers sessions still show "Superpowers", and a session started without a factory still shows "Pure chat".
 # Other session data (cost, tokens, duration, turns, tool calls, skills) is unchanged.
h2. Evidence
 * Screenshot 1: session details modal of {{/stlc-factory:suite-health}} (header with "Pure chat", cost, tokens, activity).
 * Screenshot 2: Sessions table, Oct 27, 11:24–12:07, with "Pure chat" for the {{/stlc-factory:*}} and {{/stlc-feature}} rows and "CodeMie AI Factory" / "Superpowers" for the other rows.
```
