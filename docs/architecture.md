[Back to the README](../README.md)

# Architecture

## Stack

- Python 3.11+
- Playwright (Chromium, headed - Teams rejects headless even with valid cookies) - SSO,
  token refresh, file upload
- Teams `chatsvc` REST - reading and sending messages
- `claude` CLI on `PATH` (Claude Code), default model `claude-sonnet-5-5`
- MCP (`mcp>=1.0`) - tools for the model

## C4 architecture

The full architecture (L1 + L2 + L3): [`PRD.md` - section 6](../PRD.md#6-architektura-c4).

### L1 - System Context

```mermaid
flowchart LR
    subgraph Users["👥 Users"]
        direction TB
        R["Owner\nruns and configures it"]
        T["Team\nchat members"]
    end

    subgraph BOXbox["🤖 teams-agent\nowner's computer"]
        B["Self-hosted AI agent\nreads and answers in Teams\nfrom the owner's account"]
    end

    subgraph ExtSys["🌐 External systems"]
        direction TB
        MS["Microsoft Teams"]
        CLAI["Claude CLI"]
        DAT["Team data\nJira · SharePoint"]
        NET["Internet\noptional, per chat"]
    end

    T -- "writes a message" --> MS
    R -- "runs\nand configures" --> B
    MS -- "new messages" --> B
    B -- "generates a reply" --> CLAI
    B -- "fetches data via MCP" --> DAT
    B -- "sends the reply" --> MS
    B -. "looks things up" .-> NET
```

### L2 - Container

```mermaid
flowchart TB
    subgraph Users2["👥 Users"]
        direction LR
        T2["Team"]
        R2["Owner"]
    end

    MS2["📱 Microsoft Teams"]

    subgraph BOXint["🤖 teams-agent — owner's computer"]
        CLIW["Orchestrator\ncli.py — watch · interactive\nrate-limit · commands"]
        AIM["AI engine\nai.py — builds the prompt\npersona · history · MCP"]
        CLCLI["Claude CLI"]
        MCPL["MCP Servers\njira · todos · leave (urlopy)\ndatetime · md-to-mp3 · …"]
        KB5["Knowledge base\nMarkdown per chat"]
        HIST5["Chat history\nJSONL per chat"]
        PW5["Playwright Browser\nSSO · keepalive · file uploads"]
    end

    subgraph DataS["📊 External data sources"]
        direction LR
        JT5["Jira"]
        SP5["SharePoint\nleave sheet"]
    end

    T2 -- "writes a message" --> MS2
    R2 -- "starts" --> CLIW
    MS2 -- "new messages" --> CLIW
    PW5 -- "token keepalive" --> MS2
    CLIW -- "generate a reply" --> AIM
    AIM -- "calls the model" --> CLCLI
    AIM -- "knowledge context" --> KB5
    AIM -- "chat history" --> HIST5
    CLCLI -- "MCP tools" --> MCPL
    CLIW -- "send the reply" --> MS2
    MCPL --> JT5
    MCPL -. "knowledge/urlopy/urlopy.json\n(sharepoint_poller)" .-> SP5
```

## Layout

```
teams-agent/
├── scripts/
│   ├── org_config.py      — organisation/owner settings from .env
│   ├── teams_agent/       — the Teams agent
│   │   ├── login.py       — one-time SSO + saves session.json
│   │   ├── session.py     — session.json freshness (TTL ~7 days)
│   │   ├── api_client.py  — Teams chatsvc REST: get/send/reply, persona labels
│   │   ├── scraper.py     — Playwright DOM (navigation + keepalive)
│   │   ├── ai.py          — prompt, personas, MCP groups, claude CLI call
│   │   ├── cli.py         — orchestrator: watch / interactive / sniff
│   │   ├── prompts/       — Bob's prompt, tool modules, template persona
│   │   └── eval/          — regression suite for replies (run_eval.py)
│   ├── jira/              — Jira MCP servers (read and write) + REST client
│   ├── mcp_admin/         — HTTP MCP daemons (launchd), ports, healthcheck
│   ├── broadcast/         — schedule of reports and standups to channels
│   └── …                  — todos, urlopy, datetime_bob, md_to_mp3, …
├── knowledge/             — knowledge base (Markdown) injected per chat
├── templates/             — issue and meeting-note templates
├── tests/                 — pytest (mock Playwright, REST and the claude CLI)
├── docs/                  — these pages and the README pictures
├── tools/                 — readme_images.py: redraws and checks the pictures
├── .env.example · chat-config.example.json · config/jira-fields.example.json
└── pyproject.toml         — ruff + pytest
```

The agent module in detail (module table): [`scripts/teams_agent/README.md`](../scripts/teams_agent/README.md).
