# B06 — Football Analytics with Python & R

Eric A. Eager és Richard A. Erickson, O’Reilly, Early Release, 2022. július 21. A mellékelt EPUB tíz egysége, benne hat fejezet, teljesen végigolvasva. Ez a rendelkezésre álló korai kiadás, nem a később megjelent teljes könyv. Szöveg: 254 011 karakter. A 42 beágyazott képet hét áttekintő lapon megvizsgáltam; a felületi képernyőképek apró szövegét nem mind írtam át. Az 5–6. fejezet MathML-forrását külön ellenőriztem.

## 1. fejezet — Eszközök és reprodukálhatóság

Python/R, környezet, Jupyter, konzol, szkript, adattípusok és adatkeretek bevezetése. A reprodukálható program és az explicit adatbetöltés hasznosabb alap, mint a kézi táblázatmódosítás. A két nyelv indexelése, hatványozása és objektumkezelése eltér: a hasonló alakú kód nem feltétlenül azonos művelet. Az R statisztikai, Python termékfejlesztési erőssége a szerzők gyakorlati preferenciája, nem kizárólagos alkalmassági szabály.

Az nflscrapR/nflfastR történeti adatforrásként jelenik meg, EPA és győzelmi valószínűség kontextusával. Az aktuális elérhetőséget, sémát és felhasználási feltételeket majd külön kell ellenőrizni. A könyv olvasása nem jelenti ezek bekötését.

## 2. fejezet — Feltáró elemzés

Kérdés → adat → ellenőrzés → ábra/összefoglaló → modell → értelmezés, iteratívan. A 2020-as GB–DET második heti mérkőzés 62 passzkísérlete és 38 sikeres passza demonstráció. A yards_after_catch hiánya itt rendszerint sikertelen passzhoz kapcsolódik: strukturális hiány, nem automatikusan nulla. A negatív air_yards önmagában érvényes adat. A csak sikeres passzokra szűrt összefüggés nem az összes passzkísérlet összefüggése.

Hisztogram-intervallumok, csapat/fogási irány szerinti bontás, boxplot, pontdiagram, szín és alak, feliratok. A binméret változtatása megváltoztatja a látható mintázatot. A boxplot jelölt pontja nem automatikusan adathiba; az összesített minta elfedheti a csapatonkénti eltéréseket. A facetek és a kategóriák segítenek, de egy mérkőzés kis mintájából nem következik általános prediktív törvény.

## 3. fejezet — Adatszerzés

CSV, Excel, webtáblák, útvonalak, fejléc, elválasztó, adattípus, head/tail/info/describe. Érdemes a nyers adatot helyben elmenteni, hogy egy új futás ne egy megváltozott weboldalból induljon. A szerzők a draft- és combine-adatokat használják példának. Egy lezárt draft tényei rögzítettek, de a hozzákapcsolt karrierértékek tovább változnak: a kétféle időbeliséget külön kell kezelni.

A 2000–2022-es draftpélda kapcsán saját módszertani következtetés: a mai career AV/DrAV múltbeli döntés bemeneti változójaként jövőbeli információt szivárogtatna be. Az újabb játékosok rövid karrierje cenzorálás, nem feltétlenül gyengébb tehetség. Hiányzó karrierértéket csak ellenőrzött jelentés alapján szabad nullára cserélni.

Konkrét hibák a korai kiadásban: a Python `Selection = df.iloc[:,0]` a bemutatott sémában a kört, nem az összesített Pick értéket választja; ezt a kinyomtatott 1-es és 7-es értékek is mutatják. A Pro Football Reference és a PFF neve/AV-hozzárendelése összekeveredik. Mentési és visszaolvasási fájlnevek eltérnek, szerepel kisbetűs `false`, valamint Excel-függvénynevekben elírás. Ezeket nem szabad működő referenciakódként átvenni. A macOS fájlnév-kisbetű/nagybetű viselkedése a fájlrendszertől függ, nem általánosítható a könyv mondatából.

## 4. fejezet — Adattisztítás és összekapcsolás

Szűrés, feltételek, zárójelezés, isin/%in%, numerikus konverzió, hiányzó érték, oszlopok kiválasztása/átnevezése, rendezés és négyféle join. A posztcímkék összevonása csak szemantikai egyeztetéssel helyes; a DE/OLB/edge nem minden szerepkörben felcserélhető.

Az 5000 air_yards mint hibás érték felismerhető, de az 50-re javítás ellenőrizendő hipotézis. A javítás és törlés hatását össze kell vetni, a nyers értéket és a javítás indokát meg kell őrizni. Egy oktatópélda 109 yardos küszöbe nem univerzális adatszabály.

Join előtt kulcsegyediség, kardinalitás és időbeliség; utána sorszám, hiányzó pár és oszlopséma ellenőrzése indokolt. Az inner join sok-sok kapcsolat esetén növelheti is a sorok számát. A Python away-team összeillesztés végén másodszor is home_team lesz az oszlop neve: ez a kinyomtatott eredményben látható hiba. Egyes R-példák a base `|>` csövet a magrittr `.` helyőrzőjével keverik; a write.csv egyik példájából hiányzik az adatobjektum. Kódolás előtt futtatható példákkal kell ellenőrizni.

