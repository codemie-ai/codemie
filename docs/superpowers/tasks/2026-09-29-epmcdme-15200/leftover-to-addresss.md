  "Sonar findings 4–8": I mislabelled these. They came from the independent Sonnet review you asked for.
  They are unfixed, mid-to-low severity concerns:
  - 4: the Graph query sorts by receivedDateTime desc, id. Graph may reject id in $orderby. Mocks can't
    show that, so a live tenant could return a 400, which surfaces as a generic internal error.
  - 5: EWS fetches whole messages, bodies included, when listing, and re-fetches the message for every
    attachment call. One enrich_body call can trigger many full fetches. A non-message item in a folder can
    also raise a raw AttributeError.
  - 6: a malformed message id gives "internal error" instead of "message not found" (AC3). The 403
    ErrorAccessDenied → policy-denied mapping is also a guess.
  - 7: retry wraps the whole 60 s timeout across 4 attempts, so a hanging endpoint can block a request for
    about 4 minutes.
  - 8: ocr_model is supplied by the LLM and isn't checked against the allowed multimodal models. An
    instruction hidden in an email could pick any configured model. The exception is also chained, which
    could leak text into logs.

  Dependencies (AC15): I checked the environment and lockfile.
  - kiota_abstractions:
    - graph_client.py:34 imports it directly, but pyproject.toml doesn't declare it.
    - It arrives transitively through msgraph-sdk, so it works today. A msgraph-sdk upgrade that dropped it
      would break the import.
    - Fix by declaring it, or by importing through something you declare.
  - requests-ntlm:
    - Nothing in the code imports it. ews_client.py uses NTLM through exchangelib, which already depends on
      requests_ntlm.
    - The explicit line in pyproject.toml (requests-ntlm = "^1.3") is redundant technically. It is there
      because the ticket asked for three dependencies. Keep or drop it as you decide.
  - Lockfile: poetry.lock has none of the new packages. They are installed in .venv, but the lock, which CI
    uses, doesn't know about them. AC15 asks for a regenerated lockfile, so it is unmet.

