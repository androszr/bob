---
title: "Warsztat AS IS → TO BE: {Temat}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Podsumowanie spotkania / warsztatu (Meeting Notes)

> **Jak używać:**
> - Template służy do podsumowania spotkań typu **warsztat odkrywczy / discovery / mapowanie procesu AS IS → TO BE** z biznesem (np. technicy serwisowi, dyspozytorzy, kierownicy regionów).
> - Sekcje AS IS / TO BE / Ograniczenia są opcjonalne — usuń je, jeśli spotkanie miało inny charakter (np. statusowe, decyzyjne, tylko Q&A).
> - Wypełnij na podstawie transkrypcji audio (`*.mp3.json`) lub własnych notatek; cytaty zostawiaj dosłowne — to mocny dowód intencji uczestników.
> - **Zawsze dopisuj cytat-klucz + autora przy bólach i wymaganiach** — chroni przed reinterpretacją na etapie analizy.
> - **Konwertuj relatywne daty na bezwzględne** (np. „w styczniu" → „styczeń 2026"), żeby dokument był czytelny po czasie.
> - Time-stampy z nagrania w sekcji „Załączniki / źródła" — pomagają wrócić do kontekstu bez ponownego odsłuchu.
> - Plik nazywaj wg konwencji: `warsztat-{temat-kebab}-podsumowanie.md` lub `spotkanie-{temat-kebab}-{YYYY-MM-DD}.md`.

> **Kiedy używać:**
> - po warsztatach z biznesem (zbieranie potrzeb, mapowanie procesu AS IS, definiowanie TO BE)
> - po sesjach discovery / Q&A z użytkownikami końcowymi
> - po spotkaniach decyzyjnych, których ustalenia muszą trafić do dalszej analizy / refinementu
> - jako wejście do późniejszych user stories, taskow w Jira i briefów UX
>
> **Kiedy NIE używać:**
> - jeśli to był wywiad 1-on-1 / 1-on-2 z jednym rozmówcą (bez mapowania procesu z kilkoma uczestnikami) → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była obserwacja w miejscu pracy / shadowing → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był wywiad ewaluacyjny istniejącego produktu → użyj `meeting-notes-product-feedback-interview.template.md`
> - jeśli celem było wyłącznie podjęcie decyzji → użyj `meeting-notes-decision.template.md`
> - jeśli to był status / sync → użyj `meeting-notes-status-update.template.md`
> - jeśli to było rozpoczęcie nowej inicjatywy → użyj `meeting-notes-kickoff.template.md`
> - jeśli to było podsumowanie zakończonego etapu → użyj `meeting-notes-retrospective.template.md`

---

# {Typ spotkania}: {Temat} — podsumowanie

**Plik źródłowy:** [{nazwa pliku transkrypcji}]({link-do-pliku})
**Data spotkania:** {YYYY-MM-DD}
**Czas trwania:** ~{NN} min
**Inicjatywa / Epic:** {nazwa inicjatywy, kontekst skąd zgłoszone — np. rada użytkowników pilotażu, ticket PRJ3-XXX}
**Cel spotkania:** {jedno zdanie: co konkretnie chcieliśmy z tego spotkania wynieść — np. zebranie potrzeb biznesowych do AS IS i TO BE procesu X}

## Uczestnicy (role)

- **{Grupa 1, np. Technicy serwisowi / Dyspozytorzy}:** {Imię (rola, krótki kontekst — staż, ekspertyza, rola na spotkaniu)}, {Imię (rola)}
- **{Grupa 2, np. Strona Acme / produkt}:** {Imię (PO/analiza/UX/Technologia)}, {Imię (rola)}
- **Wzmianka (osoby nieobecne, ale przywoływane):** {Imię — kontekst, ustalenie, otwarty wątek}

---

## TL;DR (jednym akapitem)

_{Maks. 5–8 zdań. Co jest sednem? Jaki problem? Jakie kluczowe ustalenie? Jaki kierunek TO BE? Co eksplicytnie odłożone do osobnego wątku? Pisz tak, żeby ktoś, kto nie był na spotkaniu, zrozumiał kierunek bez czytania reszty dokumentu.}_