## 5. fejezet — Leíró statisztika és bizonytalanság

Átlag, medián, módusz, kvantilis, terjedelem, variancia, szórás, standard hiba, konfidenciaintervallum. Különösen fontos az egyedi kimenetek szórása és a becsült átlag bizonytalansága közötti különbség. A gyakorisági CI ismételt minták lefedéséről szól; a p-érték nem annak valószínűsége, hogy a modell téves.

Ellenőrzött számpélda-hibák: a kezdeti tíz elem összege 62, miközben a könyv 68/11-et számol; a rendezett listába bekerül még egy 6-os. A MathML is ezt az inkonzisztenciát tartalmazza. A standard hiba képletében valóban szerepel a négyzetgyök: 5,81/√11 ≈ 1,75; ez nem könyvhiba, hanem a sima szövegben elvesző jelölés. A 6,18 ± 3,43 végpontjai 2,75 és 9,61, nem a nyomtatott 2,38 és 9,24. Kis mintás, ismeretlen szórású normális modellnél a pontos intervallumhoz t-kritikus érték kell; az 1,96-os példa közelítés.

A YAC-összesítésben az R n() 62 sort számol, miközben a pandas count() 38 nem hiányzó értéket. A csapatonkénti 32/30 kísérlet és 20/18 megfigyelt YAC nem ugyanaz a nevező. A standard hibához a ténylegesen felhasznált megfigyelésszám és függőségi struktúra számít. CI-k átfedése nem önálló különbségteszt. A szöveg túl általánosan állít különbséget pandas/tidyverse aggregálási képességek között.

Az 5-1. ábra tengelye air yards feliratú, a képaláírás YAC-ot mond, és az ábrabeli átlagvonal sincs összhangban a megadott 6,18-cal. A forrás belső következetessége itt különösen gyenge.

## 6. fejezet — Lineáris regresszió

Válaszváltozó, magyarázó változó, konstans modell, csoportátlagok, design matrix, egyenes illesztése, reziduum, standard hiba, t-próba, R², centerezés, skálázás, ábrás kommunikáció. GLM/GAM/vegyes modellek megemlítve, de a mellékelt kiadásban nincsenek részletesen kidolgozva.

A sikeres passzt a nem hiányzó YAC-ból állítja elő, a passzyardot siker esetén air_yards + YAC, különben nulla alakban. Ez csak a példabeli adatjelentéssel helyes; tetszőleges feedre nem általánosítható. A konstans modell átlaga 7,806452, 62 megfigyelés mellett 61 szabadságfokkal; a közeli prózában szereplő 38−1=37 nem ehhez az illesztéshez tartozik.

A csapatkontrasztos és a konstans nélküli cellaátlagos paraméterezés ugyanazokat a csoportátlagokat adhatja. A pooled variancia miatt a standard hibák eltérhetnek a csoportonként külön becsültektől. A konstans nélküli modell közöletlen R²-e nem hasonlítható a konstansos modell szokásos R²-éhez.

A GB 2018–2020-as, 52 mérkőzéses példájában a becsült éves pontnövekedés 4,0805, R² ≈ 0,122, a meredekség 95%-os intervalluma kb. 0,972–7,189. Ez történeti összefüggés; önmagában nem jövőbeli előny vagy oksági hatás. A szerzők a COVID és a nézők hiányának zavaró szerepére is kitérnek. A vártnál jobb reziduum nem automatikusan tartós képesség.

A Year−2018 centerezés a tengelymetszetet kb. 22,051-re és a numerikus kondicionálást változtatja, a meredekséget nem. A könyv ezzel ellentétes mondata hibás. A válasz standardizálása nem teszi normálisabbá az eloszlás alakját; a képek is ugyanannak az alaknak az átskálázását mutatják. A skálázás nem javítja önmagában a magyarázó erőt. Későbbi prediktív értékelésnél az előfeldolgozást csak a tanulóhalmazon szabad illeszteni.

A reziduumok normalitása egyes inferenciák feltétele; az OLS pontbecslését nem minden helyzetben teszi érvénytelenné egy eltérő hisztogram. A nulla alsó korlát és a felső korlát hiánya önmagában nem bizonyít túlszóródást. A 6-7. képaláírás egy 2022-es DET-meccset említ a tényleges 2018–2020-as GB-adatsor helyett. A forrás egyes elméleti egyenletei hiányosak; ezeket nem rekonstruált könyvtartalomként kezeltem.

## Kapcsolódás az eddig olvasott anyaghoz

Ez a kötet adatkezelési és statisztikai alapozás, nem kész fogadási modell. Jól kiegészíti Mack modellpéldáit és Buchdahl tesztelési problémáit: hibás kulcs, hiánykezelés vagy időbeli illesztés mellett a szofisztikált modell is félrevezető. A könyv alapján még nem áll rendelkezésre validált NFL-előrejelzés, és a másik négy kért ligára sincs belőle kész modell.

Az olvasási tanulságok nem rendszerterv. Online frissítés, a végleges kiadás eltéréseinek vizsgálata és a hivatkozott kód futtatása még nem történt meg.
