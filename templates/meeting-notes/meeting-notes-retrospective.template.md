---
title: "Retrospektywa: {Nazwa sprintu / etapu}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Retrospektywa / podsumowanie etapu (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Spotkanie typu **retrospektywa** — patrzymy wstecz na zakończony okres (sprint, kwartał, etap projektu, wdrożenie, incydent) i wyciągamy wnioski.
> - Typowe formaty pasujące do tego template'u: „start / stop / continue", „what went well / what went wrong / what to change", „4 L's (liked, learned, lacked, longed for)", post-mortem incydentu.
> - Cel: **lessons learned** + konkretne usprawnienia procesowe na kolejny etap. NIE jest to status („co zrobiliśmy"), tylko refleksja („dlaczego tak wyszło i co z tego wynosimy").
>
> **Kiedy NIE używać:**
> - jeśli to był sync raportujący postęp → użyj `meeting-notes-status-update.template.md`
> - jeśli celem było rozstrzygnięcie konkretnej decyzji → użyj `meeting-notes-decision.template.md`
> - jeśli to był warsztat odkrywczy z biznesem → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli to był wywiad jakościowy z użytkownikiem → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była obserwacja w miejscu pracy / shadowing → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był wywiad ewaluacyjny istniejącego produktu → użyj `meeting-notes-product-feedback-interview.template.md`
> - jeśli to było rozpoczęcie nowego etapu → użyj `meeting-notes-kickoff.template.md`
>
> **Jak używać:**
> - **Oddziel obserwacje od interpretacji** — najpierw co się stało, potem dlaczego (hipoteza), potem co z tego wynosimy.
> - Każdy action item musi mieć właściciela i termin — inaczej retrospektywa zamieni się w wentylowanie frustracji bez efektu.
> - Jeśli retrospektywa była post-mortemem incydentu — wypełnij dodatkowo sekcję „Timeline incydentu".
> - **Blame-free**: opisuj zdarzenia, nie winy. Imiona OK przy faktach („X wdrożył"), nie OK przy ocenach („X popełnił błąd").
> - **Konwertuj relatywne daty na bezwzględne**.
> - Plik nazywaj: `retro-{etap-kebab}-{YYYY-MM-DD}.md` lub `post-mortem-{incydent-kebab}-{YYYY-MM-DD}.md`.

---

# Retrospektywa: {Etap / Sprint / Inicjatywa / Incydent} — {YYYY-MM-DD}

**Plik źródłowy:** [{nazwa pliku transkrypcji / notatek}]({link})
**Data spotkania:** {YYYY-MM-DD}
**Czas trwania:** ~{NN} min
**Format retrospektywy:** {start-stop-continue / went-well-wrong-change / 4L / post-mortem / inny}
**Okres pod lupą:** {od YYYY-MM-DD do YYYY-MM-DD — np. sprint 23, Q1 2026, wdrożenie X}
**Inicjatywa / Epic / Incydent:** {nazwa, kontekst, link do epica/ticketu jeśli pasuje}

## Uczestnicy

- **{Grupa 1, np. Zespół wytwórczy}:** {Imię (rola)}, {Imię (rola)}
- **{Grupa 2, np. Stakeholderzy}:** {Imię (rola)}
- **Facylitator:** {Imię}
- **Nieobecni (ich perspektywa zebrana asynchronicznie):** {Imię — jakim kanałem}

---

## TL;DR (jednym akapitem)

_{Maks. 4–6 zdań. Co był za etap → ogólna ocena → 1–2 największe sukcesy → 1–2 największe lekcje → najważniejsza zmiana procesowa, którą wprowadzamy.}_

---

## Co poszło dobrze (continue)

