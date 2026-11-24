# Ręczne specyfikacje części (NIE są odpowiedziami AI)

Pliki `01.01.0X.json` zostały napisane ręcznie
na podstawie rysunku **i zbioru testowego** (`ground_truth.json`). Służą tylko do:

- sprawdzianu biblioteki klocków (etap 2): JSON -> bryła -> `porownaj.py`,
- testów weryfikatora i pętli poprawek, gdzie trzeba mieć znaną poprawną specyfikację.

Nie wolno ich używać jako cache odpowiedzi AI ani do benchmarku (etap 8).
Prawdziwy cache API powstaje dopiero przy pierwszym wywołaniu modelu
(`--cache-dir`) i jest kluczowany skrótem SHA promptu i obrazu.
