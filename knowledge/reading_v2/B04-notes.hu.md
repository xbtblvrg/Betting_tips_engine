# B04 — Fixed Odds Sports Betting (Joseph Buchdahl, 2003)

Állapot: a teljes, 224 oldalas PDF szövege és táblázatai végigolvasva (535196 karakter), fejezetenként jegyzetelve. Külön vizuális ellenőrzés: 33, 46, 48, 65, 66, 76, 155, 156, 177, 192, 195, 207. oldal. A B05 bájtra azonos másolat, nem külön mű. Az alábbiak olvasási megállapítások, nem kész rendszerterv.

## 1–30. oldal: alapok, piacok

A könyv megkülönbözteti a bankroll növekedését a forgalomra vetített hozamtól. A befektetés–fogadás analógia önmagában nem igazolja a nyereségességet. Fix odds, decimális/tört/amerikai odds, egyszeres és kombinált fogadások, hendikepek, ázsiai félgyőzelem/félveszteség, brit pontonként fizető spread betting. Az utóbbi nem azonos az amerikai fix tétű point spreaddel. A történeti piaci és adózási leírások 2003-asak; jelenlegi tényként nem használhatók. A teljes tét elvesztését helyenként „végtelen kockázatnak” nevezi: fix tétű fogadásnál a veszteség ténylegesen a kockáztatott összegre korlátozott. A tie/handicap elszámolás mindig a konkrét szerződés szabálya.

## 31–52. oldal: value, bookmaker-margin, arbitrázs

Az esemény legvalószínűbb kimenetele és a legjobb vétel két külön kérdés. A megadott odds és a saját becsült valószínűség szorzata határozza meg a várható hozamot. A könyv piacátlagból kiinduló eljárása feltételezi a piac helyes közös becslését; közös tévedés/copying esetén ez nem független bizonyíték.

`S = Σ(1/d_i)`, overround `S−1`, arányos marginosztás mellett elméleti hold `1−1/S`. Ezek nem felcserélhetők a tényleges eredménnyel. A 33. oldali 6,1% a 3/49 payout-arány, a 3/52 turnover-arány 5,77%. Vizuális ellenőrzés után pontosítás: a szerző ugyanitt a punter oldalán helyesen megadja az 5,8%-ot; az eltérő nevezők szöveges elnevezése könnyen félreérthető, nem hiányzó helyes számításról van szó. A 48. oldal 3.11 táblázatában vizuálisan is 1982 vendégfogadás szerepel, de 2256 összes és 364 hazai alapján 1892 lenne konzisztens; belső számbeli eltérés, nem extraction-hiba.

A 2000/01-es 2256 fogadásos példa elméleti +6% helyett −6,4%-ot eredményezett. A magas oddsok rosszabbul teljesítettek: a lineáris/proporcionális devig nem feltétlenül kezeli a favorite–longshot torzítást. Ez nem mai univerzális empirikus törvény.

Arbitrázsnál az inverz oddsok arányában kiosztott tétek csak teljes, azonos elszámolású kimeneteltérre és ténylegesen elfogadott kötésekre jelentenek matematikai lefedést. Részleges elfogadás, elmozduló odds, deviza, költség, void, partnerkockázat megtörheti.

## 53–73. oldal: saját rating → valószínűség → ár → teszt

Rateform: a ratingek csapatok közti átcsoportosítása; a hazai 7%-ot, vendég 5%-ot tesz a közös pontkészletbe, győztes viszi, döntetlennél osztoznak. Az átlag 1000 marad. Nem azonos a ma megszokott logisztikus Elo-frissítéssel. Ellenfélerősséget figyelembe vesz, szezonon át továbbvihető.

Alternatív példa: két csapat utolsó hat meccsének gólkülönbsége közti különbség. A hatmeccses ablakot más szerzőtől veszi; új adaton külön igazolás kell. 1993/94–2000/01: 14002 értékelhető meccs, 46,2% hazai, 28,1% döntetlen, 25,7% vendég. A nulla ratingkülönbség nem 50–50 és nem automatikus döntetlen, a hazai előny megmarad.

