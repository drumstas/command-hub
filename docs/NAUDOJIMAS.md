# Command Hub naudojimas

Ši versija skirta tik užduotims. Pagrindinis darbo būdas — Telegram; Discord
šiuo metu palaiko tik užduočių sąrašo peržiūrą, atlikimo žymėjimą ir paskyros
susiejimą.

## Telegram paskyros susiejimas

Pradėkite asmeninį pokalbį su botu ir parašykite jums skirtą kodą:

```text
/link JUMS_SKIRTAS_KODAS
```

Kodas susieja Telegram paskyrą su komandos nariu ir po susiejimo tampa
nebegaliojantis. Jei susiejate ir Discord paskyrą, jai reikalingas atskiras
kodas.

## Pateikti užduotį

Parašykite užduotį laisva forma, įvardydami atsakingą asmenį ir terminą, jei jį
žinote:

> Alexui reikia parengti „Acme“ pasiūlymą iki penktadienio 15:00.

Galite vietoj teksto atsiųsti balso žinutę. Gemini iš teksto ar balso parengs
užduoties pavadinimą, atsakingą asmenį ir terminą. Jei atsakingo asmens ar datos
nepavyksta nustatyti, patikslinkite užklausą.

Prieš sukuriant užduotį, administratorius Telegram pokalbyje gaus jos peržiūrą
ir galės pasirinkti **Patvirtinti** arba **Atmesti**. Užduotis į pagrindinį
užduočių sąrašą įrašoma tik administratoriui ją patvirtinus. Patvirtinus
atsakingam asmeniui išsiunčiamas pranešimas.

## Klausti apie užduotis

Galite užduoti klausimą įprasta kalba, pavyzdžiui:

- „Kurios užduotys turi būti atliktos iki penktadienio?“
- „Ką Alexas dabar turi padaryti?“

Gemini paverčia klausimą į ribotą užduočių filtrą. Programa pati saugiai
atrinks užduotis iš duomenų bazės, o Gemini suformuluos atsakymą pagal gautus
rezultatus. Gemini tiesiogiai neprisijungia prie duomenų bazės.

Taip pat galite naudoti komandas:

- `/tasks mine` — jūsų sukurtos arba jums priskirtos užduotys.
- `/tasks all` — visos užduotys.
- `/done ID` — pažymėti užduotį atlikta. Tai gali padaryti atsakingas asmuo
  arba administratorius.

## Discord

Discord šiuo metu palaiko `/link`, `/tasks` ir `/done`. Užduočių kūrimas su
administratoriaus patvirtinimu, balso žinutės ir klausimai natūralia kalba
prieinami tik per Telegram.

## Svarbu

Botas nekuria dokumentų ir nesiunčia pranešimų klientams. Kad Telegram botas
galėtų parašyti nariui, narys turi būti pradėjęs asmeninį pokalbį su botu.
