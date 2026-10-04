# B17/B18 — Sharp Sports Betting — teljes olvasási jegyzet

Stanford Wong, Pi Yee Press, copyright2001/2006/2009/2011, ISBN978-0-935926-44-6. **B18 teljes 419053 karakteres szövege, 31 egysége és 128 beágyazott képe elolvasva/áttekintve. A B17 szöveg- és képtartalmi egyezését külön ellenőriztem.**

B17 ésB18 teljes szövegének whitespace-token összevetése: kizárólag28 ismételt17. fejezetcím többlete aB17-ben; egyéb szövegtartalmuk egyezik. Bizonyíték: B17-B18-text-comparison.json. A képek tartalmi egyezését a lent ismertetett összevetés igazolja. A könyv jelentős része régi NFL-mintákon alapul, nem mai paraméterkészlet.

## 1. fejezet — fogadástípus és szerződés

Spread, moneyline, total, részidő, futures, field bet elkülönítése; tét és nyeremény pontos megkülönböztetése. MLB listed-pitchers vs action szabály, változó dobó miatti érvényesség és átárazás szolgáltatófüggő. A régi o/u jelölésből levezetett−120/even nem mai univerzális konvenció. NHL moneyline/puckline elnevezés mellett ma az időszak és hosszabbítás kezelése is szükséges. A nyomtatott ajánlat nem feltétlen aktuális; a döntés idején elérhető ár számít.

A−110→−105 javulás50%-os esélynél2,1645 százalékpont várható ROI-javulás, a könyv2,2%-a kerekített. A12 sportsbook összevetésével ígért2% általános nyereség nem itt bizonyított konstans. A Braves–Yankees2000 World Series megnevezés történetileg gyanús (külön ellenőrzendő). A jegyvisszaváltás/lejárat és a helyi jogi állítások korabeliek.

## 2. fejezet — pénzkezelés

A MinWin a célzott nettó nyeremény, a MinEdge a nettó várható ROI minimuma. Ha nincs előny, a hozamcélhoz optimális tét nulla. Előre rögzített becslés, külön naplózott magasabb-edge csoport és bankroll elkülönítése hasznos. Meglévő tőkét és összes nyitott kitettséget figyelembe kell venni.

A fix nyereményre méretezés ugyanazon edge mellett összhangban lehet az egyedi bináris Kelly-vel: nettó odds b, edge e=p*b−(1−p), f*=e/b. Ám a könyv MinWin arányai csak heurisztikus tört-Kelly szabályok; párhuzamos fogadásokhoz az edge és odds mellé közös eloszlás/függőség is kell. A1,5–2,5% MinWin nem azonos a kockáztatott bankrollarányával:−1200-nál ennek12-szerese a tét. Nincs általános „rossz hétvége nem nulláz le” garancia.

Belső hiba: a nagyobb-edge aránnyal való „divide” szöveg ellentmond a helyes szorzást alkalmazó példáknak. A tízszázalékos edge az ötszázalékoshoz képest kétszeres tétet ad azonos odds mellett. A nagy oddsos példák számtanilag ezt követik, de az edge nagyságát továbbra is bizonyítani kell.

A könyv kijelentése, hogy a bankroll elvesztését csak52,4% alatti találati arány okozhatja, **téves**. Pozitív EV, túl nagy tétek, korreláció és véges minta mellett is el lehet veszíteni a bankrollt. A néhány száz fogadás után a feltételezett edge-et meghaladó realizált ROI sem kötelező; a mintavariancia és becslési hiba számít. A flat bettingnek a könnyű könyvelésen kívül az edge-becslési bizonytalanság korlátozása is lehet indoka.70% találati arány csak az oddsok ismeretében értelmezhető; a fejezet−110 kontextusát nem szabad minden piacra általánosítani.

## 3. fejezet — online piacok

