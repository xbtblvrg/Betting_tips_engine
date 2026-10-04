# B19 — Sharper: A Guide to Modern Sports Betting

Pokerjoe / @truepokerjoe. Copyright 2016; a mellékelt előszó 2017. szeptemberi közzétételre is hivatkozik. A teljes rendelkezésre álló MOBI: 36 egység, előszó, 31 fejezet, lábjegyzetek; 218 520 karakter végigolvasva. Az egyetlen beágyazott kép a borító, külön megtekintve. Az alábbiak a könyv tanulságai és saját forráskritikám, nem kész rendszerterv.

## Alapgondolatok — előszó, 1–3. fejezet

A fogadás szerződéses kimenetből és árból áll. A kedvenc csapat, a legvalószínűbb győztes és a pozitív várható értékű fogadás nem ugyanaz. Egyetlen végeredmény a lehetséges kimenetek egy realizációja; a jó döntést nem teszi automatikusan rosszá egy veszteség. A számszerű becslés megakadályozza, hogy tetszőleges történetből tetszőleges oldalt lehessen indokolni. Saját szám készítése a piaci ajánlat megtekintése előtt segíthet az árhoz igazodó horgonyzás elkerülésében.

A szerző élesen elválasztja a saját sportértékelést és a más könyvek áraihoz viszonyított kereskedést. Nyilvános trendeknél arra figyelmeztet, hogy egy valós összefüggés sem automatikusan fogadható, ha az ár már tartalmazza. A bemutatott college-total és NFL-turnover rendszerek történeti állítások; a küszöbök keresése, a próbált változatok száma, a pont-in-time adatok és az érintetlen tesztminta nincsenek auditálva. A gyengülő találati arány önmagában nem bizonyít piaci alkalmazkodást: lehet mintavételi ingadozás is. A szerző 500 tippes 56%-os rekordja szintén saját közlés.

Korlát: a spreadet a mediánnal azonosítani csak megfelelő árazási és szimmetriafeltételekkel lehet. Az átlag, a medián, a legvalószínűbb különbség és a kiírt hendikep eltérhet. A különböző alternatív vonalak piaci egyenértékűsége nem jelenti, hogy egy más eloszlást becslő saját modell alatt azonos EV-jük van. A kockapélda szemléltető, nem bizonyítja a sportpontszámok eloszlását.

## Ár, piac és végrehajtható ajánlat — 4–11. fejezet

Amerikai odds átváltása: negatív −a esetén d=1+100/a és nullszaldós feltételes találati arány a/(a+100); pozitív +a esetén d=1+a/100 és 100/(a+100). Nyomdai hiba: −110 nem 11/10 törtodds, hanem 10/11; 11/10 decimálisan 2,10 lenne.

Az egyik oldal ára nem mondja meg a kétoldali árrést. A −200/+170 kisebb arányos margin lehet, mint a −110/−110. A könyv 1/sum(1/d)−1 képlete a fogadói nézőpontból negatív kiegyenlített elméleti hozam; nem azonos a bookmaker tényleges profitjával. A −150/+110 ajánlat favoritra jobb lehet, mint a kisebb összesített árrésű −160/+140. Az egyedi fogadás EV-jéhez saját valószínűség és végrehajtható ár kell.

Nyitóár → tétek, hírek, ügyfélprofil és kockázat alapján mozgó ajánlatok. A bookmaker nem szükségképpen egyenlő összegű téteket keres: egy jól finanszírozott, informált szolgáltató a magasabb várható profithoz vállalhat egyoldalú kitettséget. A minden bookmakert tisztán közvetítőként bemutató korábbi mondatokat maga a könyv később árnyalja. Piacvezető és másoló/retail szereplők, aktuális konszenzus, informált piac és a fogadó számára ténylegesen elérhető legjobb ár külön fogalom.

A szerző Pinnacle-t és több korabeli szolgáltatót használ referenciának. Ezek a 2015–2017-es példák nem mai adatforrás-ajánlások. Szolgáltatói limitmegkerülésre, más nevében játszásra vonatkozó leírásai nem a projekt követelményei. Az egyetlen fogadóirodával lehetetlen nyerni és a szorgalmas árvadász szinte biztosan nyer típusú állítások túlzóak, nem tételek.

A proportional de-vig a reciproksúlyok normalizálása. Hasznos összehasonlítás, de nem a valódi valószínűség azonosítása; eltérő marginelosztás és favourite–longshot bias más eredményt adhat. A szerző maga is hangsúlyozza a piaci sáv és a becslési bizonytalanság jelentőségét. Sharp/square eredetű mozgás vezető/követő szélének értelmezése érdekes hipotézis, de a tét eredete általában nem közvetlenül megfigyelt adat.

A 9. fejezet „normalized win percentage” mutatója: pozitív profitösszeg/(pozitív profitösszeg+veszteségabszolútérték). 50% felett pozitív az összes profit, de ez nem hagyományos találati arány és nem a tétek összegére vetített ROI. Eltérő odds, tétméret és kitettség mellett a kettő között nincs egyetlen univerzális átváltás. A példabeli −120,+140,−200 fogadásoknál 240/(240+200)=54,5%, de a forgalom 420 és a profit 40: ROI ≈ 9,52%. A riportban a jelentéseket külön kell tartani.

