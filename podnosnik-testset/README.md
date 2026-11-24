# Zbiór testowy: podnośnik nożycowy (rys. 01.01.00)

Wzorcowe dane i modele 3D do testowania generatora i checkera CAD.

## Zawartość
- `ground_truth.json`: wszystkie wymiary z rysunków, BOM, niejednoznaczności rysunku i przyjęte założenia
- `wzorce/czesci.py`: modele wzorcowe 6 części w CadQuery, zbudowane tylko z wymiarów rysunku
- `step/`: wyeksportowane modele wzorcowe (otwierają się w Inventorze)
- `zbuduj_wzorce.py`: buduje wzorce i sprawdza gabaryty z `ground_truth.json`
- `porownaj.py`: porównuje dowolny model STEP ze wzorcem (nadmiar i brak materiału). Sam wyrównuje modele, więc układ współrzędnych nie musi się zgadzać
- `test_porownaj.py`: test na wzorcach obróconych i przesuniętych losowo oraz na celowo zepsutych

## Uruchomienie
```
pip install cadquery
python zbuduj_wzorce.py
python porownaj.py step/01.01.06_ramie.step moje_ramie.step
```

## Ograniczenia
- Odbicie lustrzane (część „lewa” zamiast „prawej”) nie jest uznawane za zgodne.
- Skrypt mówi, ILE materiału się różni, ale jeszcze nie który wymiar (następny etap).
- Gwinty są modelowane jako walce o średnicy nominalnej.
