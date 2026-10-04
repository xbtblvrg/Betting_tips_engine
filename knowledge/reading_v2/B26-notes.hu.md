# B26 — Sports Trading on Betfair — teljes feldolgozás

Wayne Bailey, 2015. Ténylegesen elolvasott szöveg: 363028/363028 karakter, mind a 29 EPUB-egység; mind az 55 kép vizuálisan ellenőrizve 14 kontaktlapon.

## Forrástípus és relevancia

Személyes, jórészt lóversenyes exchange-mikrostruktúra és diszkrecionális trading, nem NBA/MLB/NHL/NFL/WNBA sportadatból becsült eredménymodell. 2015-ös piaci állapot/szoftverek/díjak/országok; nem mai ajánlások. A szerző Betfair oktatói háttérrel és saját kereskedési anekdotákkal dolgozik, nem auditált out-of-sample stratégiaeredményt ad. Saját pénzvesztés, losschasing, aszimmetrikus kiswin/nagyloss tanulságai őszinték; a „70%pszichológia”,„90%veszít”,„legtöbb feladó közel járt” nem bemutatott kutatási eredmény. Az önbizalom és szabálykövetés nem helyettesíti a pozitív várhatóérték bizonyítását.

## History és basics

Exchange vs sportsbook, nettó kitettség több egymást kizáró selectionre, partialfill, bid/ask, queue és ladder, matchedvolume vs várakozó ajánlat, commission és premiumcharge megkülönböztetése fontos. A bookpercentage 100%közeli értéke önmagában nem költségmentes végrehajtás vagy fair árazás. A 4/5 és evenmoney cointoss 105.5556%-os összege helyes, a valódi hold pénzallokációfüggő.

Back B összeg d_b áron; lay L összeg d_l áron: selectionwin P&L=B(d_b−1)−L(d_l−1), selectionlose P&L=L−B. Azonospiaci commission előtti teljes kiegyenlítés L=B*d_b/d_l. 100back5.5 és100lay5.2 →30ha nyer,0ha veszít. 105.76923lay→5.76923mindkétoldalon. A kerekített105.77laynél5.766winvs5.77lose; a könyv5.77kerekítés rendben. Commissiont külön kell alkalmazni a platform elszámolási alapjára; a könyv korai „garantált profit” példái nem nettók.

Téves: a100egységnyitottback „valójában nem kockáztat100-at,mert majd kiszáll” — outage/suspension/gap/partialfill miatt a teljes tét elveszhet. Nettósított felszabaduló tőke és realizált/withdrawable profit különböző fogalom. 205.77turnover, majd még3ismétlés→4trade823.08, nem617;617.31 háromtrade. A turnover nem profit és nem hitelleverage; nagyobb forgalom költséget/adverse selectiont is növelhet. A software gyorsít és mutatdepth-et, de önmagában nem edge. Minimumösszegek, API, vendorárak és prémiumdíjak később hivatalosan ellenőrizendők.

## Key prices / crossover

A könyv történeti Betfair ticklépcsője:1.01–2:.01;2–3:.02;3–4:.05;4–6:.10;6–10:.20;10–20:.50;20–30:1;30–50:2;50–100:5;100–1000:10. A nemegyenletes tick gazdasági értékét fontos ismerni. **Hiba:**2→1.99−.5%, nem−1%;2→2.02+1%, nem+2% a decimalár relatív változása. 1000lay2 majdazonos1000back1.99 →−10csakha selectionnyer,0egyébként; ez nem teljes könyvre−10garantált loss. Teljes greenup1000*2/1.99backnél−5.02513mindenágban;2.02backnél+9.90099. Egy nagyobb felfelé tick nem automatikusan pozitív layEV, mert a felfelé/lefelé átmenet és fill feltételes esélye nem50–50.