Az UIGEA/Wire Act/Nevada és offshore leírás történeti. Nem alkalmazható automatikusan a jelenlegi magyar felhasználóra vagy platformokra. A tényleges ajánlat megerősítése, win-vs-risk mező, érvényesség, payout, díj, void és teaser-push szabályok ellenőrzése fontos. Partnerkockázat, ki nem fizetés, utólagos törlés és késleltetett ár valós operatív kérdések; a Nevada biztos kifizetésére/palpable-error teljesítésére vonatkozó abszolút állítás nem általános mai szabály.

A cash bonus és stake-not-returned freebet eltérő kifizetés. A fejezet−110 freebet48%-os értéke50%-os valódi nyerési esélynél hibás: .5×100/110=.454545, azaz45,45% a névértékhez képest.48% körüli érték csak más esélyfeltétellel jön ki. Három független coinflip láb6:1 nettó kifizetésével .125×6=.75 helyes. A háromlábas parlay mint általános optimum függ a konkrét oddsoktól, limitektől és szorzóktól; nem örök szabály. Rollover és visszavonhatóság tovább módosítja az értéket.

A camouflage, token fogadás, referral és affiliate ötletek forrásleírások; nem építendők a felhasználó által kért saját sportmodell követelményei közé. A minimum1–5 dolláros tétet nem emeljük azért, mert egy régi könyv szerint az kis összegnek tűnne a bookmakernek.

## 4. fejezet — matematika

−110 nullszaldó110/210=52,38095%;50%-os p mellett nettó ROI−4,54545%. A bookmaker kiegyensúlyozott nem-push kifizetésénél a hold10/220, nem a probability overround4,7619%. Push esetén a realizált hold csökken. Nem egyenletes action mellett a vig nem garantál profitot.

A könyv „win rate” kifejezése néhol ROI-t, máskor találati arányt jelöl; a tudásbázis ezt külön változóként kezeli. EV jelölése even money is lehet, szövegkörnyezet szükséges. Árkonverziók, break-even és a p_win+p_push+p_loss=1 ellenőrzés hasznos; automatizálva pontos képlet kell interpoláció helyett.

Chicago-példa p_win=.55,p_push=.10,p_loss=.35, tét110: várható nettó16,5 és bruttó126,5. Az intermediate sor tévesen115,50+110-et ír115,50+11 helyett; végeredménye helyes. Maddux fair−350, kínált−280: pontos p=7/9, nettó ROI5,5556%, nem a korai kerekítéssel kapott5,7%. Minimum10% edge esetén pontos legrosszabb negatív odds−241,3793;−241 már teljesíti. A ticket bruttó értékéből a tétet le kell vonni a profithoz.

## 5. fejezet — bookmaker és saját elemzés

A bukméker nem feltétlen egyenlíti ki a két oldal tétjét: profit és elfogadott kockázat mellett árazhat, a vevői kereslet rugalmasságát és információt figyelembe véve. A nagy népszerű piac és a kis prop különböző költség/likviditás/információ környezet. Saját tét által mozgatott záróár nem független bizonyíték saját becslésünk helyességére. A Sosa–McGwire egészség/életkor/PA anekdotában a sportinformáció jó irány, de kvantifikált előny nincs bemutatva.

Az eltérő meccsek nem feltétlen függetlenek; a könyv diverzifikációs következtetése túl erős. A−7 versus−7,5 eltérés sem jelenti önmagában a bookmaker edge elvesztését: nyerési esélynek a vigküszöböt is át kell lépnie. Az oddsok és a közönség viselkedése nem azonos a valódi sportvalószínűséggel.

A kéréshez releváns része a sportadatokból készített előrejelzés, állapottól függő játékoserő, szabályváltás és a döntéskori hír. A power rating különbség csak várható/centrális pontkülönbség, önmagában nem a teljes margineloszlás. Az XFL játékóra és kontaktusszabály változása mechanikailag hathat a plays/score folyamatra; a közölt10% konkrét retrospektív eredmény, nem általános korrekció.

