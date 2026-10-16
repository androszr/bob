---
# Konwencja nazwy pliku:
#   {YYYY-MM-DD}-ux-brief-{slug-inicjatywy}.md
#
# - Data w nazwie = `updated:` z frontmatter (ostatnia merytoryczna aktualizacja)
# - Przy kazdej istotnej aktualizacji: zmien `updated:` oraz rename pliku, by daty byly spojne
# - Data utworzenia zyje w `date:` i w tabeli Metadane
title: "UX Brief: {Nazwa inicjatywy}"
type: ux-brief
initiative: {slug-inicjatywy}
area: {mobile | portal}
status: draft
date: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
author: {imię i nazwisko PO/PD}
epic: {link do Epica w Jira lub pusty string}
ticket: {link do US / Task lub pusty string}
tags: [{tag1}, {tag2}]
summary: "{1 zdanie — czego dotyczy inicjatywa i dla kogo}"
---


# UX Brief: {Nazwa inicjatywy}

## Metadane

| Pole | Wartość |
|------|---------|
| Projekt | {nazwa projektu} |
| Produkt / Moduł | {nazwa produktu} |
| Epic (Jira) | {link do Epica — pole "Epic Link" w Jira} |
| Ticket (Jira) | {link do US / Task} |
| Status | `draft` |
| Autor (PO/PD) | {imię i nazwisko} |
| Data utworzenia | {YYYY-MM-DD} |
| Ostatnia aktualizacja | {YYYY-MM-DD} |
| Confluence | _(po opublikowaniu uzupełnij link do strony)_ |
| Miro | _(link do tablicy Miro / ramki, jeśli dotyczy)_ |

## Wstęp

> {2–3 zdania: czego dotyczy inicjatywa, jaki jest kluczowy problem, dla kogo. Służy jako orientacja dla osoby, która trafi na ten brief po raz pierwszy — bez konieczności czytania całości.}

---

> **Jak korzystać z tego dokumentu:**
> - 🟢 **Gate 1 (PO + BO)** - PO wypełnia wstępnie przed spotkaniem otwierającym, omawiane na Gate 1
> - 🔵 **Problem Space (BA, PD)** - BA i PD pogłębiają i douzupełniają sekcje z Gate 1 + dodają nowe
> - 🟣 **Solution Space (BA, PD, SA)** - weryfikacja i finalizacja wyróżników na podstawie wypracowanych rozwiązań
> - Sekcje 1–6 żyją przez cały discovery - **Gate 1 to punkt startowy, nie ostateczny**

---

# 🟢 GATE 1 - Start Discovery (PO + BO)

_PO wypełnia wstępnie na podstawie ustaleń z BO **przed spotkaniem otwierającym**. Omawiane na Gate 1 z BA, PD, SA. Sekcje te będą pogłębiane i douzupełniane w Problem Space przez BA i PD._

## 1. Użytkownicy

**Dla kogo to robimy?**
_(Kto korzysta z tego ekranu/flow/produktu? Link do persony)_

> {opis użytkowników}

### User Story

> Jako **{kto}** chcę **{co}**, aby **{po co / wartość}**.

---

## 2. Cel i kontekst

**Jaki jest cel zadania/projektu?**
_(PM/PO/Biznes: Geneza zmiany, co chcemy osiągnąć)_

> {cel}

**Czy występują czynniki niezależne od produktu, które wpływają na ten projekt?**

> {zależności, kontekst zewnętrzny, powiązania}

**Jak zadanie/projekt odnosi się do strategii produktu/produktów?**
_(Szerszy kontekst, strategia biznesowa)_

> {odniesienie do strategii produktu}

**Jak zadanie/projekt odnosi się do strategii organizacji?**
_(Który value stream wspiera i w jaki sposób)_

> {odniesienie do strategii organizacji}

**Co stracimy, gdy nie wprowadzimy zmian?**
_(np. użytkownicy nie będą realizować zadań, powielimy błędy konkurencji, nie spełnimy oczekiwań użytkowników)_

> {konsekwencje braku działania}

**W jaki sposób projekt/zadanie polepszy doświadczenia użytkownika lub oszczędzi jego czas?**

> {oczekiwana poprawa UX}

**Czy próbowaliście wprowadzać takie zmiany wcześniej?**

> {historia prób, wnioski}

---

## 3. Problem i potrzeby użytkownika - wstępnie

_PO opisuje problem na poziomie ogólnym, z perspektywy BO i zgłoszeń. Szczegóły uzupełnia BA w Problem Space._

**Jaki jest główny problem użytkownika?**
_(Czy użytkownicy zgłaszali coś wcześniej? Dane z obsługi klienta, insighty)_

> {opis problemu - wstępny, z perspektywy PO/BO}

**Jak teraz użytkownik radzi sobie z tym problemem?**
_(Czy ma swoje obejścia, sposoby na usprawnienie pracy?)_

> {opis sposobu na workaround problemu}

---

## 4. Interesariusze (RACI)

**Kto jest odpowiedzialny za inicjatywę?**
_(Kto powinien uczestniczyć w kick-offie i konsultacjach? Kto podejmuje decyzje produktowe, projektowe, prawne?)_

| Osoba | Rola w projekcie | RACI |
|-------|-----------------|------|
| {imię} | {rola} | Odpowiedzialny |
| {imię} | {rola} | Realizujący |
| {imię} | {rola} | Konsultowany |
| {imię} | {rola} | Informowany |

---

## 5. Wyróżniki - wstępne