A6lay100→6.4back100 +40ha win,0egyébként; greenup6.25. 5.8exit teljesgreen−3.44828. Két-két tick tehát nem szimmetrikus P&L és nem bizonyított nyereség. A korai queuepozíció értékes lehet, de matched-választódáskor a rossz információjú állapot gyakoribb lehet. A14k fal lebontása vs korai202előny példa mikrostruktúrahypotézis. A stopnak trigger+végrehajtási mechanika kell; egyszerű backlimit5.8 6-os aktuális árnál azonnal marketable lehet. A két exitorder nem kezelhető automatikusan OCO-ként.

## Scalping1–2

Gaps: korai back3.65, későbbi lay3.6, egyentét100→+5a selectionwinágon,0egyébként; greenup~1.3889commissionelőtt, nem mindenágban5. MeetingsMan50back9.4/lay9.2 →10vagy0; a loseág szövegében tévesen3.65/3.60maradt. Fontos sajátqueuepozíció, piaconkénti fill, részleges20lay/10back alatt **nem kiegyenlített** a könyv. Az, hogy ár megérinti az ajánlatot, nem bizonyítja a teljesült tétet. A távoli „exitorder” önmagában teljesülhet belépés előtt, ezért az állapotkezelés kockázatos.

Quickfire: kiegyensúlyozott50/50…60/40WOM, hasonló depth, back2.64/lay2.62,1tickprofit,3–4tickstop. A kiegyensúlyozott topdepth nem azonos agresszív orderflowval, canceled/spoofed quoteokkal vagy garantált kétoldali filllel. 1nyerő:3vesztes tick mellett árfüggés nélkül is>75%winratekell jutalék előtt; 1:4nél>80%. A „nem jósolunk irányt” nem kockázatmentesség: meanreversion/no-breakout és fillidő-hipotézis. A stop-market átcsúszhat, stop-limit nemteljesülhet.

## Support/resistance és magnet

2.3–2.4range, többször tesztelt floor/ceiling, breakoututánretreat; előre definiált2tickexit/stop. A1:1tick nem pontos1:1nettópénz a decimalár változás és greenup miatt. Az50%határ jutalékot kihagy. A charttrend/crowdreakció megkülönböztetése retorikai, mindkettő jövőbeli ár/fill függő. Anchor és hagyományosfractionalprice hatás ésszerű tesztelendő felvetés, bizonyító stat nincs.

Magnet:1.5/1.6között1.54–1.56neutral,1.52–1.53downzone, másikirányuppzone;8/9között8.4/8.6neutral8.8entry→9exit. Nem fizikai törvény, utólag választott kerekár-hypotézis, külön crossoverkezelés. A későbbi példában8.6on beleüt a sorba,miközben8.8belépést mond; ténylegesfillárat kell naplózni. A látható queue nem garantált fennmaradó likviditás.

## Financial spreadtrading kitérő

Elolvasva, a felhasználó scope-ján kívül. Spreadbet egy árkülönbségre szóló szerződés, nem részvénytulajdon. £10/.10goldtick;1575.5→1577.1=16tick→160profitjutalék/spreadelőtt helyes. Short/longkitettség, depozit, maxloss és counterparty fontos. A „valódi pénznek fájnia kell hogy tanulj” nem modellezési követelmény.

S&Pgap példa1357.6→1350.2=7.4indexpont=74db.1-es lépés, nem74indexpont. A gapzárás „amerikai hazafias vétel” története nincs bizonyítva. A11:30UKés21:30USclose órák instrumento/nap/DSTfüggők; a futures nem általános24/7. A kiválasztott egy nyertes példa, szubjektívstop és newsfilter nem auditált stratégia. Costs/slippage/overnight/tailrisk/többszöröstesztek nélkül nem átvehető. Nem szükséges e pénzügyi kereskedést a sportsengine-be vinni.

## Dutching