A hazai underdog, must-win, intenzitás, újságcikkből levezetett sértettség és vereség utáni visszaesés anekdotáit hipotézisként kell kezelni. Stadionnépszavazás és győzelem nem bizonyít okságot. Jó, hogy a Green Bay példánál a szerző maga elismeri: a nyerő bet is lehetett rosszul árazott döntés. Az „NBA intenzitás felülírja a tehetséget” túl általános. Valószínűségeket kell becsülni, és lehet, hogy egyik oldal sem fogadható.

## 6. fejezet — rajongói pénz

Lokális kereslet torzíthat árakat, de csak tényleges árral és saját valószínűséggel együtt jelent edge-et. Az internet globalizálódása változtatja a régi Las Vegas-i helyi hatást. A szerző utólagos boksz/lóverseny/NBA példái nem kontrollált bizonyítékok.

De La Hoya–Trinidad háromutas állítólagos arbitrage: két KO és „goes distance” csak akkor fed le mindent, ha TKO, DQ, technical decision, no contest és minden voidszabály kompatibilis. A szöveg nem bizonyítja ezt.200+200+270=670, garantáltnak feltételezett minimum700:30/670=4,4776%, nem4,6%; pontos269,23 harmadik tét mellett≈4,598% adódik. A−200/+210 kétoldalú kombináció csak egyező elszámolás és elfogadott lábak mellett scalp.

## 7. fejezet — statisztikai bizonyíték

Erős rész: a hipotézist és küszöböt előre írjuk le; tanításhoz és módosításhoz látott adat nem érintetlen tesztadat. Ismételt módosításnál az új adatok is fejlesztési mintává válnak. A régi szezonok eltérhetnek az aktuális folyamatoktól. A torna vezetőjének rekordja több résztvevő közül kiválasztott maximum; ráadásul a stale tornaline nem feltétlen fogadható pénzért.

Korlátok/javítások: (W−L)/sqrt(N) p0=.5 nullt vizsgál, nem a−110 nullszaldós p0=11/21 hipotézist. Az npq binomiális variancia **pontos**; a normálközelítés pontatlan a széleken, nem maga a varianciaképlet. Az egyoldali pontos farok és a kétoldali körülbelül2SE elkülönítendő. A7–0 valószínűsége.5^7≈.0078125 egyoldali; kétoldali ennek kétszerese.

A4SE mint „2SE fejlesztés +2SE teszt” nem érvényes statisztikai levezetés. Az1:1000 vagy1:10000 küszöb önmagában nem oldja meg tetszőlegesen sok próbálkozás és ismételt betekintés torzítását; és nem garantál jövőbeli nyerő rendszert. A szignifikancia nem monoton nő minden új meccsel, még valós hatás mellett sem. Függő, eltérő oddsú/kimenetű tippekhez nem elég az i.i.d. binomiális modell. A későbbi validálási módszerek online szakirodalmi kiegészítést igényelnek. A65–35 példa egyik sorában66–35 elírás van.

## 8. fejezet — parlay

A board parlay és a nyomtatott parlay card külön. Független eseményeknél közös p szorzódik; nettó és bruttó odds ne keveredjen.13:5 kétlábas parlay p=.55² mellett EV=.089 helyes; két−110 láb bruttó odds-szorzata≈3.6446, nem3.6. A52,5% csak közelítő nullszaldó. A vig halmozódik coinflip lábaknál. Push miatt csökkentett lábszám szolgáltatószabály, nem általános aksioma. 

## 8. fejezet — folytatás és teljes feldolgozás

Három független, egyenként .55 valószínűségű láb 6:1 nettó kifizetéssel: 7×.55³−1=.164625 várható ROI. A könyv másik szorzópéldájában 5×1.625×2.70=21.9375, nem 13.81. A közös valószínűséget korrelált lábaknál nem szabad a marginális esélyek szorzatával helyettesíteni. A könyv „covariance=1” megfogalmazása hibás: a korreláció lehet ±1, a kovariancia mértékegységes; a nulla korreláció általában nem jelent függetlenséget.

