# B07 — Billy Walters: Gambler, 22. fejezet, Advanced Master Class

Állapot: a feltöltött 31 oldalas PDF teljes szövege és táblázatai elolvasva; 3, 12, 14, 21, 23, 26, 27, 30. oldal vizuálisan is ellenőrizve. **Ez fejezetkivonat**, nem a teljes életrajz. A teljes mű B01/B02 olvasási állapotát ez nem teszi befejezetté. Oldalszámok a kivonat 1–31 számozására vonatkoznak.

## 2–6: saját teljesítménybecslés, utána piaci összevetés

Walters több szakértői csoport elemzését személyesen súlyozza; ez a szerző beszámolója, nem az általa megosztott reprodukálható ensemble-algoritmus. A 3. oldali folyamatábra: kezdeti csapaterő + konkrét meccskörülmények + hazai előny + sérülések → saját várakozás → mérkőzés → korrigált teljesítmény → friss rating. A könyvben szereplő napi/heti ismétlés **ratingfrissítés**, nem automatikusan önmagát átíró program.

Minden tényezőt azonos, pontkülönbséggel kompatibilis mértékegységre hoz. Ranghely nem pontérték. Átlagcsapat 0, a példák kb. −10…+10. Előszezon: előző szezon végi csapaterő, játékosmozgások, draft, edzőváltás, fejlődési becslés összegezve. Giants-példa −7 +3,7 =−3,3. Más szolgáltatók ratingjeit is ajánlja kiindulópontként, ezért a bemutatott eljárás eredete nem szükségképpen teljesen saját nyersadatmodell.

## 7–11: játékosok, helyettesítők és sérülések

Játékosrankból nem lehet közvetlenül spreadet levonni. A hiányzó játékos értékét a helyettesítőéhez képest kell mérni. Sérülten játszó szereplő sem automatikusan teljes értékű. A QB külön modell, hosszú és rövid távú passz/rush/sack/turnover mutatókkal, ellenfélerő és társak minőségének figyelembevételével. A közölt 67 QB 6,0–9,5 közötti „értéke” nem univerzális replacement-adjusted sérülésveszteség: két értékelt QB cseréjénél a különbség számít.

Cluster injuries: azonos egység több hiányzója kölcsönhatást okozhat; receivers példában 2,5+1,5=4 helyett 6 pont. A +50% és defensive line +40% **illusztratív szerzői szorzó**, nyersadat, becslési hiba és külön teszt nélkül nem paraméterigazság. Wirfs–Donald–Brady példa: matchupfüggő blokkolási sérülés miatt a TE útvonalai is változnak, több egység feladata összekapcsolódik. Egy sikeres utólagos fogadásos történet nem igazolja a hatás méretét.

Edzésrészvétel, hivatalos sérülésjelentés, helyi újságíró, edzői jelentési szokás: források időben változnak és bizonytalanok. A „szerdán edzett, szinte biztosan vasárnap játszik” nem általános tény. A játékos állapotának becslése több lehetséges lineup forgatókönyvet indokolhat; az adat hiányát nem lehet egészséges állapotnak tekinteni. A könyv korabeli Twitter-fiókjai később ellenőrzendő forrásjelöltek, nem friss hírek.

## 11–15: helyzet, időjárás, utazás, motiváció

1974–2023 közti történeti vizsgálatra hivatkozik; az adatot, regressziót, bizonytalanságot nem közli. Saját figyelmeztetése szerint a 2022/23-as értékeket évente módosítják, akár törlik. Nem mai konstanslista.

S-faktorok: pályaborítás egyezése, divízió/konferencia, csütörtök/vasárnap/hétfő esti meccs és az előző heti meccsidő, harmadik idegenbeli négy meccsen belül, hosszabbítás utáni hét, bye és csapaterő, Super Bowl utáni kezdet, földrajzi útvonal, időzóna/cirkadián idő, nagy vereség utáni helyzet. Egy S-faktor egység 0,20 pont. Példák: azonos borítás +1 visitor; eltérő borítás +1 home; vasárnap esti hazai +4 S; nyugati csapat 10:00 helyi biológiai idejű kezdés −2 S; keleti csapat esti meccsen −6 S. A napszakot a releváns helyi/testóra-idő szerint kell értelmezni, nem UTC-óraszámként.

W-faktorok: meleg éghajlatról hideg szabadtérre, dome→hideg kültér, eső, hó, szél és a csapat passz/futás profilja. **Mértékegység-bizonytalanság:** fejléc 0,20/W, de a táblázat +0,25…+1,75 értékeket ad; ez nem tehető csendben közvetlen spreadponttá vagy újra skálázható értékké forrásellenőrzés nélkül. QB-specifikus W külön 0,15 pont/faktor. E-faktor: tét/motiváció, edző/játékosmozgás, előző eredmények, playoff-helyzet, 0,20 pont/faktor.

**Kritika:** az átfedő indikátorok összeadása duplán számolhat (pl. bye és bye+away, 19+ és 29+ vereség). A könyv nem ad teljes prioritás/interakció szabályt. Egy „bounce back” faktor csak új adatokon bizonyított prediktív jelként kezelhető; a nagy vereség önmagában nem teszi esedékessé a győzelmet. Évtizedek adatát szabály-, játékstílus- és szezonváltások nélkül összeönteni nem feltétlenül stabil.

