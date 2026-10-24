[Back to the README](../../README.md)

# README pictures

## The story

Every picture shows one made-up company: **Acme**, the SharePoint tenant `contoso`,
Jira projects `PRJ1` to `PRJ5`, and an English cast - Alex Morgan (the owner, whose
replies carry the `[Bob]` label), Priya Shah and Tom Becker - in four made-up chats
("Notes", "Team A · project", "Product channel", "Release notes"). Anyone beyond those
three is a "Person NN" placeholder, and the 12:00 scheduled note goes to a made-up
"Managers" chat. The panel picture (`bob-panel.png`) shows the owner Alex Morgan signed in, with the panel's tabs under the
English names the README's panel table gives them (the page itself speaks Polish). Every word in a picture is English. Times are `HH:MM`, dates are written as "20 Oct". No real Teams
screenshot is ever used, and no product logos are drawn.

| Picture | Source | Shows |
|---|---|---|
| `hero-bob-in-teams.png` | `src/hero-bob-in-teams.html` | Bob answering in a chat, and saying it is an AI when asked |
| `why-bob.png` | `src/why-bob.html` | six questions waiting vs. answered |
| `a-day-with-bob.png` | `src/a-day-with-bob.html` | six moments of one working day |
| `architecture.png` | `src/architecture.html` | how the pieces connect |
| `bob-panel.png` | `src/bob-panel.html` | the panel: tabs, status and recent conversations |
| `app-icon.svg` | itself (hand-written vector) | the icon at the top of the README |

The pictures share one dark terminal slide style - a 1200 x 1200 frame with a terminal
prompt, a two-tone headline, a muted subtitle, the stage and a mono footer. The palette
lives in `src/tokens.css` only; the shared frame is `src/base.css`.

## Redraw and check

```bash
python3 tools/readme_images.py            # redraw all five (needs Playwright's Chromium, on a Mac)
python3 tools/readme_images.py --only why-bob
python3 tools/readme_images.py --check    # verify the committed pictures; no browser needed
```

Never edit a PNG by hand: change the source and redraw. Render on a Mac (the `[Bob]` chip
uses the Apple colour emoji); a re-render on another machine changes bytes, which is why
tests pin shape, size and chunks, never a digest.

## What guarantees what

| Guarantee | Where |
|---|---|
| no private data, binaries listed as reviewed | `tests/test_no_private_data.py` |
| exact picture set, width, even height, size cap, allowed PNG chunks, one embed each with a description | `tests/test_readme_images.py` |
| links resolve, the honest safety wording is on the front page | `tests/test_readme_images.py` |

## The look a person still takes

1. Open `README.md` in a Markdown preview.
2. Check the top: icon, title, three badges, a one-line pitch, then a chat picture.
3. Say out loud what Bob is, why it exists, what a day looks like and how to start it.
4. Open each of the five pictures at 200%.
5. Read every name, chat title, project key and number; expect only the made-up company and cast.
6. Check nothing is cut off and every reply shows the `[Bob]` label.
7. If anything is off, change the source and redraw.