---

## AS IS — kanoniczny model {dokumentów / procesu / domeny}

_{Opcjonalne — gdy spotkanie mapowało proces. Jeśli pasuje, użyj diagramu ASCII (przepływ dokumentów, stanów, ról). Cel: jeden rzut oka i wiadomo, jak to działa dzisiaj.}_

```
{Krok 1} → {Krok 2} → {Krok 3}
              │
              ├──> {Wariant A — np. dokument poboczny}
              │
              └──> {Wariant B}
                    ├─ {Pod-wariant}
                    └─ {Pod-wariant}
```

**Kluczowy invariant / reguła biznesowa:** `{zapis formalny, np. zlecenie zamknięte == suma(prac na raporcie serwisowym) ± korekta}`

---

## AS IS — proces „{nazwa operacji jak nazywa ją biznes}" (krok po kroku)

_{Wypełnij, jeśli ktoś przeprowadził demo „na żywo" albo opisał przepływ. Numerowana lista czynności + co widzi użytkownik + gdzie sięga „poza system" (drukowany raport serwisowy, kalkulator, Excel, inne aplikacje).}_

1. {Krok 1 — gdzie wchodzi, co klika}
2. {Krok 2 — co wprowadza, jakie pole}
3. {Krok 3 — co system pokazuje, czego brakuje w widoku}
4. {Krok N — finalizacja, status dokumentu}

**Cykl realizacji / wolumen:**

- {Persona A}: {częstotliwość — codziennie / tygodniowo / miesięcznie}
- {Persona B}: {częstotliwość}
- Wolumen: {np. ~NNN spraw miesięcznie / zlecenie na NN pozycji}

## AS IS — {drugi etap / wariant / powiązany proces — opcjonalne}

_{Opcjonalne — jeśli na spotkaniu wypłynął równolegle ważny wątek, który nie jest głównym tematem. Streść w 3–6 punktach i jednoznacznie zaznacz status: domknięte / odłożone do osobnego wątku / wymaga decyzji.}_

