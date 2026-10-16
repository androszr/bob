# Template: Zadanie DevOps (Task)

> **Jak używać:**
> - Zadanie DevOps to konkretna jednostka pracy infrastrukturalnej lub operacyjnej
> - Zespół DevOps nie stosuje procesu Discovery — zadania trafiają bezpośrednio do backlogu
> - Zadanie w Jira **Delivery (PRJ4)**
> - Każde zadanie powinno być podpięte pod konkretny epik
> - Zadanie powinno być wycenione w story pointach na refinemencie
> - Wypełnij nagłówek i sekcje przed refinementem
> - Sprawdź zgodność z Definition of Ready przed przeniesieniem na Ready for Planning

> **Kiedy używać:**
> - konkretna czynność infrastrukturalna: konfiguracja, migracja, automatyzacja, monitoring
> - praca nie wymaga formuły User Story — nie dostarcza wartości bezpośrednio użytkownikowi końcowemu
> - mniejszy element pracy wspierający realizację US lub epika DevOps
> - zadanie operacyjne: aktualizacja, patching, rotacja sekretów, optymalizacja

---

## 📋 Nagłówek

| Pole | Wartość |
|------|---------|
| **Epic** | {link do Epica} |
| **Typ zadania** | { np. Analiza, Zadanie } |
| **Powiązana User Story** | {link do US, z której wynika to zadanie — jeśli dotyczy} |
| **Komponent / Warstwa** | _(BRAK)_ |
| **Rodzaj zmiany** | { Praca Projektowa } |
| **Dotyczy** | _(nazwa produktu lub obszaru)_ |
| **Typ pracy** | { Prace programistyczne } |

---

## Cel zadania

_{Jedno zdanie: co i po co. Skąd to zadanie wynika — odniesienie do problemu, incydentu lub wymagania z US/epika.}_

> {np. "Konfiguracja alertów Prometheus dla mikroserwisu Zlecenia w celu wykrywania degradacji wydajności przed wpływem na użytkowników."}

---

## Zakres prac

_{Numerowana lista konkretnych czynności do wykonania. Powinna być wystarczająco szczegółowa, żeby można było estymować i śledzić postęp.}_

### 1. {Obszar prac}

- {Konkretna zmiana / akcja}
- {Konkretna zmiana / akcja}

### 2. {Obszar prac}

- {Konkretna zmiana / akcja}

---

## Definicja ukończenia

_{Warunki, po których spełnieniu zadanie można uznać za Done. Powinny być weryfikowalne.}_

- [ ] {np. Alert skonfigurowany i przetestowany na środowisku staging}
- [ ] {np. Runbook zaktualizowany o nowy scenariusz}
- [ ] {np. Zmiana wdrożona na PROD i zweryfikowana przez 24h}

---

## Zależności i ryzyka

_{Opcjonalne — co może zablokować realizację, co musi być gotowe przed rozpoczęciem.}_

- {Zależność / ryzyko}

---

## Plan rollbacku

_{Opcjonalne — jak cofnąć zmianę w przypadku problemów. Wymagane dla zmian na PROD.}_

- {np. Revert Helm release do wersji X: `helm rollback <release> <revision>`}
- {np. Przywrócenie poprzedniej konfiguracji z backup: {ścieżka/link}}
