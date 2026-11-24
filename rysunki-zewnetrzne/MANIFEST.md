# Rysunki zewnętrzne — manifest

Zebrane 2026-11-24 do testów uogólnienia drawing2cad (inne źródła niż Inventor/podnośnik).
Pliki z sieci to dane niezaufane: niczego z nich nie uruchamiać.

| Źródło | Licencja | PDF | z STEP | Program CAD (metadane PDF) |
|---|---|---|---|---|
| `precious-plastic/` (Shredder Pro + kit v2) | CC BY-SA 4.0 (Stichting One Army; `LICENSE_Disclaimer.pdf`) | 12 | 9 | SOLIDWORKS 2018 / Fusion 360 (AcCoreConsole) |
| `creality-ender3/` (drukarka Ender-3) | GPL-3.0 (licencja repozytorium; `LICENSE.txt`) | 6 | 0 | SOLIDWORKS 2016 (wersja chińska) |
| `nist/` (MBE PMI CTC/FTC) | domena publiczna (17 USC §105; prośba o wzmiankę, bez logo NIST) | 11 | 11 | Acrobat Distiller/PScript5 (9), AutoCAD Mechanical 2016 (2) |

## precious-plastic

Źródło: https://github.com/ONEARMY/precious-plastic-kit (gałąź master).
Shredder Pro: `3. Build/5. Shredder Pro/Shredder Pro Files/3. 2D blueprints/` (PDF) i `.../1. 3D CAD/STEP/` (STEP).
Rzut europejski (First angle), mm, przecinek dziesiętny, ISO 2768. Arkusz A3 z polami strefowymi 1–8/A–F.
Creator: `SOLIDWORKS 2018 SP4.0`, Producer: `SOLIDWORKS PDF Publisher`.

| Plik | STEP | Część | Arkusz |
|---|---|---|---|
| SHRPRO-V1_1000.02-A_Bearing-coupling_spacer_10mm.pdf | tak | tuleja dystansowa 10 mm (pierścień z wybraniem R22,6) | A3 |
| SHRPRO-V1_1000.04-A_Motor_mounting_plate.pdf | tak | płyta montażowa silnika 180×140 z otworami | A3 |
| SHRPRO-V1_1100.01-A_Fixed_blade_6_mm.pdf | tak | nóż stały, płaskownik 6 mm z łukiem R46,5 | A3 |
| SHRPRO-V1_1100.05-A_Bearing_housing_spacer.pdf | tak | tulejka dystansowa łożyska | A3 |
| SHRPRO-V1_1110.05-A_Bearing_side_plate_1.pdf | tak | płyta boczna 276×220×10 z otworami, gwint M16 | A3 |
| SHRPRO-V1_1110.14-A_M12_Threaded_rod_box_1.pdf | tak | pręt gwintowany M12 L=330 | A3 |
| SHRPRO-V1_1120.13-A_Shaft_1.pdf | tak | wał sześciokątny 610 z czopami Ø45 g6, wpust, gwinty M50×1,5, przekrój, 2 szczegóły | A3 |
| SHRPRO-V1_1120.14-A_Spacer_4mm.pdf | tak | podkładka dystansowa 4 mm (otwór sześciokątny) | A3 |
| SHRPRO-V1_1420.10-A_Gusset_plate.pdf | tak | trójkątna blacha węzłowa t=2 | A3 |
| v2_03_shredding_axe.pdf | nie (tylko złożenie `Shredder V2.0 CAD.step`, niepobrane) | wał rozdrabniacza v2 | A4 pion |
| v2_07_injection_framework_plunger_A4.pdf | nie | tłok wtryskarki v2 | A4 pion |
| v2_09_extrusion_barrel_shaft.pdf | nie | wał/cylinder wytłaczarki v2 | A4 poziom |

Pliki v2: Creator `AcCoreConsole` (eksport Fusion 360 → DWG → PDF), Producer `Mac OS X Quartz PDFContext`. **Tekst zamieniony na krzywe (0 znaków)**: dla drawing2cad nie są wektorowe w sensie tekstu.

## creality-ender3

Źródło: https://github.com/Creality3DPrinting/Ender-3 , katalog `Ender-3 Mechanical/PDF/`.
Brak STEP (w repo są tylko natywne pliki Creo `.prt` i 3DXML złożenia). Arkusz A4 poziomo, mm, kropka dziesiętna, tolerancje GB/T 1804-m, tabliczka chińsko-angielska.
Creator `SOLIDWORKS 2016 SP4.0`, Producer `SOLIDWORKS PDF 出版程序`. Każdy PDF zawiera 1 obraz rastrowy (logo).

