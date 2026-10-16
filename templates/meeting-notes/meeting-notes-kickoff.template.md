---
title: "Kickoff: {Nazwa inicjatywy}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Kickoff inicjatywy / projektu (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Spotkanie typu **kickoff** — formalny start nowej inicjatywy / projektu / epica / dużego strumienia pracy.
> - Charakterystyczne sygnały: „startujemy temat X", „pierwsze spotkanie ws. inicjatywy", „przedstawienie zakresu i ról", „uzgadniamy cel i sukces".
> - Cel dokumentu: **utrwalić wspólne rozumienie** celu, zakresu, ról, ryzyk i harmonogramu na samym starcie — żeby uniknąć rozbieżnych interpretacji 3 miesiące później („myślałem, że robimy X, nie Y").
> - Często łączony z briefem / project charter — ten template jest jego krótszą, „notatkową" wersją.
>
> **Kiedy NIE używać:**
> - jeśli inicjatywa już trwa, a to był kolejny status → użyj `meeting-notes-status-update.template.md`
> - jeśli celem było rozstrzygnięcie pojedynczej decyzji → użyj `meeting-notes-decision.template.md`
> - jeśli mapowaliśmy proces AS IS / TO BE → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli to był wywiad jakościowy / discovery z 1–3 osobami → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była obserwacja w miejscu pracy / shadowing → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był wywiad ewaluacyjny istniejącego produktu → użyj `meeting-notes-product-feedback-interview.template.md`
> - jeśli to było podsumowanie zakończonego etapu → użyj `meeting-notes-retrospective.template.md`
>
> **Jak używać:**
> - **Sukces zdefiniuj mierzalnie** — jeśli nie umiesz powiedzieć, jak rozpoznamy, że się udało, nie zaczynaj inicjatywy.
> - **Scope: in / out** — wprost wymień, czego NIE robimy. Brak tej sekcji to gwarancja scope creep.
> - **Założenia i ryzyka rejestruj nawet jeśli „oczywiste"** — za pół roku przestaną być oczywiste.
> - **Konwertuj relatywne daty na bezwzględne**.
> - Plik nazywaj: `kickoff-{inicjatywa-kebab}-{YYYY-MM-DD}.md`.

---

# Kickoff: {Nazwa inicjatywy} — {YYYY-MM-DD}

**Plik źródłowy:** [{nazwa pliku transkrypcji / notatek}]({link})
**Data spotkania:** {YYYY-MM-DD}
**Czas trwania:** ~{NN} min
**Inicjatywa / Epic:** {nazwa + link do epica w Jira / Confluence}
**Sponsor inicjatywy:** {Imię (rola) — osoba budżetowa / decyzyjna}
**Product Owner:** {Imię}
**Tech Lead:** {Imię}

## Uczestnicy

- **Sponsor / decydenci:** {Imię (rola)}, {Imię (rola)}
- **Zespół wytwórczy:** {Imię (rola)}, {Imię (rola)}, {Imię (rola)}
- **Stakeholderzy biznesowi:** {Imię (rola)}, {Imię (rola)}
- **Wzmianka (osoby istotne, ale nieobecne):** {Imię — kontekst, kiedy zostaną włączeni}

---

## TL;DR (jednym akapitem)

_{Maks. 5–8 zdań. Po co startujemy → jaki problem rozwiązujemy → dla kogo → jak rozpoznamy sukces → kiedy chcemy skończyć → kto pilotuje. Pisz tak, żeby osoba dołączająca do inicjatywy za miesiąc wszystko z tego rozumiała.}_

---

## Cel inicjatywy

### Problem / opportunity

_{2–4 zdania. Co konkretnie się dzieje (lub nie dzieje), że potrzebujemy tę inicjatywę uruchomić? Dlaczego teraz?}_

### Cel biznesowy

_{Jednym zdaniem — czego chcemy dostarczyć biznesowi. „Aby [persona] mógł [zrobić X] szybciej / lepiej / w ogóle".}_

### Hipoteza wartości

_{Czego oczekujemy, że się stanie, gdy dostarczymy rozwiązanie. „Wierzymy, że [zmiana] doprowadzi do [efekt]; zmierzymy to przez [metryka]."}_

---

## Definicja sukcesu (jak rozpoznamy, że się udało)

_{Mierzalnie. Bez „lepiej", „szybciej", „efektywniej" bez liczb. Jeśli nie mamy jeszcze baseline'u — wpisz „do zmierzenia w fazie discovery".}_

| # | Metryka sukcesu | Baseline (dziś) | Target | Termin pomiaru |
|---|------------------|-----------------|--------|----------------|
| 1 | {np. czas obsługi procesu X} | {NN min} | {NN min} | {YYYY-MM-DD, np. 3 mies. po wdrożeniu} |
| 2 | {…} | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} | {…} |

**Anti-metryki (czego pilnujemy, by NIE spadło):** {np. NPS klientów, adopcja innej funkcjonalności, czas innego procesu}

---

## Scope

### In scope (robimy)

1. {Element zakresu 1}
2. {Element zakresu 2}
3. {Element zakresu 3}

### Out of scope (świadomie NIE robimy w tej inicjatywie)

_{Krytyczna sekcja — chroni przed scope creep. Wpisz wprost rzeczy, które ktoś mógłby chcieć dorzucić w trakcie.}_

1. {Co odkładamy + dlaczego — np. „integracja z systemem Z — odłożona do kolejnej iteracji ze względu na zależność od zespołu Q"}
2. {…}

### Mogą wejść w scope warunkowo

_{Rzeczy, które wpuścimy, jeśli zostanie czas / budżet. Wyraźne „nice-to-have", nie zobowiązanie.}_

- {…}
- {…}

---

## Personas / dla kogo to robimy

_{Główne i wtórne persony, których to dotyczy. Bez tej sekcji projekt skończy się generycznym rozwiązaniem dla nikogo.}_

- **Primary:** {persona — kto to jest, jaki jest jej kluczowy job-to-be-done}
- **Secondary:** {persona}
- **Wpływ na inne grupy:** {np. obsługa, support — w jaki sposób zmiana ich dotknie}

---

## Założenia (na których budujemy plan)

_{Jeśli któreś z tych założeń przestanie być prawdą — plan trzeba re-review.}_

1. {Założenie 1 — np. „integracja z X będzie dostępna od Q2 2026"}
2. {Założenie 2 — np. „mamy budżet na zewnętrznego dostawcę Y"}
3. {Założenie N}

---

## Ograniczenia / „rzeczy, które się nie zmienią"

_{Wymienione na spotkaniu jako twarde ograniczenia — regulacje, decyzje zarządu, integracje, których nie ruszamy.}_

- **{Ograniczenie 1}** — {dlaczego}
- **{Ograniczenie 2}** — {dlaczego}

---

## Ryzyka i mitigacja

| # | Ryzyko | Prawdopodobieństwo | Wpływ | Mitigacja | Właściciel |
|---|--------|--------------------|-------|-----------|------------|
| 1 | {opis ryzyka} | {N/Ś/W} | {N/Ś/W} | {co robimy, by się nie zmaterializowało} | {Imię} |
| 2 | {…} | {…} | {…} | {…} | {…} |
| 3 | {…} | {…} | {…} | {…} | {…} |

---

## Harmonogram (kamienie milowe)

_{Wysoki poziom — nie szczegółowy plan, ale kontrakt na główne checkpointy.}_

| Milestone | Co dostarczamy | Planowana data |
|-----------|----------------|----------------|
| M1 — {nazwa} | {np. discovery zamknięte, decyzja go/no-go} | {YYYY-MM-DD} |
| M2 — {nazwa} | {np. MVP wdrożony do pilota} | {YYYY-MM-DD} |
| M3 — {nazwa} | {np. pełen rollout} | {YYYY-MM-DD} |
| M4 — {nazwa} | {np. pierwszy pomiar metryk sukcesu} | {YYYY-MM-DD} |

**Najbliższy checkpoint:** {YYYY-MM-DD} — {co konkretnie powinno być wtedy gotowe}

---

## Role i odpowiedzialności (RACI)

_{Wymień kluczowe role — kto za co odpowiada. Nie musi być pełna macierz RACI, ale każda rola w zespole powinna wiedzieć, czego się od niej oczekuje.}_

| Rola | Osoba | Główna odpowiedzialność |
|------|-------|--------------------------|
| Sponsor | {Imię} | Budżet, eskalacja, finalna decyzja |
| Product Owner | {Imię} | Priorytety, scope, kontakt z biznesem |
| Tech Lead | {Imię} | Architektura, jakość, decyzje techniczne |
| UX / Designer | {Imię} | Projektowanie, badania jakościowe |
| Analityk | {Imię} | Wymagania, mapowanie procesu, dane |
| QA | {Imię} | Strategia testów, jakość |
| {inna rola} | {Imię} | {…} |

---

## Zależności zewnętrzne

_{Co potrzebujemy od ludzi / zespołów / systemów spoza naszego zespołu, żeby inicjatywa się udała.}_

- **{Zespół / system 1}** — {czego potrzebujemy, do kiedy, kto po stronie tamtego zespołu jest naszym kontaktem}
- **{Zespół / system 2}** — {…}

---

## Otwarte pytania

1. {Pytanie 1 — kto odpowiada, do kiedy}
2. {Pytanie 2}
3. {Pytanie N}

---

## Action items (po kickoffie)

| # | Co | Kto | Termin |
|---|----|-----|--------|
| 1 | {np. przygotowanie briefa / user story do refinementu} | {Imię} | {YYYY-MM-DD} |
| 2 | {np. wpięcie inicjatywy w Jira / Confluence} | {Imię} | {YYYY-MM-DD} |
| 3 | {np. umówienie pierwszego warsztatu z biznesem} | {Imię} | {YYYY-MM-DD} |
| 4 | {np. uzgodnienie pomiaru baseline'u metryk} | {Imię} | {YYYY-MM-DD} |

---

## Załączniki / źródła

- Transkrypcja: [{nazwa pliku}]({link})
- Powiązane dokumenty:
  - [{brief / project charter}]({link})
  - [{epic w Jira}]({link})
  - [{folder Confluence inicjatywy}]({link})
  - [{poprzednie analizy / discovery, na których budujemy}]({link})
