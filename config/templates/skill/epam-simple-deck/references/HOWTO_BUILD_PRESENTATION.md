- if user's request is not stored in `presentation.request`, save exact user request with all his/her input to `presentation.request`
- you must not explore simple_deck and its API - you have all the information to update main presentation script, and agents building presentation parts will know how to do that 
- agents for building outline and presentation parts will know location and purpose of all default files

# Creating/updating entire presentation
This instruction is exhaustive, you should follow it strictly, no additional steps and no skipping steps.
Steps are sequential, proceed with a step only when all the previous steps are completed. If some step is to be completed by another agent(s), wait till the agent fully completes its work before moving to the next step.

1. Thoroughly analyze request
2. Ask another agent to build outline
    Always delegate creation/update of outline to a separate agent. Ask the agent to use 'epam-simple-deck' for outline creation. Give brief overview of what should be done, do NOT give content slide by slide. 
3. Read created/updated outline
    if there is no `presentation.outline` file, repeat step 2.
4. Define design guidelines
    Use instruction in `references/HOWTO_BUILD_DESIGN_GUIDELINES.md`. Always save design guidelines in `presentation.design`.
5. Build presentation parts
    Always delegate building/updating presentation parts to other agents (one agent per part), run the agents in parallel.
    These agents will handle image creation if it's required.
    Provide the following input to an agent:
        - part number
        - instruction to use 'epam-simple-deck' skill
        - what it should do
    Building parts will take time - do not do anything else during that time, just wait till all the parts are completed. Do NOT start updating main presentation script until all agents are fully done, it's not enough to have `presentation_part<X>.py` present, agents might be working on addressing validation issues still.
6. Update main presentation script `presentation.py` (see instruction below)
7. Generate final presentation (see instruction below)
8. Fix validation issue if any 
    - Ask separate agent that can create/update presentation parts to fix problems in specific part. Spawn as much agents as needed, one per part that requires fix.
    - After all the fixes are done, regenerate final presentation. If any validation issue still, fix it and repeat validation process.

## How to generate final presentation
if uv is used
`uv run python presentation.py`

if no uv
`python presentation.py`

## Prerequisites
simple-deck Python package should be installed.
Installation instructions are in references/INSTALLATION.md

## How to create/update main presentation script `presentation.py`

Based on the number of parts, update `presentation.py` - include proper imports and call appropriate methods `generate_part<X>(prs)` from presentation parts.

### Example
Here 3 parts are used just as an example - actual number of parts should be determined based on the complexity and size of the presentation.
Actual files will differ from this example only in the number of included parts.

```python
from simple_deck import EPAMPresentation
from .presentation_part1 import generate_part1
from .presentation_part2 import generate_part2
from .presentation_part3 import generate_part3

prs = EPAMPresentation(
    output_path="presentation.pptx"
)

generate_part1(prs)
generate_part2(prs)
generate_part3(prs)

# Save the presentation — validation report printed automatically
prs.save()
print("Presentation created: presentation.pptx")
```