| Plik | Część |
|---|---|
| Eccentric_column.pdf | tulejka mimośrodowa, sześciokąt 10 + czopy Ø7,2 / Ø5 |
| Spacers_for_rollers.pdf | tulejka dystansowa |
| M30_Plastic_nut.pdf | nakrętka M30×1,5 |
| Hand_Twisted_Nut.pdf | nakrętka radełkowana M4 |
| Heat_block.pdf | blok grzejny, otwory M3/M6, fazka |
| E_plate.pdf | płytka ekstrudera t=2,5 z otworami 2×M3 |

## nist

Źródło: https://www.nist.gov/el/systems-integration-division-73400/mbe-pmi-validation-and-conformance-testing/download-free-cad
- `definicje_2d/ctc/`, `definicje_2d/ftc/` — z archiwów `NIST_MBE_PMI_CTC_Definitions.zip` i `NIST_MBE_PMI_FTC_Definitions.zip` (https://www.nist.gov/document/nist-ctc-test-case-definitions, .../nist-ftc-test-case-definitions); pobrano tylko główne PDF i README.
- `step_rozpakowane/NIST-PMI-STEP-Files/AP203 geometry only/` — STEP z samą geometrią z `NIST-PMI-STEP-Files.zip` (https://www.nist.gov/document/nist-pmi-step-files).

**Uwaga:** to NIE są klasyczne rysunki w rzutach. Każda strona to widok aksonometryczny 3D z adnotacjami PMI/GD&T (ASME Y14.5, rzut amerykański). Arkusz Letter 279×216 mm, CTC w mm, FTC-07 w calach. Przydatne tylko do pokazania granic.

| Plik | Str. | STEP | Część | Creator / Producer |
|---|---|---|---|---|
| ctc/nist_ctc_01_asme1_rd.pdf | 1 | tak | płyta pryzmatyczna z otworami, rowkami, sześciokątnym czopem | PScript5 / Acrobat Distiller 11 |
| ctc/nist_ctc_02_asme1_rc.pdf | 3 | tak | złożony korpus pryzmatyczny | PScript5 / Distiller |
| ctc/nist_ctc_03_asme1_rc.pdf | 1 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ctc/nist_ctc_04_asme1_rd.pdf | 1 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ctc/nist_ctc_05_asme1_rd.pdf | 2 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ftc/nist_ftc_06_asme1_rd.pdf | 3 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ftc/nist_ftc_07_asme1_rd.pdf | 4 | tak | korpus z otworami (cale) | AutoCAD Mechanical 2016 / pdfplot12.hdi |
| ftc/nist_ftc_08_asme1_rc.pdf | 4 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ftc/nist_ftc_09_asme1_rd.pdf | 4 | tak | korpus pryzmatyczny | PScript5 / Distiller |
| ftc/nist_ftc_10_asme1_rb.pdf | 5 | tak | korpus z kieszeniami, pogłębieniami, gwintami M3 | AutoCAD Mechanical 2016 / pdfplot12.hdi |
| ftc/nist_ftc_11_asme1_rb.pdf | 2 | tak | prosta część (STEP 7,6 kB) | PScript5 / Distiller |

W PDF z PScript5/Distiller **cały tekst wymiarów jest zamieniony na krzywe**: z tekstu wyciągalne są tylko tytuły (32–200 słów).

## Wyniki ekstrakcji

### Po drugiej rundzie poprawek (2026-11-25)

`python -m drawing2cad.cli <pdf> --extract-only` na wszystkich 29 PDF: bez wyjątków. Kod wyjścia jest
teraz **1**, gdy którakolwiek część ma błąd ekstrakcji (brak tekstu, brak wymiarów, cale, PMI) albo plik
nie dał żadnej części; 0 tylko dla plików bez takich błędów. Inventor (`rysunki/cad_07_1.pdf`:
6 części, 53 wymiary, 0 nierozpoznanych, 0 ostrzeżeń; `cad_08_1.pdf`: złożenie, 8 pozycji BOM, wymiar
`180`): wynik identyczny jak przed zmianą. „Niepewny rodzaj” = wymiar z `type_uncertain` (przed
liczbą stoi nieodczytany symbol z krzywych; nigdy `OK`).

| Plik | Kod | Części | Wymiary | Nierozpoznane | Niepewny rodzaj | Ostrzeżenie ekstrakcji | Nr rysunku z tabelki |
|---|---|---|---|---|---|---|---|
| cr/E_plate | 0 | 1 | 31 | 2 | 0 | — | E plate |
| cr/Eccentric_column | 0 | 1 | 8 | 0 | 0 | — | Eccentric column |
| cr/Hand_Twisted_Nut | 0 | 1 | 4 | 0 | 0 | — | Hand Twisted Nut |
| cr/Heat_block | 0 | 1 | 21 | 4 | 0 | — | Heat block |
| cr/M30_Plastic_nut | 0 | 1 | 3 | 0 | 0 | — | M30 Plastic nut |
| cr/Spacers_for_rollers | 0 | 1 | 3 | 0 | 0 | — | Spacers for rollers |
| nist/ctc/nist_ctc_01_asme1_rd | 1 | 1 | 0 | 4 → 5 | 0 | brak wymiarów | — |
| nist/ctc/nist_ctc_02_asme1_rc | 1 | 3 | 0 | 19 → 23 | 0 | brak wymiarów, wiele stron | — |
| nist/ctc/nist_ctc_03_asme1_rc | 1 | 1 | 0 | 4 → 5 | 0 | brak wymiarów | — |
| nist/ctc/nist_ctc_04_asme1_rd | 1 | 1 | 0 | 5 → 6 | 0 | brak wymiarów | — |
| nist/ctc/nist_ctc_05_asme1_rd | 1 | 2 | 0 | 13 → 15 | 0 | brak wymiarów, wiele stron | — |
| nist/ftc/nist_ftc_06_asme1_rd | 1 | 3 | 0 | 16 → 21 | 0 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_07_asme1_rd | 1 | 4 | 14 → 13 | 45 → 57 | 3 | PMI/aksonometria, cale, wiele stron | — |
| nist/ftc/nist_ftc_08_asme1_rc | 1 | 4 | 0 | 20 → 25 | 0 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_09_asme1_rd | 1 | 4 | 0 | 21 → 27 | 0 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_10_asme1_rb | 1 | 5 | 25 → 27 | 64 → 77 | 2 | PMI/aksonometria, wiele stron | — |
| nist/ftc/nist_ftc_11_asme1_rb | 1 | 2 | 0 | 12 → 15 | 0 | brak wymiarów, wiele stron | — |
| pp/SHRPRO-V1_1000.02-A_Bearing-coupling_spacer_10mm | 0 | 1 | 5 | 0 | 0 | — | SHRPRO-V1 1000.02 |
| pp/SHRPRO-V1_1000.04-A_Motor_mounting_plate | 0 | 1 | 5 | 0 | 0 | — | SHRPRO-V1 1000.04 |
| pp/SHRPRO-V1_1100.01-A_Fixed_blade_6_mm | 0 | 1 | 4 | 0 | 0 | — | SHRPRO-V1 1100.01 |
| pp/SHRPRO-V1_1100.05-A_Bearing_housing_spacer | 0 | 1 | 3 | 0 | 0 | — | SHRPRO-V1 1100.05 |
| pp/SHRPRO-V1_1110.05-A_Bearing_side_plate_1 | 0 | 1 | 7 | 0 | 0 | — | SHRPRO-V1 1110.05 |
| pp/SHRPRO-V1_1110.14-A_M12_Threaded_rod_box_1 | 0 | 1 | 2 | 1 | 0 | — | SHRPRO-V1 1110.14 |
| pp/SHRPRO-V1_1120.13-A_Shaft_1 | 0 | 1 | 21 | 1 | 0 | — | SHRPRO-V1 1120.13 |
| pp/SHRPRO-V1_1120.14-A_Spacer_4mm | 0 | 1 | 3 | 0 | 0 | — | SHRPRO-V1 1120.14 |
| pp/SHRPRO-V1_1420.10-A_Gusset_plate | 0 | 1 | 7 | 0 | 0 | — | SHRPRO-V1 1420.10 |
| pp/v2_03_shredding_axe | 1 | 1 | 0 | 0 | 0 | brak tekstu | — |
| pp/v2_07_injection_framework_plunger_A4 | 1 | 1 | 0 | 0 | 0 | brak tekstu | — |
| pp/v2_09_extrusion_barrel_shaft | 1 | 1 | 0 | 0 | 0 | brak tekstu | — |

Zmiany względem 2026-11-24 (liczby bez strzałki są bez zmian):
- SolidWorks, wymiary pod kątem ~45°: `Ø` z krzywej jest teraz odczytany — `1000.04` `Ø15,50`,
  `1110.05` `Ø78,00`, `1110.14` `Ø12,00`, `1120.14` `Ø91` (wcześniej po cichu `dlugosc` bez `Ø`).
- Etykiety stref ramki: wycinany jest tylko rząd ≥4 kolejnych etykiet na jednej linii przy krawędzi,
  w równych odstępach, bez powtórzeń (SolidWorks: 28, bez litery rewizji `A` z tabelki). Wycięte
  etykiety i teksty tabelki zostają w `raw_texts` z oznaczeniem `[ramka]` / `[tabelka]`.
- NIST: teksty wystające poza pole strony (nagłówki wydruku z nazwą pliku, część opisów i ramek GD&T)
  wcześniej znikały po cichu; teraz trafiają do wymiarów albo nierozpoznanych. Stąd więcej
  nierozpoznanych i np. `3X Ø.875 ±.020ST` (z symbolem tolerancji statystycznej) jako nierozpoznany
  zamiast wymiaru. Wszystkie strony NIST i tak są zablokowane (`BŁĄD`).
- `nist/ftc_10` str. 3: samotne `0` (rzędna) nie jest już doklejane jako tolerancja do `0.1`.

### Po poprawkach ekstrakcji (2026-11-24)

`python -m drawing2cad.cli <pdf> --extract-only` na wszystkich 29 PDF: kod 0, bez wyjątków.
Liczby: przed → po. „Ostrzeżenie ekstrakcji” blokuje część (raport `BŁĄD` bez wywołania AI);
„wiele stron” to uwaga dokumentu (każda część najwyżej `NIEPEWNE`). Inventor (`rysunki/cad_07_1.pdf`,
`cad_08_1.pdf`): wynik identyczny jak przed zmianą (53 wymiary, 0 nierozpoznanych).

| Plik | Części | Wymiary | Nierozpoznane | Ostrzeżenie ekstrakcji | Nr rysunku z tabelki |
|---|---|---|---|---|---|
| cr/E_plate | 1 → 1 | 15 → 31 | 9 → 2 | — | E plate |
| cr/Eccentric_column | 1 → 1 | 6 → 8 | 2 → 0 | — | Eccentric column |
| cr/Hand_Twisted_Nut | 1 → 1 | 7 → 4 | 2 → 0 | — | Hand Twisted Nut |
| cr/Heat_block | 1 → 1 | 20 → 21 | 4 → 4 | — | Heat block |
| cr/M30_Plastic_nut | 1 → 1 | 6 → 3 | 3 → 0 | — | M30 Plastic nut |
| cr/Spacers_for_rollers | 1 → 1 | 6 → 3 | 2 → 0 | — | Spacers for rollers |
| nist/ctc/nist_ctc_01_asme1_rd | 1 → 1 | 0 → 0 | 1 → 4 | brak wymiarów | — |
| nist/ctc/nist_ctc_02_asme1_rc | 3 → 3 | 0 → 0 | 6 → 19 | brak wymiarów, wiele stron | — |
| nist/ctc/nist_ctc_03_asme1_rc | 1 → 1 | 0 → 0 | 1 → 4 | brak wymiarów | — |
| nist/ctc/nist_ctc_04_asme1_rd | 1 → 1 | 0 → 0 | 1 → 5 | brak wymiarów | — |
| nist/ctc/nist_ctc_05_asme1_rd | 2 → 2 | 0 → 0 | 2 → 13 | brak wymiarów, wiele stron | — |
| nist/ftc/nist_ftc_06_asme1_rd | 3 → 3 | 0 → 0 | 0 → 16 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_07_asme1_rd | 4 → 4 | 0 → 14 | 36 → 45 | PMI/aksonometria, cale, wiele stron | — |
| nist/ftc/nist_ftc_08_asme1_rc | 4 → 4 | 0 → 0 | 0 → 20 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_09_asme1_rd | 4 → 4 | 0 → 0 | 0 → 21 | brak wymiarów, cale, wiele stron | — |
| nist/ftc/nist_ftc_10_asme1_rb | 5 → 5 | 21 → 25 | 37 → 64 | PMI/aksonometria, wiele stron | — |
| nist/ftc/nist_ftc_11_asme1_rb | 2 → 2 | 0 → 0 | 0 → 12 | brak wymiarów, wiele stron | — |
| pp/SHRPRO-V1_1000.02-A_Bearing-coupling_spacer_10mm | 1 → 1 | 22 → 5 | 2 → 0 | — | SHRPRO-V1 1000.02 |
| pp/SHRPRO-V1_1000.04-A_Motor_mounting_plate | 1 → 1 | 22 → 5 | 2 → 0 | — | SHRPRO-V1 1000.04 |
| pp/SHRPRO-V1_1100.01-A_Fixed_blade_6_mm | 1 → 1 | 21 → 4 | 2 → 0 | — | SHRPRO-V1 1100.01 |
| pp/SHRPRO-V1_1100.05-A_Bearing_housing_spacer | 1 → 1 | 20 → 3 | 2 → 0 | — | SHRPRO-V1 1100.05 |
| pp/SHRPRO-V1_1110.05-A_Bearing_side_plate_1 | 1 → 1 | 24 → 7 | 2 → 0 | — | SHRPRO-V1 1110.05 |
| pp/SHRPRO-V1_1110.14-A_M12_Threaded_rod_box_1 | 1 → 1 | 19 → 2 | 2 → 1 | — | SHRPRO-V1 1110.14 |
| pp/SHRPRO-V1_1120.13-A_Shaft_1 | 1 → 1 | 36 → 21 | 11 → 1 | — | SHRPRO-V1 1120.13 |
| pp/SHRPRO-V1_1120.14-A_Spacer_4mm | 1 → 1 | 20 → 3 | 2 → 0 | — | SHRPRO-V1 1120.14 |
| pp/SHRPRO-V1_1420.10-A_Gusset_plate | 1 → 1 | 24 → 7 | 2 → 0 | — | SHRPRO-V1 1420.10 |
| pp/v2_03_shredding_axe | 0 → 1 | 0 → 0 | 0 → 0 | brak tekstu | — |
| pp/v2_07_injection_framework_plunger_A4 | 0 → 1 | 0 → 0 | 0 → 0 | brak tekstu | — |
| pp/v2_09_extrusion_barrel_shaft | 1 → 1 | 0 → 0 | 0 → 0 | brak tekstu | — |

Spadek liczby wymiarów w SolidWorks to usunięte śmieci (numery stref ramki 1–8 ×2, masa, materiał), nie
zgubione wymiary; wzrost w Creality to liczby z kropką dziesiętną, które wcześniej znikały po cichu.
Przykłady po zmianie:
- `cr/Eccentric_column`: `0.5, 7.85, 2.4, 0.7, Ø7.2, Ø7.2, Ø5, 10` (8 z 8; `Ø` z krzywej).
- `cr/E_plate`: 31 wymiarów, m.in. `2xØ5`, `2xØ5.4`, `2*M3` (gwint ×2), rzędne `23.5, 14.35, 33.25…`;
  nierozpoznane tylko dwa rzędne `0`.
- `pp/1120.13 Shaft 1`: `610` z `+0,01/-0,01`, `22,5` z `+0,005/-0,005`, `300` z `0,00/-0,01`,
  `Ø45 g6 -0,009/-0,025` (×2), masa 9 kg i strefy poza wymiarami; nierozpoznane: `G4 +0,011/+0,006`
  (tak jest na rysunku).
- `pp/1000.02`: dokładnie `10, 15, 26,6, Ø65,2, R22,6`; tabelka: nr `SHRPRO-V1 1000.02`,
  nazwa `Bearing-coupling spacer 10mm`, skala `1:1`.
- `cr/Heat_block`: nierozpoznane `Ø5.6 X 90°` (pogłębienie) i trzy rzędne `0`.
- `nist/ftc_10`: strony 2–3 zablokowane (tekst wzdłuż osi aksonometrycznych; tam był błędny `43X Ø3.5`),
  strony 1, 4, 5 przechodzą z uwagą „wiele stron” i dużą listą nierozpoznanych (ramki GD&T).

### Przed poprawkami (rekonesans)

`drawing2cad.exe <pdf> --extract-only` (bez AI), 2026-11-24.
Wszystkie przebiegi zakończyły się kodem 0, bez wyjątków. Żaden plik nie dał `drawing_no`, `name` ani `title_block` (pozostają null).
„Śmieci” w kolumnie wymiarów to numery stref ramki (1–8), masa z tabliczki, materiał.

| Plik | Wektorowy? (tekst) | Części | Wymiary wyciągnięte | Problemy |
|---|---|---|---|---|
| pp/1000.02 Bearing-coupling spacer | tak | 1 (cała strona) | 22 (5 prawdziwych; 16 numerów stref, masa 0,017) | Ø (glif-krzywa) gubiony, 26,6/65,2 jako długości; strefy 1–8 ×2 jako wymiary |
| pp/1000.04 Motor mounting plate | tak | 1 | 22 (5 prawdziwych) | strefy; masa 1,38 jako wymiar |
| pp/1100.01 Fixed blade 6 mm | tak | 1 | 21 (4 prawdziwe) | strefy; masa 0,197 |
| pp/1100.05 Bearing housing spacer | tak | 1 | 20 (3 prawdziwe) | strefy; masa 0,01; Ø jako długość |
| pp/1110.05 Bearing side plate 1 | tak | 1 | 24 (7 prawdziwych) | strefy; masa 3,21 |
| pp/1110.14 M12 Threaded rod | tak | 1 | 19 (2 prawdziwe) | strefy; masa 0,29 |
| pp/1120.13 Shaft 1 | tak | 1 | 36 (~19 prawdziwych) | `Ø45 g6 -0,009/-0,025` nierozpoznane (pasowanie z tolerancją piętrową, brak Ø); `610 +0,01/-0,01` zgubiona górna odchyłka; `0,00 / 300 -0,01` rozbite; `G4 +0,011 +0,006` (wpust) nierozpoznane; masa 9 kg jako wymiar |
| pp/1120.14 Spacer 4mm | tak | 1 | 20 (3 prawdziwe) | strefy; masa |
| pp/1420.10 Gusset plate | tak | 1 | 24 (7 prawdziwych) | strefy; masa 0,01; t=2 OK |
| pp/v2_03 shredding axe | **nie** (tekst jako krzywe) | 0 | 0 | brak ostrzeżenia, że PDF nie ma tekstu |
| pp/v2_07 plunger | **nie** | 0 | 0 | j.w. |
| pp/v2_09 barrel shaft | **nie** | 1 (pusta) | 0 | j.w. |
| cr/Eccentric_column | tak | 1 | 6 (2 prawdziwe z 8) | **0.5, 7.85, 2.4, 0.7, 7.2 po cichu odrzucone** (liczby z kropką traktowane jak nr rysunku); Ø (krzywa) gubiony → Ø5 jako długość 5 |
| cr/Spacers_for_rollers | tak | 1 | 6 | 5.5, 8.35 odrzucone; „6061” (materiał) jako wymiar |
| cr/M30_Plastic_nut | tak | 1 | 6 | `M30*1.5` nierozpoznane (gwiazdka zamiast ×) |
| cr/Hand_Twisted_Nut | tak | 1 | 7 | 59.91 odrzucone |
| cr/Heat_block | tak | 1 | 20 | 5.5, 6.05, 1.5 odrzucone; „6061” jako wymiar |
| cr/E_plate | tak | 1 | 15 | 14 wartości z kropką odrzuconych (23.5, 9.5, 11.9, 14.35…); `2*M3` nierozpoznane |
| nist/ctc_01..05, ftc_06, 08, 09, 11 | **nie** (wymiary jako krzywe, tylko tytuły) | 1 na stronę (1–4) | 0 | widoki 3D, nie rzuty; brak ostrzeżenia |
| nist/ftc_07 (AutoCAD, cale) | tak | 4 (= 4 strony tej samej części) | 0 | `.250`, `Ø.250 +.003-.000`, `4X Ø.250` nierozpoznane: brak zera przed kropką |
| nist/ftc_10 (AutoCAD, mm) | tak | 5 (= 5 stron tej samej części) | 21 | `8X Ø2.5 ±0.154` (sklejone z sąsiednim „4”), `Ø3.5 G64`, `x35 ±1Ø5.5 ±0.2` (głębokość/pogłębienie z fontu AIGDT); ramki GD&T `0.65ABC` w nierozpoznanych |