## 16–20: heti ratingfrissítés

A csapat szemszögéből: `TGP = margin + opponent_rating_old + injury_self − injury_opponent − home_advantage_signed`. Semleges pályán utóbbi 0; hazainál pozitív előnyt levon, vendégnél visszaad. `rating_new = 0,9*rating_old + 0,1*TGP`.

Bears10, Vikings4, sérülés3,5 vs1,7, Bears+7: semleges TGP12,8 vs1,2; új rating10,28 vs3,72. Kétpontos hazai előnnyel TGP10,8 vs3,2; új rating10,08 vs3,92. Mindkét félhez a **meccs előtti** rating kell, különben sorrendfüggő adatkeverés történik. Saját sérülés miatti hátrányt a teljesítményből visszakorrigálni nem ugyanaz, mint a következő meccs prognózisában levonni.

Az alfa=0,10 a könyv bemutatott simítási tényezője, nem minden sport/szezon optimális tanulási üteme. A 18. oldal minden számpéldája konzisztens a képlettel. A szerző a következő meccshez a friss sérülés/helyzet faktorokat külön alkalmazza; az alap csapaterő és átmeneti meccshatás elkülönítése a lényeg. S-faktor heti átlag3,2 →0,64 pont, maximum18 →3,6 pont a példaszezonban.

## 20–26: key numbers, line és ár

Azonos távolság a modellezett és kínált spread között eltérő értékű, attól függően, hogy milyen végső marginokat lép át. A táblában 3:8%, 7:6%, 6 és14:5%, 10:4%; a számok kerekített, történeti és részben feltételes gyakoriságok. Nem olyan univerzális eloszlás, amelyből minden meccs valószínűsége feltétel nélkül kiolvasható.

23. oldali félpontvásárlási táblázat: 3-ról pushból ki 20, pushra be22 dollár/100-os nyereménycél körüli árpélda; 7-nél13. A 21/24 sor **az eredeti képen is üres**, nincs szövegkinyerési adatvesztés. A hiányzó érték nem nulla. A 26. oldal implicit spreadjei paired sorok: −3/−120 és −3,5/even kb.−3,25/−110 a szerző skáláján. Ezek heurisztikus átváltások; nem helyettesítik a konkrét win/push/loss eloszlásból számolt EV-t.

Csillagos rendszer: 5,5% pontérték-összeg fél csillag, 7% egy, 9% másfél, 11% kettő, 13% két és fél, 15% három. Egész spreadhatárra fél pontértéket számol. Nulla átlépésekor speciális korrekciót használ. A „14% majdnem három csillag” a táblázati határok közt interpoláló szerzői megfogalmazás. Ezek **nem kalibrált győzelmi valószínűségek és nem közvetlen ROI-százalékok**. A kivonatból a star→bankroll teljes korábbi definíció nem ismert. A már közzétett ár/juice mozoghat, ezért a jobb szám rosszabb díj mellett lehet rosszabb fogadás.

## 27–31: moneyline, parlays és teasers

Spread–moneyline átváltásnál a teljes margineloszlás és összpontszám is számít; a generikus táblázat nem minden találkozóra azonos igazság. **27. oldali előjelhiba vizuálisan igazolt:** „Favorite +116 / Dog −104” szerepel, a további sorokból több előjel hiányzik. A 29. oldali szöveg favorit−170 / dog+140 példát ad három pontnál, amely tisztázza az irányt; az eredeti táblát vakon parse-olni hibás lenne.

Parlay: win/push/void szabályokat platformonként kell tudni. A szerzői „current rules” 2023-as kontextus. Független 50%-os lábakhoz kettős parlay p=0,25, hármas p=0,125. A közölt 2,6-os nettó kifizetés pontos bináris EV-je 0,25*3,6−1=−10%, míg a táblában −9,8%; 6-os nettónál −12,5%, táblában −12,4%. Push-szabályok és kerekítés módosíthatnak, de ehhez nincs itt teljes eloszlás megadva.

Teaser-szövegben belső egyszerűsítések: először 25%-os kétlábas esélyt ír, majd 68%-os lábnyerési esélyt; az utóbbi mellett függetlenül 46,24% az együttes esély. A „minden hozzáadott pont 3 százalékpont” ellentmond a korábbi key-number heterogenitásnak, legfeljebb durva átlag. −120 áron és két 68%-os független lábbal, push nélkül, a stake-arányos EV kb.−15,23%; −130 mellett kb.−18,19%, a közölt −14,8/−17,7 ettől eltér. A fogadó számára ezek gyenge általános árak lehetnek, de **nem következik**, hogy minden konkrét teaser minden adott áron negatív EV. Ehhez a tényleges együttes nyerési/push valószínűség szükséges. A korabeli outlet-árlap nem aktuális ajánlat.

## Hozzájárulás a könyvtárhoz

A felhasználó modellből induló elképzeléséhez közvetlenül releváns: saját neutral team strength, aktuális lineup és matchup, külön kontextus, előrejelzés, majd árösszevetés és utólagos frissítés. A fejezet nem adja át az eredeti nyersadatokat, játékosérték-becslő algoritmust, teljes bizonytalansági modellt vagy ellenőrizhető historikus fogadási naplót. A szerző sikere ezért nem egyenlő a publikált konstansok reprodukálható mai nyereségességével. A többi könyvvel és később elsődleges online forrásokkal összevetendő.