A vihar, játéktempó és a rájátszásba jutási motiváció több fogadást közösen befolyásolhat. Az időrend is számít: egy korábbi meccs eredménye megváltoztathatja a későbbi mérkőzés ösztönzőit. Ezek a példák közös feltételes modell szükségességét szemléltetik, nem bizonyítanak általános profitot. A St. Louis–Tampa négy kimenetből háromra tett példája a feltételezett .95 összvalószínűségnél 342 várható visszafizetés/300 költség, azaz 14% ROI; az alapul szolgáló .05 negyedik-kimeneti esély csak vélemény. Bónuszhoz kötött parlay és kitöltőláb: szabály- és árfüggő történeti példa.

## 9. fejezet — Poisson és proposition bet

A PMF e^(−λ)λ^k/k!, a CDF P(X≤k), a két független számláló különbsége közös PMF-összegzéssel számítható. A függelék A-táblái PMF-ek, B-táblái inkluzív CDF-ek, C-táblái független számlálók W/L/push esélyei. Mind egész százalékra kerekített közelítések; a 0 és 100 nem feltétlen lehetetlenség vagy bizonyosság.

**Lényeges korlát:** az átlag önmagában nem határozza meg az eloszlást. A ritka vagy egyenként bekövetkező esemény még nem szükségképpen Poisson. Az eltérő kísérletszám, közös játékhelyzet, változó intenzitás és függés túl- vagy aluldiszperziót okozhat. Sacket, tackle-t, assistot, futást, elkapást, büntetőt nem indokolt automatikusan Poissonként kezelni. A két csapat mezőnygóljainak függetlensége is vizsgálandó feltételezés.

**Eloszláseltolási hiba:** ha A~Pois(2.2), akkor A−1 nem Pois(1.2). A varianciája továbbra is 2.2, és negatív értéke is lehet. A handicapet a kimeneti eseményben kell érvényesíteni. Egész számlálóknál −1.5 nyerési eseménye A−B≥2; a push tömeg önkényes megfelezése nem helyes. A 7. táblázat a nagy számlálók átlagkülönbségéből egyetlen valószínűséget ad, de a szórás a két intenzitás összegétől is függ; így nem általános átváltótábla.

Újraszámolt példák, kizárólag a könyv Poisson/függetlenségi feltételezése mellett:

- λ=4.7: P(X≤4)=.4946087861; P(X=5)=.1738296992. Az under 5 −120 várható ROI-ja 8.061247%, a szöveg 8.7%-a a durva kerekítésből eltér.
- λ=.4: P(0)=.670320; −200 mellett ROI .5480%, nem pontos nullszaldó. Ennyire kis eltérés könnyen eltűnik modellhibával.
- Duke 26.6 hárompontos-kísérlet vs Maryland 18 büntetőkísérlet −6.5: P(A−B≥7)=.6221670692, nem a könyvbeli ~65%.
- 15.4 vs 12.4 büntető −1.5: P(A−B≥2)=.6114039898. A függetlenség itt sem igazolt.
- Két irányítónál .9×.7=.63 csak függetlenséggel közös esély; a margók önmagukban nem elegendők.

A szerző helyesen óv attól, hogy passzolt yardot és kosárlabdapontot ugyanúgy Poisson-változóként kezeljünk. A mintákból becsült λ bizonytalansága a függelékből teljesen hiányzik.

## 10. fejezet — szezonösszegek, kötött tőke, middle

A szezonfogadás tőkét köt le; az éves alternatív hozam és a hátralévő idő releváns. A 30% éves→15% féléves csak egyszerű kamatozású közelítés. A void/halasztás/szezonbefejezés szabályát szerződésenként kell olvasni.

