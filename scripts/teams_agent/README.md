# Bob — AI assistant dla Jana na Teams

Bob to agent AI działający w tle i odpowiadający na wiadomości Teams w imieniu Jana Kowalskiego. Czyta i wysyła wiadomości przez Teams REST API (nie przez DOM), generuje odpowiedzi przez Claude CLI.

**Persona:** Bob, wymyślona maskotka-asystent zespołu. Zna się na product discovery, Scrum, OKR-ach — nie na programowaniu.

## Pierwsze uruchomienie

```
python scripts/teams_agent/login.py
```

Otwiera Chromium z widocznym oknem. Zaloguj się do Teams przez Microsoft SSO (MFA jeśli wymagane).
**Zaznacz "Pozostać zalogowanym" = Tak** — bez tego Microsoft nie zapisuje trwałego cookie.
Poczekaj aż zobaczysz Teams (lista czatów po lewej), wróć do terminala i naciśnij Enter.

Sesja zapisuje się do `session.json` (tryb 600, gitignored). Ważna ~7 dni.

## Normalne uruchomienie

```
python scripts/teams_agent/cli.py
```

Bob startuje w trybie daemon na kanale **Kanal AI**:
- Czyta nowe wiadomości przez REST API (nie scraping DOM)
- Odpowiada na każdą wiadomość jako Bob
- Kursor oparty na `sequence_id` — restarty nie powodują powtórzeń
- Własne wiadomości są ignorowane (echo prevention przez `sender_mri`)

Ctrl-C żeby zatrzymać.

## Bezpośrednie pytanie do Boba

Jan może pisać do Boba bezpośrednio z Teams:

```
Bob, co sądzisz o tym roadmapie?
Bob: kiedy mamy retrospektywę?
```

Bob odpowie tylko na wiadomości zaadresowane do niego — pozostałe własne wiadomości są ignorowane.

## Opcje

```
--model MODEL        Model Claude (domyślnie: claude-sonnet-5-5)
--force-login        Wymuś ponowne logowanie SSO
--log-file PATH      Ścieżka do logu JSONL (domyślnie: log.jsonl)
--headless           Chromium bez okna. Niezalecane — Teams odrzuca headless sesje.
--watch NAME         Nazwa kanału do obserwowania (domyślnie: "Kanal AI")
--poll N             Interwał pollingu w sekundach (domyślnie: 5, min 1)
--rate-limit N       Minimalny czas między odpowiedziami w sekundach (domyślnie: 3)
--watch-state PATH   Plik stanu (domyślnie: watch-state.json)
--interactive        Tryb ręczny z menu zatwierdzania zamiast daemona
--sniff-api          Loguj HTTP requests Teams do api-sniff.jsonl (diagnostyka)
--sniff-ws           Tryb pasywny: loguj WebSocket frames do ws-sniff.jsonl, zero wysyłania
```

## Tryb pasywny (WebSocket sniff)

```
python scripts/teams_agent/cli.py --sniff-ws
```

Bob tylko nasłuchuje — nie odpowiada na nic. Wszystkie WebSocket frames Teams trafiają do `ws-sniff.jsonl`. Przydatne do odkrycia formatu eventów real-time przed implementacją trybu push (bez pollingu).

## Tryb interaktywny

```
python scripts/teams_agent/cli.py --interactive
```

Klasyczny tryb z menu zatwierdzania: `[s]end / [e]dit / [r]egen / [n]ext / [q]uit`

## Architektura

| Komponent | Rola |
|-----------|------|
| `login.py` | Jednorazowe logowanie SSO przez Playwright, zapisuje `session.json` |
| `api_client.py` | REST API Teams — czytanie i wysyłanie wiadomości przez cookies z `session.json` |
| `ai.py` | Wywołuje Claude CLI (`--tools` + `--allowedTools` + `--permission-mode dontAsk`) — zero Bash, `Read` tylko dla obrazów (katalog per wywołanie) |
| `scraper.py` | Playwright DOM — używany tylko do nawigacji (keepalive sesji) |
| `cli.py` | Orkiestrator: watch loop, sniffery, tryb interaktywny |
| `session.py` | Zarządzanie świeżością `session.json` |
| `roles.py` | Role czatów (`roles.json`): dostęp do grup narzędzi, umiejętności, wiedza doklejana do promptu; czysty moduł, ten sam dla Boba i panelu |
| `token_health.py` | Maszyna stanów odnawiania tokenu Teams: kiedy otworzyć przeglądarkę od nowa, kiedy wysłać alert na pulpit |
| `bob_files.py` | Pamięć plików wysłanych przez Boba (`user-data/bob-files/`): gdy ktoś wskaże plik, Bob dostaje jego treść |

## Pliki (gitignored)

- `session.json` — cookies Playwright (nie commitować)
- `log.jsonl` — audit log wysłanych wiadomości
- `watch-state.json` — ostatni `sequence_id` per kanał (pozwala wznowić po restarcie)
- `timers-state.json` — kiedy ostatnio ruszyły wolne zadania cykliczne (rozgrzewka SharePoint, odpytywanie urlopów); restart ich nie powtarza
- `perf-metrics.jsonl` — czas i koszt każdego wywołania Claude (źródło dla statystyk i panelu)
- `bob-control.json` — pauza / wyłączenie Boba i lista wyłączonych czatów (zapisywane atomowo, czyta je też panel)
- `api-sniff.jsonl` — logi HTTP z `--sniff-api`
- `ws-sniff.jsonl` — logi WebSocket z `--sniff-ws`

## Bezpieczeństwo

Claude CLI wywołuje się z `--tools` (definiuje wbudowane narzędzia: puste albo tylko WebSearch/WebFetch/Read), `--allowedTools` (jawne narzędzia MCP) i zawsze z `--permission-mode dontAsk` (co nie jest na liście, jest odrzucane). Model nie może wykonać poleceń shell ani niczego zapisać. `Read` jest tylko w wywołaniach z obrazami i działa wyłącznie w katalogu `bob_read_*` utworzonym na to jedno wywołanie (kopie obrazów, to samo co cwd). WebFetch jest wyłączony, gdy załadowane są narzędzia MCP z danymi firmowymi (zostaje WebSearch). Demony HTTP MCP wymagają tokenu Bearer, a `cli.main()` ustawia `umask 077` (sekrety i dane czatów `0600`). Bez względu na treść wiadomości przychodzącej.
