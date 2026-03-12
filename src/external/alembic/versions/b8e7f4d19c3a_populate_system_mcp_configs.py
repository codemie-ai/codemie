"""Populate system MCP configurations

Revision ID: b8e7f4d19c3a
Revises: 9ad50751a56a
Create Date: 2025-10-20 12:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
from sqlalchemy import text
from datetime import datetime
import json
from uuid import uuid4

# revision identifiers, used by Alembic.
revision: str = 'b8e7f4d19c3a'
down_revision: Union[str, None] = '9ad50751a56a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# System user ID for system-provided configurations
SYSTEM_USER_ID = "system"


# Predefined MCP Server Configurations
MCP_SERVERS = [
    {
        "name": "Fetch MCP Server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch#fetch-mcp-server",
        "description": "A Model Context Protocol server that provides web content fetching capabilities. This server enables LLMs to retrieve and process content from web pages, converting HTML to markdown for easier consumption.",
        "categories": ["API"],
        "config": {"command": "uvx", "args": ["mcp-server-fetch"]},
        "required_env_vars": [],
    },
    {
        "name": "Filesystem MCP Server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem#filesystem-mcp-server",
        "description": "Node.js server implementing Model Context Protocol (MCP) for filesystem operations.",
        "categories": ["Filesystem"],
        "config": {
            "command": "mcp-server-filesystem",
            "args": ["$WORKING_FOLDER"],
            "headers": {},
            "env": {"WORKING_FOLDER": "/home/codemie"},
            "type": None,
            "auth_token": "SecretAccessToken",
            "single_usage": False,
        },
        "required_env_vars": [
            {
                "name": "WORKING_FOLDER",
                "description": "Allowed directories. Example: /home/codemie/dir1 /home/codemie/dir2. Must start with `/home/codemie`",
            }
        ],
    },
    {
        "name": "Git MCP server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/git#mcp-server-git-a-git-mcp-server",
        "description": "A Model Context Protocol server for Git repository interaction and automation. This server provides tools to read, search, and manipulate Git repositories via Large Language Models.",
        "categories": ["Git"],
        "config": {"command": "uvx", "args": ["mcp-server-git"], "single_usage": False},
        "required_env_vars": [],
    },
    {
        "name": "Knowledge Graph Memory Server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory#knowledge-graph-memory-server",
        "description": "A basic implementation of persistent memory using a local knowledge graph.",
        "categories": ["Memory"],
        "config": {"command": "mcp-server-memory"},
        "required_env_vars": [],
    },
    {
        "name": "Sequential Thinking MCP Server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking#sequential-thinking-mcp-server",
        "description": "An MCP server implementation that provides a tool for dynamic and reflective problem-solving through a structured thinking process.",
        "categories": ["AI"],
        "config": {"command": "mcp-server-sequential-thinking"},
        "required_env_vars": [],
    },
    {
        "name": "PostgreSQL",
        "server_home_url": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/postgres#postgresql",
        "description": "A Model Context Protocol server that provides read-only access to PostgreSQL databases. This server enables LLMs to inspect database schemas and execute read-only queries.",
        "categories": ["Database"],
        "config": {
            "command": "mcp-server-postgres",
            "args": ["postgresql://$USERNAME:$PASSWORD@$HOST:$PORT/$DBNAME"],
            "env": {},
            "single_usage": False,
        },
        "required_env_vars": [
            {"name": "HOST", "description": "Hostname or IP address of the PostgreSQL server."},
            {"name": "PORT", "description": "Port number on which the PostgreSQL server is listening."},
            {"name": "DBNAME", "description": "Database name to connect to."},
            {"name": "USERNAME", "description": "A username used to connect to the database."},
            {"name": "PASSWORD", "description": "A password used to connect to the database."},
        ],
    },
    {
        "name": "SQLite MCP Server",
        "server_home_url": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite#sqlite-mcp-server",
        "description": "A Model Context Protocol (MCP) server implementation that provides database interaction and business intelligence capabilities through SQLite. This server enables running SQL queries, analyzing business data, and automatically generating business insight memos.",
        "categories": ["Database"],
        "config": {
            "command": "uvx",
            "args": ["mcp-server-sqlite", "--db-path", "/home/codemie/SQLite/$DB_NAME.db"],
            "env": {"DB_NAME": "testSQLite"},
            "single_usage": False,
        },
        "required_env_vars": [
            {
                "name": "DB_NAME",
                "description": "The name of the SQLite database file (without the .db extension) that will be used to work with.",
            }
        ],
    },
    {
        "name": "Puppeteer",
        "server_home_url": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/puppeteer#puppeteer",
        "description": "A Model Context Protocol server that provides browser automation capabilities using Puppeteer. This server enables LLMs to interact with web pages, take screenshots, and execute JavaScript in a real browser environment.",
        "categories": ["API", "Automation"],
        "config": {
            "command": "mcp-server-puppeteer",
            "env": {
                "DEBIAN_FRONTEND": "noninteractive",
                "DOCKER_CONTAINER": True,
                "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
                "PUPPETEER_SKIP_CHROMIUM_DOWNLOAD": True,
            },
            "single_usage": False,
        },
        "required_env_vars": [],
    },
    {
        "name": "Slack MCP Server",
        "server_home_url": "https://github.com/zencoderai/slack-mcp-server?tab=readme-ov-file#slack-mcp-server",
        "description": "A Model Context Protocol (MCP) server for interacting with Slack workspaces. This server provides tools to list channels, post messages, reply to threads, add reactions, get channel history, and manage users.",
        "categories": ["Automation"],
        "config": {"command": "npx", "args": ["-y", "@zencoderai/slack-mcp-server"], "env": {}, "single_usage": False},
        "required_env_vars": [
            {
                "name": "SLACK_BOT_TOKEN",
                "description": "The token for your Slack bot, which can be obtained from the Slack API website.",
            },
            {
                "name": "SLACK_TEAM_ID",
                "description": "Your Slack workspace's team ID, which can be found in the URL of your Slack workspace.",
            },
            {
                "name": "SLACK_CHANNEL_IDS",
                "description": "Optional: predefined channels the bot can access, separated by comma. Example: C12345678,C23456789,C34567890",
            },
        ],
    },
    {
        "name": "Firecrawl MCP Server",
        "server_home_url": "https://docs.firecrawl.dev/mcp-server",
        "description": "A Model Context Protocol (MCP) server implementation that integrates Firecrawl for web scraping capabilities. The MCP server is open-source and available on GitHub.",
        "categories": ["API"],
        "config": {"command": "npx", "args": ["-y", "firecrawl-mcp"], "env": {}, "single_usage": False},
        "required_env_vars": [
            {
                "name": "FIRECRAWL_API_KEY",
                "description": "Your Firecrawl API key, which can be obtained from the Firecrawl website after signing up for an account.",
            }
        ],
    },
    {
        "name": "GitHub MCP Server",
        "server_home_url": "https://github.com/github/github-mcp-server",
        "description": "The GitHub MCP Server connects AI tools directly to GitHub's platform. This gives AI agents, assistants, and chatbots the ability to read repositories and code files, manage issues and PRs, analyze code, and automate workflows. All through natural language interactions.",
        "categories": ["Git", "API"],
        "config": {
            "command": "/codemie/additional-tools/github-mcp-server/github-mcp-server",
            "args": ["stdio"],
            "env": {},
            "single_usage": False,
        },
        "required_env_vars": [
            {
                "name": "GITHUB_PERSONAL_ACCESS_TOKEN",
                "description": "Your GitHub personal access token with appropriate scopes to allow the MCP server to access repositories and perform actions on your behalf.",
            }
        ],
    },
    {
        "name": "Microsoft Learn MCP Server",
        "server_home_url": "https://github.com/microsoftdocs/mcp",
        "description": "The Microsoft Learn MCP Server is a remote MCP Server that enables clients like GitHub Copilot and other AI agents to bring trusted and up-to-date information directly from Microsoft's official documentation. It supports streamable http transport, which is lightweight for clients to use.",
        "categories": ["API", "Cloud"],
        "config": {"url": "https://learn.microsoft.com/api/mcp", "single_usage": False},
        "required_env_vars": [],
    },
    {
        "name": "Azure MCP Server",
        "server_home_url": "https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server",
        "description": "All Azure MCP tools in a single server. The Azure MCP Server implements the MCP specification to create a seamless connection between AI agents and Azure services. Azure MCP Server can be used alone or with the GitHub Copilot for Azure extension in VS Code. This project is in Public Preview and implementation may significantly change prior to our General Availability. Useful for dedicated MCP-Connect containers.",
        "categories": ["Cloud"],
        "config": {
            "command": "npx",
            "args": ["-y", "@azure/mcp@latest", "server", "start"],
            "env": {},
            "single_usage": False,
        },
        "required_env_vars": [],
    },
    {
        "name": "MarkItDown-MCP",
        "server_home_url": "https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp",
        "description": "The markitdown-mcp package provides a lightweight STDIO, Streamable HTTP, and SSE MCP server for calling MarkItDown. It exposes one tool: convert_to_markdown(uri), where uri can be any http:, https:, file:, or data: URI.",
        "categories": ["Development"],
        "config": {"command": "uvx", "args": ["markitdown-mcp"], "single_usage": False},
        "required_env_vars": [],
    },
    {
        "name": "Playwright MCP",
        "server_home_url": "https://github.com/microsoft/playwright-mcp?tab=readme-ov-file#playwright-mcp",
        "description": "A Model Context Protocol (MCP) server that provides browser automation capabilities using Playwright. This server enables LLMs to interact with web pages through structured accessibility snapshots, bypassing the need for screenshots or visually-tuned models.",
        "categories": ["API", "Automation"],
        "config": {
            "command": "npx",
            "args": ["@playwright/mcp@latest", "--isolated", "--headless", "--no-sandbox"],
            "env": {},
            "single_usage": False,
        },
        "required_env_vars": [],
    },
    {
        "name": "MCP Mermaid",
        "server_home_url": "https://github.com/hustcc/mcp-mermaid",
        "description": "Generate mermaid diagram and chart with AI MCP dynamically.",
        "categories": ["Development"],
        "config": {"command": "mcp-mermaid", "args": [], "env": {}, "single_usage": False},
        "required_env_vars": [],
    },
    {
        "name": "MCP Server Chart",
        "server_home_url": "https://github.com/antvis/mcp-server-chart",
        "description": "A Model Context Protocol server for generating charts using AntV. You can use this mcp server for chart generation and data analysis.",
        "categories": ["Development"],
        "config": {"command": "npx", "args": ["-y", "@antv/mcp-server-chart"], "env": {}, "single_usage": False},
        "required_env_vars": [],
    },
    {
        "name": "Framelink Figma MCP Server",
        "server_home_url": "https://github.com/GLips/Figma-Context-MCP",
        "description": "Give your coding agent access to your Figma data. Implement designs in any framework in one-shot.",
        "categories": ["Development"],
        "config": {
            "command": "npx",
            "args": ["-y", "figma-developer-mcp", "--figma-api-key=$FIGMA_API_KEY", "--stdio"],
            "env": {},
            "single_usage": False,
        },
        "required_env_vars": [
            {
                "name": "FIGMA_API_KEY",
                "description": "The token for your Figma developer, which can be obtained from the Figma website.",
            }
        ],
    },
    {
        "name": "Elasticsearch MCP Server",
        "server_home_url": "https://github.com/cr7258/elasticsearch-mcp-server",
        "description": "A Model Context Protocol (MCP) server implementation that provides Elasticsearch interaction. This server enables searching documents, analyzing indices, and managing cluster through a set of tools.",
        "categories": ["Search", "Database"],
        "config": {"command": "uvx", "args": ["elasticsearch-mcp-server"], "env": {}, "single_usage": False},
        "required_env_vars": [
            {
                "name": "ELASTICSEARCH_HOSTS",
                "description": "Comma-separated list of hosts (default: https://localhost:9200)",
            },
            {
                "name": "ELASTICSEARCH_API_KEY",
                "description": "API key for Elasticsearch or Elastic Cloud Authentication. You should provide either ELASTICSEARCH_API_KEY or ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD",
            },
            {
                "name": "ELASTICSEARCH_USERNAME",
                "description": "Username for basic authentication. You should provide either ELASTICSEARCH_API_KEY or ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD",
            },
            {
                "name": "ELASTICSEARCH_PASSWORD",
                "description": "Password for basic authentication. You should provide either ELASTICSEARCH_API_KEY or ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD",
            },
        ],
    },
    {
        "name": "Elasticsearch/OpenSearch MCP Server",
        "server_home_url": "https://github.com/cr7258/elasticsearch-mcp-server",
        "description": "A Model Context Protocol (MCP) server implementation that provides OpenSearch interaction. This server enables searching documents, analyzing indices, and managing cluster through a set of tools.",
        "categories": ["Search", "Database"],
        "config": {"command": "uvx", "args": ["opensearch-mcp-server"], "env": {}, "single_usage": False},
        "required_env_vars": [
            {
                "name": "OPENSEARCH_HOSTS",
                "description": "Comma-separated list of hosts (default: https://localhost:9200).",
            },
            {"name": "OPENSEARCH_USERNAME", "description": "Username for OpenSearch basic authentication."},
            {"name": "OPENSEARCH_PASSWORD", "description": "Password for OpenSearch basic authentication"},
        ],
    },
]


def upgrade() -> None:
    """Upgrade schema - populate system MCP configurations."""
    connection = op.get_bind()

    print(f"Loading {len(MCP_SERVERS)} system MCP configurations...")

    # Prepare insert statement
    insert_stmt = text("""
        INSERT INTO mcp_configs (
            id, date, update_date, name, description, server_home_url,
            source_url, logo_url, categories, config, required_env_vars,
            user_id, is_public, is_system, created_by, usage_count, is_active
        ) VALUES (
            :id, :date, :update_date, :name, :description, :server_home_url,
            :source_url, :logo_url, :categories, :config, :required_env_vars,
            :user_id, :is_public, :is_system, :created_by, :usage_count, :is_active
        )
    """)

    now = datetime.utcnow()
    inserted_count = 0
    skipped_count = 0

    for server in MCP_SERVERS:
        try:
            # Generate unique ID
            server_id = str(uuid4())

            # Extract fields
            name = server.get("name", "").strip()
            description = server.get("description", "").strip()
            server_home_url = server.get("server_home_url", "").strip()
            categories = server.get("categories", [])
            config = server.get("config", {})
            required_env_vars = server.get("required_env_vars", [])

            # Prepare created_by
            created_by = {"id": SYSTEM_USER_ID, "name": "System", "username": "system"}

            # Insert record
            connection.execute(
                insert_stmt,
                {
                    "id": server_id,
                    "date": now,
                    "update_date": now,
                    "name": name,
                    "description": description or None,
                    "server_home_url": server_home_url or None,
                    "source_url": None,
                    "logo_url": None,
                    "categories": json.dumps(categories),
                    "config": json.dumps(config),
                    "required_env_vars": json.dumps(required_env_vars),
                    "user_id": SYSTEM_USER_ID,
                    "is_public": True,
                    "is_system": True,
                    "created_by": json.dumps(created_by),
                    "usage_count": 0,
                    "is_active": True,
                },
            )

            inserted_count += 1
            print(f"✓ Inserted: {name}")

        except Exception as e:
            skipped_count += 1
            print(f"✗ Error inserting '{server.get('name', 'unknown')}': {e}")
            continue

    print(f"\nMigration complete:")
    print(f"  - Inserted: {inserted_count} configurations")
    print(f"  - Skipped: {skipped_count} configurations")


def downgrade() -> None:
    """Downgrade schema - remove system MCP configurations."""
    connection = op.get_bind()

    # Delete all system MCP configurations
    delete_stmt = text("""
        DELETE FROM mcp_configs
        WHERE is_system = true AND user_id = :user_id
    """)

    result = connection.execute(delete_stmt, {"user_id": SYSTEM_USER_ID})
    print(f"Removed {result.rowcount} system MCP configurations")
