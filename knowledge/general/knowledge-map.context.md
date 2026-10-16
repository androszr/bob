# Knowledge Map — dostepne MCP i zrodla wiedzy

Kontekst: fikcyjna firma Acme buduje produkt dla serwisu terenowego — aplikacje mobilna
dla technikow serwisowych (PRJ1) i portal back-office dla dyspozytorow i klientow (PRJ2).
Discovery w PRJ3, infrastruktura i DevOps w PRJ4.

## Dostepne serwery MCP

| Serwer | Narzedzia | Zrodlo danych |
|---|---|---|
| `jira-bob` | `jira_issue`, `jira_search`, `jira_comment`, `jira_create`, `jira_attach`, `jira_link`, `jira_transition`, `jira_update_*`, `jira_sprint_search`, `jira_assign_sprint`, `jira_boards`, `jira_search_users` | Jira REST (odczyt + zapisy z tokenem wlasciciela) |
| `jira-read-bob` | `find`, `jira_issue`, `jira_search`, `jira_boards` | Jira REST, bez zapisow |
| `todos-bob` | `add_todo`, `list_todos`, `update_todo`, `list_upcoming_todos`, `delete_todo` | Lokalna lista zadan wlasciciela |
| `urlopy-bob` | `urlopy_meta`, `urlopy_check`, `urlopy_list` | Arkusz urlopow zespolu (SharePoint poll) |
| `datetime-bob` | `now`, `resolve_date`, `weekday`, `date_range` | Zegar i kalendarz (Europe/Warsaw) |
| `md-to-mp3-bob` | `convert_md_to_mp3`, `list_md_to_mp3_files` | Lokalne pliki MP3 wygenerowane z Markdown |

Grupy spoza tej listy w `chat-config.json` sa ignorowane.

## Kiedy uzywac urlopy-bob

Pytania o urlopy, nieobecnosci, dyspozycyjnosc czlonkow zespolu — kieruj do `urlopy-bob`.

Rekomendowana kolejnosc: `urlopy_meta` (raz/sesje) -> `urlopy_check(date)` (konkretny dzien)
lub `urlopy_list(month)` (przeglad miesiaca).

## Kiedy uzywac jira-bob

Tickety, sprinty, wyszukiwanie JQL, zakladanie i komentowanie zgloszen — `jira-bob`
(kanaly bez prawa zapisu dostaja `jira-read-bob`).

## Czego tu nie ma

Baza wiedzy, archiwum spotkan, sklad zespolu i Confluence nie maja serwera MCP w tym repo.
Gdy ktos o nie pyta, powiedz wprost, ze nie masz do nich dostepu — nie zgaduj.
