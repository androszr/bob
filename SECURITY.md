# Security policy

## Supported version

Only the `main` branch is supported. There are no released versions to patch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: open the **Security** tab of this
repository and press **Report a vulnerability**. Do not open a public issue, and do not
post details in a discussion or pull request.

What to expect:

- an acknowledgement within 7 days;
- a fix or a written explanation of why it will not be fixed, as soon as is practical;
- credit in the fix if you want it. There is no bug bounty.

## What the agent opens, sends and stores

Knowing this helps you judge whether something is a vulnerability.

- **It acts as you.** Bob is not a registered Teams bot. It calls the Teams web client's
  internal API with a bearer token taken from your own signed-in browser session, and
  every reply is posted from your account (labelled `[Bob]`). Anyone who gets the saved
  browser session can read and write your chats for as long as Microsoft keeps that
  session alive (Bob asks you to sign in again after about seven days, but the session
  can outlive that).
- **It sends chat text to the model.** The text of chats you configured, their history
  and their knowledge files go to the local `claude` CLI, and from there to the model
  provider under your Claude Code login.
- **It reaches only what you configure:** Microsoft Teams, your Jira, and the SharePoint
  site holding the leave sheet. Two more services see text only when you set their keys:
  Google's Gemini text-to-speech (the Markdown you turn into an MP3) and Giphy (a short
  GIF search phrase). Web search is off by default; per chat you can turn it on for
  yourself only (`only_me`) or for everyone in the chat (`all`). Fetching a page is never
  offered when the reply also loads data tools (Jira, the leave sheet, to-dos).
- **It stores everything locally, inside the clone:** `.env` (your tokens), the browser
  session and Teams tokens (written `0600`), chat history and per-person data under
  `user-data/`. Outside the clone: `~/.bob/` (the tool daemons' bearer token and
  generated config), `~/Library/Logs/bob/` (daemon logs), a few temp files listed in
  the README's Uninstall section, and the MP3s if you set `MD_TO_MP3_DIR` outside the
  clone.
- **The model is boxed in.** It runs with no shell and no file-editing tools, only an
  explicit allow-list of tools per chat, and "owner only" actions are checked in code.
  The tool daemons listen on `127.0.0.1` only and require a bearer token.

In scope: anything that lets someone other than the owner read the tokens, make Bob post
or call a tool they should not, or reach the tool daemons. Out of scope: Microsoft's or
Atlassian's own services, and the fact that using a session token this way may break your
organisation's rules (the README says so up front).
