---
title: "Wywiad ewaluacyjny: {Imię / Rola}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Wywiad ewaluacyjny istniejącego produktu (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Rozmowa z użytkownikiem **istniejącego, używanego produktu / aplikacji** — pytamy „jak korzystasz z X, co działa, co boli, co byś poprawił".
> - Wyjście: **mapa real-usage** + lista pochwał + lista bólów + sugestie usprawnień (pogrupowane tematycznie + priorytetami).
> - Cel: wkład do roadmapy produktu, priorytetyzacja bóli, identyfikacja niedoużywanych modułów.
>
> **Kiedy NIE używać:**
> - jeśli to był **wywiad discovery o problemie** (produktu jeszcze nie ma, badamy potrzebę) → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to była **obserwacja w miejscu pracy** (contextual inquiry / shadowing) → użyj `meeting-notes-contextual-inquiry.template.md`
> - jeśli to był warsztat mapowania procesu AS IS → TO BE z kilkoma uczestnikami → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli celem było rozstrzygnięcie decyzji → użyj `meeting-notes-decision.template.md`
> - jeśli to był status / sync → użyj `meeting-notes-status-update.template.md`
>
> **Jak używać:**
> - **Struktura jest produktowa, nie chronologiczna** — TOP używanych funkcji, co działa, co nie, sugestie pogrupowane tematycznie (per moduł). Rozmówca naturalnie myśli „w kontekście funkcji X", nie „w kolejności tego, co powiedział".
> - **Cytaty dosłowne** zostawiaj zawsze przy mocniejszych momentach (pochwały, bóle, sugestie). Bez parafraz wewnątrz cytatu.
> - **Sekcja sugestii grupowana per moduł / obszar produktu** — to ułatwia później przepisanie na backlog. W obrębie modułu kolejność wg priorytetu.
> - **Priorytety eksplicytnie** — 🔴 krytyczne, 🟠 średnie, 🟢 nice-to-have. Bez tego cały dokument wygląda na „all P1".
> - Oddziel **pochwały** („co działa dobrze") od sugestii — to ważne dla zespołu produktowego, żeby nie zepsuć tego, co już działa.
> - **Konwertuj relatywne daty na bezwzględne** (np. „w styczniu" → „styczeń 2026").
> - Frontmatter YAML wg konwencji folderu (`type: interview`, app, platform, role, tags, summary).
> - Plik nazywaj: `{YYYY-MM-DD}-{rola}-{imie-nazwisko-kebab}-wywiad.md`.

---

```yaml
---
type: interview
initiative: {nazwa-inicjatywy-kebab}
date: {YYYY-MM-DD}
role: {technik / dyspozytor / klient / po / ekspert / inna}
interviewee: {Imię Nazwisko}
app: {nazwa-produktu-kebab}
platform: {ios / android / web / desktop / mobile+desktop / n/a}
source: {transkrypt / notatki / nagranie}
tags: [{moduł1}, {moduł2}, {temat1}, {ux}]
summary: "{2–4 zdania — kto, kontekst używania, najmocniejsze punkty (co działa), największe bóle, główne sugestie}"
---
```

# Wywiad — {Rola} {Imię Nazwisko} ({YYYY-MM-DD})

## Kontekst rozmówcy

_{3–6 zdań. Jaki ma kontekst używania produktu? Jak długo? Jakie urządzenie / platforma dominuje? Specyfika jego pracy / oddziału / organizacji, która wpływa na to jak korzysta z produktu. To jest fundament — bez tego sugestie wiszą w próżni.}_

- {Punkt kontekstu 1 — np. „Kierownik regionu z jednym oddziałem + koordynuje dodatkowo podwykonawców"}
- {Punkt kontekstu 2 — np. „Głównie korzysta z aplikacji na telefonie — tablet używa rzadziej"}
- {Punkt kontekstu 3 — np. specyfika ruchu, godziny pracy, struktura zespołu, narzędzia poza produktem}

---

## TOP — z czego korzysta najczęściej

_{Najważniejsza sekcja. Co rozmówca **realnie** klika codziennie / kilka razy dziennie? Daj 3–5 głównych + listę dodatkowych częstych. Cytat-klucz przy najmocniejszej funkcji, jeśli padł.}_

### Top 3 codziennie

1. **{Funkcja 1}** — {kontekst użycia, częstotliwość, dlaczego ta jest #1}. „{cytat jeśli pomocny}"
2. **{Funkcja 2}** — {kontekst, częstotliwość}
3. **{Funkcja 3}** — {kontekst}

### Dodatkowo bardzo częste użycie (codziennie / kilka razy dziennie)

- **{Funkcja 4}** — {kontekst}
- **{Funkcja 5}** — {kontekst}
- **{Funkcja N}** — {kontekst}

---

## Z czego korzysta rzadziej / w ogóle nie korzysta

_{Druga z najważniejszych sekcji — funkcje, które istnieją, ale rozmówca ich nie używa. Zawsze pytaj **dlaczego**. Brak adopcji = albo zła ekspozycja, albo bezwartościowa funkcja, albo zła implementacja.}_

- **{Funkcja A}** — {dlaczego nie używa, cytat jeśli był: „{cytat}"}
- **{Funkcja B}** — {dlaczego}
- **{Funkcja C}** — {dlaczego}
- **{Funkcja N}** — {dlaczego}

---

## Co działa dobrze (pochwały)

_{Pochwały zbieraj świadomie — łatwo skupić się tylko na bólach i przegapić, co już działa. To kluczowa informacja dla zespołu („nie ruszać tego, działa"). Każda pochwała: krótko + cytat.}_

- **{Element / funkcja 1}** — „{dosłowny cytat pochwały}"
- **{Element / funkcja 2}** — „{cytat}" / {opis sukcesu wdrożonej zmiany, kto za nią odpowiada}
- **{Element / funkcja 3}** — {co konkretnie dobre}
- **Ogólnie:** „{cytat ogólnej pozytywnej oceny, jeśli padł}"

---

## Sugestie usprawnień (podzielone tematycznie)

_{Najobszerniejsza sekcja — sugestie pogrupowane **per moduł / obszar produktu**, bo tak rozmówca o nich myśli. W obrębie modułu kolejność od najważniejszych. Każdy punkt: krótki opis + powód + oszacowanie wpływu (jeśli wynikło z rozmowy).}_

### {Moduł 1 — np. Pulpit / strona główna}

- **{Sugestia 1}** — {opis, czego konkretnie brakuje, jaki efekt by miało. Cytat jeśli pomocny.}
- **{Sugestia 2}** — {opis}
- **{Sugestia 3}** — {opis}

### {Moduł 2 — np. Zamówienia}

- **{Sugestia 1}** — {opis}
- **{Sugestia 2}** — {opis}

### {Moduł 3 — np. Powiadomienia}

- **{Sugestia 1}** — {opis}
- **{Sugestia 2}** — {opis}

### {Moduł N}

- **{Sugestia}** — {opis}

### Inne / ogólne braki

_{Sugestie przekrojowe, które nie mieszczą się w pojedynczym module. Np. dotyczące nawigacji, ogólnego designu, ogólnej higieny powiadomień, brakujących funkcji systemowych.}_

- **{Sugestia 1}** — {opis}
- **{Sugestia 2}** — {opis}

---

## Priorytety (perspektywa rozmówcy lub badacza)

_{Pomocna sekcja podsumowująca — która z sugestii to „dziś by mi to życie zmieniło", a która „byłoby miło, ale przeżyję". Nie zawsze jest oczywiste z poprzedniej sekcji; tu wprost.}_

### 🔴 Krytyczne / wysokie

1. **{Sugestia}** — {1 zdanie uzasadnienia, dlaczego krytyczna}
2. **{Sugestia}** — {…}
3. **{Sugestia}** — {…}

### 🟠 Średnie

4. **{Sugestia}** — {…}
5. **{Sugestia}** — {…}

### 🟢 Nice-to-have

6. **{Sugestia}** — {…}
7. **{Sugestia}** — {…}

---

## Cytaty warte zapamiętania

_{Best-of cytatów — 4–8 najmocniejszych, które oddają ton rozmowy. Pomagają później wrócić do dokumentu i przypomnieć sobie kontekst bez czytania całości. Każdy cytat opatrzony krótką etykietą (o czym).}_

- O całości: *„{cytat ogólnej oceny}"*
- O {moduł / funkcja A}: *„{cytat}"*
- O {moduł / funkcja B}: *„{cytat}"*
- O {ból}: *„{cytat}"*
- O {sugestii}: *„{cytat}"*

---

## Wzorzec użytkowania (kompresja jednym akapitem)

_{Opcjonalne — bardzo wartościowe podsumowanie 1 akapitem (4–6 zdań). Jak ten typ użytkownika myśli o produkcie? Co dla niego jest rdzeniem, a co peryferiami? Ta sekcja jest najczęściej cytowana w briefach i podsumowaniach inicjatyw.}_

{4–6 zdań kompresji. Pisz tak, żeby ktoś, kto nie czytał reszty dokumentu, rozumiał perspektywę rozmówcy i miał kierunek, w którym warto rozwijać produkt dla tego segmentu użytkowników.}

---

## Załączniki / źródła

- Transkrypcja audio: [{nazwa pliku}]({link})
- Kluczowe momenty w nagraniu (jeśli odsłuchowy):
  - {MM:SS} — {temat / cytat-klucz}
  - {MM:SS} — {moment „aha"}
  - {MM:SS} — {pochwała / ból #N}
- Powiązane wywiady / dokumenty:
  - [{inny wywiad z tej samej persony / inicjatywy}]({link})
  - [{user story / brief / epic}]({link})
  - [{indeks wywiadów — README folderu}]({link})