60–68: ritka szélső ratingeknél kevés adat miatt zajos gyakoriságok. Sima regressziós függvényekkel becsül valószínűséget; hazai `0.0156x+0.4647`, vendég `0.0003x²−0.0127x+0.2365`, döntetlen `−0.0003x²−0.0029x+0.2948`. **Kritika:** a kinyert kerekített konstansok összege 0,996; külön regressziók nem biztosítják az 1-re összegződést vagy a [0,1] tartományt. Extrapoláció negatív vagy >1 értéket adhat. A binenkénti R² nem meccsszintű kalibráció és nem fogadási profit bizonyítéka; nem súlyozott bin-fit a ritka szélsőértékeket túlhangsúlyozhatja. A draw alacsony R²-jéből önmagában nem következik, hogy döntetlenre soha nem érdemes fogadni.

69–70: Tottenham–Leeds példában saját hazai fair odds 2,15, elérhető 2,20, becsült hozam kb. 2,3%. A tényleges 2–0 eredmény önmagában semmit nem igazol; a becslés szerint így is nagyobb volt a nem hazai kimenet esélye.

71–73: valóban későbbi szezonon (2001/02) tesztel. Minden hazai jelzés, legjobb elérhető odds: 526 bet, +2,10%; átlagoddson −2,75%. Rating −2…+2 szűrés: 171 bet, +10,13% best odds. Vendégmodell: 407 bet, −14,99% best odds; középső szűk tartomány még rosszabb (−38,78%). A könyv maga felveti, hogy a modell egyszerűen nem elég jó, és a hazai eredmény lehet szerencse. **További kritika:** a több tartomány és oldal utólagos összehasonlítása újabb érintetlen időszakot igényel. A legjobb elérhető árnak ténylegesen, a döntés pillanatában hozzáférhetőnek kell lennie. A bookmaker számára is ismert hatmeccses forma nem automatikus információs előny.

## 74–95. oldal: várható érték, bankroll és kockázat

Négy kérdés: veszély, veszteségméret, bekövetkezési valószínűség, csökkenthetőség. Külön fogadási bankroll; életviteli kiadásokra szükséges pénz nem bankroll. Előbb pozitív várható érték, utána tétméret: pénzkezelés nem alakít át negatív EV-t pozitívvá.

76–77: `E[profit] = s[p(d−1)−(1−p)] = s(pd−1) = s(d/f−1)`, ahol `f=1/p`. A PDF-kivonat törtjei összecsúsznak; az eredeti képletet vizuálisan ellenőriztem, a fenti átírás helyes. Push/félkimenetel esetén ez a bináris alak nem elegendő.

78–82: yield = profit/összes tét, nem bankroll-hozam. Fix tét mellett elméleti `F=B+YSN/100`. Ez várhatóérték-könyvelés, nem garantált pálya vagy a cél eléréséig eltelt idő pontos várható értéke. A múltbeli hozam nem azonos a bizonyított edge-dzsel, noha a szerző néhol túl közvetlenül azonosítja őket. Doubles gyökös átlag-edge következtetése azonos/független előnyök idealizált esetére korlátozott.

82–88: igazságos pénzérme 2,10 oddson, 100 induló bank, 2 tét, 1000 dobás. A záró tőke binomiális eloszlása nem méri a korábbi tönkremenetelt. Monte Carlo a teljes pályát követi, megáll 0-nál vagy 200-as célnál. 1000 futásból 6 csőd: **szimulált gyakoriság**, nem pontos/univerzális 0,6%-os kockázat. A 0 és cél elérése abszorbeáló állapot, nem enged utólagos csodás visszatérést negatív tőkéből. Nagyobb tét súlyosabb ruin-risk pozitív EV mellett is. A „5% még megfelelő” állítás nem általános limitajánlás.

