---
title: "Spotkanie decyzyjne: {Temat}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Spotkanie decyzyjne (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Spotkanie, którego celem była **podjęcie konkretnej decyzji** — wybór opcji, zatwierdzenie kierunku, rozstrzygnięcie dylematu, akceptacja proponowanego rozwiązania.
> - Charakterystyczne sygnały: padło sformułowanie typu „musimy zdecydować…", „wybieramy A czy B", „potrzebujemy zatwierdzenia", „uzgadniamy podejście".
> - Główny output dokumentu: **jednoznaczny zapis decyzji** + jej uzasadnienie + co się dalej dzieje. Dokument służy też jako ADR (architectural decision record) lub PDR (product decision record) dla potomności.
>
> **Kiedy NIE używać:**
> - jeśli spotkanie tylko raportowało postęp → użyj `meeting-notes-status-update.template.md`
> - jeśli to był warsztat odkrywczy / mapowanie procesu → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli rozmowa dotyczyła jednego rozmówcy / wywiadu jakościowego → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była obserwacja w miejscu pracy / shadowing → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był wywiad ewaluacyjny istniejącego produktu → użyj `meeting-notes-product-feedback-interview.template.md`
> - jeśli to był review zakończonego etapu → użyj `meeting-notes-retrospective.template.md`
> - jeśli to był start nowej inicjatywy → użyj `meeting-notes-kickoff.template.md`
>
> **Jak używać:**
> - **Decyzja musi być sformułowana JEDNOZNACZNIE** — bez „chyba", „raczej", „w sumie tak".
> - Zapisz wszystkie rozważone opcje, nawet odrzucone — to chroni przed kwestionowaniem w przyszłości („czemu nie rozważyliście X?").
> - Przy każdej opcji: argumenty za/przeciw + kto je wnosił (cytat-klucz, jeśli była dyskusja).
> - Zaznacz **kto był decydentem** (RACI: A — Accountable) — nie zawsze to konsensus.
> - **Konwertuj relatywne daty na bezwzględne**.
> - Plik nazywaj: `decyzja-{temat-kebab}-{YYYY-MM-DD}.md` lub `adr-{NN}-{temat-kebab}.md`.

---

# Decyzja: {Tytuł decyzji — jednym zdaniem, jednoznacznie}

**Plik źródłowy:** [{nazwa pliku transkrypcji / notatek}]({link})
**Data spotkania:** {YYYY-MM-DD}
**Czas trwania:** ~{NN} min
**Inicjatywa / Epic:** {nazwa, kontekst}
**Status decyzji:** {podjęta / warunkowa — wymaga X / odroczona / propozycja do zatwierdzenia}

## Uczestnicy (i ich rola w decyzji wg RACI)

- **Decydent (A — Accountable):** {Imię (rola)} — osoba, która formalnie zatwierdziła
- **Współ-decydenci (C — Consulted):** {Imię (rola)}, {Imię (rola)}
- **Wykonawcy decyzji (R — Responsible):** {Imię (rola)}, {Imię (rola)}
- **Poinformowani (I — Informed):** {Imię / zespół — komu należy zakomunikować}

---

## TL;DR (jednym akapitem)

_{Maks. 4–6 zdań. Co było do zdecydowania → jakie opcje → co wybrano → dlaczego → co się teraz zmienia. Ktoś, kto nie był na spotkaniu, ma rozumieć decyzję bez czytania reszty.}_

---

## Decyzja

> **{Pełne sformułowanie decyzji — najlepiej w czasie teraźniejszym, jako stwierdzenie faktu, nie zalecenia. Np. „Migrujemy raportowanie z systemu X do Y do końca Q3 2026."}**

**Data wejścia w życie:** {YYYY-MM-DD lub „natychmiast"}
**Horyzont obowiązywania:** {kiedy decyzja podlega review — np. „po pierwszym wdrożeniu" / „za 6 miesięcy" / „bezterminowo"}

---

## Kontekst — czemu w ogóle podejmowaliśmy tę decyzję

_{2–5 zdań. Co zmieniło się w otoczeniu, że trzeba było decydować? Jaki problem rozwiązujemy? Co się stanie, jeśli nie podejmiemy decyzji?}_

- **Problem / motywacja:** {…}
- **Trigger (co wywołało potrzebę decyzji):** {…}
- **Koszt zaniechania:** {co konkretnie ucierpi, jeśli zostaniemy przy status quo}

---

## Rozważone opcje

### Opcja A: {nazwa} — **{WYBRANA / odrzucona / odłożona}**

- **Opis:** {co konkretnie ta opcja oznacza}
- **Za:** {argumenty + ewentualnie cytat: „{dosłowny cytat}" — {Imię}}
- **Przeciw:** {argumenty + cytat}
- **Koszt / wysiłek:** {orientacyjnie}
- **Ryzyka:** {główne}

### Opcja B: {nazwa} — **{WYBRANA / odrzucona / odłożona}**

- **Opis:** {…}
- **Za:** {…}
- **Przeciw:** {…}
- **Koszt / wysiłek:** {…}
- **Ryzyka:** {…}

### Opcja C: {nazwa} — **{WYBRANA / odrzucona / odłożona}**

_{Dodaj kolejne opcje wedle potrzeby. Jeśli była tylko jedna realna opcja — zostaw jedną i wpisz to wprost: „pozostałe opcje wykluczone w fazie wstępnej, bo X".}_

---

## Uzasadnienie wybranej opcji

_{Dlaczego wygrała ta, a nie inna. Konkretnie — który argument przeważył. Jeśli był kontrowersyjny — zaznacz, kto się sprzeciwiał i czemu (to ważne dla wracających do dokumentu po roku).}_

- **Główny argument:** {…}
- **Czego się świadomie wyrzekamy, wybierając tę opcję:** {trade-off, który zaakceptowaliśmy}
- **Głosy odrębne (jeśli były):** {Imię — „{cytat}"} → {jak zostały zaadresowane / dlaczego pominięte}

---

## Założenia, na których oparta jest decyzja

_{Jeśli któreś z tych założeń przestanie być prawdą — decyzję trzeba re-review. To kluczowa sekcja dla przyszłej analizy.}_

1. {Założenie 1 — np. „budżet kwartalny pozostaje na poziomie X"}
2. {Założenie 2}
3. {Założenie N}

---

## Konsekwencje — co się zmienia

- **Co zaczynamy robić:** {…}
- **Co przestajemy robić:** {…}
- **Kogo trzeba poinformować:** {konkretnie — komunikacja do zespołu X, mail do Y}
- **Co trzeba zaktualizować w dokumentacji:** {linki / nazwy dokumentów}
- **Wpływ na inne inicjatywy / zespoły:** {…}

---

## Otwarte pytania (rzeczy, których nie rozstrzygnęliśmy)

_{Czego nie domknęliśmy — z właścicielem odpowiedzi i terminem. Nie wpisuj tu pytań retorycznych — tylko realne kwestie do zaadresowania.}_

1. {Pytanie 1} — odpowiedź: {Imię}, termin: {YYYY-MM-DD}
2. {Pytanie 2} — odpowiedź: {Imię}, termin: {YYYY-MM-DD}

---

## Action items

| # | Co | Kto | Termin |
|---|----|-----|--------|
| 1 | Zakomunikować decyzję {do kogo, jakim kanałem} | {Imię} | {YYYY-MM-DD} |
| 2 | {wdrożeniowy krok 1} | {Imię} | {YYYY-MM-DD} |
| 3 | {wdrożeniowy krok 2} | {Imię} | {YYYY-MM-DD} |

---

## Załączniki / źródła

- Transkrypcja: [{nazwa pliku}]({link})
- Kluczowe momenty w nagraniu:
  - {MM:SS} — {przedstawienie problemu}
  - {MM:SS} — {moment podjęcia decyzji}
  - {MM:SS} — {głos odrębny / kontrargument}
- Powiązane dokumenty:
  - [{poprzednie decyzje, które tę unieważniają / dopełniają}]({link})
  - [{analiza, brief, RFC}]({link})
