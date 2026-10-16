---
title: "Status update: {Temat}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Status update / sync (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Spotkanie typu **status / sync / progress update** — regularne (cotygodniowe, dwutygodniowe) lub ad hoc check-iny.
> - Główny cel: **wymiana informacji o postępie**, identyfikacja blokerów, aktualizacja ryzyk. **Nie podejmuje się tu strategicznych decyzji** ani nie mapuje procesów AS IS / TO BE.
> - Uczestnicy raportują, co zrobili, co robią, co ich blokuje — w formacie zbliżonym do dziennego stand-upu, ale na poziomie tygodnia/sprintu/inicjatywy.
>
> **Kiedy NIE używać:**
> - jeśli spotkanie miało jeden konkretny problem do rozstrzygnięcia → użyj `meeting-notes-decision.template.md`
> - jeśli to był warsztat mapowania procesu z biznesem → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli zespół podsumowywał zakończony etap → użyj `meeting-notes-retrospective.template.md`
> - jeśli to było rozpoczęcie nowej inicjatywy → użyj `meeting-notes-kickoff.template.md`
> - jeśli to był wywiad jakościowy / discovery z jednym rozmówcą → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była obserwacja w miejscu pracy / shadowing → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był wywiad ewaluacyjny istniejącego produktu → użyj `meeting-notes-product-feedback-interview.template.md`
>
> **Jak używać:**
> - Wypełnij na podstawie transkrypcji audio (`*.mp3.json`), notatek własnych lub Slacka.
> - Przy każdym work-streamie krótko: postęp od ostatniego razu → co planowane → co blokuje.
> - **Konwertuj relatywne daty na bezwzględne** (np. „w przyszłym tygodniu" → „tydzień 2026-05-22").
> - Plik nazywaj: `status-{inicjatywa-kebab}-{YYYY-MM-DD}.md` lub `sync-{zespół}-{YYYY-MM-DD}.md`.

---

# Status update: {Temat / Inicjatywa / Zespół} — {YYYY-MM-DD}

**Plik źródłowy:** [{nazwa pliku transkrypcji}]({link})
**Data spotkania:** {YYYY-MM-DD}
**Czas trwania:** ~{NN} min
**Cykl:** {jednorazowy / cotygodniowy / dwutygodniowy / inny}
**Poprzedni sync:** [{link do poprzedniego podsumowania}]({link}) — {YYYY-MM-DD}
**Cel spotkania:** {jedno zdanie — np. status realizacji inicjatywy X w sprincie Y}

## Uczestnicy

- **{Grupa 1, np. Produkt}:** {Imię (rola)}, {Imię (rola)}
- **{Grupa 2, np. Technologia}:** {Imię (rola)}
- **Nieobecni (przywoływani):** {Imię — co i kiedy dostarczy}

---

## TL;DR (jednym akapitem)

_{Maks. 3–5 zdań. Gdzie ogólnie jesteśmy? Czy idziemy na czas? Jakie 1–2 kluczowe blokery / ryzyka? Czy jest coś, co wymaga decyzji od kogoś spoza zespołu?}_

---

## Status per work-stream / temat

_{Jedna sekcja na work-stream. Jeśli było tylko jedno — usuń pod-nagłówki. Status: na czas / obserwujemy ryzyko / zagrożone / wstrzymane.}_

### {Work-stream 1 — nazwa} — status: {na czas / ryzyko / zagrożone / wstrzymane}

- **Postęp od ostatniego razu:** {co konkretnie dostarczone, mergowane, zatwierdzone}
- **W toku:** {nad czym aktualnie pracujemy, kto się tym zajmuje}
- **Planowane do następnego sync'u:** {co konkretnie}
- **Blokery / ryzyka:** {co blokuje, kto ma to odblokować, do kiedy}
- **Zmiany zakresu / scope creep:** {jeśli coś doszło/odeszło — krótko}

### {Work-stream 2 — nazwa} — status: {na czas / ryzyko / zagrożone / wstrzymane}

- **Postęp od ostatniego razu:** {…}
- **W toku:** {…}
- **Planowane do następnego sync'u:** {…}
- **Blokery / ryzyka:** {…}

---

## Blokery / ryzyka — przegląd zbiorczy

| # | Bloker / ryzyko | Wpływ | Właściciel | Termin rozwiązania |
|---|------------------|-------|------------|--------------------|
| 1 | {opis} | {co opóźni / czego nie dostarczymy} | {Imię} | {YYYY-MM-DD lub „następny sync"} |
| 2 | {opis} | {…} | {Imię} | {…} |

---

## Metryki / liczby (jeśli omawiane)

_{Opcjonalne — np. burn-down, % completion, liczba ticketów, SLA. Tylko jeśli na spotkaniu padły konkretne liczby.}_

- {Metryka 1}: {wartość} (trend od ostatniego sync'u: w górę / bez zmian / w dół)
- {Metryka 2}: {wartość}

---

## Decyzje podjęte na spotkaniu

_{Drobne, taktyczne — bez tych warto rozważyć osobny template decyzyjny. Krótko: co i dlaczego.}_

1. **{Decyzja 1}** — {1 zdanie uzasadnienia}
2. **{Decyzja 2}** — {…}

---

## Action items

| # | Co | Kto | Termin | Status z poprzedniego sync'u |
|---|----|-----|--------|------------------------------|
| 1 | {akcja} | {Imię} | {YYYY-MM-DD} | {nowe / przedłużone z poprzedniego / zamknięte} |
| 2 | {akcja} | {Imię} | {YYYY-MM-DD} | {…} |

### Zamknięte od poprzedniego sync'u

- {akcja} — {Imię} ({krótki rezultat})
- {akcja} — {Imię}

---

## Następny sync

- **Data:** {YYYY-MM-DD HH:MM}
- **Główne tematy do omówienia:** {1–3 punkty}

---

## Załączniki / źródła

- Transkrypcja: [{nazwa pliku}]({link})
- Powiązane:
  - [{poprzedni status}]({link})
  - [{board / dashboard}]({link})