89–95: azonos, független lábaknál a gross edge szorzódik kombinációban, a találati esély csökken. `n` lábnál `∏p_i d_i−1`; az `edge^n` csak azonos lábakra. A 1000 fogadásos 100 bankos példában 5% edge/láb és 10 pontos tétnél singles csőd 36,6%, doubles 53,5%, trebles 68,3%. Egy pont/tét singles jóval alacsonyabb, de szimulációs nulla nem lehetetlenséget jelent. A magas várható profit és a megélhető tipikus eredmény külön fogalom. A korai csőd miatt a ténylegesen elérhető átlagbank kisebb a korlátlan hitellel számított képletnél. Fogadások közti és lábak közti korreláció még külön kockázat.

## 96–106. oldal (folyamatban): staking-szimulációk

Fix tét, fix nyereségcél (oddsfüggő tét), bankarányos tét, progressziók, Kelly. Benchmark a fix tét. Martingale nem teremt edge-et. Egyetlen megvalósult bankrollgörbe nem jellemzi az összes lehetséges pályát.

Könyvbeli vizsgálat: 250 single/szezon, 100 induló bank; több gross edge 0,90–1,20, több book-implied valószínűség 0,2–0,6, véletlenül változó egyedi edge-ek és oddsok, 10000 futás/scenárió. Átlagbank, szórás, csőd, nem nyereséges befejezés. Az edge és odds alig korrelál a mesterséges adaton; ez feltételezés, nem megfigyelt sportpiaci törvény. A maximális edge-t a `p≤1` korlát is behatárolja. A 99. oldalon 0,95 mellé −10% szerepel; helyesen −5%. Az átlagos odds nem az átlagos implicit p reciproka. A táblák szimuláltak, nem backtestek.

A 104–105. táblák: kicsi fix tétnél az átlagprofit közel a lineáris EV-képlet; magasabb odds nagyobb szórás. Nagy tétnél korai csőd levágja a további profitot.

## 106–154. oldal: staking-eredmények és korlátaik

Az összes köztes táblázat szövege elolvasva. Fix tét: azonos odds/edge mellett nagyobb tét általában nagyobb ruin-risk. Hosszú odds mellett a pozitív EV is sokáig maradhat veszteséges. A szórás növekedése az edge-dzsel **nem általános tétel**: bináris profit varianciája `s²d²p(1−p)`, ami `p>0,5` fölött csökkenhet a p növekedésével; ezt a könyv egyes táblái is mutatják. A 108. oldali „20-nál több adatpontból már jó normálközelítés” nem megfelelő feltétel; a generáló eloszlás és szélek is számítanak, a szimulációk száma nem teszi a bankrollt normálissá.

115–124: bankarányos tét csökken veszteség után. Véges, pontosan osztható pénz idealizált világában 0<k<1 mellett nincs pontos nulla; valós minimumtét miatt lehet gyakorlati csőd. A könyv a kezdőtőke 1%-át használja csődküszöbnek, és egyszerre csak egy lezáruló fogadást feltételez. **Fontos:** az azonos időben nyitott sok fogadásra nem alkalmazható önállóan ugyanaz a bankrollarány kitettség-összesítés nélkül. Eloszlás jobbra ferde, átlagot kevés szélsőséges nyertes húzza fel. A nagyobb átlagos végbank együtt járhat a nyereséges szezon kisebb valószínűségével (példában fix tét 77%, bankarányos 62%). Ez nem ellentmondás: külön kockázati funkcionálok.

124–135: fix nyereségcél `stake=target/(d−1)`, így a longshotokra kevesebb jut. A szimuláció azonos teljes turnoverre **utólag** skálázza a fix-profit és fix-stake rendszert; ezt élőben jövőismeret nélkül nem lehet pontosan lemásolni. A csökkent szórás és ruin-risk ennek a mesterséges odds/edge-eloszlásnak az eredménye, nem minden stratégiára szóló dominancia. Rövid odds felé a képlet korlát nélkül hatalmas tétet javasolhat. A végbank normálközelítése nem számolja az időközi csődöt: példában 0,5% végső negatív bank becslés vs 1,8% pályabeli csőd.