_Wyróżniki to elementy nowego systemu, które usprawniają pracę techników serwisowych i dyspozytorów, oszczędzając czas. Efektywność mierzymy w minutach zaoszczędzonych miesięcznie w skali jednego technika. PO wypełnia wstępnie na Gate 1, BA i PD pogłębiają w Problem Space, finalizacja w Solution Space._

**Szacowane oszczędności per element:**

| Wyróżnik (element usprawnienia) | Oszczędność min/mies./technika | Źródło danych analitycznych | Etap wyceny |
|---------------------------------|-----------------------------|----------------------------|-------------|
| {nazwa elementu} | {X min} | {np. analityka, badania, obserwacja} | 🟢 Gate 1 |
| {nazwa elementu} | {X min} | {źródło} | 🟢 Gate 1 |

**Sumaryczna oszczędność dla inicjatywy:** {suma min/mies./technika}

> ⚠️ Tabela jest aktualizowana iteracyjnie:
> - 🟢 Gate 1 - PO podaje wstępne szacunki na podstawie ustaleń z BO
> - 🔵 Problem Space - BA/PD weryfikują i doprecyzowują na podstawie analizy as-is i badań
> - 🟣 Solution Space - finalizacja po wypracowaniu rozwiązań, weryfikacja z BO

---

## 6. Plan działania - wstępny

_PO z BA, PD, SA ustalają wstępny harmonogram na spotkaniu otwierającym._

**Czy jest ustalony plan/etapy pracy?**

> {plan / harmonogram - wstępny}

**Ile mam czasu?**

> {deadline, ramy czasowe}

**Jakie są ograniczenia/ryzyka w realizacji planu?**
_(np. nie mamy czasu na testy użyteczności, ograniczenia biznesowe, technologiczne)_

> {lista ograniczeń}

---
---

# 🔵 PROBLEM SPACE - pogłębienie i douzupełnienie (BA, PD)

_Po Gate 1 rozpoczyna się praca analityczna. BA i PD **wracają do sekcji 1–6 wyżej** i pogłębiają je o wnioski z analizy as-is i badań (np. doprecyzowują User Story, uzupełniają kontekst, weryfikują cel, doprecyzowują wyróżniki). Poniżej - dodatkowe sekcje, które powstają dopiero na tym etapie._

## 7. Problem i potrzeby użytkownika - pogłębione

_BA uzupełnia na podstawie analizy as-is. PD uzupełnia po badaniach. Pogłębia wstępny opis problemu z sekcji 3._

**Na jakim etapie ścieżki użytkownika występuje problem?**

> {etap / touchpoint}

**Jak wygląda obecny system/widok?**

> {opis obecnego stanu, linki do screenshotów}

**Jakie są potrzeby użytkownika?**

> {lista potrzeb - na podstawie analizy i/lub badań}

**Czy użytkownicy zgłaszają pomysły na usprawnienia? Jakie?**

> {lista pomysłów usprawnień - na podstawie analizy i/lub badań}

### Obecny widok

_(Screenshoty / linki do aktualnego stanu UI)_

| Screen | Link / opis |
|--------|-------------|
| {nazwa} | {link} |

### Potrzeby (notatki)

- {potrzeba 1}
- {potrzeba 2}
- {potrzeba 3}

---

## 8. Dane i metryki

_BA uzupełnia danymi z analizy as-is. PD uzupełnia wynikami badań._

**Czy dany problem jest poparty danymi?**
_(Analityka, dane ilościowe, zdarzenia biznesowe, zgłoszenia z obsługi klienta / sprzedaży, komentarze, desk research i konkurencja, nagrania użytkownika, raporty z badań)_

> {dostępne dane i źródła}

**Jeśli nie mamy danych, to jakie są możliwości analizy ilościowej/jakościowej?**

> {propozycje pozyskania danych}

**Jakie dane jeszcze musimy zdobyć? Kto je dostarczy i ile to potrwa?**

| Dane do pozyskania | Kto dostarczy | Termin |
|--------------------|---------------|--------|
| {dane} | {osoba/zespół} | {kiedy} |

**Jak sprawdzimy, czy nasze rozwiązanie działa?** _(KPI + Metryki)_

| KPI / Metryka | Obecna wartość | Cel |
|---------------|----------------|-----|
| {metryka} | {baseline} | {target} |

---

## 9. Plan działania - zaktualizowany

_Aktualizowany po ustaleniu scope i zakończeniu badań. Zastępuje wstępny plan z Gate 1._

**W którym momencie przetestujemy?**

> {planowany moment walidacji}

---
---

# 🟣 SOLUTION SPACE - weryfikacja i finalizacja (BA, PD, SA)

_Na etapie Solution Space, po wypracowaniu wymagań i makiet, wracamy do wyróżników (sekcja 5) i finalizujemy je._

## 10. Wyróżniki - finalne

_Po akceptacji wymagań i makiet przez PO i BO - finalna tabela wyróżników z zweryfikowanymi danymi._

| Wyróżnik (element usprawnienia) | Oszczędność min/mies./technika | Źródło danych | Status weryfikacji |
|---------------------------------|-----------------------------|---------------|--------------------|
| {nazwa elementu} | {X min} | {źródło} | ✅ Zweryfikowany / ⏳ Do weryfikacji |

**Sumaryczna oszczędność dla inicjatywy:** {suma min/mies./technika}

**Zmiany vs Gate 1 / Problem Space:**

> {opis co się zmieniło w szacunkach i dlaczego}