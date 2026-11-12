# Command Hub naudojimas

Ši versija skirta tik užduotims. Administratorius valdo paskyrų susiejimą;
komandos nariai patys savo paskyrų susieti negali.

## Administratoriaus komandos

Surinkęs nario paskyros ID, administratorius jį priskiria esamam `TEAM_ROSTER`
nariui Telegram pokalbyje su botu:

```text
/link <roster_key> <telegram|discord> <numeric_user_id>
```

Pavyzdžiui, susieti Alexo Discord paskyrą:

```text
/link alex discord 123456789012345678
```

Nario Telegram paskyrai naudojama `telegram` platforma. Administratoriaus
Telegram paskyra nustatoma per `OWNER_TELEGRAM_CHAT_ID` ir
`OWNER_TELEGRAM_USER_ID`; jo Discord paskyrą galima susieti komanda
`/link owner discord <user_id>`.

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
užduoties informacija paskelbiama Discord kanale ir pažymimas atsakingas
asmuo. Atsakingo asmens Discord ID turi būti priskirtas administratoriaus.

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

Discord palaiko `/tasks` ir `/done`. Užduočių kūrimas su administratoriaus
patvirtinimu, balso žinutės ir klausimai natūralia kalba prieinami per Telegram.

## Telegram pranešimai

Kad Telegram botas galėtų parašyti nariui apie jam priskirtą užduotį, narys
turi būti pradėjęs asmeninį pokalbį su botu.
