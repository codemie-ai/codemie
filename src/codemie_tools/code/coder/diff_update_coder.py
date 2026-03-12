# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import re
import time
import uuid
from typing import Tuple, List

from langchain_core.messages import AIMessage, HumanMessage

from codemie_tools.code.coder.diff_update_prompts import base_messages
from codemie_tools.code.linter.facade import LinterFacade

logger = logging.getLogger(__name__)


def update_content_by_task(old_content, task_details, llm):
    origin_temp, origin_top_p = set_llm_params(llm)
    try:
        return solve_task_with_retry(old_content, task_details, llm)
    finally:
        restore_llm_params(llm, origin_temp, origin_top_p)


def solve_task_with_retry(old_content, task_details, llm):
    messages = base_messages + [
        HumanMessage(content=old_content),
        AIMessage(content="Ok. I will focus on making changes in the current file content"),
        HumanMessage(content=f"Here it the task: {task_details}"),
    ]
    return call_and_process_llm(llm, messages, old_content)


MAX_RETRIES = 3
BASE_DELAY = 1  # in seconds


def call_and_process_llm(llm, messages, old_content):
    # Generate a unique ID for this method call
    call_id = str(uuid.uuid4())[:8]  # Using first 8 characters of a UUID for brevity

    for attempt in range(MAX_RETRIES):
        llm_response = llm.invoke(messages)
        try:
            new_content, edits = extract_and_apply_edits(llm_response.content, old_content)
            return new_content, pretty_format_edits(edits)
        except ValueError as e:
            delay = BASE_DELAY * (2**attempt)
            logger.warning(
                f"Diff coder. Error while processing llm response for call {call_id}."
                f" Error: {str(e)}"
                f" Attempt {attempt + 1} failed. Retrying in {delay} seconds..."
            )
            if attempt == MAX_RETRIES - 1:
                raise e
            messages = messages + [
                llm_response,
                HumanMessage(content=f"I got the following error processing your response: \"{str(e)}\". Fix it"),
            ]
            time.sleep(delay)


def pretty_format_edits(edits: List[Tuple[str, str]]) -> str:
    return "\n".join(
        [
            f"Change {i + 1}:\nOriginal Code:\n{original}\n\nNew Code:\n{new}\n{'-' * 40}"
            for i, (original, new) in enumerate(edits)
        ]
    )


def set_llm_params(llm):
    if (temperature := getattr(llm, 'temperature', None)) is not None:
        setattr(llm, 'temperature', 0.1)
    if (top_p := getattr(llm, 'top_p', None)) is not None:
        setattr(llm, 'top_p', 0.2)
    return temperature, top_p


def extract_and_apply_edits(llm_response, old_content):
    edits = get_edits(llm_response)
    if not edits:
        raise ValueError("There are no *SEARCH/REPLACE* blocks in the response")

    content_candidate = apply_edits(edits, old_content)
    suc, errors = LinterFacade().lint_code(get_lang_from_response(llm_response), old_content, content_candidate)
    if not suc:
        logger.warning(f"Code check failed Errors:{errors}")
        raise ValueError(f"Code check failed. Errors:\n{errors}")
    return content_candidate, edits


def get_lang_from_response(llm_response: str) -> str:
    match = re.search(r'!!!(\w+)', llm_response)
    if match:
        return match.group(1)
    else:
        logger.info("Language not found in the LLM response.")
        return ""


def restore_llm_params(llm, origin_temp, origin_top_p):
    if hasattr(llm, 'temperature'):
        setattr(llm, 'temperature', origin_temp)
    if hasattr(llm, 'top_p'):
        setattr(llm, 'top_p', origin_top_p)


def get_edits(content):
    return list(find_original_update_blocks(content))


def apply_edits(edits, content):
    failed = []
    passed = []
    for edit in edits:
        original, updated = edit
        new_content = do_replace(content, original, updated)
        if new_content:
            content = new_content
            passed.append(edit)
        else:
            failed.append(edit)

    if not failed:
        return content

    blocks = "block" if len(failed) == 1 else "blocks"

    res = f"# {len(failed)} SEARCH/REPLACE {blocks} failed to match!\n"
    for edit in failed:
        original, updated = edit

        res += f"""
## SearchReplaceNoExactMatch: This SEARCH block failed to exactly match lines
<<<<<<< SEARCH
{original}=======
{updated}>>>>>>> REPLACE

The SEARCH section must exactly match an existing block of lines including all white space, comments, indentation,
docstrings, etc
"""
    raise ValueError(res)


def find_original_update_blocks(content):
    # make sure we end with a newline, otherwise the regex will miss <<UPD on the last line
    if not content.endswith("\n"):
        content = content + "\n"

    head = "<<<<<<< SEARCH"
    divider = "======="
    updated = ">>>>>>> REPLACE"

    separators = "|".join([head, divider, updated])

    split_re = re.compile(r"^((?:" + separators + r")[ ]*\n)", re.MULTILINE | re.DOTALL)

    pieces = re.split(split_re, content)

    pieces.reverse()
    processed = []

    try:
        while pieces:
            cur = pieces.pop()

            if cur.strip() != head:
                processed.append(cur)
                continue

            processed.append(cur)  # original_marker

            original_text = pieces.pop()
            processed.append(original_text)

            divider_marker = pieces.pop()
            processed.append(divider_marker)
            if divider_marker.strip() != divider:
                raise ValueError(f"Expected `{divider}` not {divider_marker.strip()}")

            updated_text = pieces.pop()
            processed.append(updated_text)

            updated_marker = pieces.pop()
            processed.append(updated_marker)
            if updated_marker.strip() != updated:
                raise ValueError(f"Expected `{updated}` not `{updated_marker.strip()}")

            yield original_text, updated_text
    except ValueError as e:
        processed = "".join(processed)
        err = e.args[0]
        raise ValueError(f"{processed}\n^^^ {err}")
    except IndexError:
        processed = "".join(processed)
        raise ValueError(f"{processed}\n^^^ Incomplete SEARCH/REPLACE block.")
    except Exception:
        processed = "".join(processed)
        raise ValueError(f"{processed}\n^^^ Error parsing SEARCH/REPLACE block.")


