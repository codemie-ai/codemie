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

FILE_ANALYSIS_TOOL = ToolMetadata(
    name="file_analysis",
    description="""
    Use this tool to read the content of files and convert it into markdown format.
    It supports various file types such as plain text files, HTML, zip archives, etc.
    This tool ensures that content is structured and easy to read with markdown syntax.
    Do not use this tool for PDFs, PowerPoint presentations (PPTX), Excel files (XLS/XLSX), Word documents (DOCX) or CSV files,
    as separate tools handle those formats. Call this tool when tasks involve reading and analyzing file content or
    extracting information in a structured, markdown-friendly format. The output will include elements like headers,
    lists, tables, and more, converted into an easy-to-read markdown style. Useful for tasks like summarization,
    knowledge extraction, or reasoning based on file input.
    """,
    label="File Analysis",
)

PPTX_TOOL = ToolMetadata(
    name="pptx_tool",
    description="""
    Use this tool to extract content from PowerPoint presentation files (PPTX). The tool parses slide data, including
    text, titles, bullet points, and other slide content. Ideal for tasks requiring analysis, summarization, or
    question-answering based on the content of PPTX slides. This tool enables processing of multi-slide presentations
    with structured output for each slide, making the content easier to interpret and utilize in further
    reasoning tasks
    """,
    label="PPTX Processing Tool",
)

PDF_TOOL = ToolMetadata(
    name="pdf_tool",
    description="""
    A specialized tool for extracting content from PDF documents. This tool supports text extraction from both the
    main document and embedded elements such as tables and images. Additionally, it leverages LLM-based image
    recognition to extract text from embedded images within PDFs. This is useful for processing scanned documents,
    extracting knowledge from PDF reports, or summarizing document content. Use this tool when dealing with any PDF
    file requiring text-based or image-based information retrieval
    """,
    label="PDF Processing Tool",
)

CSV_TOOL = ToolMetadata(
    name="csv_tool",
    description="""
    Tool for interpreting and working with tabular data inside CSV files. This tool allows for
    structured data handling, such as column-based querying, applying filters, aggregations, and analysis of numeric or
    textual data. Use this tool when tasks involve extracting insights or calculations from CSV datasets, or when
    reasoning over structured tabular data is required.
    """,
    label="CSV Interpretation",
)

EXCEL_TOOL = ToolMetadata(
    name="excel_tool",
    description="""
    Use this tool to extract and analyze content from Excel files (XLS, XLSX). The tool processes spreadsheet data,
    converting it into readable markdown tables. It can handle multiple sheets, filter out empty rows and columns,
    and filter rows based on multiple cell values with AND logic. Works with pivot tables and complex Excel layouts.
    Ideal for tasks requiring analysis, data extraction, or question-answering based on tabular data in Excel format.

    Key capabilities:
    - Process all sheets or specify sheets by name using the `sheet_names` parameter
    - Get a list of all sheet names in an Excel file with `get_sheet_names=True`
    - Access a specific sheet by its index (0-based) using `sheet_index` parameter
    - Get comprehensive statistics about the Excel file with `get_stats=True` (includes sheet counts, column names, data types, sample values)
    - Control sheet visibility with `visible_only=True/False` parameter to include or exclude hidden sheets
    - **Filter rows by multiple values**: Use `filter_values` (list) to include only rows containing ALL specified values (AND logic)
    - **Filter modes**: Control matching with `filter_mode`: 'exact' (default) for exact matches, 'contains' for substring matches
    - Works with complex layouts: Filtering searches across all cells in a row, not just specific columns
    - Each filter value can appear in any cell of the row (OR logic within each value)
    - Automatically normalize column names for better readability
    - Process multiple Excel files with clear separation between documents
    - Intelligent data cleaning to remove empty rows and columns

    Examples:
    - Single exact match: `filter_values=["NATI-5DME"]` to get rows containing "NATI-5DME"
    - Multiple values (AND): `filter_values=["Critical", "Open", "Bug"]` returns only rows containing all three values
    - Substring match: `filter_values=["NATI"]` with `filter_mode="contains"` to get rows containing "NATI" anywhere
    - Complex filtering: `filter_values=["Sprint 42", "In Progress", "John"]` finds rows with all three criteria
    - Status + Type filtering: `filter_values=["Complete", "Feature"]` for rows matching both criteria
    """,
    label="Excel Processing Tool",
)

DOCX_TOOL = ToolMetadata(
    name="docx_tool",
    description="""
    Use this tool to extract and analyze content from Microsoft Word documents (DOCX files). Select the appropriate query type based on what information you need from the document.

    When to use this tool:
    - When you need to read or analyze content from Word documents (.docx files)
    - When you need to extract text, images, or tables from Word documents
    - When you need to summarize or perform analysis on document content
    - When you need information about document structure, like headers and sections

    Required parameter - query (choose one):
    - "text" - Extract plain text content (use for basic reading of document text)
    - "text_with_metadata" - Get text plus document properties like author, title, creation date
    - "text_with_images" - Get text including OCR from embedded images
    - "structure_only" - Get document hierarchy (headers, sections, styles)
    - "image_extraction" - Extract embedded images from the document
    - "table_extraction" - Extract tables from the document
    - "summary" - Generate a concise summary of the document
    - "analyze" - Perform comprehensive document analysis

    Optional parameters:
    - "instructions" - Add specific instructions for how to analyze the document (use with "analyze" or "summary" queries)
    - "pages" - Specify which pages to process: single page ("1"), multiple pages ("1,3,5"), or range ("1-4")

    Examples:
    - To read a document: Use query="text"
    - To summarize a document: Use query="summary"
    - To analyze specific aspects: Use query="analyze" with instructions="Identify key arguments in this legal document"
    - To extract tables: Use query="table_extraction"
    - To process only the first 3 pages: Add pages="1-3" to any query
    """,
    label="DOCX Processing Tool",
)