A szezonbeli győzelemszám binomiális modelljéhez független, azonos p-jű meccsek kellenek. Eltérő ismert p-k esetén Poisson-binomiális az eloszlás, varianciája Σp_i(1−p_i); rejtett csapaterő-bizonytalanság viszont pozitív meccsek közötti kovarianciát okozhat. Az átlagból nem következik a middle tartományának tömege. Az 81 körüli baseballtömeg nem másolható automatikusan a 85-ös vonalhoz. A 16/82/162 mérkőzéses példák korabeliek.

A két tét együttes, kimenetenkénti pénzáramát kell számolni, külön a teljes és fél middle-t. A könyv százalékos P jelölése és dollár-EP képlete egységkeverést tartalmaz. Ha p∈[0,1], a két 100 nettó nyereményre méretezett láb, egyenlő külső farokesély és megfelelő vig-definíció mellett EP=200p−(vig1+vig2)(1−p)/2. Pozitív amerikai odds mellett a „negatív vig” lineáris átváltás pontatlan: +110-hez 100 nyereményre 90.9091 tét kell, nem 90.

A példák újraszámolva: p=.063, két 110-es tét esetén EP=3.23, ROI=1.46818%, nem 1.6%; p=.124 esetén EP=16.04, ROI=7.2909%; p=.184 esetén EP=28.64, ROI=13.0182%. Ezek nem mai MLB-előnyök, csak feltételes számolások.

## 11. fejezet — NCAA konferenciagyőzelmek

64 csapatos egyenes kieséses táblán 63 győzelem oszlik el. A csapat várható győzelme fordulónkénti továbbjutási esélyek összege, a lehetséges ellenfelek valószínűségével súlyozva. A pozitív erősségratingek r_A/(r_A+r_B) képlete szemléltető páros modell. A seedhez tartozó várható győzelem nem független a ratingek megválasztásától; majdnem egyenlő csapatoknál mindegyik várható győzelme közel 63/64.

A konferencia csapatai ugyanazon ágon találkozhatnak, tehát győzelmeik függnek, az eloszlás korlátos. A Poisson csak ellenőrzendő közelítés. A kiírt konferenciatotalok összege nem feltétlen várható értékek összege: mediánszerű fogadási küszöb és átlag különbözik. Így az 57 kontra 63 önmagában nem bizonyít under/over előnyt.

Belső hibák: SEC-példa hat csapatot ígér, de hét nevet sorol Virginia bevonásával; a megoldás már hatot használ. A 9-es push helyén 8-at ír. ACC-megoldásban „other SEC” szerepel. Big 12 under 7.5 esetén a 7 győzelem is nyer: a kerekített helyes p=.58, nem .43; −115 mellett ROI≈8.435%, így a hiba a döntés irányát is megváltoztatja. Big East feladatban 7.5, megoldásban 5.5 van; az under −105 mellett indokolatlanul 125 tétet használ. Ha a megoldás 5.5 küszöbét és .67 esélyét vesszük, −105 ROI≈30.81%, nem 21%.

## 12–13. fejezet — hazai pálya és line szerinti NFL-adatok

A hazai pálya előnye empirikus, korszak- és helyzetfüggő hatás. A rájátszás hazai csapatai erősebbek is lehetnek a kiemelés miatt. Átlagos pontkülönbség, medián és ATS-küszöb nem felcserélhető; az átlagos 3 pont nem bizonyít minden spreadnél 3 pontos hatást. Semleges helyszín sem szünteti meg automatikusan az utazás/pihenés/közönség különbségét.

A 13–15. táblázat 1990–2010 adatai: 5114 mérkőzés, 2981 hazai és 2129 vendéggyőzelem, 4 döntetlen; pontok 113086–99198, átlagos hazai többlet≈2.7. A +3 kiigazítás utáni 2331–2343–440 mutatja a diszkrét ponttömeg szerepét. A 16. táblázat line szerinti ATS W/L/push összesenje 2596–2595–133; a neutral, Super Bowl és áthelyezett meccsek kezelése eltér a különböző táblázatokban. A táblázat kizárásai és nézőpontja a felhasználás része.

