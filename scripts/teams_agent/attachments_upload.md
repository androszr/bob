# `attachments_upload` — instrukcja integracji w innym procesie

Moduł: `scripts/teams_agent/attachments_upload.py`
Public API: `upload_and_send(chat_name, file_path, text="", conv_id=None, timeout_s=60.0) -> str | None`

---

## Minimalna integracja

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "teams_agent"))
from attachments_upload import upload_and_send

msg_id = upload_and_send(
    chat_name="Notatki",           # substring rail-nazwy chatu
    file_path=Path("/tmp/raport.md"),
    text="Raport tygodniowy",       # opcjonalny dopisek (markdown OK)
    conv_id="48:notes",             # opcjonalnie: omija REST lookup
    timeout_s=60.0,                 # opcjonalnie: ile czekać na verify
)
if msg_id:
    print(f"OK, wiadomosc {msg_id}")
else:
    print("Upload nie wyszedl")
```

## Wymagania środowiska

Proces musi mieć dostęp do:

1. **`session.json`** w `scripts/teams_agent/` — sesja Teams (gitignored, regenerowane przez `login.py`).
2. **`.auth-token`** w tym samym katalogu — Bearer dla chatsvc REST verify.
3. **Playwright + Chromium**: `python -m playwright install chromium` raz.
4. Pakiety: `playwright`, `requests` (już w repo).

## Krytyczne uwagi

### 1. Konflikt z daemon Bobem

`cli.py` trzyma własną instancję Playwright z `session.json`. Drugi proces uruchamiający `upload_and_send` w tym samym czasie **otwiera osobny Chromium** — to OK, ale:

- Oba czytają tę samą `session.json` (jednokierunkowo, więc bezpiecznie).
- `.auth-token` jest cache file (pisze go cli.py z token interceptora, czyta upload). Świeży token wystarcza.
- Bez aktywnej sesji Bobem (`cli.py` chodzi) → `.auth-token` może wygasnąć (~1h). Upload sam też je odświeża z chatsvc call w trakcie, ale jeśli REST `find_conversation_id` jest pierwsze wywołanie — może 401. Bezpieczniej **podawać `conv_id` jawnie** (omija REST lookup):

```python
upload_and_send("nie-ma-znaczenia", path, conv_id="19:00000000000000000000000000000000@thread.v2")
```

### 2. Koszt

~5–8 s per call: launch Chromium (1s) + Teams shell (3s) + chat switch (1s) + CDP drop + Enter + verify (~3s). **Nie wołaj per wiadomość** — tylko gdy faktycznie chcesz wgrać plik. Dla bulk: lepiej zbuduj wrapper z reused Playwright sesją.

### 3. Limit 512 KB

Hardcoded `_MAX_UPLOAD_BYTES`. Wystarczy zmienić jeśli planujesz większe pliki, ale: większe niż 1–2 MB powinny iść chunked uploadem (`createUploadSession`) — wtedy CDP drag nie wystarczy.

### 4. Conflict rename

SP zwraca `name "raport.md"` w 1. uploadzie, `"raport 1.md"` w 2., `"raport 2.md"` w 3. — `?@name.conflictBehavior=rename` jest hardcoded w sniff URL. Jeśli chcesz overwrite albo unikalne nazwy z prefix-em, kontroluj `file_path.name` z timestampem przed wywołaniem.

## Typowe scenariusze

### A) MCP tool / cron / scheduler

Proces krótkotrwały, ad-hoc:

```python
result = upload_and_send("Notatki", report_path, conv_id="48:notes")
if not result:
    raise RuntimeError("upload failed — sprawdz session.json + .auth-token")
