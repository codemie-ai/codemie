# Skill Files in the Agent Workspace

A skill is a bundle: the main instructions (`SKILL.md`) and supporting ("companion") files such as scripts, templates
and data. This page describes how an assistant's skill files become available in a conversation's workspace, so a script
in a skill bundle can be run with the workspace script tool without the user uploading anything.

## What happens

The companion files of every attached skill appear in the conversation's workspace under `skills/<skill-name>-<id>/`, where `<id>` is the first 8 characters of the skill id.
They are **referenced, not copied**: a workspace file row points at the skill's stored file, so nothing is written and
no storage grows per conversation. When a person, a script or the model changes or creates a file there, the change
becomes the conversation's own copy; the skill's file and every other conversation are unaffected. Deleting such a file
only removes it from that workspace, and it is not brought back on a later request.

`SKILL.md` is never part of the workspace; the model gets it from the skill tool.

## When it happens

Once per skill and workspace, at the assistant's first request in a conversation or workflow run, before the tools are
built (`prepare_skill_workspace` in `skill_workspace_preparation.py`, called by `AssistantService.build_agent` and
`build_agent_for_workflow`). It happens only when all of these hold:

- the assistant has skills attached and the request has a conversation id (for a workflow run, the execution id);
- the script-execution tool (`execute_workspace_script`) is among the assistant's selected tools, including the tools
  its skills contribute;
- the `workspaceScriptBridge` setting is on and the sandbox runs in jobs mode.

A skill attached later is seeded on the next request. Opening the workspace in the UI or through the workspace API
before the first request shows no skill files.

Workflows get skill files through their assistant nodes. The other paths that build workspace toolkits from
`ToolkitService.get_toolkit_methods()` (a workflow script step, a tool invocation, tool-call resolution for a workflow)
have no assistant skills and intentionally do not seed; a script step sees the files that earlier assistant nodes of the
same run already placed in the shared run workspace.

## Writing scripts for a skill

A script runs from the skill's folder in the workspace, not from the bundle root, so build every path from the
script's own location instead of the current working directory:

```python
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent  # skills/<skill-name>-<id>/ (or a subfolder of it)
template = (SKILL_DIR / "templates" / "report.md").read_text()
```

Do not rely on relative paths such as `open("templates/report.md")`; they break when the script is started from
another folder.

## Rules to know

| Topic | Behavior |
|---|---|
| Frozen file set | The files are fixed at the first seeding of a skill in a workspace. Files added to the skill later do not appear in workspaces that already have it. |
| Pinned content | Skill files are stored under content-addressed keys (`<sha256[:32]>/<path>`). A skill update writes new versions and never overwrites the earlier ones, so a workspace keeps the content it was seeded with. |
| Name conflict | The folder name carries the first 8 characters of the skill id, so same-named skills get different folders. A conflict is now only possible if two ids share those 8 characters; the folder-owner check is kept for that case: the first skill in attachment order keeps the folder, and the second is reported to the log and to the model. |
| Limits | Per skill: at most `SKILL_WORKSPACE_SEED_MAX_FILE_COUNT` files (default 100), each at most `SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES` (default 10 MB), together at most `SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES` (default 50 MB). Files over a limit are skipped; they stay readable through the skill tool. The notice to the model names the skipped files (the first few, then a count). The skill editor warns about them at upload: the bundle preview response and skill detail responses carry a `bundle_warning`. A bundle path may be at most 943 characters (it was 952 before the id joined the folder name): a stored path of 944–952 characters is skipped at seeding as `invalid_path` and flagged in `bundle_warning`, and a client that checks the length itself must use 943. |
| Older skills | A skill whose companion files are stored inline in the skill record (no stored file to point at) is skipped: nothing is seeded, a warning is logged, and the model is told that the companion files are not available in the workspace and must be read through the skill tool. There is no copy fallback. |
| Missing files | If a referenced stored file can no longer be read, it is skipped for that script run and the run's result carries a warning naming the path. |
| Notices to the model | Skipped files, name conflicts and failures are added to the system prompt as "Workspace setup notices (skill files)" for chats and for workflow assistants alike. Notices about limits appear on the request that seeds the skill; the skill tool's location hint always says that files over the limits and inline files are not in the folder. |

## Path rules

Workspace and bundle paths must be relative, free of `..` segments, control characters and leading or trailing
whitespace, and within the length limit. Unicode format characters (zero-width joiner in emoji, left-to-right and
right-to-left marks) are allowed, which also means a name can contain a right-to-left override (U+202E) and display
as a different name. Reading a skill's companion file by the exact path stored on the skill always works, even for
older files whose stored path would not pass these rules today.

## Reserved paths

The script runtime owns `.codemie_bridge/` and a root `codemie_runtime_sdk.py` in the workspace. These names are
checked at the workspace root only, ignoring letter case and Unicode compatibility forms (`.CODEMIE_BRIDGE/x` and
fullwidth look-alikes count). Writing or deleting them is refused (a single write or delete returns a validation error;
inside a batch the file is skipped with a warning). Reading and listing are not restricted. A folder with the same name
deeper in the tree, for example inside a skill folder, is an ordinary folder.

## For maintainers

- Skill blobs may be referenced by workspaces. Do not delete or overwrite them without a reference check; skill update
  and delete deliberately leave old versions in place. Cleaning up unreferenced versions is not implemented.
- Seeding is one database transaction (`AgentWorkspaceRepository.seed_references`, called with source kind `skill`): a folder-owner row
  (`agent_workspace_folder_owners`, unique per skill and per folder) plus file rows inserted with `ON CONFLICT DO NOTHING`.
  A crash leaves nothing behind, and existing rows, including soft-deleted ones, are never touched.
- A skill whose files are all skipped writes no folder-owner row, so its notice repeats and a later fix is not frozen out.
- Download tokens for files whose stored file belongs to a seeding source (today a skill: `skill-<id>` owner) are
  accepted when a live file of the requesting user's own workspace points at it (`download_rules`, rule D2: source blob
  referenced by a live row of the user's own workspace). Such tokens are never registered as new workspace files, and a
  workflow run that carries one is refused.

## Rollout notes

- Removed settings: `SKILL_WORKSPACE_SEED_MAX_ATTEMPTS`, `SKILL_WORKSPACE_SEED_STALE_THRESHOLD_MINUTES` and
  `SKILL_WORKSPACE_SEED_WAIT_SECONDS`. Nothing in the repository sets them.
- The migration `y1z2a3b4c5d6` was rewritten in place (it was not merged): it now creates only
  `agent_workspace_folder_owners`.
