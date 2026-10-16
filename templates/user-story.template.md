---
title: "{Krótki tytuł User Story}"
type: user-story
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — jako kto, co, po co}"
---

# Template: User Story

> **Jak używać:**
> - Wypełnij wstępnie nagłówek i sekcje przed startem Discovery a następnie w poszczególnych fazach - rozwijaj
> - Sprawdź zgodność z Definition of Ready przed przeniesieniem do Delivery na Ready for Planning
> - Kryteria akceptacji = scenariusze do weryfikacji (nie wymagania funkcjonalne — te idą do sekcji Wymagania)
> - ⚠️ Poniższe trzy zasady dotyczą projektów **Acme — aplikacja mobilna (PRJ1) i portal (PRJ2)**. Dla innych projektów nazwy kolejek i konwencje mogą się różnić.
> - US w Jira **Discovery (PRJ3)** = US tworzona i rozwijana w fazie Discovery
> - US w Jira **Delivery (PRJ1)** = US przekazana do realizacji przez zespół
> - Do sprintu może wejść tylko US z kolejki PRJ1 — po akceptacji PO i spełnieniu Definition of Ready
> - Każda US powinna być podpięta pod konkretny epik
> - Powiąż US w Jira z powiązanymi ticketami — zależności, blokady, zadania realizacyjne
> - Dokumentacja w języku polskim; terminy branżowe EN dopuszczalne (User Story, refinement, sprint, discovery)
> - US nie może wejść na refinement z otwartymi kwestiami — wszystkie pytania muszą być zamknięte lub jawnie przeniesione do Out of scope

---

## 📋 Nagłówek

| Pole | Wartość |
|------|---------|
| **Epic** | {link do Epica} |
| **UX Brief** | {link do UX Briefu w Confluence} |
| **Figma** | {link do makiety — konkretny node} |
| **Powiązane US** | {linki do powiązanych User Stories, jeśli dotyczy} |
| **Zależności** | {linki do ticketów blokujących lub blokowanych} |
| **Etykieta** | {np. zlecenia / harmonogram / mapa / offline / powiadomienia / design} |
| **Rodzaj zmiany** | {np. Praca Projektowa} |
| **Dotyczy** | {np. Aplikacja mobilna / Portal / Platforma} |
| **Typ pracy** | {np. Prace programistyczne / Analizy biznesowe wdrożeniowe — stan TO-BE} |

---

## User Story

> Jako **{rola użytkownika}** chcę **{działanie / możliwość}**, aby **{cel / wartość dla użytkownika}**.

---

## Kontekst i cel

_{Krótki opis — po co ta story istnieje, jaki problem rozwiązuje, co się dzieje bez tej funkcji. 2–4 zdania. Może zawierać odniesienie do procesu biznesowego lub ścieżki użytkownika.}_

---

## Wymagania funkcjonalne

_{Szczegółowa logika działania systemu. To nie są kryteria akceptacji — to opis tego, co system ma robić. Może zawierać mapowania pól, stany ekranów, obsługę błędów, warunki brzegowe.}_

### 1. {Nazwa obszaru wymagań}

1.1. {Wymaganie}

1.2. {Wymaganie}

### 2. {Nazwa obszaru wymagań}

2.1. {Wymaganie}

_(Sekcje numerowane — każde wymaganie ma unikalny identyfikator np. 1.1, 2.3 — ułatwia odnoszenie się do nich w QA i w komentarzach)_

---

## Kryteria akceptacji

_{Testowalne scenariusze weryfikujące, że story jest ukończona z perspektywy użytkownika. Każde kryterium powinno dawać jednoznaczną odpowiedź TAK/NIE. Nie opisują jak system działa w środku — opisują co użytkownik widzi i może zrobić.}_

> **Wskazówki:**
> - Dodaj 1–2 zdania opisujące zakres tej sekcji — czego dotyczą AC (np. "AC obejmują ścieżkę główną zamknięcia zlecenia serwisowego oraz obsługę braku części")
> - Jeśli kryterium odnosi się do konkretnego widoku — dodaj link do nodu Figmy przy danym AC
> - Elementy jawnie wykluczone umieść w sekcji Out of scope z uzasadnieniem

- [ ] **AC1:** {Użytkownik może / System prezentuje / Gdy X, to Y}
- [ ] **AC2:** {Scenariusz błędu — gdy X, system wyświetla Y}
- [ ] **AC3:** {Scenariusz brzegowy — gdy X jest puste/null/nieprawidłowe, to...}

_(Wskazówka: każde AC powinno odpowiadać na pytanie "czy tester może to zweryfikować bez zaglądania do kodu?")_

---

## Wycena wstępna

- Analiza / architektura: —
- BE: —
- Web: —
- Android: —
- iOS: —
- Design: —
- Testy: —
- PM / DevOps: —
- **TOTAL: —**

**Role Identity:**
- [ ] Technik serwisowy
- [ ] Dyspozytor
- [ ] Kierownik regionu
- [ ] Klient
- [ ] Admin

Uwagi: —

---

## Out of scope

_{Co jest jawnie wyłączone z tej story. Ważne szczególnie gdy część funkcji jest "widoczna ale nieaktywna" lub "obsłużona w osobnym wymaganiu".}_

- {Element X — obsłużony w {link do ticketu}}
- {Element Y — poza zakresem tej iteracji, decyzja: {powód}}

---

## Kwestie otwarte

| # | Pytanie | Właściciel | Status |
|---|---------|------------|--------|
| 1 | {pytanie} | {imię / rola} | ⏳ Otwarte |
| 2 | {pytanie} | {imię / rola} | ✅ Zamknięte: {odpowiedź} |

---

## Notatki / Decyzje projektowe

_{Opcjonalne — ustalenia z refinementu, retrospektywnie ważne decyzje, kontekst nie pasujący do innych sekcji.}_

---

## Kryteria techniczne (opcjonalne)

_{Dodatkowe wymagania techniczne — obsługa błędów API, limity wydajnościowe, bezpieczeństwo. Wypełnia SA lub BA po konsultacji z zespołem deweloperskim.}_

- {np. Endpoint musi odpowiadać w < 500ms dla p95}
- {np. W przypadku błędu HTTP 5xx — system wyświetla komunikat X i loguje zdarzenie}
- {np. Dane wrażliwe nie są logowane po stronie klienta}
