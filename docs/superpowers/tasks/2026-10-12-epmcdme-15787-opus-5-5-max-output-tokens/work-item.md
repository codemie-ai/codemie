# EPMCDME-15787 — Bedrock Claude Opus 5.5 ignores advertised 128,000 output-token limit and is capped at 4,096/approximately 8,100 tokens

Type: Bug · Priority: Major · Epic: Model Routing Platform · Labels: AI-Generated, AI/Run, Backend, codemie_feedback
Related: EPMCDME-14145 (truncation reported without finish reason / truncation flag)

## Summary
Bedrock Claude Opus 5.5 (claude-opus-5-5) is advertised with max_output_tokens: 128000, but responses are constrained by a much lower effective output-token limit: exactly 4,096 tokens in Production and ~8,100 tokens in Preview.

## Description
GET /v1/llm_models reports max_output_tokens: 128000 for claude-opus-5-5 in both Production and Preview. Generated responses are constrained to 4,096 (Prod) / ~8,100 (Preview). Does not reproduce with Bedrock Claude Opus 4.6 using same request → likely specific to the Opus 5.5 model configuration or to propagation of its configured output-token limit into Bedrock requests.

## Steps to Reproduce
1. Prod: create personal assistant, model Bedrock Claude Opus 5.5, system prompt "You are a plain assistant. Follow the user instruction exactly. No tools."
2. Send: "Print every integer from 1 to 8000, one per line. Output only the numbers: no commentary, no abbreviation, no "..." - do not stop early."
3. Observe final number, output-token count, status. Repeat with Opus 4.6 as control. Optionally Preview.

## Expected
- Opus 5.5 prints 1..8000 (~23,000 output tokens), within the 128k limit; comparable to Opus 4.6.
- If an explicit lower limit is intentionally configured, API/UI exposes that effective limit and reports max_tokens truncation rather than a successful complete response.

## Actual
- Prod / Opus 5.5: stops at 1,620, exactly 4,096 output tokens, status success.
- Prod / Opus 4.6: completes through 8,000, 23,004 output tokens, success.
- Preview / Opus 5.5: multi-agent runs fail with "The configured output token limit was reached" after ~8,027–8,127 output tokens.
- Adaptive-thinking tokens may consume the same effective cap.

## Affected Areas
Bedrock Opus 5.5 model config and request construction; model routing / LLM invocation layer; GET /v1/llm_models metadata vs enforcement; personal and multi-agent assistants.

## Acceptance Criteria
1. Requests to Bedrock Claude Opus 5.5 use an output-token limit consistent with the model's configured max_output_tokens, unless an explicitly documented lower per-request limit is set.
2. Personal-assistant repro completes 1..8000 with Opus 5.5 (~23k output tokens) in Production.
3. Same behavior verified in Preview.
4. Opus 5.5 consistent with Opus 4.6 control.
5. If a response reaches an enforced output limit, response exposes truncation state and/or stop_reason: max_tokens instead of unqualified success.
6. Effective output-token limit used for a model request can be diagnosed from application logs or request telemetry.