136–146: Martingale nyolc 3-dobásos kimenetelének felsorolása demonstrálja, hogy a nyereséges lefutások arányát a ritka nagy veszteség árán növeli, fair játék EV-je marad 0. Változó odds esetén `stake_n=(korábbi veszteség+cél)/(d_n−1)`. A 100-as bankrollnál már 1-es indulótét is súlyos csődarányt adhat. Tétemelés, korlátozott bank és maximumtét együtt ellehetetleníti a garantált visszanyerést.

147–154: Pyramid/D’Alembert lineáris tétnövelés veszteség után, győzelemkor oddsfüggő csökkentés minimum 1-ig. Kevésbé meredek emelés ellenére továbbra is jelentős veszteségüldözés; a 250-es szimulációban sok esetben Martingale-hoz hasonló vagy rosszabb túlélés. A 149–152 táblák átlagai ritka túlélőket is tartalmaznak, önmagukban félrevezetők.

## 155–167. oldal: Kelly

`f*=(pd−1)/(d−1)=(E−1)/(d−1)`, pozitív edge esetén. Ismert valószínűség, egyetlen bináris kifizetés, újratétezhető tőke idealizált esetében a várható **logaritmikus** tőkenövekedést maximalizálja. **Nem** minden kockázatot minimalizáló univerzális optimum, nem várható pénzösszeg-maximum. A szerző „maximum growth minimum risk” megfogalmazása túlságosan erős.

A 155. oldal lábjegyzete a kétkimenetelű versenyre korlátozza az alakot; pontosabban a **fogadás kifizetésének** kétértékűsége számít. Egyetlen csapat 1X2 győzelmére fogadásnál a két másik sportkimenetel mind veszteség, így a bináris formula alkalmazható. Több együtt nyitott/kölcsönösen kizáró vagy korrelált pozícióra közös optimalizáció szükséges, pushnál más képlet.

156–157: a bemutatott `F=1+f(E−1)` számtani várható tőkeszorzó; **nem a lognövekedés**. Példa 100 bank, 100 fair-independent 2,00 oddson fogadás, p=0,55 és f=0,10: átlag kb.270,5, a 55 győzelmes bank kb.165. A könyv lábjegyzetben helyesen elválasztja ezt, a főszövegből könnyű lenne félreérteni. A sorrend független konstans tétaránynál, de menet közbeni csőd/drawdown/nyitott fogadások kérdése ettől külön.

158–167: a Kelly-szimuláció minden egyedi betnél az átlag edge-et használja tétre, miközben a generált valódi edge változó. A nagy számtani átlagok nem prognózisok egy valódi felhasználóra. Overbetting bizonytalan p mellett romboló. Fél/harmad/negyed Kelly az edge túlbecslésének kockázatát és ingadozást mérsékli; a konkrét tört nem könyvből vakon átvett beállítás. A „kétszer/hatszor nagyobb esély profitra” 164. oldali állítást a táblával ellenőrizni kell: a veszteség valószínűségének arányos csökkenése nem azonos a profit esélyének ugyanekkora növekedésével (19,6%→9,7% veszteség =80,4%→90,3% profit, nem duplázódás).

## 168–175. oldal: fegyelem, nyilvántartás, favorite–longshot

Teljes fogadási napló: sport/esemény, kiválasztott kimenetel, odds, tét, ajánlás/fogadás/lezárás időpontja, bookmaker, eredmény, saját fair odds/edge. A fix tétes ellenőrzés külön méri a jelzések minőségét a tétkezeléstől. A sokat idézett 95% vesztes arány itt becslés/hallomás, nem modern mért adat.