## 14. fejezet — pontvásárlás és moneyline

A 17–18. táblázatot maga az EPUB kiadás hagyta el; nem szövegkinyerési veszteség. A 19. táblázat ±2 line-sávjai átfedik egymást, bizonyos pick/±1 események két perspektívából is szerepelnek. Ezek nem független minták. A 20. táblázat szubjektíven kerekített, különböző feltételekhez tartozó ponttömegeket tartalmaz; nem egy közös eloszlás. A 17 pontos különbség táblázatban 6%, szövegben 5%.

A fél pont értéke a pontos kifizetéstől és attól függ, hogy veszteséget pushra vagy pusht nyerésre változtat. A „10 cent ára 5%” közelítés nem általános. Ha a −3 push esélye .10 és két külső farok .45/.45, a −2.5 fair odds −122.222; −120 mellett ROI .8333%, nem pontos nullszaldó. Két teljes middle −2.5/+3.5 láb −120-nál p=.10 mellett EP=2 a 240 téten; −110-nél EP=11 a 220 téten. A fél middle −110-nél EP=1 a 220 téten. A nulla pont tömege kicsi lehet, de nem univerzálisan nulla.

A 21. táblázat moneyline-átváltásai közelítések, feltételezett ponttömegekkel. Az alacsonyabb break-even győzelmi küszöb önmagában nem garantálja a nagyobb EV-t két eltérő kifizetésű terméknél. A tényleges pontkülönbség-eloszlásból kell mindkét szerződés várható pénzáramát számolni. A 2 százalékpontos küszöbkülönbségből nem általánosan következik 4% edge.

## 15. fejezet — NFL-totalok

A 23. táblázat feltétel nélküli tényleges totalokat tartalmaz és kizárja a Super Bowlt; a 24. a kiírt total ±2 sávjára feltételes és bevonja. A minták eltérnek, nem keverhetők. Az 1990–2010 összminta 5338. A 25. táblázat szerint 44 és 45 tömege egyaránt 3%; így a 43.5→45.5 teljes nyerési oldalcseréjének összhatása 12%, a szövegbeli 14%-kal szemben. Egy szélesebb middle sem automatikusan pozitív EV: a konkrét tömeg és ár számít. Hosszabbítás és időelőtti befejezés szerződésfüggő.

## 16. fejezet — teaser

A 3 és 7 körüli diszkrét tömegek fontosak, de a kiválasztott 1999–2010 sávokból nem lesz mai univerzális szabály. Az egyes lábak győzelmi arányán túl a közös kimenetek és a push/void szabályok is kellenek: lehet visszafizetés, csökkentett lábszám vagy a vesztes láb dominanciája. Két független, push nélküli, azonos esélyű láb −110-nél sqrt(11/21)≈72.37% szükséges.

A széles −9…+3 kiinduló sáv hatpontos teasere a 27. táblázatban 68.0% W,29.3% L,2.7% push. Push nélkül feltételesen csak 69.9% nyer, ez a fenti küszöb alatt van. Még a bármely push esetén teljes visszafizetési szabállyal is két független ilyen láb ROI-ja −6.3965%. A megfelelő szűk sáv kiválasztása és utólagos szelekciója nem hagyható figyelmen kívül.

A kiválasztott hatpontos rekord 461–169–5 feltételesen 73.1746%; a 6.5 pontos 616–216–2 arány 74.038%; a hétpontos összeg 679–236≈74.208%. A hétpontos vendégdog 218–78 azonban 73.6486%, nem 76%. A hatpontos hazai dog szöveges 120 győzelme és a 29. táblázatból összeadott 119 további eltérés. A hétpontos összarány a 27. táblázatban 70.1/28.6/1.3, a prózában 70.0/28.6/1.4. A félpontos javulás nem állandó 1%, és a teaser kiinduló line-ja sem feltétlen egyezik a sima fogadáséval.