## Push, alternatív vonal, parlay, teaser — 12–16. fejezet

Diszkrét eredményeknél a kulcsszámok körüli fél pont értéke egyenetlen. A push valószínűsége ligától, szabályoktól, total/spread környezettől és időszaktól függ. Tizedesjegyek száma nem a becslés pontossága. A szerző ezért nem ajánl örök push-táblát; a bookmaker alternatív vonalaiból következtetett eloszlást használja. Ez a felhasználó kérésében legfeljebb külső ellenőrzés lehet, nem az önálló sportmodell alapja.

Helyes azonosság: ugyanannak az oldalnak a koherens, marginmentes CDF-értékei közötti különbség adja a közrefogott egész eredmény valószínűségét. A könyv MAX-alapú táblázatképlete a két vonal valószínűbb oldalait használja; ez csak megfelelő oldalváltás mellett egyezik a kívánt különbséggel. Ha mindkét vonalnál ugyanaz az oldal a valószínűbb, a MAX-ok összege−1 hibás lehet. Példa: 0,65 és 0,60 ugyanazon oldalhoz tartozva 0,05 tömeget jelent, nem 0,25-öt. A képletet nem szabad vakon általánosítani.

A middle-nél többnyire kis veszteség, középre érkezéskor két nyereség van; ez nem arbitrage. Két azonos tétű −110 lábnál a teljes közép nullszaldós valószínűsége 1/21≈4,762%. A részleges pushokat pontos kifizetési fával kell számolni. Különböző elszámolási szabály, void vagy hibás ajánlat miatti törlés az elméleti arbitrázs kifizetését is megváltoztatja.

Parlay független lábaknál közös valószínűség szorzata; függő lábaknál közös eloszlás kell. A teaser k-adik gyöke az azonos esélyű független lábak nullszaldós esélye, illetve eltérő lábaknál geometriai, nem számtani átlag. A −120 kétlábas példa √(120/220)≈0,73855. A 0,647×0,642-es példánál a kb. −24%-os EV a szorzat×1,833−1; a szöveg utolsó zárójeléből a −1 hiányzik. A korrelált futball/soccer példák szemléltetések, nem ma elfogadott ajánlatok.

A 14. fejezet a fél pushfrekvenciát és a nullszaldós valószínűségek különbségét összevető rövidítéseket általános szabályként írja le. Ezek feltételekhez kötött közelítések. Pontosan EV=p_win×(d−1)−p_loss, push esetén nulla profit. Nem elég a feltételes nyerési küszöböt a feltétel nélküli tömeg felével összevetni. A „ha egy pontot veszel, vedd meg az összeset” következtetés sem általános: az egyes számok tömege és a díjszabás eltér.

A 15. fejezet éles piaci árhoz mért eltérése gyors relatív összehasonlítás; nem garantál EV-sorrendet eltérő odds, push és margin mellett. A 16. fejezet p×d−1 képlete push nélküli bináris fogadásra jó. Ha p a push nélküli feltételes nyerési esély, az egységnyi eredeti tétre jutó EV-hez a nem-push valószínűségét is figyelembe kell venni.

## Tétméret és értékelés — 17–21. fejezet

Kelly: f=(bp−q)/b, b=d−1, p+q=1 a könyvbeli bináris esetben. Hosszú távú várható lognövekedést céloz, nem általában a várható pénzprofit maximumát. A pushos, egyidejű és korrelált fogadásokhoz módosított probléma tartozik. A tört Kelly lehet modellezési bizonytalanságra, kockázati korlátra és hasznosságra adott válasz, nem csak érzelmi komfort. A bankroll előre elkülönített keret; a könyv maga elutasítja a minden jövőbeli kölcsönt beleértő értelmezést.

A szerző helyesen figyelmeztet: becsült edge nem ismert edge, a túlbecslés költsége aszimmetrikus. Sport/marktípus/módszer szerinti eredménynapló, korlátozott tét és folyamatos felülvizsgálat szükséges. Azonos ROI mellett a favorit Kelly-tétje valóban nagyobb lehet, de a bizonytalanság favorit/dog szerinti feltételezett iránya nem bizonyított univerzális tény.

Z=(W−L)/√(W+L) az 50%-os, független, azonos Bernoulli-nullhipotézis közelítő tesztje. Nem a −110-es profitküszöb tesztje. Változó odds, függő fogadások, szelekció és sok próbált modell mellett nem elég. A p-érték nem annak utólagos valószínűsége, hogy a siker szerencse volt. A könyv a sok tesztelő miatti szerencsés győztesek problémáját jól érzékelteti, de a megfogalmazás néhol összekeveri ezt az értelmezést.