Azonosbruttóreturnhoz B_i∝1/d_i, összstakeK esetén B_i=K*(1/d_i)/Σ(1/d_j). **A könyv 50fontos példája100fontra vált**:3.5/4.5helyes50kereteloszlás28.125+21.875, nettó48.4375ha valamelyiknyer,−50haegyiksem. A közölt56.25+43.75és96.875profit100keretnél igaz. A nyerőjelöltek közötti egyenlőpayout nem összeskimeneteles garancia. Magasfavoritwinrate nemmutatprofitotárnélkül. DutchSchultz anekdotát a szerző is bizonytalannak mondja.

Összesrunnerback book<100 vagy lay>100 elméleti arbitrázs lehet egyező teljes eseménytérrel és nettó elszámolással; nem garantálható nematomikusfillexecutionnél. SP utólagos98%-os összeg nem bizonyítja, hogy a szükséges összegek egyszerre elérhetők voltak. Crossmatching, csökkenő tétlimit, withdrawals/reductionfactor és újonnan belépő résztvevő változtatja a kockázatot. Hasznos a realityshow példája: hiányos mezőn a zöld kijelzés nemteljeskörű hedge.

## Arbitrage

Bookback B,d_b és különexchange layL,d_l, nyerőlayprofiton c commission esetén L=B*d_b/(d_l−c).500back4ésc=.05,d_l=3.65→L555.55556;winprofit27.77778;loseprofit27.77778. Lekerekített555.56nál527.782nettólay, a könyv527.79 kicsi kerekítéshiba. A szükségesfinanszírozás500back+1472.22liability, nem csak „500-as fogadást bírjon mindkét account”. A currencyk euroból poundra váltanak, valósarb esetén FX is számít.

**SprinterSacre20back2/20lay1.26 nem kockázatmentes**, mert vesztesselectionnél20*.95−20=−1; a szöveg itt komiszt kihagyva állítja nemveszteség. Egységeshedge20*2/(1.26−.05)=33.05785→11.40496mindkétágban, ez a következőpéldában helyes. Offerfeltételek, stakevisszatérülés, void, Rule4/reduction, withdrawal, acceptedodds és tétkorlát meghatározó. Oddscomparisonbackprice nem executablelayprice; snapshotkésés fontos.

## WOM és layingfield

WOMalkalmanként weightofmoney, máskor movement névvel: a könyv top3összegből számláltkülönbség4685−1900=2785 és arány4685/1900≈2.4663→2.5. A próza összekeveri melyiket osztja/melyiket kivonja. Depthstock nemorderflow, és nem valódi meccsprob; spoof/cancel lehet, ezt a szerző felismeri. Reversebookújlimitárat kér a sorban,nem azonnal elfogadható jobbárat teremt.

Layfield1.8,20egység,egyetlenwinner: teljesülőnyerőlay mellettm veszteslay→P&L=20m−16. Egy teljesülőwinneronly−16; egyvesztes+winner+4;3vesztes+winner44. Commissionésrészfillmódosít. Nem biztos, hogy a végsőgyőzteslay teljesül (hirtelenfinish/suspension/queue); ilyenkor más payoffág is lehet. Többwinner/deadheat vagy voidkivételhez más elszámolás kell. A múltminár1.7 nem garancia saját1.8orderfillre. A szerző itt korrektül elismeri, hogy kevésadat, workinprogress, racecourse/distance/pricefiltermégtesztelendő.

## Expectations és stake

1.67/1000nap,5%havi→1.05^12−1≈79.586%,36hó≈5791.82jófeltételeskamatszámítás;24hópontosan3225.10,nem3255.10. A feltételezettstabilhozam és valós bizonytalan hozam különbözik: E[log(1+r)]nemlog(1+E[r]), volatilitás és piacméret limitálhat.3%havi≈42.576%évi; nem garantált vagy szükségképpen reális. Egyetlenrosszpensionpélda nem bizonyítja amatőrsportstrading fölényét; munkaidő/fees/riskszámítás kihagyott.

