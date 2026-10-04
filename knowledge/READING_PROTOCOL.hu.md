# Könyvfeldolgozási protokoll

Aktuális státusz: lásd `../docs/STATUS.hu.md` és `reading_v2/reading_ledger.jsonl`; a hozzáférhető másolat rendelkezésre álló tartalma feldolgozva; az online kiegészítés külön fázis.
Eredeti hely: `/Users/belavarga/Downloads/Betting_tips`.
Hozzáférhető másolat: `/Users/belavarga/Betting_tips_books`.

## Teljes feldolgozás

1. Teljes fájlleltár és SHA-256; cím, szerző, kiadás, nyelv, formátum,
   oldalszám, duplikátumok és hiányzó kötetek azonosítása.
2. PDF-ek oldalszintű kinyerése; szkennelt oldalak OCR-je. Az új `extract_library.py` PDF/TXT/MD/EPUB/MOBI/AZW3 fájlokat kezel.
   A nem támogatott formátumokat hibaként naplózza, nem hagyja el észrevétlenül.
3. Tartalomjegyzék és fejezethatárok rekonstruálása. A PDF oldalszám és a
   nyomtatott oldalszám külön mező, mert eltérhetnek.
4. Minden fejezet tényleges olvasása, képletek/táblázatok/ábrák vizuális
   ellenőrzése. A sparse page jelzés csak heurisztika: egy képlet hibás
   kinyerését a hosszú szöveg sem zárja ki.
5. Fejezetenként saját szavas összefoglaló; állítások és feltételezések,
   megvalósítható eljárások és az őket cáfoló tesztek.
6. Könyvek közötti szintézis, egymásnak ellentmondó tanácsok, elavult
   platformszabályok ellenőrzése aktuális elsődleges forrással.
7. Követelmény → forrásoldal → kódmodul → teszt összekapcsolása.

## Olvasottság bizonyítása

Az automatikus `manifest.json` csak kinyerési jelentés. Újrafuttatáskor
újraépül; ne ebben tároljuk az olvasási munkát. A tartós `reading_ledger.jsonl`
fájlba külön kerülnek az olvasási bejegyzések, ilyen mezőkkel:

```json
{"book_sha256":"...","chapter":"...","pdf_pages":[1,2,3],"printed_pages":"...","reviewed_at":"...","summary_path":"...","visual_review_pages":[],"unresolved":[]}
```

Az olvasottság az összes rendelkezésre álló fejezet lefedése és a kinyerési/olvasási hiányok tisztázása után jelölhető teljesnek. A forrás empirikus bizonytalanságait és a még nem reprodukált állításokat külön nyitottként kell őrizni; a teljes olvasás nem teljes tudományos igazolás. Egy keresés találatainak átnézése nem teljes olvasás.
Egy hosszú könyvtár feldolgozása több munkamenetet igényelhet: a ledger
biztosítja, hogy pontosan ott folytatódjon, ahol a feldolgozás megállt.

## Tudáskártya sablon

```text
ID:
Állítás saját szavakkal:
Forrás: könyv hash / fejezet / PDF-oldal / nyomtatott oldal
Típus: definíció | tétel | empirikus állítás | szerzői vélemény | saját következtetés
Feltételezések:
Képlet és változók:
Mikor nem alkalmazható:
NBA / MLB / NHL / NFL / WNBA következmény:
Adatigény és ismertségi idő:
Tesztelhető hipotézis:
Ellentmondó forrás:
Megvalósítási kapcsolat:
Ellenőrzési állapot:
```

Tematikus fejezetek: valószínűség és fair ár; bookmaker margin;
kalibráció; sportági modellezés; időbeli adatszivárgás; EV és költségek;
bankroll és korreláció; piaci mikrostruktúra; pszichológiai torzítások;
visszatesztelés és többszörös hipotézisvizsgálat; működtetés és tanuló ciklus.

A teljes kinyert könyvszöveg helyi keresésre szolgál; nem kerül publikálásra.
Nem hozunk létre kitalált könyvösszefoglalót vagy hivatkozott oldalszámot.