A záróárhoz képest jobb ár kétféle jelentése: kedvező időzítés, illetve marginmentes benchmarkhoz viszonyított érték. Az általában informatívabb záróár nem minden meccsen igazság. Későbbi sérüléshír nem teszi visszamenőleg irracionálissá a korábbi döntést; saját/szindikátusi tétek is mozgathatják a viszonyítási alapot. A CLV hasznos diagnosztika, de nem oksági forrása a nyereségnek, és nem teljes bizonyíték. A könyv CLV-kritikája sem igazolja a CLV teljes elhagyását.

## Saját értékelés és játékosok — 22–30. fejezet

100-as vagy nullás középpontú csapaterő, párosításkor különbség, hozzá helyszín, pihenő, időjárás, utazás, személyi változások. Külön erősség- és totalrating. Az NFL-total egyszerű összeadása, NBA/college eltérő korrekciói a szerző módszerei, nem bizonyított ligatörvények. A játékos- és csapatszintű modellek közti fogalmi felosztása saját szóhasználat; a játékosszintű modell fölénye adat és validálás nélkül nem automatikus.

A csak eredménykülönbségből és ellenfélerőből álló SRS nem tud minden sérülést kezelni. A visszatérő játékos miatt a csapat múltbeli és mai állapota eltérhet. A szerző SRS-reziduumokból becsül kontextushatásokat. Ebből oksági hatás nem következik automatikusan: kihagyott változók, az alaprendszer saját HFA-kezelése és in-sample illesztés torzíthat. Az aktuális időpontban elérhető ratinget kell használni prediktív teszthez.

A 26. fejezet generic-pont/spread-pont átváltótáblái saját, empirikusan módosított 2015 körüli skálák; a szerző maga sem ad teljes elméleti levezetést. A 3-as/7-es kulcsszámok külön kezelése hasznos felismerés, de ezek a konkrét táblák nem másolhatók át mai NFL-modellbe. A pontskála helyett közvetlen kimeneteloszlás jobban ellenőrizhető összehasonlítási alap lehet — ez saját következtetés, nem a könyv implementációja.

A 27. fejezet teljes heti sérülési jegyzete jól mutatja a munka részletességét: státusz, kezdő, helyettes, szerep, együttes hiány és csapatrendszer. A hiányzó védő nem szükségképpen növeli a total-t, mert megváltozhat a labdabirtoklás és tempó. A régi sérüléshatásokat rögzíteni kell, hogy a visszatérést ne számoljuk kétszer.

A 28. fejezet Madden-ratingből indul: kezdő mínusz helyettes, /15, majd posztsúly. Ez heurisztikus prior, nem validált teljesítményvalószínűség. Ellenőrzött számtani ellentmondás: a listában QB-szorzó 3,0, a következő példában 3,5 szerepel, de a 20/15 különbségre közölt 4 pont csak 3,0-val adódik; 3,5-tel 4,667. Snap/usage szerinti módosítás, csapatrendszer és együtt játszó egységek fontosabbak a vak összeadásnál. Egy kezdőnél magasabbra értékelt csere nem önmagában adatbizonyíték hibára.

A 29. fejezet szerzői véleménye a csapatspecifikus HFA elutasítása. Hasznos kritika az ellenfélerővel nem korrigált hazai/idegenbeli átlagokra és önkényesen súlyozott stadionadatokra. Viszont HFA és utazási/road hátrány nem szükségképpen azonos, ellenkező előjelű csapatszintű paraméter; a (hazai HFA+vendég HFA−liga HFA) képlet modellfeltevés. Az odds-szorzó képlet h×p/[h×p+(1−h)(1−p)] logiteltolásnak felel meg. A liga nyers hazai győzelmi arányának h-ként való használata csak megfelelő csapaterő- és menetrendi korrekció után indokolt. Soccer háromkimenetű piacon a bináris képlet külön draw-modell nélkül nem teljes.

A 30. fejezet legerősebb tanulsága: az eredeti ratingeket és minden módosítást meg kell őrizni, és megvizsgálni, hogy a változtatás javított-e a jövőbeli becslésen. Nem elég folyamatosan felülírni a számokat. A játék képe, play-by-play, turnover-szerencse, felállás és ellenfélerő segít a zajos eredmény értelmezésében, de a „valódi eredmény” továbbra is becslés, nem megfigyelt tény. Az ellenfelek ratingjeinek iteratív visszacsatolása csak időhelyes adatokkal használható történeti szimulációban.

## 31. fejezet és összkép

A professzionális fogadás munkafolyamata és életvitele a szerző személyes tapasztalata. A könyv nem ad teljes statisztikai tananyagot vagy reprodukálható, igazoltan nyereséges algoritmust. Erőssége a pontos ajánlat, elszámolás, modellbizonytalanság, személyi változások és naplózás hangsúlya. Korlátja az időnként bizonyítatlan magabiztosság, a képletrövidítések és a belső számtani hibák.

Kapcsolat B15/B04/B07/B21 anyagával: a piaci referencia és az önálló rating elkülönítése; key-number eloszlás; játékoshelyettesítés; becslési bizonytalanság; múltbeli nyerési arány helyett időhelyes, későbbi ellenőrzés. A könyv hivatkozott oldalainak mai tartalmát és a történeti rekordokat még nem ellenőriztem online. Az összes forrás olvasása továbbra is folyamatban van.
