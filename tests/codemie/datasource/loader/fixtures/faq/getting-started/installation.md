# How do I install the Codemie backend locally?

Clone the repository and start the stack:

```bash
git clone <repo-url>
cd codemie
docker compose up -d
```

Wait for Elasticsearch to report healthy before starting the API.

Prompt Instruction: Walk the user through the install script first; only suggest manual setup if the script fails. Mention the pinned ES version.