A szerző1–2%trade, ritkán3%,max5%; a20%strikerate→2%bank,30%→3%szabály nem EV/Kellyalapú, reward és correlation hiányzik. Betfairlaystake nem liability, stop által várt veszteség nemworstcase. Daily40/1000stop mint fegyelmi szabály nem biztosít edge-et,de megszakítás helyes lehet. E pont érdemben eltér Appelbaum „veszteség esetén se hagyd abba” tanácsától. A felhasználó1–5USDszabályát nem írják felül a szerzői tétek.

## Inplay

TV/streamkésés, helyszíniinfo, APIexecution, suspend és gyorsállapotváltozás valós időbélyegprobléma. Korabeli5másodpercelőny nem mai bemértadat. A korábbi laySP→50%driftcounterback magaswinrate/nagyloss torzítást mutat; a későbbi backSP→half-fractionallay más szerződés.

**Definíció:** a szerző „50%pricefall”-ja a nettfractionaloddsk felezése: d_l=1+(d_b−1)/2=(d_b+1)/2, nem decimald_b/2. EmiattL=2B eseténwinnerprofit0,loserprofit+B (ha layfullfilled); ha nemfilled ésveszít,−B. Ez nem bizonyít edge-et.17.87SPnél9.435nemtickvalid, kerekítéshezmaradékkitettség-számításkell;6.32SP→3.66semvalid. A finalwinner nem garantálja előzőhalfprice-fillt. A szerző3payoffága csak teljesfill/feltétel mellett,partialfillre incomplet.

A 44.86→6.4 nettfractional csökkenés~87.69%, nem84%;13.5→6=60%és20→2.6≈91.579%jó. Legutóbbi6racekiválasztás és utólagjobb50%küszöb holdoutnélkül overfitveszély. Low/highrecordnemfillbacktest, a videóban favouritefall vs saját runningstyle megkülönböztetése hasznos oksági kritikakísérlet. Meghibásodó számítógép nyitottkitettséget hagyhat, ezt saját példája igazolja. Igényeltmagas emberi race-reading és szubjektívselction miatt nem megismételhető specifikáció.

## Momentum és PuttingItAllTogether eddig

Hatékonyárból nem következik50%fel/50%leugrás, különösen eltérőtickméret és oddsvalószínűség-transzformáció mellett. Farleigh55/45folytatás más pénzügyipiaci mintából nem bizonyít sportsedge-et;10százalékpontnettwinners nem automatikus10%ROI. Smooth5–10%trend, diszkrecionálisstartingpoint, manualtrailingstop és utólagosrangeexit: előre rögzített szabályok nélkül hindsight. Dublin2004–2012házpélda illusztráció, nem out-of-sampleteszt.

8→7.6fractionalváltozás5.714%,6→6.8viszont16%,nemközel15%;7.6→6.2 hét.2-estickhelyes. A stopbreakevenbe helyezése nem veszteségkizárás végrehajtás előtt. Helyes az eredmény és tökéletesentry/exit szétválasztása. A munkát/jellemhibát túlhangsúlyozó részben a losingoutcome nem bizonyítja, hogy „túljártakazeszedben”; jóEVdöntés is veszít. Tradersközött jutalékután negatívösszeg, nemtisztazéróösszeg. A pontos2×lossaversion/happiness állításhoz a könyv nem ad azonosított kutatást. Eddig elolvasva24egység a „Be wary too when people say...” kezdetű bekezdésig, amely félbeszakad „It’ll work out” szövegnél; innen folytatandó.

## Putting it all together — a fejezet hátralévő része

A véletlen veszteség elfogadása, üldözés kerülése, house-money és endowment bias, döntési folyamat és eredmény külön értékelése hasznos. A kaszinónak szükségesek alkalmi nyertesek, de ettől a tradernek még nincs előnye. A szerző banki hitel elutasításáról szóló története és a tranzakciók más számlára/PayPalra terelése nem átveendő elrejtési gyakorlat; személyes, történeti beszámoló.

