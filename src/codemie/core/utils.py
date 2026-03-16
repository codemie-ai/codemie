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

import io
import os
import re
import secrets
import string
import zipfile
from functools import lru_cache
from typing import Any, Generator, List
from urllib.parse import urlparse

import json
import markdown
from pygments import highlight
from pygments.lexers import JsonLexer
from pygments.formatters import HtmlFormatter
import pathspec
import hashlib

from codemie_tools.base.utils import get_encoding
from codemie_tools.base.file_object import FileObject

from codemie.configs import logger
from codemie.configs.llm_config import CostConfig
from codemie.service.llm_service.llm_service import llm_service


def sanitize_string(input_string: str) -> str:
    """
    Sanitize a string by replacing or masking potentially sensitive information.

    This function uses predefined regular expressions to identify and replace common patterns
    of sensitive data such as passwords, usernames, IP addresses, email addresses,
    API keys and credit card numbers.

    Args:
        input_string (str): The original string to be sanitized.

    Returns:
        str: The sanitized string with sensitive information removed or masked.

    Example:
        >>> original_string = "Error: Unable to connect. Username: admin, Password: secret123, IP: 192.168.1.1"
        >>> sanitize_string(original_string)
        'Error: Unable to connect. Username: ***, Password: ***, IP: [IP_ADDRESS]'
    """
    patterns = [
        (r'\b(password|pwd|pass)(\s*[:=]\s*|\s+)(\S+)', r'\1\2***'),  # Passwords
        (r'\b(username|user|uname)(\s*[:=]\s*|\s+)(\S+)', r'\1\2***'),  # Usernames
        (r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[IP_ADDRESS]'),  # IP addresses
        (r'\b(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b', '[EMAIL]'),  # Email addresses
        (r'\b(api[_-]?key|access[_-]?token)(\s*[:=]\s*|\s+)(\S+)', r'\1\2[API_KEY]'),  # API keys and access tokens
        (r'\b(?:\d{4}[-\s]?){4}\b', '[CREDIT_CARD]'),  # Credit card numbers
    ]

    sanitized_string = input_string

    for pattern, replacement in patterns:
        sanitized_string = re.sub(pattern, replacement, sanitized_string, flags=re.IGNORECASE)

    return sanitized_string


def extract_text_from_llm_output(llm_output: Any, return_last: bool = False) -> str:
    """
    Extracts text from various input formats.

    This function handles different input types and converts them to a string format:
    - If input is a string, returns it as-is
    - If input is a list of dictionaries, returns the 'text' value from the first dictionary
    - If input is an empty list, returns an empty string
    - For all other cases, converts the input to string

    Args:
        llm_output (Any): The input to process. Can be a string, list of dictionaries,
                         empty list, or any other type.

    Returns:
        str: The extracted or converted text

    Examples:
        >>> extract_text_from_llm_output("hello")
        'hello'
        >>> extract_text_from_llm_output([{"text": "hello"}])
        'hello'
        >>> extract_text_from_llm_output([])
        ''
    """
    needed_index = -1 if return_last else 0

    if isinstance(llm_output, str):
        return llm_output

    if isinstance(llm_output, list):
        if len(llm_output) > 0 and isinstance(llm_output[needed_index], dict):
            return llm_output[needed_index].get('text', '')
        if len(llm_output) == 0:
            return ''

    return str(llm_output)


def unpack_json_strings(obj):
    """
    Recursively converts string fields containing JSON objects or arrays into native Python data structures.

    This function traverses dicts and lists, and attempts to parse any string value as JSON.
    Only JSON strings representing objects or arrays are parsed; others are left untouched.
    The function is applied recursively to all levels of the input structure.

    Args:
        obj (Any): The input data structure (dict, list, or scalar).

    Returns:
        Any: The input structure with all JSON-object/array string values decoded as Python dicts/lists.

    Examples:
        >>> unpack_json_strings({'a': '[1, 2, 3]', 'b': '{"c": "d"}', 'e': 'not json'})
        {'a': [1, 2, 3], 'b': {'c': 'd'}, 'e': 'not json'}

        >>> unpack_json_strings(['{"foo": "bar"}', 'baz'])
        [{'foo': 'bar'}, 'baz']

        >>> unpack_json_strings({'x': '{"nums": "[1, 2]"}'})
        {'x': {'nums': [1, 2]}}

        >>> unpack_json_strings('42')
        '42'

        >>> unpack_json_strings({'a': '42', 'b': 'true'})
        {'a': '42', 'b': 'true'}
    """
    if isinstance(obj, dict):
        return {k: unpack_json_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unpack_json_strings(item) for item in obj]
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
            # Avoid parsing plain numbers/bools/null
            if isinstance(parsed, (dict, list)):
                return unpack_json_strings(parsed)
        except Exception:
            pass
    return obj


def calculate_tokens(text: str, llm_model: str = llm_service.default_llm_model):
    encoding = get_encoding(llm_model)
    return len(encoding.encode(str(text)))


@lru_cache(maxsize=128)
def _create_pathspec_from_filter(files_filter: str) -> tuple[pathspec.PathSpec, pathspec.PathSpec, bool]:
    """
    Create and cache PathSpec objects from filter string.
    This significantly improves performance when the same filter is used repeatedly.

    Filter behavior:
    - Empty filter: Include all files
    - Patterns (e.g., *.py): Include ONLY matching files (whitelist)
    - !Patterns (e.g., !*.nupkg): EXCLUDE matching files (blacklist)
    - Combined: Include matching files, but exclude !patterns

    :param files_filter: The string representation of gitignore-like pattern rules.
    :return: Tuple of (include_spec, exclude_spec, has_include_patterns).
    """
    # Split the filter into include and exclude patterns
    include_patterns = []
    exclude_patterns = []

    pattern_count = 0
    for line in files_filter.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue  # Skip empty lines and comments
        pattern_count += 1
        if line.startswith('!'):
            exclude_patterns.append(line[1:])
        else:
            include_patterns.append(line)

    # Log warning for large filter patterns
    if pattern_count > 20:
        logger.info(
            f"Large filter pattern detected: {pattern_count} patterns. "
            f"Repository scanning may take several minutes for large repositories."
        )

    # Parse include and exclude patterns
    include_spec = pathspec.PathSpec.from_lines('gitwildmatch', include_patterns)
    exclude_spec = pathspec.PathSpec.from_lines('gitwildmatch', exclude_patterns)

    return include_spec, exclude_spec, len(include_patterns) > 0


def check_file_type(
    file_name: str,
    files_filter: str,
    repo_local_path: str,
    excluded_files: List[str],
) -> bool:
    """
    Check if a file matches certain criteria including being filtered by a gitignore-like pattern.

    Filter behavior:
    - Empty filter: Include all files
    - Patterns (e.g., *.py): Include ONLY matching files (whitelist)
    - !Patterns (e.g., !*.nupkg): EXCLUDE matching files (blacklist)
    - Combined (e.g., *.py + !test_*.py): Include .py files except test_*.py files

    :param file_name: The name of the file to check.
    :param files_filter: The string representation of gitignore-like pattern rules.
    :param repo_local_path: The local path of the repository.
    :param excluded_files: A list of file extensions to exclude.
    :return: True if the file matches the criteria, False otherwise.
    """
    # Normalize file_name by removing the repo_local_path prefix
    if repo_local_path and file_name.startswith(repo_local_path):
        file_name = file_name[len(repo_local_path) + 1 :]

    # Reject files that are in the excluded_files list
    if os.path.splitext(file_name)[1] in excluded_files:
        return False

    # If files_filter is empty, return True by default
    if not files_filter.strip():
        return True

    # Use cached PathSpec objects for improved performance
    include_spec, exclude_spec, has_include_patterns = _create_pathspec_from_filter(files_filter)

    # Check exclusions first (blacklist with ! prefix)
    if exclude_spec.match_file(file_name):
        return False

    # If there are include patterns (whitelist), file must match at least one
    if has_include_patterns:
        return include_spec.match_file(file_name)

    # No include patterns, so include by default (already passed exclude check)
    return True


def generate_zip(files: dict) -> Generator[bytes, None, None]:
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in files.items():
            zip_file.writestr(filename, content)

    zip_buffer.seek(0)
    while chunk := zip_buffer.read(8192):  # 8KB chunks
        yield chunk


def format_json_content(json_content):
    pretty_json = json.dumps(json_content, indent=4)
    highlighted_json = highlight(pretty_json, JsonLexer(), HtmlFormatter())
    return highlighted_json


def format_markdown_content(markdown_text):
    return markdown.markdown(markdown_text)


def get_url_domain(url: str) -> str:
    """
    Extract the domain part from a URL, preserving the port number.
    Args:
        url (str): The URL to parse
    Returns:
        str: The domain part of the URL (scheme + hostname)
    Example:
        >>> get_url_domain("https://github.com/user/repo")
        'https://github.com'
    """
    if not isinstance(url, str):
        raise ValueError("Input must be a string representing a URL.")

    parsed_url = urlparse(url)
    if not parsed_url.scheme or not parsed_url.hostname:
        raise ValueError("Invalid URL provided.")

    if parsed_url.port:
        return f"{parsed_url.scheme}://{parsed_url.hostname}:{parsed_url.port}"
    return f"{parsed_url.scheme}://{parsed_url.hostname}"


def hash_string(string_to_hash: str) -> str:
    """
    Generate a SHA-256 hash for the given string.

    Args:
        string_to_hash (str): The input string to hash.

    Returns:
        str: The hexadecimal SHA-256 hash of the input string.

    Example:
        >>> hash_string("hello")
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    """
    return hashlib.sha256(string_to_hash.encode()).hexdigest()


def calculate_token_cost(
    llm_model: str,
    cost_config: CostConfig,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> tuple[float, float, float]:
    """
    Calculate the cost of tokens based on the provided CostConfig.

    This function calculates the total cost of tokens used in an LLM operation, supporting
    both LangChain format (input_tokens = total) and Claude native format (separate tokens).

    Cache creation cost is only calculated if cache_creation_input_token_cost is configured
    in the model's CostConfig. If not configured, cache_creation_cost will be 0.

    Args:
        llm_model: LLM Model name
        cost_config: CostConfig object containing token pricing information
        input_tokens: Total input tokens (LangChain: prompt+cached+cache_creation, CLI: prompt only)
        output_tokens: Number of output tokens generated
        cached_tokens: Number of cache read tokens (default: 0)
        cache_creation_tokens: Number of cache creation tokens (default: 0)

    Returns:
        tuple[float, float, float]: (total_cost, cached_tokens_cost, cache_creation_cost)
            - total_cost: Total cost in USD (prompt + cache_creation + cache_read + output)
            - cached_tokens_cost: Cache read cost only in USD (0 if not configured)
            - cache_creation_cost: Cache creation cost only in USD (0 if not configured)

    Example:
        >>> from codemie.configs.llm_config import CostConfig
        >>> config = CostConfig(input=0.01, output=0.02)
        >>> # LangChain format (no cache_creation yet)
        >>> total, cached, creation = calculate_token_cost("gpt-4", config, 100, 50, 20)
        >>> # Claude native format (with cache_creation)
        >>> config_claude = CostConfig(input=0.000003, output=0.000015, cache_creation_input_token_cost=0.00000375)
        >>> total, cached, creation = calculate_token_cost("claude", config_claude, 7, 17, 18504, 37202)
    """
    # Calculate prompt tokens for cost calculation
    # CLI native format: input_tokens is pure prompt
    # LangChain format: input_tokens includes cached, subtract to get pure prompt
    # If input < cached, it means input_tokens is already pure prompt (not inclusive)
    if cache_creation_tokens > 0 or input_tokens < cached_tokens:
        prompt_tokens = input_tokens
    else:
        prompt_tokens = input_tokens - cached_tokens

    # Calculate pure prompt tokens cost
    prompt_tokens_cost = prompt_tokens * cost_config.input

    # Calculate output tokens cost
    output_cost = output_tokens * cost_config.output

    # Calculate cache creation tokens (applicable only for Anthropic models)
    cache_creation_cost = 0.0
    if (
        cache_creation_tokens > 0
        and hasattr(cost_config, 'cache_creation_input_token_cost')
        and cost_config.cache_creation_input_token_cost is not None
    ):
        cache_creation_cost = cache_creation_tokens * cost_config.cache_creation_input_token_cost

    # Calculate cached tokens cost if available and cached tokens are present
    cached_tokens_cost = 0.0
    if cost_config.cache_read_input_token_cost is not None and cached_tokens > 0:
        cached_tokens_cost = cached_tokens * cost_config.cache_read_input_token_cost

    # Apply batch token costs if available for prompt tokens
    if cost_config.input_cost_per_token_batches is not None:
        prompt_tokens_cost += prompt_tokens * cost_config.input_cost_per_token_batches

    # Apply batch token costs if available for output tokens
    if cost_config.output_cost_per_token_batches is not None:
        output_cost += output_tokens * cost_config.output_cost_per_token_batches

    # Calculate total cost
    total_cost = prompt_tokens_cost + output_cost + cached_tokens_cost + cache_creation_cost

    cache_creation_rate = getattr(cost_config, 'cache_creation_input_token_cost', 0.0) or 0.0
    logger.debug(
        f"Token cost [{llm_model}]: "
        f"Prompt({prompt_tokens}*${cost_config.input:.6f}=${prompt_tokens_cost:.6f}) + "
        f"CacheWrite({cache_creation_tokens}*${cache_creation_rate:.6f}=${cache_creation_cost:.6f}) + "
        f"CacheRead({cached_tokens}*${cost_config.cache_read_input_token_cost or 0:.6f}=${cached_tokens_cost:.6f}) + "
        f"Output({output_tokens}*${cost_config.output:.6f}=${output_cost:.6f}) = "
        f"Total: ${total_cost:.6f}"
    )

    return total_cost, cached_tokens_cost, cache_creation_cost


def calculate_cli_metric_cost(attributes: dict) -> tuple[float, float, float]:
    """Calculate cost for CLI metrics from attributes dictionary.

    Wrapper around calculate_token_cost() that extracts token counts from CLI metric attributes.
    CLI sends Claude's native format with separate token types:
    - total_input_tokens: Pure prompt tokens only (no cache included)
    - total_cache_creation_tokens: Tokens used to CREATE cache (1.25x price for Claude)
    - total_cache_read_input_tokens: Tokens READ from cache (0.1x price for Claude)
    - total_output_tokens: Output tokens generated

    Args:
        attributes: CLI metric attributes dict containing token counts and model name

    Returns:
        tuple[float, float, float]: (total_cost, cache_read_cost, cache_creation_cost)

    Example:
        >>> attrs = {
        ...     'llm_model': 'claude-3-5-sonnet-20241022',
        ...     'total_input_tokens': 7,
        ...     'total_cache_creation_tokens': 37202,
        ...     'total_cache_read_input_tokens': 18504,
        ...     'total_output_tokens': 17
        ... }
        >>> total, cached, creation = calculate_cli_metric_cost(attrs)
    """
    from codemie.service.llm_service.llm_service import llm_service

    try:
        llm_model = attributes.get('llm_model', 'unknown')
        input_tokens = int(attributes.get('total_input_tokens', 0))
        cache_creation_tokens = int(attributes.get('total_cache_creation_tokens', 0))
        cache_read_tokens = int(attributes.get('total_cache_read_input_tokens', 0))
        output_tokens = int(attributes.get('total_output_tokens', 0))

        # Get model cost configuration
        cost_config = llm_service.get_model_cost(llm_model)

        # Use unified cost calculation (handles Claude native format)
        return calculate_token_cost(
            llm_model=llm_model,
            cost_config=cost_config,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    except Exception as e:
        logger.warning(f"Failed to calculate CLI metric cost: {e}")
        return 0.0, 0.0, 0.0


def _process_file_names_to_objects(file_names: list[str], unique_files_dict: dict[str, FileObject]) -> None:
    """
    Process a list of file names into FileObject instances with content and add them to the dictionary.

    Args:
        file_names: List of encoded file URLs to process
        unique_files_dict: Dictionary to store unique FileObjects by name
    """
    from codemie.service.file_service.file_service import FileService

    for file_name in file_names:
        try:
            file_object = FileService.get_file_object(file_name)
            unique_files_dict[file_name] = file_object
        except ValueError as v_err:
            logger.error(f"Could not extract file: {file_name}, error: {v_err}")
        except Exception as e:
            logger.warning(f"Failed to load content for file '{file_name}': {e}. Using file object without content.")
            # Still add file object without content if loading fails
            try:
                file_object = FileObject.from_encoded_url(file_name)
                unique_files_dict[file_name] = file_object
            except ValueError:
                pass


def _should_include_message(message, history_index: int | None) -> bool:
    """
    Determine if a message should be included in file collection.

    Args:
        message: The message to evaluate
        history_index: Optional history index to limit file collection

    Returns:
        True if the message should be included, False otherwise
    """
    from codemie.rest_api.models.conversation import GeneratedMessage

    if not isinstance(message, GeneratedMessage):
        return False

    if message.history_index is None:
        return False

    # Skip messages that are at or after the current history_index when editing/resubmitting
    return not (history_index is not None and message.history_index >= history_index)


def _get_unique_messages_from_history(history: list, history_index: int | None) -> dict:
    """
    Extract unique messages from conversation history, keeping only the latest version of each.

    When multiple messages exist with the same (role, history_index), only the most recent
    version is kept. This handles the case where a message is edited and resubmitted.

    Args:
        history: List of messages from conversation history
        history_index: Optional history index to limit message collection

    Returns:
        Dictionary mapping (role, history_index) tuples to the latest message version
    """
    unique_messages = {}

    for message in history:
        if not _should_include_message(message, history_index):
            continue

        # Use tuple of (role, history_index) as the key to keep only the latest version
        key = (message.role, message.history_index)
        unique_messages[key] = message

    return unique_messages


def _collect_files_from_conversation(conversation_id: str, history_index: int | None, unique_files_dict: dict) -> None:
    """
    Collect files from conversation history and add them to the unique files dictionary.

    Args:
        conversation_id: The conversation ID to retrieve history from
        history_index: Optional history index to limit file collection
        unique_files_dict: Dictionary to store unique FileObjects
    """
    from codemie.rest_api.models.conversation import Conversation

    conversation = Conversation.find_by_id(conversation_id)
    history = getattr(conversation, "history", [])

    # Deduplicate messages to keep only the latest version of each (role, history_index)
    # This ensures edited messages with removed files don't leave old file references accessible
    unique_messages = _get_unique_messages_from_history(history, history_index)

    # Collect files only from the latest version of each message
    for message in unique_messages.values():
        if message.file_names:
            _process_file_names_to_objects(message.file_names, unique_files_dict)


def build_unique_file_objects(
    file_names: list[str] | None = None, conversation_id: str | None = None, history_index: int | None = None
) -> dict[str, FileObject]:
    """
    Builds a dictionary of unique FileObject instances from file names and conversation history.

    This function extracts FileObjects from both the provided file_names list and the conversation
    history if a conversation_id is provided, ensuring that each file is only included once in the
    result set. Files are considered unique based on their name attribute.

    When history_index is provided, only files from messages with history_index strictly less than
    the provided value are included from conversation history. This prevents accessing files from
    future messages when editing and resubmitting messages in the middle of a conversation.

    Args:
        file_names: Optional list of encoded file URLs to process
        conversation_id: Optional conversation ID to retrieve file names from conversation history
        history_index: Optional history index to limit file collection. Only files from messages
                      with history_index < this value will be included from conversation history.

    Returns:
        A dictionary mapping file names to unique FileObject instances
    """
    unique_files_dict: dict[str, FileObject] = {}

    # Process current files if present
    if file_names:
        _process_file_names_to_objects(file_names, unique_files_dict)

    # Process files from conversation history if conversation_id is provided
    if conversation_id:
        _collect_files_from_conversation(conversation_id, history_index, unique_files_dict)

    return unique_files_dict


def build_unique_file_objects_list(
    file_names: list[str] | None = None, conversation_id: str | None = None, history_index: int | None = None
) -> list[FileObject]:
    """
    Builds a list of unique FileObject instances from file names and conversation history.
    File content is loaded from the repository automatically.

    This is a convenience wrapper around build_unique_file_objects that returns a list
    instead of a dictionary.

    Args:
        file_names: Optional list of encoded file URLs to process
        conversation_id: Optional conversation ID to retrieve file names from conversation history
        history_index: Optional history index to limit file collection. Only files from messages
                      with history_index < this value will be included from conversation history.

    Returns:
        A list of unique FileObject instances with content loaded from repository
    """
    file_objects = build_unique_file_objects(
        file_names=file_names, conversation_id=conversation_id, history_index=history_index
    )
    return list(file_objects.values())


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning a default value if the denominator is zero.

    This utility function prevents division by zero errors by checking the denominator
    before performing the division operation. It's particularly useful for calculating
    averages and ratios where the divisor might be zero.

    Args:
        numerator: The number to be divided
        denominator: The number to divide by
        default: The value to return if denominator is zero (default: 0.0)

    Returns:
        The result of numerator / denominator if denominator > 0, otherwise the default value

    Examples:
        >>> safe_divide(100, 5)
        20.0
        >>> safe_divide(100, 0)
        0.0
        >>> safe_divide(100, 0, default=-1.0)
        -1.0
        >>> safe_divide(50.5, 2.5)
        20.2
    """
    return numerator / denominator if denominator > 0 else default


def append_random_suffix(value: str) -> str:
    """
    Return a new string with a random 15-character lowercase suffix appended.

    Args:
        value: The base string value.

    Returns:
        A new string in the format value_{15 random lowercase letters}.
    """
    suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(15))
    return f"{value}_{suffix}"