2000/01–2001/02, közel 12000 európai labdarúgó-meccs záróárai: longshotok blind-betting vesztesége nagyobb. A részletes árazási példa megmutatja, hogy azonos abszolút p-hiba a kis valószínűségnél nagyobb arányos árazási hibát jelenthet. Ez lehetséges magyarázat a hosszú odds nagyobb marginjára; a bookmaker kockázatkerüléséről és bennfentes információról tett általánosításokat nem szabad bizonyított univerzális tényként átvenni. A könyv „profit margin” táblái gyakran `q/p−1` értéket közölnek, nem a tényleges stake-arányos `1−pd` holdot. Az alacsonyabb hátrány még negatív EV, nem automatikus value.

## 176–191. oldal: variancia, modellválasztás, adatbányászat

177–178: azonos tét és odds mellett a fogadásonkénti profitszórás `s√[E(d−E)] = sd√[p(1−p)]`, gross edge `E=pd`. Break-even esetben `s√(d−1)`. A gyökjelek a szövegkinyerésben hiányoztak, a 177. oldal képéről ellenőrizve. A sorozat összvarianciájánál a kovarianciák sem hagyhatók el.

180–191: négy lépés (prediktor, rating–p kapcsolat, value-kiválasztás, teszt) és a többletváltozók korlátai. A meredekebb regressziós kapcsolat önmagában nem jobb modell. Menedzser-keresztnév és tabellahely példája: utólag érdekes együttjárás lehet oksági tartalom nélkül. Két döntetlen után a harmadik „már nem jöhet” szerencsejátékos-tévedés; ugyanakkor a sportesemények nem szükségképpen függetlenek, sérülés/forma változtathat az esélyen.

Döntetlen-stratégia: utolsó 4 idegenbeli meccsből legalább 3 győzelem után draw. 1000 meccsen +6,28% a **feltételezett mindenhol 3,25 oddson**, ezért ez nem végrehajtható historikus profit igazolása. Más szezon/liga eltér: Németország −22%, Spanyolország +7,41%, erősen eltérő lefutás. 3 és 5 meccses ablakok vizsgálata a paraméterérzékenységet teszteli. Manchester United utólagos kivétele újabb szelekció, külön tesztelendő. A „legalább 10% kiválasztott meccs kell a szignifikanciához” nem statisztikai szabály: effektív elemszám, effektus, variancia, függés és tesztek száma számít, nem önmagában a kiválasztási arány. A könyv maga is elismeri az utólagos adatbányászatot.

## 192–198. oldal: hasznosság és tippszolgáltatók

A konkáv/konvex/lineáris hasznosság eltérő certainty equivalentet ad ugyanarra a kifizetésre. Vizuális 192. oldali ábra: kockázatkereső/kockázatkerülő/semleges preferenciák. **Kritika:** attól, hogy valaki csak +EV fogadást vesz, nem következik, hogy kockázatkerülő a hasznossági függvénye. Kockázatsemleges ember is így dönthet. A 195. oldali 8.12 ábra a „nincs profit” görbét tárgyalja, de a vízszintes tengelyen „Bankruptcy risk” maradt: vizuálisan igazolt hibás felirat.

Tippszolgáltatónál ellenőrizhető, teljes napló és reálisan elérhető odds kell, hozam és kockázat a reklámozott bankroll-növekedés mellett. Késői követő már más árat kap, díj/előfizetés/pénzmozgatás ronthatja a nettó eredményt. Ez különösen lényeges az 1–5 dolláros tétek gazdaságosságánál, de aktuális költségvetés későbbi feladat. A könyv akkori szolgáltatói díjai, maximális ügyfélszáma és bookmakerpéldái nem mai ajánlások.

## 199–212. oldal: nyereség statisztikai vizsgálata

100 fogadásos mintanapló: 66 win, 34 loss, 14,45 egység profit; oddsok többsége 2 alatt. Átlagos implicit p 58,2%, feltételezett marginfaktor 1,05 után „nullhipotézis” p-átlag 55,47%. Chi-square 4,49, könyvbeli p≈3,41%; t-statisztika 2,234, egyoldali p≈1,39%, Monte Carlo hasonló görbe. A marginfeltételezés változtatása nagyon megváltoztatja a következtetést, ezt a szerző jól bemutatja.