A fair lottóban bármely konkrét kombináció azonos esélyű, a népszerűség a megosztott nyereményt befolyásolhatja. Független érmedobásnál a gambler's fallacy kritikája helyes; sportsikerek viszont nem szükségképpen függetlenek és stacionáriusak. Tíz előre rögzített fair bináris tipp telitalálata 1/1024, de sok tipster közül utólag kiválasztva ez nem a képesség bizonyítéka. Az elágazó e-mailes tipscam 1000→100→20→5 címzett története szelekciós torzítást szemléltet, nem javasolt tevékenység.

A bemutatott 1.10-es in-play automata és veszteség utáni tétemelés 100→111 korai eredménye eltakarja a ritka nagy bukást. Kifizetési eloszlás, maximális kitettség, díjak, teljes minta szükséges. A napló tartalmazzon belépési/kilépési indokot, árakat, téteket, mezőnyt, likviditást, volatilitást, végrehajtási eltéréseket és a veszteseket is. Néhány hét pozitív napló nem elég validáció. A mezőnyméret/rövid favorit és volatilitás kapcsolatát a szerző maga is adat nélküli tapasztalatnak mondja.

Tíz különálló even-money fogadás öt győzelemmel és öt vereséggel, egységnyi téttel és 5% nyerőoldali jutalékkal −0.25: helyes. Egy piacon nettósított elszámolás ettől eltérhet. Egy vesztes stratégia mechanikus megfordítása a költségeket nem szünteti meg. A későbbi fejezet helyesen választja el a tétet a kockázattól, de a 3%-os általános szabály nem becsült optimális tét. Az egy font/tick táblázat 100,50,20,10,5,2 tétje a tickméret szerinti, adott kimeneti P&L-re vonatkozik; nem normalizálja a teljes greenupot, a crossover aszimmetriát vagy a gap miatti maximális veszteséget.

A stop nem garantált teljesülés. A stop-hunting történetek nem bizonyítják az eladók szándékát vagy egy árpadló stabilitását. A 6.6-os lay után 6.2-es stopról szóló példában a későbbi „backing at 6.6”, magasabb árnál kilépéssel iránytévesztés: a leírt trade lay. Szélesebb stop csak megváltoztatja a kitettséget; nem javítja bizonyítottan az edge-et. Az 1.2-es odds fair implicit esélye 83.333%, nem 80%. A szerző nyerő nap végén szabályt megszegő, eredményfüggő pozíciótartását nem lehet következetes automatikus szabályként átvenni.

## Napi napló, trading plan, zárás (EPUB25–29)

A napló „Friday 16 June 2012” felirata naptár szerint hibás: 2012-06-16 szombat. A valódi dátumot nem találgatom. A reggeli kutatás, naplózás, technikai környezet, munkamegszakítások és esti kereskedés mutatja az időigényt. A £82-es délutáni eredmény és órabér emlegetése nem teljes költségkimutatás, ha a reggeli kutatás kimarad. Az Ascot-említés és a korábbi felsorolt helyszínek összhangja bizonytalan. Hír, lóvisszalépés, késői riasztás és véletlenül nyitva maradó in-play pozíció operatív kockázatok. A belépési árra húzott stop sem garantál tőkevédelmet. A szórakozásból kötött focifogadás külön kezelendő; életmódbeli/bor-egészség állítás nem sportsmodellezési tudás.

A trading plan tíz kérdése: motiváció, időbeosztás, stratégiák, tőke/kockázat, nyilvántartás, nyerő-vesztő napok kezelése, kivét, költségek, nem pénzbeli célok és tanulás. Az 5000 össztőke/4000 tartalék/1000 account, heti 12–16 óra, napi 200 veszteség/400 profit stb. példabeállítások, nem személyre szabott vagy statisztikailag optimális szabályok. Az „out of zone” állapotot nem bizonyítja a napi P&L. Az 1000→1000 tőkenövekedési példa és a 112→122 ébren töltött heti óra elírás; a risk:reward arány sorrendje sem következetes. Célszerű mindig konkrét nyereség/veszteségösszeget megadni.

