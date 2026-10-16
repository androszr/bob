---
title: "{Krótki tytuł zadania}"
type: task
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — co jest do zrobienia i dlaczego}"
---

# Template: Zadanie (Task)

> **Jak używać:**
> - Zadanie to mniejsza zakresem inicjatywa podejmowana przez Zespół do Discovery lub Delivery.
> - ⚠️ Poniższe trzy zasady dotyczą projektów **Acme — aplikacja mobilna (PRJ1) i portal (PRJ2)**. Dla innych projektów nazwy kolejek i konwencje mogą się różnić.
> - Zadanie w Jira **Discovery (PRJ3)** = zadanie podejmowane do Discovery
> - Zadanie w Jira **Delivery (PRJ1)** = zadanie zespołowe do realizacji
> - Do sprintu może wejść tylko zadanie z kolejki PRJ1
> - Oba typy zadań powinny być wycenione w fazie refinementów ze story pointami
> - Zadanie dla zespołu PRJ4 powinno zawierać: `label: PRJ4`, `Sprint: PRJ4 BACKLOG`
> - Każde zadanie powinno być podpięte pod konkretny epik
> - Wypełnij wstępnie nagłówek i sekcje przed startem, następnie rozwijaj w kolejnych fazach
> - Sprawdź zgodność z Definition of Ready przed przeniesieniem do Delivery na Ready for Planning

> **Kiedy używać:**
> - gdy praca nie wymaga formuły User Story — nie musi samodzielnie dostarczać wartości użytkownikowi końcowemu
> - mniejszy element funkcjonalności lub pracy wspierający realizację US lub epika
> - konkretna czynność: techniczna (BE/FE/DB), analityczna, QA, DevOps, design

---

## 📋 Nagłówek

| Pole | Wartość |
|------|---------|
| **Epic** | {link do Epica} |
| **Typ zadania** | {Implementacja / Analiza / DevOps / Design / QA} |
| **Powiązana User Story** | {link do US, z której wynika to zadanie} |
| **Komponent / Warstwa** | {np. BE / FE / DB / Infrastruktura} |
| **Etykieta** | {np. zlecenia / harmonogram / mapa / offline / powiadomienia / design / PRJ4} |
| **Rodzaj zmiany** | {np. Praca Projektowa} |
| **Dotyczy** | {np. Aplikacja mobilna / Portal / Platforma} |
| **Typ pracy** | {np. Prace programistyczne / Analizy biznesowe wdrożeniowe — stan TO-BE} |

---

## Cel zadania

_{Jedno zdanie: co i po co. Skąd to zadanie wynika — odniesienie do funkcji lub problemu z US.}_

> {np. "Dostosowanie modelu danych mikroserwisu Zlecenia do aktualnego kontraktu eventów, umożliwiające poprawne działanie US PRJ1-XXXX."}

---

## Zakres prac

_{Numerowana lista konkretnych czynności do wykonania. Powinna być wystarczająco szczegółowa, żeby można było estymować i śledzić postęp.}_

### 1. {Obszar prac}

- {Konkretna zmiana / akcja}
- {Konkretna zmiana / akcja}

### 2. {Obszar prac}

- {Konkretna zmiana / akcja}

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

## Zależności i ryzyka

_{Opcjonalne — co może zablokować realizację, co musi być gotowe przed rozpoczęciem.}_

- {Zależność / ryzyko}