def do_replace(content, before_text, after_text):
    before_text = strip_quoted_wrapping(before_text)

    after_text = strip_quoted_wrapping(after_text)

    if content is None:
        return

    if not before_text.strip():
        # append to existing file, or start a new file
        new_content = content + after_text
    else:
        new_content = replace_most_similar_chunk(content, before_text, after_text)

    return new_content


def strip_quoted_wrapping(res):
    if not res:
        return res

    res = res.splitlines()

    if res[0].startswith("!!!") and res[-1].startswith("!!!"):
        res = res[1:-1]

    res = "\n".join(res)
    if res and res[-1] != "\n":
        res += "\n"

    return res


def replace_most_similar_chunk(whole, part, replace):
    """Best efforts to find the `part` lines in `whole` and replace them with `replace`"""
    occur_count = whole.count(part)
    if occur_count > 1:
        raise ValueError(f"The following SEARCH block is not unique in the original content: \n\"{part}\"")

    whole, whole_lines = prep(whole)
    part, part_lines = prep(part)
    replace, replace_lines = prep(replace)

    res = perfect_or_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    # drop leading empty line, GPT sometimes adds them spuriously¬
    if len(part_lines) > 2 and not part_lines[0].strip():
        skip_blank_line_part_lines = part_lines[1:]
        res = perfect_or_whitespace(whole_lines, skip_blank_line_part_lines, replace_lines)
        if res:
            return res

    # Try to handle when it elides code with ...
    try:
        res = try_dotdotdots(whole, part, replace)
        if res:
            return res
    except ValueError:
        pass


def prep(content):
    if content and not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines(keepends=True)
    return content, lines


def perfect_or_whitespace(whole_lines, part_lines, replace_lines):
    # Try for a perfect match
    res = perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    # Try being flexible about leading whitespace
    res = replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res


def perfect_replace(whole_lines, part_lines, replace_lines):
    part_tup = tuple(part_lines)
    part_len = len(part_lines)

    for i in range(len(whole_lines) - part_len + 1):
        whole_tup = tuple(whole_lines[i : i + part_len])
        if part_tup == whole_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + part_len :]
            return "".join(res)


def replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines):
    # GPT often messes up leading whitespace.
    # It usually does it uniformly across the ORIG and UPD blocks.
    # Either omitting all leading whitespace, or including only some of it.

    # Outdent everything in part_lines and replace_lines by the max fixed amount possible
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]

    if leading and min(leading):
        num_leading = min(leading)
        part_lines = [p[num_leading:] if p.strip() else p for p in part_lines]
        replace_lines = [p[num_leading:] if p.strip() else p for p in replace_lines]

    # can we find an exact match not including the leading whitespace
    num_part_lines = len(part_lines)

    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = match_but_for_leading_whitespace(whole_lines[i : i + num_part_lines], part_lines)

        if add_leading is None:
            continue

        replace_lines = [add_leading + rline if rline.strip() else rline for rline in replace_lines]
        whole_lines = whole_lines[:i] + replace_lines + whole_lines[i + num_part_lines :]
        return "".join(whole_lines)

    return None


def match_but_for_leading_whitespace(whole_lines, part_lines):
    num = len(whole_lines)

    # does the non-whitespace all agree?
    if not all(whole_lines[i].lstrip() == part_lines[i].lstrip() for i in range(num)):
        return

    # are they all offset the same?
    add = {whole_lines[i][: len(whole_lines[i]) - len(part_lines[i])] for i in range(num) if whole_lines[i].strip()}

    if len(add) != 1:
        return

    return add.pop()


def try_dotdotdots(whole, part, replace):
    """
    See if the edit block has ... lines.
    If not, return none.

    If yes, try and do a perfect edit with the ... chunks.
    If there's a mismatch or otherwise imperfect edit, raise ValueError.

    If perfect edit succeeds, return the updated whole.
    """

    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE | re.DOTALL)

    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)

    if not validate_pieces(part_pieces, replace_pieces):
        return

    part_pieces = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    replace_pieces = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

    pairs = zip(part_pieces, replace_pieces)
    for part, replace in pairs:
        if not part and not replace:
            continue
        if not part and replace:
            if not whole.endswith("\n"):
                whole += "\n"
            whole += replace
            continue
        if whole.count(part) != 1:
            raise ValueError
        whole = whole.replace(part, replace, 1)

    return whole


def validate_pieces(part_pieces, replace_pieces):
    if len(part_pieces) != len(replace_pieces):
        raise ValueError("Unpaired ... in SEARCH/REPLACE block")
    if len(part_pieces) == 1:
        return False
    all_dots_match = all(part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2))
    if not all_dots_match:
        raise ValueError("Unmatched ... in SEARCH/REPLACE block")
    return True
