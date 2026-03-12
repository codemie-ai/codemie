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

from codemie_tools.base.models import ToolMetadata

# System tools available in the sandbox environment
COMMON_SANDBOX_SYSTEM_TOOLS = [
    "git",
    "node (v22 LTS)",
]

# Python libraries available in the sandbox environment
COMMON_SANDBOX_LIBRARIES = [
    # Data manipulation and analysis
    "pandas",
    "numpy",
    # Plotting and visualization
    "matplotlib",
    # Data serialization
    "pydantic",
    "pydantic-settings",
    "python-dotenv",
    "python-dateutil",
    "click",
    "PyYAML",
    "structlog",
    # Document processing
    "pymupdf",
    "python-pptx",
    "lxml",
    "python-docx",
    "docx2txt",
    "openpyxl",
    "clevercsv",
    "markdownify",
    "markdown",
    "markitdown",
    "chardet",
    "requests",
    "py7zr",
]

# Safe Python standard library modules that can be used
SAFE_STDLIB_MODULES = "json, datetime, re, math, random, collections, itertools, functools, statistics, copy, csv, base64, hashlib, hmac, secrets, string, textwrap, unicodedata, difflib, heapq, bisect, array, enum, decimal, fractions, typing, dataclasses, time"

PYTHON_RUN_CODE_TOOL = ToolMetadata(
    name="python_repl_code_interpreter",
    description="""
    A Python shell. Use this to execute python commands when you need to perform calculations, computations.
    Use this tool to generate diagrams, plots, charts and utilize available mermaid-py, matplotlib, python-mermaid libs
    Input should be a valid python command.
    """.strip(),
    label="Code Interpreter",
    user_description="""
    Provides access to a Python shell, allowing the AI assistant to execute Python commands for various computational tasks, data analysis, and visualization purposes.
    Usage Note:
    Use this tool when you need to perform calculations, computations, or generate visual representations of data. It's particularly useful for:
    - Complex mathematical operations
    - Data manipulation and analysis
    - Generating diagrams, plots, and charts
    - Creating flowcharts and diagrams using mermaid-py
    - Plotting graphs and visualizations with matplotlib
    - Utilizing python-mermaid for sequence diagrams and flowcharts
    The tool provides access to common Python libraries for these tasks.
    """.strip(),
)

CODE_EXECUTOR_TOOL = ToolMetadata(
    name="code_executor",
    description=f"""
    Execute Python code for data analysis, plotting, calculations, or file processing.

    UPLOADED FILES (CRITICAL):
    - Files listed at top of code field description
    - MUST use EXACT filenames including ALL special characters: brackets [], parentheses (), spaces
    - Available in working directory - use directly by shown name
    - Example: If shown '[Video] Template.pptx', use that exact string, NOT 'Video Template.pptx'

    LIBRARIES: {', '.join(COMMON_SANDBOX_LIBRARIES)}
    STDLIB MODULES: {SAFE_STDLIB_MODULES}
    SYSTEM TOOLS: {', '.join(COMMON_SANDBOX_SYSTEM_TOOLS)}

    FILE EXPORT: Set export_files parameter. Returns: File 'name', URL `sandbox:/v1/files/[base64]`
    Convert to markdown: [name](sandbox:/v1/files/[base64]) - preserve sandbox:/ protocol, no http://.

    Isolated sandbox with timeout protection.
    """.strip(),
    label="Code Executor",
    user_description="""
    Execute Python code to analyze data, generate visualizations, process documents, and perform calculations.

    Key Capabilities:
    - File Upload: Upload files to the sandbox environment for processing
    - Data Analysis: Process and analyze datasets using pandas and numpy
    - Visualizations: Create charts, graphs, and plots with matplotlib
    - Document Processing: Work with Excel, Word, PowerPoint, and PDF files
    - Computations: Perform mathematical calculations and data transformations
    - Image Processing: Manipulate and process images
    - File Export: Export generated files for download

    Use Cases:
    - Upload CSV or Excel files and generate insights
    - Process uploaded images and create visualizations
    - Create visualizations to illustrate trends and patterns
    - Extract information from uploaded documents
    - Perform complex calculations on uploaded data
    - Transform data between different formats and export results

    Workflow:
    1. Files are automatically uploaded to sandbox if provided to the tool
    2. Execute code that processes the uploaded files (available by filename)
    3. Generate new files or visualizations
    4. Export results using export_files parameter

    The code executes securely with all files isolated in a sandbox environment.
    """.strip(),
)