```

### B) Long-running serwis

`upload_and_send` sam launchuje/zamyka Playwright każdorazowo. To prosty, ale powolny model. Jeśli wołasz częściej niż raz/min, zrób wrapper który trzyma `browser`/`context` żywy między wywołaniami (zachowaj `page.goto(TEAMS_URL)` jako warm-up raz przy starcie).

### C) Bob daemon (`cli.py`)

Można wołać bezpośrednio, ale:

- daemon ma już otwartą sesję Playwright → spawnowanie drugiej kosztuje dodatkowy proces Chromium
- lepsza droga: dorzucić tryb "wrzuć plik" do `ai.py` jako tool (Claude woła `upload_attachment` → cli.py używa istniejącej `page` zamiast nowej)

## Diagnostyka failures

Logi z `[upload]` na stderr pokażą gdzie wysypka:

| Komunikat | Co znaczy | Fix |
|-----------|-----------|-----|
| `BLAD: plik nie istnieje` | bad path | sprawdź `file_path` |
| `BLAD: plik X B > limit` | > 512 KB | zmień limit albo rozbij plik |
| `BLAD: nie znalazlem conv_id` | REST find nie znalazł chatu | podaj `conv_id` jawnie |
| `BLAD: nie wszedlem na chat` | switch w rail nie znalazł | użyj bardziej unikalnego substring, lub zaloguj się manualnie do Teams + sprawdź czy chat w Favorites/widocznych |
| `BLAD: composer nie zmountowal sie` | DOM wyrenderował się długo (slow disk / cold start) | spróbuj ponownie; jeśli powtarzalne — zwiększ `wait_for_selector` timeout (linijka ~228) |
| `BLAD: REST nie potwierdzilo` | drop poszedł, ale wiadomość nie pojawiła się | sprawdź ręcznie w Teams; może blokada po stronie Teams, może `.auth-token` wygasł |

## Co dotyczy session.json

Jeżeli session wygaśnie (typowo po kilku dniach bez Bobem), upload zwróci `BLAD: nie wszedlem na chat` lub zatrzyma się na ekranie loginu Microsoft. **Odśwież**:

```bash
python3 scripts/teams_agent/login.py
```

Tam interaktywnie odpalisz Teams web i Playwright zapisze sesję.

## Test sanity przed uruchomieniem w prod

```bash
python3 scripts/teams_agent/attachments_upload.py /tmp/test.md \
    --chat "Notatki" --conv-id "48:notes" --text "smoke test"
```

Jeśli wraca `OK msg_id=...` — gotowe do integracji w innym procesie.

## Jak to działa pod spodem (TL;DR)

1. Playwright headless Chromium z `session.json`.
2. `page.goto("https://teams.microsoft.com/v2/")` + `scraper.switch_to_chat_by_name(page, chat_name)` przełącza na docelowy chat.
3. **CDP `Input.dispatchDragEvent`** doręcza plik do composera. To kluczowy element: zwykły JS-syntezowany `DragEvent` z `DataTransfer` ma `isTrusted=false` i Teams go odrzuca. CDP-level event przepuszczony przez Chromium ma `isTrusted=true` — Teams traktuje go identycznie jak prawdziwy drag-and-drop z desktopu.
4. `Enter` w composerze → Teams wysyła wiadomość z `properties.files` po wcześniejszym uploadzie na SharePoint (cały OAuth handshake załatwia Chromium sam).
5. Polling chatsvc REST (`api_client.get_messages`) sprawdza że wiadomość z `sequence_id > baseline` faktycznie się pojawiła z atachmentem. Bez tego ciche failure (blokada po stronie Teams, błąd sieci) wyglądałby jak sukces.

### Dlaczego nie REST-only

Bezpośredni PUT na `/_api/v2.0/drive/root:/<folder>/<file>:/content` nie działa: SharePoint odrzuca go (401/403) bez pełnego OAuth handshake Teams Web Client (POST `_layouts/15/filebrowser.aspx?app=TeamsFile` + POST `_api/SP.OAuth.NativeClient/Authenticate`). Odtworzenie tego z poziomu czystego Pythona to godziny pracy, dlatego moduł używa przeglądarki.

## Pliki powiązane

- `attachments_upload.py` — production module (this).
- `attachments.py` — download path (ścieżka pobierania), tutaj exposes `ensure_cookies` / `invalidate_cookies` (jeszcze nie używane przez upload, ale gotowe na ewentualny REST-only rewrite).
