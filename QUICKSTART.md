# Solr MCP Quick Start Guide

This guide walks through setting up a Solr MCP (Model Context Protocol) server with Claude Desktop, including all the troubleshooting steps we encountered.

## Prerequisites

- macOS (this guide is macOS-specific)
- Homebrew installed
- Git access to your Solr MCP repository

## Step 1: Clone and Setup Solr MCP Project

```bash
git clone git@github.com:albertyusunepiq/solr-mcp.git
cd solr-mcp

# Start Solr with Docker
brew install docker-compose
docker-compose up -d
```

## Step 2: Install Python Environment Tools

```bash
# Install pipx to manage Python tools
brew install pipx
pipx ensurepath

# Install Poetry for dependency management
pipx install poetry

# Reload shell to pick up new PATH
source ~/.zshrc

```

## Step 3: Setup Python Environment

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Configure Poetry to use this Python version
poetry env use python3

# Install all project dependencies
poetry install

```

## Step 4: Install Missing Dependencies

During our troubleshooting, we found some dependencies were missing from the project configuration:

```bash
# Make sure you're in the venv
source venv/bin/activate

# Install critical missing dependencies
pip install aiohttp pysolr loguru

# Or add them via Poetry (recommended)
poetry add aiohttp

```

## Step 5: Setup Solr Data

```bash
# Create directories and process sample data
mkdir -p data/processed
python scripts/process_markdown.py data/bitcoin-whitepaper.md --output data/processed/bitcoin_sections.json
python scripts/create_unified_collection.py unified
python scripts/standalone_vector_index.py data/processed/bitcoin_sections.json --collection unified
```

## Step 6: Install Node.js (for Filesystem MCP Server)

```bash
# Install Node.js for the filesystem MCP server
brew install node

# Verify installation
node --version
npm --version

```

## Step 7: Test Solr MCP Server

**Critical Discovery**: The Solr MCP server has two modes - web server mode and stdio mode. For Claude Desktop integration, you MUST use stdio mode.

```bash
cd /Users/YOUR_USERNAME/projects/solr-mcp
source venv/bin/activate

# Test in stdio mode (required for MCP)
poetry run solr-mcp --transport stdio

# You should see:
# - Zookeeper connection established
# - Ollama vector provider initialized
# - Server starting in MCP mode (not web server mode)

```

## Step 8: Configure Claude Desktop

### Find Your Poetry Virtual Environment Path

```bash
# Find the exact path to your Poetry virtual environment
poetry env info --path
# Example output: /Users/Albert.Sun/Library/Caches/pypoetry/virtualenvs/solr-mcp-opjUMSi1-py3.13

```

### Create/Update MCP Configuration

Add the following MCP Server `~/Library/Application Support/Claude/claude_desktop_config.json`, 

<aside>
💡

For the following script, make sure you:

- change “command” to the location of your python interpreter
- change “cwd” to the location of your solr-mcp folder and
- change all instances of `YOUR_USERNAME` to your laptop username.
</aside>

```json
{
  "mcpServers": {
    "solr-search": {
      "command": "/Users/YOUR_USERNAME/Library/Caches/pypoetry/virtualenvs/solr-mcp-XXXXX-py3.13/bin/python",
      "args": ["-m", "solr_mcp.server", "--transport", "stdio"],
      "cwd": "/Users/YOUR_USERNAME/projects/solr-mcp"
    }
  }
}

```

**Key Points:**

- Replace `YOUR_USERNAME` with your actual username
- Replace the virtual environment path with your actual Poetry env path
- The `-transport stdio` flag is CRITICAL - without it, the server runs in web mode and won't work with Claude Desktop

## Step 9: Restart Claude Desktop

1. Completely quit Claude Desktop (Cmd+Q or Force Quit)
2. Restart Claude Desktop
3. Check for the hammer icon (🔨) indicating MCP tools are available

## Troubleshooting Common Issues

### Issue 1: `spawn npx ENOENT`

**Solution**: Install Node.js via `brew install node`

### Issue 2: `spawn poetry ENOENT`

**Solution**: Use the full path to Poetry executable instead of just `poetry`

### Issue 3: Missing Python dependencies

**Solution**: Run `poetry install` and manually install missing packages like `aiohttp`

### Issue 4: Server runs but doesn't connect to Claude Desktop

**Solution**: Add `--transport stdio` to force stdio mode instead of web server mode

### Issue 5: Only filesystem server shows in logs, no solr-search

**Solution**: Usually means the command path is wrong or missing the `--transport stdio` flag

## Verification

After successful setup, you should see both servers in Claude Desktop logs:

```
[filesystem] [info] Initializing server...
[filesystem] [info] Server started and connected successfully
[solr-search] [info] Initializing server...
[solr-search] [info] Server started and connected successfully

```

And you should see MCP tools available in the Claude Desktop interface (hammer icon).

## Key Lessons Learned

1. **Poetry vs Local venv**: Poetry creates its own virtual environments in a cache directory, separate from local `venv` folders
2. **Transport modes matter**: MCP servers can run in different modes - always use `stdio` for Claude Desktop
3. **Dependencies**: Some Python packages may be missing from `pyproject.toml` but required by the code
4. **Full paths required**: Claude Desktop needs absolute paths to executables, not just command names
5. **Node.js dependency**: Even if you only want Python MCP servers, Node.js is needed for other MCP servers like filesystem

## Dependencies Summary

**System Level:**

- Homebrew
- Docker & Docker Compose
- Node.js (for filesystem MCP server)
- Python 3.10+

**Python Environment:**

- Poetry (for dependency management)
- Virtual environment with all project dependencies
- Additional packages: aiohttp, pysolr, loguru (if missing)

**External Services:**

- Zookeeper (via Docker)
- Solr (via Docker)
- Ollama (for vector embeddings)

This guide should help anyone set up Solr MCP with Claude Desktop while avoiding the common pitfalls we encountered!