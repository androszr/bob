"""jira-read-bob -- read-only Jira MCP server (find, jira_issue, jira_search, jira_boards).

Same code as scripts/jira/mcp_server.py, started with JIRA_MCP_TOOLSET=read so only the
read tools are registered. Runs as the HTTP daemon started by cli watch
(ai._DAEMON_SERVERS, scripts/mcp_admin/daemon.py); write tools stay in jira-bob (stdio).
"""

import os
import runpy
from pathlib import Path

os.environ["JIRA_MCP_TOOLSET"] = "read"
runpy.run_path(str(Path(__file__).resolve().parent / "mcp_server.py"), run_name="__main__")