Költség a díj, adat, szoftver, hardver, oktatás, tőke és idő. A gyors internet önmagában nem alacsony teljes végrehajtási késés, a piaci felfüggesztést sem oldja meg. A második PC/ISP személyes megoldás. Papíron is modellezhető a végrehajtási költség; nem szükségszerű valódi pénzt veszteni a tanuláshoz. A könyv nem jogosít fióknyitásra vagy üzenetküldésre.

A zárszó 2014 januári, a könyv 2015-ös; a végén 2015 júniusi saját WBR-tipsterszolgáltatás reklámja áll. A piac változásának elismerése helyes, a kemény munka nem profitbizonyíték. Az ajánlott bibliográfia elolvasása nem jelenti az ott felsorolt külső művek elolvasását.

## Teljes vizuális ellenőrzés — 55 kép, 14 kontaktlap

Mind az 55 képet megtekintettem a B26-sheet01…14 lapokon. A price-ladder, árrés, crossover, stop, dutching, WOM, arbitrázs, lay-the-field, kamatos kamat, in-play és reklámeredmény ábrák a szöveghez vannak ellenőrizve. A képfájlok és eredeti EPUB-nevek: B26-visual-manifest.json.

- A crossover táblázat 1.99/2/2.02, 2.98/3/3.05 stb. értékei megerősítik a korábbi százalékos hibakritikát. A 6-os ladder képen az exit-ajánlat 6.4 és stopcél 5.8; ez utóbbi nem lehet előre beküldött egyszerű piacképes back-limit megfelelő trigger nélkül.
- A fractional-magnet ladder egyik ábráján 2.74,2.72 után 1.60,1.59, majd ismét 1.60,1.59 jelenik meg: szerkesztési hiba, nem valódi monoton ársor.
- A 98%-os BSP-könyv és a futamvégi in-play minimumok nem bizonyítják, hogy a trader mindent ugyanakkor és a saját méretével végre tudott hajtani. Az all-lay képen kifejezetten nyolc nem teljesen matched fogadás látható; a végrehajtást nem szabad kész ténynek venni.
- A kamatos kamat képe az első évben 1795.86-ot ad, helyesen; a második évi szöveges 3255.10 hibát nem javítja.
- A Big Buck's táblázat az emelkedés százalékát decimális oddsra számolja, pl. 1.3→1.66:27.69%; más fejezetek fractional oddsra. A definíció változik, nem szabad egy feature-be keverni. 4.74→40 esetén743.88% helyes decimális emelkedés, de nem realizált return. A minta kiválasztott sikeres ló, végponti maximum és saját fill között nincs bizonyított kapcsolat.
- A WBR-kép évösszegei:25066 fogadás,13888 sikeres lay,5268.57 profit10-es téthez. A találati összarány **55.4057%**, nem a táblában álló55.48%. Az éves darabszámok és profitok összege egyezik a total sorral; a találati total hibás. Az éves találati arányok többnyire kerekítéssel helyesek. 2010 veszteséges, az összprofit nagy része2013–14-ből jön. A liability, egyedi oddsok, drawdown, selection-időpontok, díjak és adat-hozzáférési feltételek hiányoznak. A „proofed to Racing-Index” bélyeg önmagában nem függetlenül ellenőrzött mai teljesítmény.

## Lezárás

A mellékelt EPUB teljes rendelkezésre álló szövege és összes képe feldolgozva. A legjobban átvihető tudás a teljes állapotfüggő kifizetés, tényleges teljesülés és költség naplózása; a stopkockázat és önbecsapás kezelése. A leírt lóversenyes, piaci árfolyamra építő stratégiák nem szolgáltatnak igazolt NBA/MLB/NHL/NFL/WNBA modellt. Aktuális platform- és API-állítások későbbi hivatalos ellenőrzést igényelnek.
