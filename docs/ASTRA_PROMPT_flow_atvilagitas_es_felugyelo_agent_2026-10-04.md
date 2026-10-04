# UTASÍTÁS – Astra: a teljes flow kötelező átvilágítása, javítása, és ellenőrző agent indítása

Ez végrehajtandó parancs, nem kérdőív és nem tervezési feladat. Járd végig a teljes flow-t az alábbi lista szerint, élő futáson. Ahol elakadást, hibát, hiányt vagy le nem fejlesztett részt találsz:

1. találd ki a megoldást;
2. fejleszd le;
3. teszteld;
4. élesítsd;
5. ellenőrizd újra élőben.

Addig folytasd, amíg az adott pont élőben bizonyítottan működik. Nem elfogadható válasz, hogy valami „nincs kész”, „blokkolva van” vagy „nem igazolt”: ezek javítandó feladatok.

Párhuzamosan indíts egy független **ellenőrző agentet** (lásd a 3. pontot), amely felügyeli ennek végrehajtását.

## KÖTELEZŐ

- Minden ponton végig kell menni, és mindent meg kell javítani, ami nem működik.
- Ha nem jönnek pénzes ajánlások, azonnal meg kell keresni és ki kell javítani az okát: bekötés, szűrő, küszöb, validáció, modell vagy adat.
- Minden be nem kötött Stake-fogadástípus mögé ki kell fejleszteni a teljes rendszert: adatot, modellt, árazást, elszámolást, tanulást és kiadást.
- **Napi minimum 100 különálló, téttel (1–5 USD) kiadott ajánlás** kell az öt ligában összesen, a legjobb tudás alapján kiválasztva. Felső plafon nincs.

## 1. Átvilágítandó flow – minden pontot élő adaton ellenőrizni, és ha kell, javítani

**1.1 Ligák és meccsek szkennelése**

- Az NFL, NHL, MLB, NBA és WNBA minden meccse, amely a Stake-en a gördülő 48 órás ablakban kezdődik, bekerül-e minden ciklusba?
- Ha egy meccs kimarad, javítsd: feed, párosítás, lapozás, előszezon/rájátszás, horizont.

**1.2 Fogadástípusok meccsenként**

- Minden meccsnél a Stake összes lapjának összes piactípusa le van-e töltve, értelmezve és árazva? Ez minden időszakra és variánsra vonatkozik, a játékos-propokra, a kombinált, a speciális és a statisztikai piacokra is.
- Ahol nem, ott fejleszd le a hiányzó részt.

**1.3 Kiértékelés**

- Minden piactípusnál működik-e a modell, a valószínűség, a fair odds és az érték számítása?
- A szükséges bemenetek ténylegesen a modellbe mennek-e? Ide tartozik a sérülés, a lineup, a kezdődobó, a kapus, a QB és az időjárás.
- A modell–piac súlyozást ligánként és piaccsaládonként tanulja-e a rendszer?
- Ahol a modell gyenge, javítsd. Ne blokkold a piacot.

**1.4 Kiválasztás élő, pénzes fogadásra**

- A teljes piackínálatból a legjobb jelöltek kapnak-e tétet, 1–5 USD-s, tanult teljesítményen és magabiztosságon alapuló tétezéssel, a beállított limiteken belül?
- Teljesül-e a napi 100?
- Ha nem, keresd meg, melyik szűrő vagy modell fogja vissza, és javítsd.

**1.5 Konzisztencia**

- Ugyanaz a döntési logika fut-e a backendben, a kiadásban, a dashboardon és a naplóban?
- Nincs-e duplikált, elveszett vagy ellentmondó tipp?
- Ugyanazt a meccset és piacot minden komponens azonosan azonosítja-e?

**1.6 Stabilitás**

- A ciklusok hiba nélkül, kimaradás nélkül futnak-e?
- A szolgáltatás újraindulás után helyreáll-e?
- A forráshibák (Stake, ESPN, egyéb) kezelve vannak-e úgy, hogy a kiadás ne álljon le?

**1.7 Bet lifecycle**

