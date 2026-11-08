# Knowledge Map — available MCP servers and knowledge sources

Context: the fictional company Acme builds a product for field service — a mobile app
for service technicians (PRJ1) and a back-office portal for dispatchers and customers (PRJ2).
Discovery lives in PRJ3, infrastructure and DevOps in PRJ4.

## Available MCP servers

| Server | Tools | Data source |
|---|---|---|
| `jira-bob` | `jira_issue`, `jira_search`, `jira_comment`, `jira_create`, `jira_attach`, `jira_link`, `jira_transition`, `jira_update_*`, `jira_sprint_search`, `jira_assign_sprint`, `jira_boards`, `jira_search_users` | Jira REST (reads + writes with the owner's token) |
| `jira-read-bob` | `find`, `jira_issue`, `jira_search`, `jira_boards` | Jira REST, no writes |
| `todos-bob` | `add_todo`, `list_todos`, `update_todo`, `list_upcoming_todos`, `delete_todo` | The owner's local to-do list |
| `leave-bob` | `leave_meta`, `leave_check`, `leave_list` | The team leave sheet (SharePoint poll) |
| `datetime-bob` | `now`, `resolve_date`, `weekday`, `date_range` | Clock and calendar (time zone from `BOB_TIMEZONE`, default Europe/Warsaw) |
| `md-to-mp3-bob` | `convert_md_to_mp3`, `list_md_to_mp3_files` | Local MP3 files generated from Markdown |

Groups not on this list are ignored in `chat-config.json`.

## When to use leave-bob

Questions about leave, absences, team members' availability — send them to `leave-bob`.

Recommended order: `leave_meta` (once per session) -> `leave_check(date)` (a specific day)
or `leave_list(month)` (an overview of the month).

## When to use jira-bob

Tickets, sprints, JQL search, creating and commenting on issues — `jira-bob`
(chats without write access get `jira-read-bob`).

## What is not here

The knowledge base, the meeting archive, the team roster and Confluence have no MCP server in this repo.
When someone asks about them, say plainly that you have no access to them — do not guess.