## 17. fejezet — a bajnok körüli minták és valódi holdout

Hasznos az eredeti 1985–2000 vizsgálat elkülönítése a 2001-es publikáció után keletkezett 2001–2010 eredményektől. A bajnok elleni, a következő és előző ellenfél elleni stratégiák összevonásakor azonban ugyanaz a meccs több szerepben is előfordulhat. A 143 fogadást ezért nem szabad 143 független Bernoulli-próbának tekinteni. A nullhipotézis a vig miatt sem pusztán .50. Az összevont stratégia és kiválasztott időablak előzetes rögzítése külön kérdés.

A szöveg és az évösszesítő képek elolvasva. Publikáció előtt: bajnok ellen 52–36–1; over 47–41–1; következő ellenfél ellen 42–39–4; előző ellenfél ellen 33–34–1. Utána: 28–25–2, over 34–21, következő 29–20, előző 29–12–1; a három oldalfogadási csoport összesen 86–57–3. Ez nem mai profitgarancia és nem igazolja bármely erős csapatra az általánosítást.

Adatminőségi jelzések, később külső ellenőrzéssel tisztázandók: 1987 Buffalo–Giants total ismeretlen, mégis under minősítés; 1994 Washington −13 és 17–21 W összeférhetetlen; 2000 SF+17 P mellett a pontsorrend fordítottnak látszik; 2003 első heti total 36 vs 49.5; 2004 Jets–SF line szöveges és soronkénti eltérése. Az évösszesítők egy része kétszer szerepel az EPUB-ban.

## Függelék, szótár, bibliográfia és vizuális ellenőrzés

A teljes szöveg a szótárral és bibliográfiával együtt elolvasva. A binomiális modellhez a két kimenet önmagában nem elég; a függetlenség és azonos p is feltétel. A szótár nulla korreláció/függetlenség megfogalmazása pontatlan. A „dime line” megfogalmazása és példája sem teljesen következetes. Az idézett Kelly 1956 elsődleges forrásként később online ellenőrizhető.

B18: mind a 128 beágyazott kép áttekintve: 126 táblázat/összesítő képe 69 kontaktlapon, továbbá a borító és smiley. A numerikus táblázatok struktúrája, fejlécei és a fenti példák ellenőrizve; ez nem valamennyi táblázatcella független újraszámolása. A C5 címében „12 to 3” elírás, az oszlopok 2.2–3.0; több C-tábla címbeli tartománya kerekített és eltér a tényleges szélső oszloptól. A kontaktlap méretezésétől levágott C2 bal oldala a B17 eredeti képen külön ellenőrizve.

B17: mind a 126 képnek különböző, de azonos tartalmú B18-párja van. Azonos méretek és 128×128 szürke képeken legfeljebb 1.914/255 RMS eltérés; ez JPEG-újratömörítéssel konzisztens. Külön vizuális ellenőrzés: 00001,00048,00114. A B18 két többletképe C1/C2 alsó sorainak ismétlése. Bizonyíték: B17-B18-perceptual-comparison.json. A szöveg- és képösszevetés alapján B17 tartalmát B18 teljes olvasása lefedi.

## Tudásbázisbeli súly

Erős forrás az odds, feltételes várható érték, szerződés, a diszkrét ponttömeg és a fejlesztési/tesztadat elválasztásának megértéséhez. Poisson- és közös kimeneti közelítéseit, történeti sávstratégiáit és több számolási példáját kritikával kell kezelni. Önálló mai NBA/MLB/NHL/NFL/WNBA sportmodellt nem szolgáltat. A korabeli mintákból átvett állandók nem tekinthetők jelenleg validált paraméternek.
