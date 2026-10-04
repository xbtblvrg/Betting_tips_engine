# UTASÍTÁS – Astra: teljes rendszerellenőrzés a mai fejlesztések után, kötelező javítással

Ez végrehajtandó parancs. A mai fejlesztések és módosítások után ellenőrizd, hogy a rendszer rendben felállt-e, és minden funkció működik-e. Teljes ellenőrzést csinálj, élő futáson, nem a kód olvasása alapján.

**Ami nem jó, azt kötelező javítani.** Ehhez:

1. kiderítem az okot;
2. javítom;
3. tesztelem;
4. élesítem;
5. újra ellenőrzöm élőben.

Ezt addig kell ismételni, amíg minden pont teljesül. A „nem kritikus”, „később”, „ismert korlát” minősítés nem elfogadható lezárás.

## 1. Felállás és verzió

- Minden szolgáltatás fut és egészséges: web/dashboard, engine-ciklus, supervisor, learning, development, release, schedule és a többi `scripts/*_service.py`. A health endpoint rendben van.
- A futó kód megegyezik a legutolsó committal. Nincs régi verzió a memóriában, nincs félbemaradt release, nincs nem commitolt módosítás.
- Újraindítás után minden komponens magától helyreáll és tovább fut.
- A konfiguráció egységes és érvényes (`config/*.json`). Minden komponens ugyanazokat az értékeket olvassa.

## 2. Tesztek

- A teljes tesztcsomag zöld: `pytest` és `npm test`.
- A mai módosítások mindegyikéhez van teszt. Ahol nincs, írd meg.

## 3. Funkcionális végigjárás élő adaton

Az alábbi lépéseket ellenőrizd az NFL, NHL, MLB, NBA és WNBA mindegyikére:

1. **Meccsek szkennelése:** minden Stake-meccs a gördülő 48 órás ablakban bent van.
2. **Fogadástípusok:** minden Stake-kínálatú kötelező fogadástípus le van töltve, értelmezve és kiértékelve, minden meccsen.
3. **Modellek:** minden modell betöltve, friss, és értelmes valószínűséget ad. Nincs NaN, nincs szélsőérték-hiba, nincs elavult modellverzió.
4. **Kiválasztás és tét:** a legesélyesebb fogadások pénzes ajánlásként kimennek. Működik az 1–5 USD tétezés és a dinamikus meccsenkénti limit, és a napi minimum 100 ütemezetten teljesül.
5. **Bet lifecycle:** minden kiadott pénzes ajánlás azonnal megkötött fogadásként rögzül, és végigmegy az életcikluson: kiadva → megkötve → lezárva → elszámolva → P&L.
6. **Eredménykiértékelés:** a WR, a ROI, a kumulatív P&L, a kalibráció és a CLV számolódik.
7. **Öntanítás:** az eredmények visszahatnak a modellekre, a súlyozásra, a liga × piac preferenciára és a tétezésre.
8. **Dashboard:** minden adat friss és helyes rajta.

## 4. Guardok ellenőrzése

- Listázd az összes guardot, kaput, validációt és szűrőt, ami ajánlást, piacot vagy meccset kiejthet.
- Mindegyikre élő adatból mérd meg, hány jelöltet ejt ki és milyen okkal.
- Ellenőrizd mindkét irányt:
  - **Túl szigorú-e?** Indokolatlanul blokkol-e fogadástípust, meccset vagy ligát, vagy viszi-e nullára a kiadást.
  - **Átenged-e hibásat?** Rossz vonalat, rossz oldalt, elavult árat, hibás időszakot, duplikátumot, rossz elszámolási szabályt.
- Ami túl szigorú, azt lazítsd vagy javítsd. Ami hibásat enged át, azt javítsd. Minden guard döntése legyen naplózva, okkóddal.

## 5. Agentek munkájának ellenőrzése

Ellenőrizd az összes agentet és automatikus hurkot: a supervisort, az engineering/development loopot, a learning workert, a release coordinatort, az AI-delegációt és a felügyelő/ellenőrző agentet.

Mindegyiknél nézd meg:

- fut-e, nem akadt-e el, és nincs-e végtelen ciklusban;
- azt csinálja-e, ami a feladata, és ténylegesen végre is hajtja-e, nem csak tervez vagy naplóz;
- a kimenete helyes és élesbe került-e;
- nincs-e ellentmondás vagy ütközés két agent között, például az egyik visszacsinálja, amit a másik javított;
- a felügyelő agent valóban élő bizonyítékot ellenőriz-e, és a hibákat visszaküldi-e javításra.

Ami nem működik, azt javítsd.

## 6. Runtime check

- Nincs futásidejű hiba, kivétel vagy újraindulási hurok a logokban a mai módosítások óta.
- A ciklusidő tartja a beállított ütemet, és a ciklusok nem maradnak ki.
- Az adatfrissesség rendben van: az árak, a meccsek és az eredmények nem elavultak.
- Minden külső forrás elérhető (Stake, ESPN, NHL és a többi), a hibakezelés és az újrapróbálás működik.
- Az erőforrások rendben vannak: CPU, memória, lemez, adatbázis-zárolások, WAL-méret, logrotáció. Nincs szivárgás és nincs növekvő késleltetés.
- Az időzónák és időbélyegek konzisztensek (UTC tárolás, helyes megjelenítés).

## 7. Konzisztencia check

- Ugyanaz a tipp, tét, odds és állapot szerepel az engine-ben, az adatbázisban, a kiadási naplóban, a fogadási nyilvántartásban és a dashboardon.
- Nincs duplikált, elveszett, árva vagy ellentmondó rekord. Ide tartozik:
  - a kiadott, de nem rögzített tipp;
  - a rögzített, de el nem számolt fogadás;
  - az elszámolt, de tanulásba nem került fogadás.
- A meccs-, piac- és kimenetel-azonosítás minden komponensben egységes.
- A P&L, a WR és a ROI ugyanaz minden helyen, ahol megjelenik.
- A konfigurációban rögzített szabályok (limitek, tétezés, napi minimum) ténylegesen így futnak.

## 8. Riport (magyarul, röviden)

Pontonként (1–7):

- rendben / hibás volt;
- mit találtál;
- mit javítottál, commit hash-sel;
- a javítás utáni élő ellenőrzés eredménye.

A végén:

- a teljes rendszer állapota;
- ha valami még nem teljesül: a konkrét ok és a folyamatban lévő javítás.

Késznek csak akkor jelölheted, ha minden pont élőben teljesül, és ezt a felügyelő agent is megerősítette.