- {Co to jest / czemu służy}
- {Jak dziś działa}
- {Realne incydenty wyłapane dzięki temu — z dowodem, np. „technik w marcu — zlecenie zamknięte dwa razy"}

**Status w warsztacie:** {np. „zgłoszony jako kolejny równie ważny etap — uczestnicy ustalili, że wrócą do niego osobno"}

---

## AS IS — problemy / „bóle" (lista jednoznacznie wyartykułowanych)

_{Każdy ból = jedno zdanie + dosłowny cytat lub dowód z warsztatu. Bez interpretacji. Numeruj — później łatwiej odwoływać się w taskach „rozwiązuje ból #3, #7".}_

| # | Ból | Cytat / dowód z warsztatu |
|---|-----|---------------------------|
| 1 | {Krótki opis bólu} | {Imię: „dosłowny cytat"} |
| 2 | {Krótki opis bólu} | {Imię: „dosłowny cytat" / opis sytuacji} |
| 3 | {Krótki opis bólu} | {Imię: „dosłowny cytat"} |

---

## TO BE — oczekiwania spisane podczas warsztatu

> **Cytat-klucz ({Imię}):** „{dosłowny cytat, który najlepiej oddaje docelowy kierunek — często pada jako spontaniczne podsumowanie przez uczestnika}".

### Wymagania funkcjonalne (uporządkowane wg priorytetu z warsztatu)

1. **{Wymaganie 1 — krótka nazwa}** — {opis, co konkretnie ma się dziać; warunki brzegowe; cytat uczestnika, jeśli pomocny}.
2. **{Wymaganie 2}** — {opis}:
   - {pod-warunek},
   - {pod-warunek},
   - {pod-warunek}.
3. **{Wymaganie 3}** — {opis}.
4. **{Wymaganie N}** — {opis + ewentualne uściślenia w cytacie}.

### Wymagania niefunkcjonalne

- {Skala / wolumen — np. zlecenia z setkami pozycji}
- {Wydajność / czas reakcji — np. dane dostępne natychmiast}
- {Dostępność / UX dla nowych użytkowników}
- {Bezpieczeństwo / zgodność / regulacje}

---

## Ograniczenia / „rzeczy, które się nie zmienią"

_{Bardzo ważne — uczestnicy wprost wyartykułowali, co JEST regułą biznesową i NIE podlega negocjacji w TO BE. Pomijając, narażasz się na propozycję projektową, która zostanie odrzucona w 5 minut.}_

- **{Reguła 1}** — {dlaczego nie zmieniamy}:
  1. {powód 1},
  2. {powód 2}.
- **{Reguła 2}** — {uzasadnienie}.
- {Dwoisty / wielowariantowy charakter procesu — rozwiązanie TO BE musi obsłużyć **wszystkie** warianty}.

---

## Otwarte pytania (do zaadresowania w mapowaniu procesu / z analitykiem)

_{Wszystko, czego nie udało się rozstrzygnąć na spotkaniu. Każde pytanie powinno mieć właściciela odpowiedzi — albo wyraźnie „do analizy" / „do technologii" / „do biznesu".}_

1. {Pytanie 1 — konkretne, sformułowane tak, by dało się na nie odpowiedzieć tak/nie lub liczbą}
2. {Pytanie 2}
3. {Pytanie N}

---

## Action items (kolejne kroki ustalone na warsztacie)

| # | Co | Kto | Termin |
|---|----|-----|--------|
| 1 | {Konkretna akcja — np. zmapowanie procesu z analitykiem} | {Imię / role} | {YYYY-MM-DD lub „przed kolejnym spotkaniem"} |
| 2 | {Akcja} | {Imię} | {termin} |
| 3 | {Akcja} | {Imię} | {termin} |

---

## Wskazówki dla analizy AS IS

_{Co konkretnie analityk powinien domknąć po tym spotkaniu — żeby nie zaczynać od zera. Wypunktowane, krótkie.}_

- Zmapować **diagram BPMN / sekwencję** procesu „{nazwa}" z artefaktami: {lista dokumentów / encji wymienionych na warsztacie}.
- Zinwentaryzować **miejsca, w których użytkownik sięga „poza system"** — {przykłady z warsztatu: drukowany raport serwisowy, kalkulator, Excel, e-mail}.
- Zmierzyć **wolumeny** (jeśli możliwe z danych): {co konkretnie — np. liczba dokumentów per persona miesięcznie, średnia liczba pozycji, rozkład typów rozbieżności}.
- Skatalogować **klasy {przypadków / rozbieżności / wariantów}** z przykładami z warsztatu jako test cases.
- Zewidencjonować **zależności zewnętrzne** — {np. uzgodnienia z konkretnymi osobami, integracje z systemami zewnętrznymi}.

## Wskazówki dla projektu TO BE

_{Krótkie hinty dla UX / Product Designera. Nie projekt — kierunki.}_

- Domyślny ekran użytkownika: {pomysł na „happy path" — np. lista z flagą zgodności, większość pozycji auto-matched}.
- Dla {wyjątków / rozbieżności}: {szczegół z etykietą typu + odnośnik do dokumentu źródłowego + „one-click" akcja}.
- Mechanizm {wykrywania duplikatów / X} oparty na: {atrybuty, poziomy pewności}.
- Onboarding nowego użytkownika: {tryb prowadzony / tutorial — wprost zgłaszany przez uczestników jako wartość}.
- {Źródło prawdy} dla {encji}: zaprojektować integrację tak, by zniknęła potrzeba {dzisiejszej obejścia}.

---

## Załączniki / źródła

- Transkrypcja audio (segmenty z time-stampami i mówcami): [{nazwa pliku}]({link})
- Kluczowe momenty w nagraniu:
  - {MM:SS} — {kto i o czym — np. „wprowadzenie, cel spotkania"}
  - {MM:SS} — {kto i o czym}
  - {MM:SS} — {kto i o czym}
  - {MM:SS} — {ustalenie kolejnych kroków}
- Powiązane dokumenty:
  - {link do user story / taska w Jira / poprzedniego podsumowania, jeśli kontynuacja}
  - {link do ux-brief / task.template, jeśli wynikły bezpośrednio z warsztatu}