_{Rzeczy, które chcemy powtarzać. Nie ogólniki („dobra atmosfera") — konkrety, które dają się powtórzyć w kolejnym etapie.}_

1. **{Sukces 1}** — {opis + cytat lub przykład: „dzięki temu udało się X"}
2. **{Sukces 2}** — {opis}
3. **{Sukces N}** — {opis}

---

## Co nie zadziałało (problem)

_{Konkretne sytuacje, decyzje, mechanizmy, które przeszkodziły. Bez personalizacji — opisuj fakty, nie winy.}_

| # | Co | Kiedy / kontekst | Skutek |
|---|----|-------------------|--------|
| 1 | {co się stało / czego brakowało} | {kiedy, w którym etapie} | {co przez to ucierpiało} |
| 2 | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} |

---

## Pomysły na poprawę (start / change)

_{Co zaczynamy robić / co zmieniamy. Każdy pomysł powinien mieć powiązanie z konkretnym problemem powyżej — inaczej dryfujemy w abstrakcję.}_

1. **{Pomysł 1}** — adresuje problem #{numer} → {opis zmiany w procesie}
2. **{Pomysł 2}** — adresuje problem #{numer} → {…}
3. **{Pomysł N}** — {…}

---

## Lessons learned (przekrojowe)

_{Co wyciągamy ponad pojedynczy etap — wnioski, które dotyczą sposobu pracy w ogóle. Krótko, hasłowo.}_

- {Lekcja 1 — np. „nie startujemy implementacji bez zatwierdzonych makiet UX, bo to rodzi przeróbki"}
- {Lekcja 2}
- {Lekcja N}

---

## Timeline incydentu (TYLKO dla post-mortemu)

_{Wypełnij wyłącznie, jeśli retrospektywa była post-mortemem incydentu produkcyjnego. W innym wypadku — usuń tę sekcję.}_

| Czas | Co się stało | Kto wykrył / zareagował | Decyzja / akcja |
|------|--------------|--------------------------|------------------|
| {YYYY-MM-DD HH:MM} | {trigger — co odpaliło incydent} | {monitoring / user / Imię} | {…} |
| {YYYY-MM-DD HH:MM} | {wykrycie} | {Imię} | {…} |
| {YYYY-MM-DD HH:MM} | {mitigacja} | {Imię} | {…} |
| {YYYY-MM-DD HH:MM} | {resolucja} | {Imię} | {…} |

**Detection time (od triggera do wykrycia):** {NN minut}
**Mitigation time (od wykrycia do mitigacji):** {NN minut}
**Resolution time (od triggera do pełnej resolucji):** {NN minut/godzin}
**Wpływ na użytkownika:** {ilu użytkowników, jakie funkcje, ile czasu}
**Root cause:** {jednym zdaniem — co było faktyczną przyczyną}
**Contributing factors:** {co dodatkowo umożliwiło / pogłębiło problem}

---

## Metryki etapu (jeśli mierzone)

_{Opcjonalne. Wartości faktyczne vs. planowane.}_

| Metryka | Plan | Wynik | Komentarz |
|---------|------|-------|-----------|
| {np. velocity} | {NN} | {NN} | {…} |
| {np. % ticketów dostarczonych} | {NN%} | {NN%} | {…} |
| {np. liczba bugów po wdrożeniu} | {<NN} | {NN} | {…} |

---

## Action items (kto, co, kiedy)

| # | Co | Kto | Termin | Powiązany problem |
|---|----|-----|--------|--------------------|
| 1 | {konkretna akcja procesowa} | {Imię} | {YYYY-MM-DD} | #{numer problemu} |
| 2 | {…} | {Imię} | {YYYY-MM-DD} | #{…} |
| 3 | {…} | {Imię} | {YYYY-MM-DD} | #{…} |

---

## Sentiment / nastrój zespołu (opcjonalne)

_{Krótko — jak zespół się czuł podczas tego etapu. Pomocne dla obserwowania trendów wypalenia / motywacji w czasie.}_

- **Energia:** {niska / średnia / wysoka}
- **Pewność co do kierunku:** {niska / średnia / wysoka}
- **Główne źródła frustracji:** {1–3 hasła}
- **Główne źródła satysfakcji:** {1–3 hasła}

---

## Załączniki / źródła

- Transkrypcja: [{nazwa pliku}]({link})
- Powiązane:
  - [{retrospektywa z poprzedniego etapu}]({link}) — porównanie trendów
  - [{board / dashboard z metrykami}]({link})
  - [{post-mortem / RCA}]({link}) — jeśli dotyczy incydentu
