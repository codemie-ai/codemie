# EPMCDME-13944: STT cost availability (additional work)

## Problem
Cost for speech-to-text (STT) calls in `audio_transcription` mode was not available.

## Acceptance criteria
1. The cost of an STT call made through LiteLLM is available in LiteLLM.
2. The cost of the same STT call is available in Langfuse, on the `stt.transcribe` generation.

## Context
- LiteLLM prices the call itself on the proxy, using its model price data.
- CodeMie reports STT calls to Langfuse and Phoenix. Duration-priced models reported usage under a custom `audio_seconds` key that neither backend can price.
- Price source for CodeMie: `input_cost_per_second` (or per-token prices) from the LiteLLM `model_info`.

## Evidence supplied by the author
- LiteLLM spend log: `whisper-1`, call type `atranscription`, cost $0.000504.
- Langfuse trace: `stt.transcribe` generation, total cost $0.0006 for a 6-second call.