- Minden kiadott pénzes ajánlás a kiadás pillanatában megkötött fogadásként rögzül-e, a kiadáskori téttel és oddsszal?
- Végigmegy-e az életciklus: kiadva → megkötve → meccs lezárul → elszámolva (nyert / vesztett / push / void) → P&L?
- Nem marad-e elszámolatlan fogadás?

**1.8 Eredménykiértékelés**

- Minden lezárt fogadásnál számolódik-e a WR, a ROI és a kumulatív P&L?
- Számolódik-e a kalibráció (Brier, log loss) és a CLV, ligánként és piactípusonként?

**1.9 Öntanítás és fejlődés**

- Az eredmények automatikusan visszahatnak-e a modellsúlyokra, a modell–piac súlyozásra, a liga × piac preferenciára és a tétméretezésre?
- Mérhetően javul-e a kiválasztás?
- Ahol a hurok nincs zárva, zárd be.

## 2. Kötelező kimenet: ligánkénti, névszerinti fogadástípus-lista

Ezt a listát a futó rendszer adataiból kell generálni (kódból, nem kézzel írva). Legyen elérhető a dashboardon, és mentsd fájlba is (`docs/`).

Ligánként (NFL, NHL, MLB, NBA, WNBA) minden fogadástípus **pontos Stake-megnevezéssel** és időszakkal szerepeljen, és mindegyiknél:

- bekötve (letöltés + értelmezés) – igen/nem;
- kiértékelve (modell + árazás) – igen/nem;
- pénzes kiadásra jogosult – igen/nem;
- ma kiadott pénzes ajánlások száma;
- elszámolva és tanulásba visszacsatolva – igen/nem.

Ligánként legyen összesítés is: hány fogadástípust értékel ki az engine, és hányra adhat pénzes ajánlást. Ahol bármelyik oszlop „nem”, az a következő javítási feladat, és ezt végre is kell hajtani.

## 3. Ellenőrző agent – kötelező indítani

Indíts egy külön, független ellenőrző agentet a meglévő AI-delegációs és felügyeleti struktúrában (`ai_delegation`, `supervisor`, `engineering_loop`), amely ennek a feladatnak a végrehajtását felügyeli.

**Feladata:**

1. Minden fejlesztési lépés után és folyamatosan, ciklusonként élőben ellenőrzi az 1.1–1.9 pontokat és a 2. pont listáját.
2. Csak élő bizonyítékot fogad el: futó ciklus, adatbázis-rekord, dashboard-állapot vagy teszteredmény. Egy állítás vagy terv nem bizonyíték.
3. Ha egy pont nem teljesül, javítási feladatot ad vissza Astrának konkrét hibaleírással, és ellenőrzi, hogy a javítás elkészült-e és élőben működik-e.
4. Külön figyeli a napi 100 pénzes ajánlás ütemét. Ha a nap folyamán elmarad, azonnal javítási feladatot ad.
5. Nem engedi „késznek” jelölni a feladatot, amíg minden pont élőben teljesül.
6. Állapotát a dashboardon mutatja pontonként (teljesül / nem teljesül / javítás alatt), az utolsó ellenőrzés idejével.

Az ellenőrző agent nem fejleszt és nem kapcsol ki funkciót. Ellenőriz, és visszaküldi a hibát javításra.

## 4. Kész akkor van, ha

- az 1.1–1.9 minden pontját az ellenőrző agent élőben teljesültnek jelöli, több egymást követő ciklusban;
- a 2. pont listája generálva van, és minden fogadástípus minden oszlopa „igen”;
- a gördülő 24 órában legalább 100 különálló, téttel kiadott ajánlás teljesül;
- a tesztek zöldek, és a szolgáltatás újraindítás után is stabilan fut.

**Riport magyarul, röviden:**

- pontonként mit találtál, mit javítottál, commit hash-ekkel;
- a 2. pont listájának elérési útja;
- az ellenőrző agent aktuális állapota pontonként;
- ha valami még nem teljesül: a konkrét ok, és az a javítás, amin már dolgozol.
