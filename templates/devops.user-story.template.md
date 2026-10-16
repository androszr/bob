# Template: User Story DevOps

> **Jak używać:**
> - User Story DevOps opisuje wartość dostarczaną zespołom deweloperskim lub operacjom — nie użytkownikowi końcowemu aplikacji
> - Zespół DevOps nie stosuje procesu Discovery — US trafia bezpośrednio do backlogu PRJ4
> - US w Jira **Delivery (PRJ4)** z `label: PRJ4`, `Sprint: PRJ4 BACKLOG`
> - Każda US powinna być podpięta pod konkretny epic
> - Kryteria akceptacji = scenariusze do weryfikacji (nie wymagania — te idą do sekcji Wymagania)
> - Powiąż US w Jira z powiązanymi ticketami — zależności, blokady, zadania realizacyjne
> - Dokumentacja w języku polskim; terminy branżowe EN dopuszczalne (pipeline, deploy, cluster, node, Helm, Terraform)
> - US nie może wejść na refinement z otwartymi kwestiami — wszystkie pytania muszą być zamknięte lub jawnie przeniesione do Out of scope

---

## 📋 Nagłówek

| Pole | Wartość |
|------|---------|
| **Epic** | {link do Epica} |
| **UX Brief** | {link do UX Briefu w Confluence — opcjonalne} |
| **Figma** | {link do makiety — opcjonalne} |
| **Powiązane US** | {linki do powiązanych User Stories, jeśli dotyczy} |
| **Zależności** | {linki do ticketów blokujących lub blokowanych} |
| **Rodzaj zmiany** | { Praca Projektowa } |
| **Dotyczy** | _(nazwa produktu lub obszaru)_ |
| **Typ pracy** | { Prace programistyczne } |

---

## User Story

> Jako **{rola: DevOps / Platform Engineer / SRE}** chcę **{działanie / możliwość}**, aby **{cel / wartość — np. szybszy deploy, lepsza obserwowalność, wyższa dostępność}**.

---

## Kontekst i cel

_{Krótki opis — po co ta story istnieje, jaki problem rozwiązuje, co się dzieje bez tej funkcji. 2–4 zdania. Może zawierać odniesienie do procesu operacyjnego, SLA lub incydentu.}_

---

## Wymagania funkcjonalne

_{Szczegółowa logika działania — co system/infrastruktura ma robić. Konfiguracja, zachowanie pipeline'u, polityki, alerty.}_

### 1. {Nazwa obszaru wymagań}

1.1. {Wymaganie}

1.2. {Wymaganie}

### 2. {Nazwa obszaru wymagań}

2.1. {Wymaganie}

_(Sekcje numerowane — każde wymaganie ma unikalny identyfikator np. 1.1, 2.3 — ułatwia odnoszenie się do nich w review i komentarzach)_

---

## Kryteria techniczne

_{Wymagania niefunkcjonalne — wydajność, bezpieczeństwo, dostępność, zgodność.}_

- {np. Rozwiązanie musi być kompatybilne z Kubernetes 1.28+}
- {np. Sekret nie może być przechowywany w repozytorium — wymagane użycie Vault/Sealed Secrets}
- {np. Czas przełączenia failover < 30s}
- {np. Zmiana musi być odwracalna bez downtime'u (blue-green / canary)}

---

## Kryteria akceptacji

_{Testowalne scenariusze weryfikujące, że story jest ukończona. Każde kryterium powinno dawać jednoznaczną odpowiedź TAK/NIE.}_

> **Wskazówki:**
> - AC powinny być weryfikowalne bez zaglądania do kodu infrastrukturalnego
> - Uwzględnij scenariusze: happy path, failure/rollback, edge case
> - Jeśli AC dotyczy metryki — podaj próg i okres pomiaru

- [ ] **AC1:** {np. Pipeline wykonuje deploy na staging w < 5 min}
- [ ] **AC2:** {np. W przypadku błędu deploy automatycznie wykonuje rollback do poprzedniej wersji}
- [ ] **AC3:** {np. Alert w Slack pojawia się w < 1 min od przekroczenia progu CPU > 80%}

---

## Out of scope

_{Co jest jawnie wyłączone z tej story.}_

- {Element X — obsłużony w {link do ticketu}}
- {Element Y — poza zakresem tej iteracji, decyzja: {powód}}

---

## Kwestie otwarte

| # | Pytanie | Właściciel | Status |
|---|---------|------------|--------|
| 1 | {pytanie} | {imię / rola} | ⏳ Otwarte |
| 2 | {pytanie} | {imię / rola} | ✅ Zamknięte: {odpowiedź} |

---

## Notatki / Decyzje

_{Opcjonalne — ustalenia z refinementu, decyzje architektoniczne, kontekst nie pasujący do innych sekcji.}_