**Javítandó statisztikai értelmezések:**

- A p-érték nem annak a valószínűsége, hogy az eredmény „véletlen”, és `1−p` nem annak valószínűsége, hogy skill van. A konkrét nullmodell alatti legalább ilyen szélsőséges adat valószínűsége. Nem bizonyít jövőbeli vagy időtlen edge-et.
- A marginos veszteségre felállított nullhipotézis elutasítása nem azonos a pozitív nettó EV bizonyításával. A break-even és költségek utáni hozam külön referencia.
- Változó oddsoknál a nyerési darabszám nem elég a profit megállapításához. A független, különböző `p_i` Bernoulli-összeg Poisson-binomiális, nem pontosan a közös átlag-p binomiális modell. Azonos tétek sem teszik egyformává a győzelmek értékét.
- A könyv a marginfaktor 1,05-höz −0,05 átlagprofitot használ a t-tesztben. A korábbi reciprok convention szerint `1/1,05−1≈−0,047619` konzisztens; kis, de tényleges eltérés.
- A t-képlet `√n (x̄−μ)/s`; a kép alapján ellenőrizve. CLT-közelítésnek nincs minden eloszlásra elegendő mágikus n=100 határa. Hosszú odds, függő meccsek, változó tétek, adaptív kiválasztás ronthatják.
- A χ² kétoldali eltérést is észlel, a bemutatott t-teszt egyoldali; a két p közötti eltérés egy részét ez is magyarázza, nem csak a normalitás sérülése.
- Monte Carlo csak a választott nullmodellt ellenőrzi. 1000 ismétlésből 0,1%-os szélső esemény nem becsülhető stabilan, és a pontosság nem helyettesíti a nullmodell helyességét.
- Sok teszt/stratégia/ablak kipróbálása, naponta újrapróbált szignifikancia, túlélő tippszolgáltatók kiválasztása mind torzíthat. Hosszabb nyerő napló sem bizonyít automatikusan stacioner, megmaradó előnyt.
- A többes fogadások nagyobb implicit marginjából önmagában nem következik nagyobb szignifikancia ugyanakkora realizált profithoz: kifizetés-variancia és korreláció is megváltozik.

## 213–224. oldal: zárás és függelék

A 12 záróelv: külön bankroll; szelektív sportkutatás; pontos napló; value; tesztelés; alacsony költségű piacok; singles; indokolható rövid odds; ár-összehasonlítás; kockázati preferencia; folyamatos értékelés; veszteségek elfogadása üldözés nélkül. A value-feltétel a kedvenc/favorit ajánlást is felülírja. Egy üres fogadási nap elfogadható eredmény.

218–219 bibliográfia és korabeli URL-ek, forráslistaként őrzendők, működésük/jelenlegi tartalmuk nem ellenőrzött. 220–224 teljes 250 soros szimulációs függelék: öt odds/expectancy-scenárió és hét edge-scenárió. Mesterséges input, nem valódi meccstörténet. Az összes sor végigolvasva; a táblázat átlagai egy gépi újraszámolással ellenőrizendők, ha később reprodukáljuk a szimulációt.

## Mit ad hozzá a könyv, és mit nem?

Erős hozzájárulás: saját rating valószínűséggé alakításának konkrét folyamata, sikertelen backtest bemutatása, szimulált bankrollpályák, tét–odds–variancia kapcsolat, teljes napló és önellenőrzés. Gyenge pontok: elavult piaci állítások, néhány hibás általánosítás, p-érték értelmezése, bizonytalan edge tényként kezelése. Nem tartalmaz mai NBA/MLB/NHL/NFL/WNBA adatvezetéket, modern kalibrált modellt vagy autonóm tanulási eljárást. A saját modellezés és a kockázatkezelés témájában kiegészíti B15-öt, de egyik könyv sem igazol garantált nyereséget.
