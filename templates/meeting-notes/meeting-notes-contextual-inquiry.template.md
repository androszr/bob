---
title: "Wizyta badawcza: {Temat}"
type: meeting
date: {YYYY-MM-DD}
author: {Imię Nazwisko}
summary: "{1 zdanie — o czym było spotkanie i jaki jest główny wniosek}"
---

# Template: Wizyta badawcza / obserwacja w miejscu pracy (Meeting Notes)

> **Przeznaczenie tego template'u (czytaj uważnie zanim wybierzesz):**
> - Sesja typu **contextual inquiry / shadowing / wizyta badawcza** — badacz jedzie do użytkownika i obserwuje, jak ten realnie wykonuje proces w swoim środowisku pracy (oddział, biuro, magazyn części, u klienta).
> - Hybryda **obserwacji + wywiadu** — najpierw patrzymy „co naprawdę robi", potem dopytujemy. Cel: zobaczyć **rzeczywiste obejścia, własne reguły, momenty frustracji**, których nie usłyszysz w rozmowie zdalnej.
> - Wyjście: surowy materiał obserwacyjny + cytaty + hipotezy badacza. Dane wejściowe do user story, briefów UX, decyzji produktowych.
>
> **Kiedy NIE używać:**
> - jeśli to był wywiad bez obserwacji procesu (zdalna rozmowa, „jak Ci się pracuje?") → użyj `meeting-notes-discovery-interview.template.md`
> - jeśli to był wywiad ewaluacyjny o istniejącym produkcie („jak korzystasz z aplikacji X") → użyj `meeting-notes-product-feedback-interview.template.md`
> - jeśli to był warsztat mapowania procesu z kilkoma uczestnikami → użyj `meeting-notes-as-is-to-be.template.md`
> - jeśli to był status / sync → użyj `meeting-notes-status-update.template.md`
> - jeśli celem było rozstrzygnięcie decyzji → użyj `meeting-notes-decision.template.md`
>
> **Jak używać:**
> - **Oddziel obserwację od interpretacji** — w sekcji „Obserwacja procesu" zapisuj fakty (co kliknął, co powiedział, w jakiej kolejności). Hipotezy i wnioski idą do osobnej sekcji na końcu.
> - **Cytaty dosłowne** — każdy mocniejszy moment werbalny zapisuj cytatem z imieniem rozmówcy.
> - **Notuj własne reguły i obejścia** — to najcenniejszy materiał z wizyty (karteczka obok komputera, własna kolejność klikania, korzystanie z innego systemu „bo tu się nie da").
> - **Notuj emocje i postawę** — ton głosu, frustracje, rutyna, pewność. To kontekst, którego brak w zdalnym wywiadzie.
> - **Konwertuj relatywne daty na bezwzględne** (np. „w styczniu" → „styczeń 2026").
> - Frontmatter YAML wg konwencji folderu (`type: field-visit` / `site-visit`, lokalizacja, segment, role, duration_min, tags, summary).
> - Plik nazywaj: `{YYYY-MM-DD}-{typ-wizyty-kebab}-{identyfikator-miejsca}.md` (np. `2026-04-07-field-visit-br-014.md`).

---

```yaml
---
type: field-visit
initiative: {nazwa-inicjatywy-kebab}
date: {YYYY-MM-DD}
{identyfikator-miejsca}: {np. OD-014}
{lokalizacja}: {np. site_city: Miasto-A}
{segment / typ}: {np. site_segment: obiekt przemysłowy}
role: {technik / dyspozytor / klient / kierownik / inna}
interviewee: {Imię Nazwisko}
researcher: {Imię Nazwisko badacza}
duration_min: {NN}
tags: [{tag1}, {tag2}, {tag3}]
summary: "{2–4 zdania — kto, kontekst miejsca, najmocniejszy insight, główne pain pointy}"
---
```

# Wizyta badawcza — {Temat / Obszar}
## {Identyfikator miejsca} | {YYYY-MM-DD}

**Badacz:** {Imię Nazwisko}
**Czas trwania:** ok. {NN} min

---

## Karta miejsca / oddziału

_{Opcjonalne — wypełnij, jeśli wizyta dotyczyła konkretnego oddziału / klienta / miejsca zlecenia z bazy referencyjnej. Wklej kluczowe pola z bazy, żeby raport stał się samowystarczalny (czytelnik nie musi szukać kontekstu w innym systemie).}_

### Tożsamość i status

| Pole | Wartość |
|------|---------|
| Nr / ID | `{identyfikator}` |
| Status | `{OTWARTY / inny}` |
| {Właściciel / rozmówca} | `{Imię Nazwisko}` |
| Kontakt | `{telefon / e-mail}` |

### Lokalizacja i charakterystyka

| Pole | Wartość |
|------|---------|
| Miejscowość | `{…}` |
| Adres | `{…}` |
| Województwo / region | `{…}` |
| Powierzchnia / wielkość | `{…}` |
| Segment / typ placówki | `{…}` |

### Struktura opieki / kontakty pośrednie

| Pole | Wartość |
|------|---------|
| {Manager / opiekun} | `{Imię Nazwisko}` |
| {Partner / przełożony} | `{Imię Nazwisko}` |
| {Dyrektor / region} | `{Imię Nazwisko}` |

---

## 1. Profil rozmówcy

| Pytanie | Odpowiedź |
|---------|-----------|
| Staż w roli / w organizacji | {…} |
| Liczba miejsc / oddziałów pod opieką | {…} |
| {Kontekst operacyjny — np. wielkość magazynu, struktura zespołu} | {…} |
| {Rytm pracy — np. godziny przyjmowania zleceń, godziny wizyt u klientów} | {…} |
| Skąd wykonuje obserwowany proces (dom / biuro / oddział / terenowo) | {…} |
| Sposób przygotowania do procesu (notatnik / Excel / telefon służbowy / WhatsApp / inne systemy) | {…} |

---

## 2. Kontekst przed otwarciem {narzędzia / aplikacji}

> _Co rozmówca robi zanim wejdzie do narzędzia? Notatnik, Excel, zdjęcia, WhatsApp z zespołem, telefon służbowy, inne systemy?_

{3–6 zdań opisu rzeczywistego setupu. Co miał otwarte, co przygotowane na papierze, na czym oparł się przed wejściem do narzędzia. To pokazuje **co narzędzie pomija** — bo skoro użytkownik robi to gdzie indziej, znaczy że nie ufa lub nie umie tego zrobić w narzędziu.}

---

## 3. Obserwacja procesu {nazwa procesu}

### Kolejność działań

{Opisz krok po kroku to, co badacz **widział** — nie to, co rozmówca powiedział że robi. Konkretnie: które menu kliknął, w jakiej kolejności przewijał, gdzie zatrzymał się dłużej. Numerowana lista lub akapit, jak czytelniej.}

### {Główna interakcja — np. zmiany ilości / wprowadzanie danych}

| Typ {zmiany / akcji} | {Kategoria / obszar} | Powód / komentarz rozmówcy |
|----------------------|----------------------|----------------------------|
| {np. korekta stanu} | {…} | „{dosłowny cytat}" |
| {np. zmniejszenie} | {…} | {…} |
| {np. zwiększenie} | {…} | {…} |

> _{Pytanie pomocnicze, jeśli pasuje — np. „Czy zmiany są masowe (cała kategoria) czy indywidualne (konkretne pozycje)?"}_

### Wchodzenie w szczegóły / inne ekrany

{Ile razy w trakcie procesu rozmówca wchodził w szczegóły? Na co patrzył? Czy był wzorzec (rutyna) czy weryfikacja problematycznych przypadków?}

### Wyszukiwanie / dodawanie spoza domyślnego widoku

{Czy korzystał z wyszukiwarki? Jak — po nazwie, kodzie, kategorii? Jakie pozycje dodawał spoza rekomendacji systemu?}

### Korzystanie z innych źródeł podczas procesu

{Czy otwierał inne systemy / dokumenty / aplikacje w trakcie? Jakie i po co? To kluczowy sygnał: każde wyjście „poza system" to potencjalna luka funkcjonalna.}

### Komentarze werbalne rozmówcy

{Krótkie cytaty wypowiedziane spontanicznie podczas obserwacji. Bez interpretacji.}

- „{dosłowny cytat}"
- „{dosłowny cytat}"
- „{dosłowny cytat}"

### Emocje i postawa

{1–3 zdania — jak się zachowywał. Spokojnie i rutynowo? Z frustracją? Z entuzjazmem? Czy widać było momenty wahania, sprawdzania, zatrzymania?}

### Własne reguły i obejścia

_{Bardzo wartościowa sekcja — to, co rozmówca wypracował sam, bo system go nie wspiera. Każda taka reguła = sygnał o brakującej funkcji lub o tym, że domyślny flow nie pasuje.}_

- {Reguła / obejście 1 — np. „Zawsze zatwierdza korekty stanu busa bezpośrednio przed zamówieniem części"}
- {Reguła / obejście 2 — np. „Przegląda formularz od dołu do góry"}
- {Reguła / obejście 3}

---

## 4. Pytania dodatkowe po obserwacji

_{Po obserwacji badacz dopytuje o konkretne tematy — według scenariusza wywiadu. Każdy podtemat jako pod-nagłówek z tabelą pytanie → odpowiedź lub krótki akapit.}_

### {Podtemat 1 — np. Rekomendacje systemu i prognozowanie}

| Pytanie | Odpowiedź |
|---------|-----------|
| {pytanie} | {odpowiedź — krótko, z cytatem jeśli pomocny} |
| {pytanie} | {…} |
| {pytanie} | {…} |

### {Podtemat 2 — np. Formularz / atrybuty}

| Pytanie | Odpowiedź |
|---------|-----------|
| {pytanie} | {…} |
| {pytanie} | {…} |

### {Podtemat N — np. Inne kategorie / wątki nie omówione w obserwacji}

{…}

---

## 5. Hipoteza {konkretna — np. braków części w busie / nadmiarowych zamówień / itp.}

_{Opcjonalne — wypełnij, jeśli scenariusz wizyty obejmował weryfikację konkretnej hipotezy badawczej.}_

> _{Pytanie pomocnicze — np. „Czy zdarza się X? Co mogłoby pomóc?"}_

{Odpowiedź rozmówcy + obserwacje badacza weryfikujące lub falsyfikujące hipotezę.}

---

## 6. Pytanie zamykające

> _Czy jest jeszcze coś, na co powinniśmy zwrócić uwagę?_

{Cokolwiek, co rozmówca chciał dodać poza scenariuszem. Czasem tu pojawia się najmocniejszy insight.}

---

## 7. Kluczowe obserwacje i wnioski

_{Najważniejsze insighty z wizyty — do wykorzystania w projektowaniu. To **interpretacja badacza**, więc tutaj można już wnioskować (osobno od surowej obserwacji powyżej). Każdy punkt: tytuł + akapit z dowodem (cytat / konkretna obserwacja).}_

1. **{Insight 1 — krótki tytuł, np. „technik ufa proponowanej kolejności zleceń jako punktowi wyjścia, ale ma wyraźną własną logikę korekty"}**
   {3–5 zdań rozwinięcia. Co konkretnie zaobserwowano, jaki cytat to potwierdza, co z tego wynika dla projektowania.}

2. **{Insight 2}**
   {…}

3. **{Insight 3}**
   {…}

4. **{Insight N}**
   {…}

---

## 8. Luźne przemyślenia

_{Nieskategoryzowane obserwacje, intuicje, hipotezy badacza. Materiał do dalszej weryfikacji w kolejnych wizytach lub przez analitykę. Nie wnioski formalne — surowy strumień myśli badacza po wizycie.}_

- {Przemyślenie 1}
- {Przemyślenie 2}
- {Przemyślenie 3}

---

## Załączniki / źródła

- {Nagranie / zdjęcia z wizyty — jeśli były}: [{nazwa pliku}]({link})
- Transkrypcja (jeśli była): [{nazwa pliku}]({link})
- Powiązane wizyty / wywiady:
  - [{inna wizyta z tej samej inicjatywy / segmentu}]({link})
  - [{wywiad z tą samą personą zdalnie}]({link})
- Powiązane dokumenty:
  - [{user story / brief / epic}]({link})
  - [{indeks wizyt — README folderu}]({link})

---

*Raport wygenerowany przez {skill / autor} | {YYYY-MM-DD generowania}*
