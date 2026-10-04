# B08 — Mathletics, 2. kiadás — teljes mellékelt könyv feldolgozva

Wayne L. Winston, Scott Nestler, Konstantinos Pelechrinis, Princeton University Press, 2022. 609 PDF-oldal, 60 fejezet, hat rész. Ténylegesen elolvasva: 1195992/1195992 karakter, minden fejezet, előrész, bibliográfia és index. Összesen180 különböző PDF-oldal képleteit, ábráit vagy táblázatait oldalképen is ellenőriztem. A lenti köztes megjegyzéseket a záró vizuális ellenőrzés pontosításai felülírják; a külső hivatkozások ellenőrzése külön későbbi munka.

## Könyv és módszertani keret

Excelben bemutatott, részben Python-kóddal kiegészített sports analytics tankönyv: baseball, NFL, NBA, foci/NHL/röplabda/golf/esport, fogadási matematika, Bayes/mátrixfaktorizáció/hálózat/Elo/DFS. Külső kísérőforrásjelölt mathleticsbook.com és github.com/mathletics-book; később aktuálisan ellenőrzendő, még nem letöltött. Az eszköz helyett a modell feltételezése a fontos. Előszó, köszönet, rövidítések és tartalomjegyzék végigolvasva. A „Kelley” fejezetcím a Kelly-név variánsa/elírása.

## 1. fejezet: Pythagorean win expectancy

P=RS^k/(RS^k+RA^k), R=RS/RA, így P=R^k/(1+R^k). Pozitív futások és k mellett monoton, [0,1]; RS=RA=0 nincs definiálva. A baseball k≈1.8/2, NFL2.37/2.8, NBA13.91/14.4 könyvbeli korszak- és veszteségfüggő paraméter, nem univerzális állandó. A bemutatott baseball MAE-minimum1.8 és MAE1.99%; k2 MAE2.05%. MAE nem átlagos előjeles hiba; a második bias, nem önmagában értelmetlen mérőszám. A korabeli teljes szezon RS/RA-ból visszaillesztett W% nem pregame modell. Időalapú validáció kell az exponent kiválasztásához.

A szerző külön tárgyalja jövőbeli playoffjóslatot és eltérő dobórotációt: a szezontotal csak durva alaperősség. A 2005 Nationals50–32 kezdése mellett0.991runratio regressziót indokolhat, de egyetlen90vs99előrejelzés és81végkimenet anekdota. A csere utáni850→870RS/800RA győzelmi különbség1.9 csak minden más változatlan és azonos playingtime mellett. A 10run/win közelítés helyi.

Forráskritika: baseball MAE előbb2.17%, később2.05%; NFL ábra fejléc0.051, szöveg2.37k mellett6.39%; playoff46/84=54.76%,44/80=55%, mégis „csak55%” és később „54%alatt”: összehasonlításból nem következik a Pythagorean módszer jobb volta, a lefedés is eltér. Normális, központos hibánál MAE=σ√(2/π), így2.5MAE≈1.995σ és kb95.4% intervallum; ez feltételes közelítés, nem automatikus kalibrált jövőbeli intervallum. Sok csapat közül kiválasztott outlier nem bizonyít tankolást. Az Excel egyváltozós Data Table mechanizmus elolvasva; 1–3 tizedenként21érték, a N7:N26 csak20cella és az ábra1.1-től indul: tartományhibajelölt.

## 2. fejezet: Runs Created és exposure

RC=(H+BB+HBP)*TB/(AB+BB+HBP), TB=1B+2*2B+3*3B+4*HR. Az összesített csapatszinten működő nemlineáris kapcsolat extrém játékosra való alkalmazása extrapoláció. Ezt a könyv maga hangsúlyozza. Totalcontribution és per-opportunity skill külön kérdés; több PA miatti nagyobb RC önmagában nem „rossz” rangsor, más célt mér. Outsbecslés .982AB−H+GIDP+SF+SH+CS és26.83out/teamgame; hibaarány és játékosszituáció nem univerzális. RC/game=RC*26.83/outs, nem tényleges egyedi meccsátlag. Kilenc azonos hitter hipotetikus ellenpélda a nonlinear interakciókhoz; a többiekkel együtt játszó csapathoz adott marginális hatás hasznosabb.

Trout2016RC134.02,9.39/game;Bryant129.09,8.11;Cabrera2013147.54,10.61. Táblázathibák ellenőrizendők képen: Christian190/700=.2714, a szöveg.257; Gregory5.90a táblában vs5.88szöveg. A Fig2.2 képletoszlop-hivatkozások önhivatkozó/eltolt alakúnak látszanak (J2-re J2 képletben), ezért képi megerősítés nélkül nem tekintem végleges kódnak.

## 3. fejezet: lineáris run weights

210csapatév2010–16 OLS: intercept−411.813, BB/HBP.326,1B.459,2B.805,3B1.072,HR1.428,SB.250,CS−.254; R².901. CS elhagyásával−422.321,.328,.462,.809,1.057,1.432,.204; R².9004. A koefficiensek aggregált feltételes összefüggések, nem változatlan oksági runértékek. CS p=.183 nem jelenti, hogy CS-nek nincs értéke vagy hatása; multikollinearitás, kis mintaszám és hiányzó exposure is számít. Automatizált p<.05változószelekció nem prediktív validáció.

**Lényeges statisztikai hiba PDF44:** a p-érték nem annak valószínűsége, hogy a nullhipotézis igaz, a változó haszontalan vagy az eredmény pusztán véletlen. Nullhipotézis és modellfeltételek mellett a megfigyelthez legalább ilyen szélsőséges tesztstatisztika valószínűsége. A kis p nem bizonyít jövőbeli előrejelző erőt vagy okságot. R² magas volta itt részenként ugyanazon szezon kimeneteiből rekonstruált runsra vonatkozik.

OBP+SLG regresszió−738.752+2338.122OBP+1707.332SLG,R².885; az OBP koefficiens1.37-szeres a SLG-hez képest, de skála/eloszlás/együttmozgás miatt ez nem univerzális fontossági sorrend. OBP standard nevezője nem minden PA (SH/interference kivétel). Above-average módszer: fix csapatouts mellett hozzáadni játékos eseményeit és a többiek eventjeit outsarányosan csökkenteni. Fig3.6Bryant+58.3runs kontextusfüggő, nem replacement WAR.

Belső eltérések: PDF43egyenlet46*single látszik .46helyett;3.1történeti táblázat „Our Regression” .63single/.71double eltér a bemutatott.462/.809-től. PDF46„26runs per game” RC-hiba előzőfejezet21runs/teamseason helyett; „Absolute Error” oszlopban negatív számok. PDF48Troutouts366.118 kihagyja az előző17extraoutot; szöveges képletből−Hhiányzik;26.72 vs előző26.83out/game. Cabrera singles122a táblában, de193−26−1−44=122, előzőfejezet102hibás; emiatt RC148éskésőbbi11.25vs10.61is eltér. Ezek számszaki auditot kívánnak, nem kész referencia-fixture-ök.

## 4. fejezet: Monte Carlo baseball

Iid50%HR50%out példánál a 3outig szerzett HR száma negatív binomiális, E=3;9innings27runs. RC54ésLW36.77rossz extrapoláció. A véletlen szimuláció értéke az állapotok és események explicit kezelése, nem a „Monte Carlo” címke; hibás mechanizmust a sok minta csak pontosabban szimulál.9hitter/lineupazonosítás, outs,baserunners, immediate runs és terminálás szükséges.

A könyv17eseménye:K,BB,HBP,error;3singlehossz;2doublehossz;triple,HR,GIDP,normalgroundout,line/infieldfly,3flyhossz. Determinisztikus átmeneti egyszerűsítések: error mindenkit1base, 30/50/20%single,80/20%double,53.8/15.3/30.9%GO/line/fly,GOfeleGIDPpotenciál. Ezek1966adat+szakértői értékek; korszerű inputként nem automatikusak. Kétoutos GIDP, force-third-out, runner advancement és scoring szabályait pontosítani kell. SB/CS/WP/PB/balk kihagyva. Ligaátlag scoring1%-onbelüli egyezés nem igazolja tail,egyén,lineupvagy más korszak helyességét. MC sampling error és modellbizonytalanság külön.

Bonds232BBebből120IBB: a kilencBondscsapatban az IBBmechanizmusa változna. Az IBBsimakivonás és az összes többiesemény újranormalizálása nem teljes oksági ellenforgatókönyv. Az átlaghitter ésTrout PA-véletlen keveréke nem fix battingorder; a PAeloszlást maga az offenseis alakítja. A with-Trout actual717 és without-Trout simulated626 összevetésében a szimulátor alapeltérése is belekerül; mindkét állapotot ugyanazzal a modellel kell számolni és validálni. A Pythagoreanból nyert10.91winsnem mért oksági hatás.

Hibajelöltek:36.77/9=4.086,nem4.01;Ulam1930s atomreakciótörténet külön ellenőrzendő. SH+SB jelölés itt feltehetően SF+SH (SB máshol steal), címkeütközés. AngelswithoutTrout5360PAés4962+355+40=5357eltér;681/6153=.11068,nem.102, később helyesen11.1%;161vs162szorzó Pythagoreanpéldában. DataTableleírásJ3helyettkimenetJ5/összegzésJ3 lehet, ésAVERAGE(I6:I1005) iterációs sorszámot átlagolna500.5-re,nem runs3.01; a tényleges runs J-oszlopot kell átlagolni. Képi ellenőrzés hátra.

## 5. fejezet: pitcher forecasting, DIPS/FIP/Statcast

ERA függ scoringdöntéstől, védelemtől és inheritedrunners reliefkezeléstől; pitcherW/L a saját offense-től; save nyersdarabszám nem szituációérték. Könyvi2015→16ERA regresszió2.637+.3815*ERAelőző,R².1048,MAE.93; az azonos játékosok két évében survivor/exposureselection van. r ésR²kapcsolata egyszerűinterceptesOLSben; r0nemfüggetlenség,nemlineáris kapcsolat maradhat.

DIPS:K/BB/HBP/HR kevésbéfieldingfüggő, BABIP nem zérusképesség és nem kizárólag szerencse. A felsorolt évek közti rK.78,BB.66,HR.34,BABIP.24 történeti mintafüggő. DICE/FIP=C+(13HR+3(BB+HBP)−2K)/IP;C évente ligaERA-illesztés, baseballRkönyv egyszerűsített FIPjétől eltér. FIP− park és ligaskála ellenőrzendő hivatalos definícióból.

**Összehasonlítási hiba:**2018DICE→2019ERAmin100IPmintaR².1352,MAE.64 összevetve2015ERA→2016mintaR².1048,MAE.93nem tiszta head-to-head, másévek és beválogatási szabály. Az előrejelzés célidejének IP>=100feltétele már jövőbeli szelekció. Ugyanazt az időszakot és univerzumot kell használni. KershawERA1.80nagyobb(rosszabb),mint1.75DICE, mégis „outperformed”szöveg; a példamagyarázat iránya hibás.

Statcast fogalmak:active spin,extension,perceived speed,movement,putaway,spinrate,xERA,EV,launchangle,barrel. Mind as-of és mérési korszak verzióval. xERA nem bizonyított „átlagos szerencsével kapott igaz ERA”; becsült batted-ball outcome modell és időben változó input. Extension4ft mellett60.5−4=56.5,nem56; a könyv90*(60.5/56)97.23mph példája belső mértékegység/számszaki eltérés. Launchangle0–10groundball kihagyja a negatív szögeket és a típus nem „resultofatbat”. Barrett98mph26–30°és100mph24–33° korabelidefiníció, később hivatalosan ellenőrizendő. Pozitív elv: nyilvános, időbélyeges forecastingaccuracyéskalibráció kell egymás mellé, a narratíva nem elég.

## 6. fejezet: döntés az állapotértékekből

24base-outstate itt outs-first bitstring, eltér B03outs-lastjelöléstől. EV=sum(p_i*v_i), feltételes várhatóérték; célváltozófüggő döntés. Általában runsmaximalizálás, késői tiedgame-ben P(atleast1run) vagy pontosgame-winprobability; expectedmargin és winprobability nem minden korai állapotban matematikailag ekvivalens, kockázat/erőkülönbség számít.

Bunt0out1st:empirikusp(.10safe,.70advanceout,.02DP,.08leadout,.10stayrunner),értékek1.46,.67,.10,.53,.53→.7124vs.8744statusquo. Gyenge85%K10%single5%BBhitter .85*.53+.10*1.68+.05*1.46=.6915mellett megfordulhat; a bunt-sikerek ugyanannak a hitternek képességfüggők. Egy run esélye.39988vs.417, ez1.7pphátrány, nem pontosindifferencia; bizonytalanság és extrasytovábbiállapotok kellenek.

Steal threshold p>(SQ−F)/(S−F),0out1stszámai .87,1.09,.27→.7317. Extra base0outfirst→thirdthreshold .93/1.15=.8087. Tagup1outbeforecatch→2outaftercatch:success1+.10,failure0,stay.36→p>.3273. Az immediate scoring rewardot hozzá kell adni az utóállapotRE-jéhez. Döntésifeltételhelyes a leegyszerűsített állapotban, az egészligás megfigyelt3–5%failure alapján „túlóvatos” stratégia viszont nem azonosított: lehetcsak97%-osvagy10%-oslehetőség,marginális80–90%nincs. A könyv lábjegyzetbenemlítselfselectiont, de a következtetés ennél erősebb.

Statehibák:PDF85state1000nakírja0out1st0100helyett;PDF89safe+bases1and2sort0111nekírja0110helyett,deRE1.46ahelyesállapotrautal. PDF88gyengehitterküszöb.93vs.87eltérés. P6AppendixRi=.5Ri−1+.5(1+Ri),R0=0→Ri=ihelyes50/50iidpéldában; általánosHRp,out1−p eseténRi=i*p/(1−p).

## 7. fejezet eleje: defense

Fielding%=(PO+A)/(PO+A+E)csak elért labdákon, rangefactor exposure-normalizált és pozíciós átlaghoz viszonyítható. K-ráták,handednessspray,park és startingposition változtatják az esélyszámot. Zone-probabilityresidual:madeout−p, pl.20%esélynélcatch+.8,miss−.2. Aggregálva expectedoutsaboveaverage; környezetfüggő runértékkel és lokálisrun/winátváltással összegezhető, de a mérési bizonytalanságot meg kell tartani. StatcastOAAhely/időalapú, infieldkomplexebb. OAA/UZR eltérőmodellek, nem abszolút igazság. Robles23OAA/19runs szöveg vs20a táblában. JeterGoldGlove ésRaysUZR történeti illusztráció, nem okozati bizonyítás. Shiftoptimalizációmestergondolat: battedballhelyeloszlás+védőelérési terület+pozíciós korlát; a könyv ötfeet/négyfielder/synthetic87%rightoldalú példája nem mostaniMLBszabályokat és dinamikusmozgást követő valósmodell. Fejezet itt még folyamatban.


## 7. fejezet lezárása, 8. fejezet: WPA

A shift nélküli .975 és shiftes .643 Howard OPS különbségből önmagában nem következik, hogy a shift tette tönkre a karrierjét: szezon, egészség, ellenfél, count és batted-ball selection is változhat. Catcher framing a called-strike reziduális hatásból runérték; counton végződő és counton áthaladó PA nem azonos. A framing táblázat top10-et ígér, 9 nevet mutat.

WPA=P(win after)−P(win before), előjeles, kontextusfüggő realizált eseményérték. Teljes eseménysorban teleszkóposan a végső0/1 és kezdeti p különbségére adódik össze; az offense/defense kredit kiosztása ettől külön attribúciós döntés. A .5 induló esély egyenlő csapatokra feltételezés, nem univerzális pregame alap. A hitter pozitív és a pitcher negatív kreditje ugyanazon esemény két oldala, ezért csapat- vagy összjátékos szintű összeadásnál nem duplázható. A fielding és baserunning run/10 utólagos hozzáadása WPA-hoz vegyes kontextusú mutatót ad, az SB/GDP esetében kettős elszámolás is felmerül. Nem nevezhető ettől standard WPA-nak vagy WAR-nak.

Empirikus win expectancy ritka állapotban bizonytalan; Markov/MC pótolhatja a mintahiányt, de csak modellfeltevéssel. Több millió szimuláció nem helyettesíti a modellkalibrációt. Az inning, top/bottom, margin, base-out mellett csapaterő, aktuális dobó/lineup, stadion, extras szabály is szükséges lehet. A könyvi24*18*21=9072 nem teljes valós állapottér: extra innings és ±10-en túli margin kimarad. Thomson kezdőesély27%→27.89%→másmodell30.1%, tehát a WPA is modellspecifikus.

## 9. fejezet: WAR

A referencia itt replacement, nem átlag. wRAA=(wOBA−leaguewOBA)/scale*PA; ehhez park/league korrekció, UBR+wSB+wGDP, fielding, pozíció és replacement credit, majd runs/win váltás. Évenkénti wOBA eseménysúlyok és skála nem állandók. A pozíciókorrekció táblázat C+12.5,1B−12.5,2B/3B/CF+2.5,SS+7.5,LF/RF−7.5,DH−17.5 teljes szezonpélda; tényleges védekező idő/pozíciómegoszlás szerinti szabályt hivatalosan kell ellenőrizni. A 20replacementruns/1/9teamPA csak egyszerűsítés.

Trout56.3batting+9.3running−.3fielding+2.5position+20replacement=87.8runs;8.78WAR10runs/win mellett. A FanGraphs9.4 eltérése részben park, league és átváltás. A 6.lépés szöveges összege kihagyja a pozíciót, a példaszámítás tartalmazza. A wRAA56.3runsot a szöveg wOBA-nak nevezi: egységtévesztés. Roster/minsalary/48wins és2016$3.08m/WAR bérértékelés retrospektív könyvi konstrukció. Összátlagos payroll/WAR nem marginális szabadügynöki ár; szolgálati idő, kontrollált szerződések, sérülési kockázat és jövőbeli várható teljesítmény fontos. A fair-salary táblából a replacement alapbér is kimarad. Winner's curse csak bizonytalan közös értékű, szelekciós feltételek mellett; nem bizonyít minden nagy szerződésről túlfizetést.

## 10. fejezet: parkfactor

PF=((homeRS+homeRA)/homeG)/((awayRS+awayRA)/awayG). 2016Coors985/720=1.368, Miami604/80 osztva733/81≈.834. Csak home scoringot osztani PF-fel a bemutatott neutralizálásban, nem az egész szezont. A könyv helyesen említi a végén az egyenlőtlen schedule és interleague torzítást, de előbb túl erősen állítja, hogy minden eltérés a park miatt van. Weather, roster/startingpitcher mix, travel/HCA, park-schedule, innings és umpire is eltérhet. Egy szezon home/away ráta nem oksági stadionparaméter; többévű shrinkage és ex-ante elérhetőség kell. Futás/HR/2B/3B/BB faktort külön kell kezelni; handedness és az adott nap állapota is számíthat.

## 11. fejezet: streak és hot hand — erős forráskritika

Iid Bernoulli is ad hosszú streaket. A 91W/71L fix elemszám véletlen permutációja azonban nem független sorrend (negatív feltételes függés), és91/162=.562,nem.60. A .6p-s szezon-szimuláció és a feltételes permutáció két külön nullmodell. Z-score bármely véges varianciás eloszláson definiálható; szimmetria nem elég normalitáshoz vagy ±2/3σ normális tailértelmezéséhez. Tőzsdei14.67σ és emberi magasság9.75σ összevetése normális extrapolációval nem megbízható ritkaságrangsor, különösen eltérő tail és keverékpopuláció esetén.

Wald–Wolfowitz runs: µ=1+2SF/N, variance=(µ−1)(µ−2)/(N−1). Kevés runs pozitív serial clustering, sok runs váltakozás. **Számszakilag ellenőrizve:**S=F=5,N=10 eseténµ=6 (nem5.5),SD=1.490712 (nem1.32),R2/R10 szimmetrikus z±2.68328. Ilyen kis mintára exact runs/permutation teszt indokolt. A Ripken táblában SDképletC4−1=outs−1-et mutat N−1 helyett; a megjelenített10.618 SD a helyes N596-ból jön, ezért szöveges képlet és kimenet ellentmond. Two-sidedp=2*(1−Φ(abs(z))), a könyviabs nélküli képlet negatívz-nél>1lehet.

**A Dawkins-értelmezés fordított:**250hit153miss mellett190.8expectedruns,220actual: több váltakozás, tehát anti-streak, nem hot hand. A9játékosos táblában a z előjele várható−tényleges, később Ripken/NBA táblában tényleges−várható. Az erre épülő szöveges következtetés nem konzisztens. Az átlagz SD1/√n csak független, megfelelően standardizált változókkal igaz; az azonos meccsek/ellenfelek, játékos- és csapatkapcsolatok miatt ez nem magától értetődő. A nullhipotézis el nem vetése nem elfogadása/igazolása, és a p ismét nem P(random|data).

Hot hand: a feltételes találati arány véges mintás becslője szelekciósan torzulhat; a pooledcounts-ratio és per-sequence arányok átlaga eltér. **Egy következő független dobás valódi esélye továbbra is50%; a könyvi46% a százdobásos sorozatokból számolt kiválasztott statisztika átlaga, nem a fizikailag következő dobás valószínűsége.** Emiatt a szimulációs nullmodellnek ugyanazt az estimator/szelekció eljárást kell alkalmaznia.

A16 négybites sorozatot Pythonban teljesen enumeráltam. Egy megelőző találat után a per-sequence találatiarányok átlaga17/42=.404762 (csak ahol definiált). Ha a saját teljes sorozatátlaghoz hasonlítunk:2sor magasabb,9alacsonyabb; a könyv3/9-es példája keveri a valódi.5 és a saját sampleátlag referenciát (MMM-miss .667<.75, mégis+1). Két egymás utáni találat után1magasabb/4alacsonyabb valóban helyes a sajátátlaghoz mérve; pooled4/8=.5, per-sequenceátlag5/12=.416667. A módszertani jelenség valós, az első táblázat nem konzisztens demonstráció.

A shot-difficulty adjustment érdemi: siker után nehezebb dobást választva a rawrate nem feltétlenül nő. A könyvi NN2013–14tanító/2014–15teszt és500sequence-szimuláció jobb elválasztás, de a p-k kalibráltsága, játékosonkénti modelleltérés és függések kellő ellenőrzése nélkül21független.05Bernoulli binomtesztje túl erős. 500szimulációból empirikusplus-onepminimum1/501≈.002; a táblázat<.001értékei további eloszlásfeltevést vagy más eljárást igényelnek, a fejezetből nem rekonstruálhatók. Az Albright korrigált pitcherhand/ERA/home/turf modell és évek közti gyenge reprodukálhatóság a confounding veszélyét mutatja. A NBA momentum vizsgálat saját teljes szezonból illesztett pontkülönbségre vonatkozik; ex-ante teszthez lagged erősség kell, ellenfélpárok miatt a csapatreziduumok nem függetlenek.

## 12–13. fejezet: platoon és clutch

Platoon négy pitcher/batterhand interakció, önmagában szezonátlagkülönbség nem egyedi stabil skill. Hierarchikus shrinkage, PAexposure, ellenfél/park/switchhitter és lineupselection kell. A táblából ellenőrizve LHH P(LHP)=7.43/(7.43+31.81)=18.93%, RHH P(LHP)=18.97/(18.97+41.80)=31.22%; a szöveg28%és43%-a nem ezek a feltételes arányok. A .060/.027 OPSkülönbségek a táblából helyesek. Maximális két éves split felsorolása winnerselection, nem előrejelzés.

Clutch WPA/PA regressziówOBA-ra: −.019258+.061324wOBA,146játékos,R².549, residualSD.001749. Ez a szezon megélt opportunity/leverage eloszlásából is ered, nem elkülönített nyomástűrés. A raw residual/σ nem pontos studentized residual (leverage és PAheteroszkedaszticitás hiányzik). A1980-as kalibráció más korszakúPerez/Rice-re, illetve2016-ra illesztett formulaTrout2012–19-re nem szigorú ex-ante teszt. Sagarin/FanGraphs WPAdefiníciók egyezése is kell. Éves z-k összevonása1/√n-nel függés és stabil nulleltolás esetén hibás. Perezmean.48→1.26aggregáltz nem szignifikáns a könyv saját±2határa szerint, mégis „great”caption. Rice−2.96,Trout3.11 csak e modellben. Utólagos csúcsévek és kiemelkedő játékosok kiválasztása többespróba. GoalSeek wOBA-korrekció leíró átszámítás, nem valódi skillfejlesztés és nem igazolt HOF-rangsor.

## 14. fejezet: pitch workload

Times-through-order, pitchcount, repertoire/movement/velocity és bullpenalternatíva fontos sportadatok. TTO1:−8wOBApoint,TTO2:+1,TTO3:+8,TTO4:+1 korabeli eredmény. A negyedik körben maradó pitcherek kiválasztott jóformájúak; a könyv helyesen felismeri a survivorhatást. A Pedro2003rossz kimenetéből nem bizonyítható, hogy a csere biztosan megnyerte volna a meccset. A döntéshez alternatívreliever, fáradtság és aktuális lineup kell.

PAP=max(0,pitches−100)^3 történeti heurisztika, nem validált sérülési valószínűség. A példában100+100+100+120+130=550pitch, nem500;35000/550=63.636,nem70. A 14%CU a táblázatban curveball, a szöveg egyszer cutternek mondja, utána helyesen no cutters; forráscímkéket nem szabad keverni. A Tampa2018opener név „Sergio Ramos” valószínű névhiba, külső ellenőrzés vár; a történeti siker és rosterpolicy együttmozgás, nem randomizált bizonyíték. Velocity/spin csökkenés használható változásjelölt, de sérülés diagnózisát önmagában nem jelenti.

## 15–16. fejezet: korszak és ritka események

Közös játékosok két egymást követő évi BAkülönbségének láncolása a korszak pitching/defense becslésére. Erős feltétel, hogy az átfedő kohorsz képessége átlagosan változatlan; öregedés, survivor, PAarány, egészség, league/rule/park és bekerülési különbség torzíthat. A sokéves apró torzítás összeadódik. TedWilliams.406→.306becslés nem bizonyított mai kontrafaktuális képesség; a könyv2019-ig számol.

Poisson p(X=x)=e^(−λ)λ^x/x!, x≥0, 0!=1; rareeventstátusz önmagában nem elég, clustering/mix és exposure számít. A kinyert képlet e^(−λx)-nek látszik, képi audit szükséges. Linearity of expectation függetlenség nélkül is igaz; Poisson-eloszlás és sorozatok közti szorzás viszont további feltétel. Perfectgame .68^27=.00003004iidközelítés; OBP nem minden reachbase (error/interference), és az outs különböző batters/rotation/fielding során nem azonosfüggetlen események. Két pitcherstart/game miatt„14szezon”frekvencia fél exposuret használ; játékosszintűcompletegame ésteamcombinedperfect külön.

**Számszaki ellenőrzés:** .5*.73^27+.5*.63^27=.000103931 (nem.000051966);396062start mellett41.16várható, nem20.6. A könyvheterogenitás-logikája (E[g(p)]≠g(E[p])) hasznos, de a számított példa felezési hibás. A11.9várhatóvs21megfigyelt modell alulbecsül, a szöveg „more” iránya hibás; a20regularseason vs21allperfect tailküszöbök összekeverednek.

56game hittingstreak: exposure-számolásban az új streak csak előzőhitlessgame után kezdődjön, így nincs átfedő56windows többszörözése. A ≥500ABkorlát és fix28háromAB+28négyAB kizárás egyszerűsítés; noABgame kezelést pontosítani kell. A szezonvégi BA mint igazi p a skillzajt is beépíti, az iidfeltétel erős. λ=.024→P≥1≈.023714, nem egzakt. A teljes sportvilág utólagos „legnagyobb rekord” kiválasztása mást jelent, mint egy előre kiválasztott rekord rarityje.

No-hitter rész számos belsőhiba: .00064/.00062keveredik, a második egymást követő siker faktora hiányzónak látszik a képletből,1900–2007→2000–2007szöveg. 1−exp(−.145)=13.4978%,nem15.7%. Adjacentpáresemények átfednek, így union nem egyszerűfüggetlenszorzat; ritka eseménynél közelítés vagy exact recurrence kell. NolanRyan6nohitters állítás külsőellenőrzendő, nem currentrekord. A25pitchbrainteaser speciális egyout/pitch, HRveszteskimenet feltételekkel példa; nem általánosminimum, hiszen többoutosplay és pitch nélküli esemény lehetséges.

## 17. fejezet: projekció

MLE minors→majors OBP≈.89korabeli feljutóadatból, nem univerzális és nem bizonyítottan alkalmazható ugyanígySLG-re. A feljutók szelekciója, park, kor, versenyerő ésjátékidő fontos. .89²=.7921,nem.78; RC összeg nem pusztaOBP*SLG, exposurekell. KATOH age28target ésactualthrough2020 eltérőkorhatár/megfigyelés, top10lista helyett teljesmintás kalibráció kell.

Marcel előny: recencyweights3/4/5 ésPAexposure, leagueshrinkage1200weightedPA, külön PAforecast200+.1PAyear−2+.5PAyear−1, majdageadjust. KönyvipéldaDavisweightedHRrate.060158,league.029114,reliability.863326,shrunkenp.055915,PA599.5→33.521,age31factor.994→33.320. A 1200prior strength, 29peak, .006/.003agefaktor történeti paraméter, sportonként/eseményenként validálandó. Üres múlt és injuryforecast nélkül200PAfloor nem megfelelő minden játékosnak. Az utolsó „2017ben36HR” történetiadat külsőellenőrzésre jelölve; nem validáció egyetlenpélda.

## 18. fejezet: NFL team margin

2014–17,128csapatév, intercept nélküli OLS: 9.872RET TD−.444PENDIF+69.043netpassingY/A+23.240rushingY/A−4.997TO−53.684defnetpassingY/A−39.192defrushingY/A+2.014DTO. Sacks attemptnek és sackyards levonva; definíciót explicit őrizni kell. R².7863 ésresidualSD43.63pont/szezon, nem meccs. Év végi ráták/turnover/returnTD a történeti margin részelemei, nem előzetes ismert inputok. Szezonhossz és scoringkörnyezet változása skálázást kíván. InterceptnélküliR²más összehasonlítást igényel, bár balancedleague marginmean közel0.

A p ismét helytelenül P(coef=0)-ként van leírva. ResidualSE nem bizonyít normáleloszlást és jövőbeli predikciós σ-t; paraméter- és featureforecast-uncertainty is van. Standardizedeffect .66*69≈45.6vs.39*23.24≈9.1leíró, nem pénzráfordítás oksági hozama. A PY/A ésRY/A .12korreláció nem cáfolja a rushing stratégiai passhatását; a fejezet végén a szerző is jelzi a game-theoretic értelmezés szükségességét. Turnovercoefficient átlagabs(4.9966,2.0144)=3.5055,nem3.13; returnTDelhagyása után(4.79+2.60)/2=3.695. Ugyanazt az eseményt éskövetkezményét kétszer beszámítva félreértelmezhető a weight.

A 19. fejezet eleje (NFL passer rating története) elolvasva; a folytatása még hátravan.

## 19. fejezet: irányítók értékelése (PDF 187–192)

A hagyományos passer rating négy, [0,2.375]-re vágott komponense: (C/A−.3)/.2; (Y/A−3)/4; (TD/A)/.05; (.095−INT/A)/.04; összegük 100/6-szorosa. Az egyenlő komponenssúly nem azonos a nyers mutatók egyenlő együtthatójával. Completion részben a yards/attemptben is megjelenik; csapatfal és elkapók hatása nem különül el.

Burke 2002–06 csapatszintű regressziója: konstans5.26, TRUOPASS1.54, TRUDPASS−1.67, ORUNAVG.92, DRUNAVG−.55, OINTRATE−50.1, DINTRATE83.7, OFUMRATE−63.9, DFFRATE78.7, PENRATE−4.49, OPP PENRATE6.58. Net passing a sackeket a nevezőbe és elvesztett yardot a számlálóba is beveszi. Minden fumble és lost fumble eltérő definíció. Az ebből kiemelt 1.543*TRUOPASS−50.0957*OINTRATE egyszerű passer mutató nem izolált QB-hozzájárulás, a QB futása/fumble-ja is hiányzik. 2016 Ryan/Brady/Prescott élmezőny, Rodgers12 vs hagyományos4; ez rangkülönbség, nem validáció. A .90/.87/.82 ratingkorreláció együttmozgást, nem előrejelző pontosságot bizonyít. ESPN QBR akkori formulájának/tervezett opponent adjustmentjének mai állapota később ellenőrizendő.

## 20. fejezet: állapotérték (PDF 193–200)

Yard line, down, distance, margin, idő mellett birtoklási perspektíva, félidő, timeout, kickoff és szabályrendszer is szükséges. A végtelen horizont relatív pontértéke, next-score EP és véges win probability különböző célváltozó: különböző források V-táblái nem keverhetők. A leegyszerűsített 4*30*99=11880 cellából nem mind megengedett, és 40k play sem tesz minden cellát jól becsülhetővé. CM1969, CSW1981, Romer és Football Outsiders értékei eltérnek; például saját25 CSW.13 vs Romer.6. A 2019-es Yurko/Ventura/Horowitz többkimenetű next-score modell és GAM-WP korszerűbb irány; aktuális elérhetősége később ellenőrizendő.

Az ötcella-játék egyenletei: V1=.5V2−.5V5; V2=.5V3−.5V4; V3=.5V4−.5V3; V4=.5V5−.5V2; V5=.5(7−V1)−.5V1. Megoldás −5.25,−1.75,1.75,5.25,8.75. A 8.75 nem hiba önmagában: relatív hosszú távú állapotérték meghaladhat egy touchdown pontját; nem next-score EP vagy valószínűség. Undiscounted végtelen pontkülönbséghez stacionaritás/relatívérték-feltételek kellenek, eltérő erejű csapatokra nem automatikusan véges. A szöveg hétyardos pályát mond, a koordináták 0–6-ra utalnak: definíció ellenőrizendő ábrán.

## 21. fejezet: fourth down és büntetések (PDF 201–208)

4th4 opponent30: siker V75=3.884, kudarc opponent28. A könyv előbb V28=−.336-ot ír, majd −V28 helyén −.336-ot használ; táblázat alapján V28 pozitív kellene legyen. Előjelhiba gyanúja, vizuális ellenőrzés még hátra. 47yard FG logistic(5.697−.097*47)=.7573; becsült EV1.84 a megadott kickoff/miss helyekkel. Go EV3.884p−.336(1−p), határ (1.84+.336)/4.22=.5156. A távolság szerinti .67/.55/.51/.48/.45 sikerarány válogatott történeti play-ekből származik, nem bármely csapat saját p-je.

Saját30 példa V35=.839, ellenfél68=3.265, punt után ellenfél25-re −.46: az utóbbi eltér a korábbi CSW .13 értéktől. Ezekkel a számokkal a küszöb (3.265−.46)/(3.265+.839)=.68348, nem .678. A Romer45% vs CSW68% modellfüggést mutat; az intuitív elfogadhatóság nem empirikus validálás. Net punt45.5 állítás definíciója később ellenőrizendő. Büntetéspélda .983 vs .956 előnye csak .027pont: a becslési hiba ennél nagyobb lehet.

2016–18 first own25: pass EPA .17 vs run−.02, pozitív EPA gyakoriság54% vs40%. Leíró különbség; playválasztás, kontextus és védelem alkalmazkodása miatt nem tiszta oksági eredmény. A sikerarány nem ugyanaz, mint a várható pontérték.

## 22. fejezet: játékelmélet (PDF 209–217)

Payoff [[−5,5],[10,0]]: offense run q=.5, defense run x=.25, érték2.5. Egyensúlyban az egyoldalú eltérés nem javít; nem szükségképpen ront, ahogy a könyv általánosan mondja. A közömbösségi egyenletek belső kevert egyensúlyra vonatkoznak; dominált vagy határesetben tiszta stratégia lehet.

Általános [[r−k,r+k],[p+mk,p−mk]] esetben q=m/(m+1), x=.5+(r−p)/(2*k*(m+1)), másik védekezés1−x. A kinyert szövegben 2(m+k) nevező és mindkét védekező valószínűségnél azonos plusz szerepel: algebrailag hibás, vizuális ellenőrzés kell. Belső megoldáshoz k,m>0 és |r−p|≤k(m+1). Az offense mix függetlensége p,r-től csak ezen belül igaz. Az egyik run payoff5→10 javulása után run.4, defense run.6, érték1 a második példában helyes. 15*10 play payoff állapotonként ritka adatú, bizonytalan és endogén playválasztásból származik; EPA célfüggvény jobb lehet a nyers yardnál, de nem meccsvégi WP minden helyzetben.

## 23. fejezet: kétpontos döntés (PDF 218–229)

PAT≈.94 és two-point≈.5 esetén 1pont EV.94 vs kétpont EV1; a WP-cél ettől eltérhet. A történeti .07előny és [.01,.15] CI mintanagysága/súlyozása nem tisztázott, és nem szünteti meg a play-szelekciót. A 2015 PAT szabályváltozás pontos leírása külön ellenőrizendő. A coach kockázatkerülése nem automatikusan a csapat győzelmi célja.

Dinamikus program: F_n(i) saját, G_n(i) ellenfél birtoklás, n hátralévő birtoklás, i saját margin. F0/G0=1 pozitív,0 negatív,.5 döntetlen. F_{n+1}(i)=.13G_n(i+3)+.68G_n(i)+.19max[.94G_n(i+7)+.06G_n(i+6), qG_n(i+8)+(1−q)G_n(i+6)]. G tükörképe minimumot választ. Feltételek: azonos csapatok, TD.19,FG.13,scoreless.68, nincs safety/defensive score, OT.5; ±30 csonkítás sok birtoklásnál torzíthat. 23.6birtoklás/meccs→2.54perc csak durva időkonverzió, nem ismert birtoklásszám. A táblázatok marginja birtoklás ELŐTTI, nem touchdown utáni. A kiemelt döntési mezők vizuális ellenőrzése szükséges.

Down14, három birtoklás példa: a könyv mindigPAT .0108 és go2 .0133 számolása hiányos eseményfát használ. q=.5 esetén a leírt két go2 ág .01460606, a sikeres első kétpont után elrontott PAT és OT ágát is hozzávéve .01497428. q=.45-re a két ág .013422; a kihagyott PAT-miss/OT ággal .013753017. A könyv .0133 száma és hivatkozott q=.5 táblája nem egyezik. A firstPATmiss utáni adaptív two-point ág sincs az alwaysPAT összehasonlításban. Ezek kézi algebrai auditok, nem teljes n=3 DP újrafuttatása.

Morris marginonkénti WP-növekménye hasznos: 10perc mellett 0→1 +8.4pp,1→2+1.8,2→3+6.5,3→4+5.0 stb. De az egyszerű „második pont növekménye nagyobb az elsőnél” csak pPAT=1,q2=.5 mellett pontos. Általánosan q2*(W(x+2)−W(x)) > pPAT*(W(x+1)−W(x)); .94 és .5 esetén Δ2>.88Δ1. A lead/deficit tükrözése és birtoklás/erőkülönbség feltételei is szükségesek. A WP-modell bizonytalansága a döntésbe továbbterjed.

## 24–25. fejezet: college/NFL overtime (PDF 230–244)

A könyvben „current” szóval jelölt OT-szabályok történeti állapotok, nem vehetők át 2026-os szabályként. College: második birtoklás megfigyelt54.9%, első TD.466,FG.299,zero.235. EXTRAFG=.30 és PRESSURETD=.74 az ismert54.9%-hoz igazított paraméterek, nem független validáció; egy megfigyelés két ismeretlent nem azonosít. A levezetés .466-ról .469-re vált, így a kimeneti valószínűségek összege1.003; kerekítés/typo ellenőrzendő. A következő fordulóban ismét.549 feltételezi azonos szabályt/politikát; későbbi kötelező two-point miatt ez nem általános. PAT/failedconversion sincs részletesen kezelve.

Régi NFL sudden death toy modell: K=p+(1−p)^2K=1/(2−p), p=.32→.595238. Feltétel 0<p≤1; p=0 esetén örökké nincs győztes és a leosztás érvénytelen. Egyforma p minden drive-ra, nincs turnoverTD/safety/returnTD, időkorlát. A szöveg <1% tied game érve valószínűleg összes meccs és OT-meccs nevezőjét keverheti; az OT-feltételes hatást kell mérni, nem általános <1% hibakorlátot állítani.

A 2012–17-es 47/93=.5054 és [41%,60%] CI nem bizonyítja a fair OT-t: nemszignifikancia nem ekvivalenciabizonyítás. Intercept-only modell nem azonosít receive okozta hatást egy kontrollcsoporttal; a team strength és coin-toss policy számít. Logistic spreadcoefficient .0633 esetén 1pontos favorite p≈.5210,5pontos p≈.5835 (intercept.0206), nem a szöveg64%-a. A táblázat második sorát „Receiving Kickoff”-nak címkézi, miközben a szöveg szerint point spread a változó; ellenőrizendő. A closing spread itt könyvbeli módszer, nem a felhasználó által kívánt független engine alapja.

A yardline-aukció, cut-and-choose, illetve Massey/Sagarin yardline+clock sealed-bid OT mind javaslat, nem hatályos szabály. A fair osztás érve azonos/ismert erő, közös értékelés és megfelelő elérhető állapotok feltételei mellett áll; heterogén csapatoknál az általános50%-garancia nem következik automatikusan. A Patriots/Steelers példák döntés és kimenetel különválasztását szemléltetik; utólagos jó történet nem ex ante validáció.

## 26. fejezet: draftérték (PDF 245–251)

A pick piaci ára, játékos várható teljesítménye, szerződés feletti surplus és superstar-esély külön cél. v(n)=exp(−a*(n−1)^b) mellett a szöveg a=−.148-at ad; ez növekvő v-t eredményezne, a csökkenő ábrához a=+.148 kell (v10≈.502). Vizsgálni kell, hogy nyomdai vagy kinyerési hiba. A not-roster/no-start/1–8/9–16/ProBowl kategóriákon belüli heterogenitás miatt a bérből közelített érték zajos. A későbbi AV/game görbén .46→.34 önmagában nem surplus: szerződés, játékidő, pozíció, draftév, rookiescale és karrier-cenzorálás számít. Career per-game élettartam/survivor szelekciót is hordoz; első négy év más estimand.

Mock draft és tényleges draft sorrend R²≈.8 nem igazolja, hogy a pundit ugyanolyan jól választ játékost mint GM: sorrendet prognosztizál, nem játékosok jövőbeli értékét. Winner's curse maximum-zaj szelekció sportspecifikus alkalmazása hasznos analógia, nem minden draftpiac inefficienciájának bizonyítéka.

## 27. fejezet: NFL tracking (PDF 252–269)

RFID, könyv szerint12.5Hzplayer/25Hzball,2017elsőhat hét: korabeli adat, elérhetőség/licenc/mintavétel később hivatalos forrásból ellenőrizendő. Release time átlag2.8,medián2.6; formation/call/QB/OL külön szerepek. Airyards OLS intercept−3.2,time3.3,rushers.7, R².11: leíró és gyenge összmagyarázat. Sack logistic táblában time szintén3.3: intercept−8.5,4rusher+2.4,shotgun−.4→logit=−6.5+3.3t, p(t=2)=.525; ez gyanúsan nem egyezik a 27.3 ábra görbéjével. Vizuális vizsgálat kell. Jumbo SE324/333 kváziszeparáció/ritka kategória jele, nem stabil hatás.

A release/sack eseményéig mért idő nem meccs előtti bemenet; competing event és informatív szelekció, nem egyszerű oksági QB-control bizonyíték. Interceptionnél target depth hozzáadására a time .14→−.05,airyards.05: a nemszignifikánssá válás önmagában nem bizonyít teljes mediációt. Logit együtthatók skálázása/noncollapsibility, confounding és target választás is számít; Sobel lineáris αβ teszt feltételekkel. Időbeli sorrend nem zárja ki a közös okokat. Rushing vs screen EPA eloszlás/szórás, konfidenciasávok és átlagok összemérése leíró; nemszignifikáns eltérés nem egyenlőségbizonyíték, és nem alap szabálymódosításra.

OL+QB convex hull area: nagyobb sokszög nem garantáltan tiszta pocket, lehet benne rusher; RB/TE blocking assignments hiánya, scrambles kizárása szelekció. Completion logistic intercept.8,depth−.1,area.03; a táblázat heading „Pass Air Yards” tévesnek látszik. LL−2677.1 és3paraméter esetén AIC5360.2, nem5340.1; vizuálisan ellenőrizendő. Release pillanatbeli feature kizárólag akkor ismert, pregame-hoz történeti aggregáció/előrejelzés szükséges. OL collapse survival/AFT logikus, de throw mint censoring nem feltétlen független: competing risk/policy kell.

CPAE = actual completion−expected pass-wise model; receiver/depth/pressure kontextus. Next-gen-scraPy passchartból becsült koordinátákat ad, nem teljes player trackinget. 100passküszöb és passchartcoverage szelekció. Tracking CPAE-velρ.83 egyezés nem bizonyítja, hogy minden fontos információ a locationben van; year-to-year .41 nem biztos individuális stabilitás. A névtáblázat több teljesnevet/aliasformát használ: név helyett stabil playerID kell. „Derrick Watson” külön lehetséges névhiba. Rounded fourth&1 valójában inches–1.8yards lehet; mérési felbontás számít.

## 28. fejezet kezdete (PDF 272–273, részleges)

EFG=(FGM+.5*3PM)/FGA, érvényes shot-value normalizálás. A példában Mavs45/100 és15hárompontos→105pont, EFG52.5%; Knicks50/100 és1hárompontos→101pont, EFG50.5%. Az ORB nyers darabszám félrevezető lehet, mert a rebound-lehetőségek száma a miss-ekkel változik; folytatása még hátra.

## 28. fejezet folytatás: four factors (PDF 273–281)

Nyolc mutató: offense/defense eFG, turnover rate, rebound rate, FT rate. A nevezők nem felcserélhetők: play/chance vs possession; FTM/FGA vs FTA/FGA; saját DRB% vs ellenfél ORB%. A 28.1 ábra defensive rebound oszlopa az ellenfél ORB%-át mutatja (.24 körül), míg a regresszió ORP−DRP jelölése és59.05interceptje inkább saját DRB% (.76) használatára utal. Ez akár kiegészítőarány-átalakítás lehet, de a szöveg nincs egyértelműen végigvezetve; a társított korrelációs előjelek is ellenőrzendők. A „virtually uncorrelated” túlzó: −.67,−.47,+.46 lényeges korreláció. A negatív defensive shooting/rebounding kapcsolat értelme a rebound-definíciótól függ. Ökológiai team-year korreláció nem bizonyítja az egyes rebounderek gyenge labdakezelését.

30csapat egyetlen2015–16szezon: wins=59.04678+383.31318ΔeFG−244.36862ΔTO+34.49081ΔREB+84.26961ΔFT; R².84081,SE5.96607,adjR².81534. A számok csak a pontos definíciókkal értelmesek. R≈−.11 esetén R².0121, nem4%, ahogy a reboundegyváltozós magyarázat írja. +.01turnoverdifference koefficiens szerint ront, az „improvement” csökkenést jelent. Rebounding kombinált példa harmadik sora második offensive helyett defensive reboundot kívánna. A fejezet helyesen külön kimondja: szezonvégi adatokkal leíró modell, előrejelzéshez csak addig ismert becsült inputok, és az input bizonytalansága is hiba.

## 29. fejezet: box-score súlyok (PDF 282–287)

EFF minden pozitív eseményt+1 és miss/TO-t−1 súlyoz: 3PTp>.25,2PTp>1/3 mellett a volumen növeli a pontszámot, akár ligaátlag alatti hatékonysággal. A megadott EFF képletből blocks hiányzik; a 29.1 ranglista például Kanter21MPG/EFF24 lehet per-minute/36 normálás vagy más mutató, nem a deklarált egyszerű per-game formula; hivatalos definícióval később ellenőrizendő. Öt találat18kísérletből27.78%, nem26.67%, bár a konkrét15−13=2 szám helyes.

PER reverse-engineering 2018–19 legalább1000minute játékosokra: .68+26.77FG−35.58FGA+26.32FT−25.47FTA+42.74OR+11.45DR+29.7AST+47.3STL+38BLK−47.9TO−18.8PF+41.1PTS, minden bemenet per-minute. R².99 nem jövőbeli játékoserő-validáció, hanem egy eleve boxscorefüggvény közelítése. .47residualSD nem automatikus95%±.94garancia. A shootingmarginalpélda33/100300perc: (26.77*33−35.58*100+41.1*66)/300=.1267, nem2.3! A PER15átlag alá eső kicsi pozitív nyerskomponens nem jelenti automatikusan, hogy a tényleges játékos PER-je emelkedik a plusz percek/shots hatására; baseline és nevező is számít.

WinScore=PTS+REB+STL+.5AST+.5BLK−FGA−TO−.5FTA−.5PF. Shootbreak-even50%/33.33%, de a rebound teljes birtoklásként való egyéni jóváírása csapaton belüli helyettesítést, contested/uncontested és ORB/DRB különbséget elfed. Teamwinshez összeadó játékosérték nem bizonyít jó individuális felosztást. Boxscore sok védekező/screen/spacing eseményt kihagy, a könyv80% nem részletesen alátámasztott arány. A „BSB kiváló” szerzői megítélés, nem itt bemutatott out-of-sample bizonyíték.

## 30. fejezet: adjusted plus-minus (PDF 288–304)

Stintenként azonos tíz játékos, outcome scoringmargin; design saját játékos+1,ellenfél−1. A per48 és per100possessions eltérő mértékegység. KorabeliHCA3.2/48:3perces9–7 eredményből8.9–7.1 vagy egyszeri.2marginlevonás; ne legyen kétszer homeadjust. Azonos nyers0 egy rossz/jó csapatban önmagában nem bizonyítja, melyik játékos jobb, mert más teammate/opponent/usage/rotation lehet.

20játékosötös-példa18játékos: a30.2 táblában ismételtplayerID látszik (row14ellenfél15kétszer;row21saját7kétszer,ellenfél18kétszer); ez nem szabályos lineup. Célfüggvény felső index14, miközben20meccset mond. Ábrák vizuális ellenőrzése hátra. A tökéletes in-sample illeszkedés nem teszi „igazzá” a coefficienteket. Öt+öt design mellett közös konstanshozzáadás minden playerhez azonos predictiont ad; mean0 normalizálás nélkül nincs egyedi OLS, és további együttjátszás-collinearitás mean0 mellett is lehetséges. Az appendix végül megadja mean0feltételt és változó hosszúságú stinteknél per-minute error^2*minutes súlyozást. Per-possession regressziónál megfelelő possessionsúly és számlálás szükséges; .44FTA közelítés rövid stintekre pontatlan lehet, az összesített kétcsapatos formulában /2 csak akkor kell, ha valóban két csapat statisztikái szerepelnek.

Ridge: SSE+λsumβ², prior0, λvalidationsetten, végsőértékelés külön testsetten. A splitet időben és meccsenként kell kezelni; stintenként randomsplit ugyanazt a meccset átszivárogtathatja. Nagyλ interceptonly csak ha van nem büntetett intercept; ismertHCA/zero-centered modellen más limit. A szerző WINVAL érdemeit ranglista/anekdoták támasztják alá, ezek nem önálló predikciós validációk. 18pontos LeBron per48 hatás és per100RPM nem közvetlen összevethető. WINVALdefense negatívjó, RPMdefense pozitívjó: normalizálni kell. Additív hatás nem bizonyított oksági cserehatás, matchup-interakció és szerepváltozás is lehet.

On/off LeBron/Booker/KevinMartin példák lineup/ellenfél/game-state szelekciót hordoznak. Booker egy70pontos meccse nem cáfolhatja önmagában szezonos PER-ét; a Booker-ábra utolsórowcímke out/otherin ismétlődik out/out helyett. Impact rating WP-változásra illesztett APM, így garbage time kisebb súlyú, de high leverage zajt felerősíthet. .5+x értelmezés Lowry.40→.90 hipotetikus winprob nem validált valószínűségkalibráció, és linearitás akár[0,1]-en kívüli értéket adhat. Negyedik-negyed23per48 nem23negyedpont és nem automatikus tartós clutchképesség; mintacsökkenés/sok összevetés miatt óvatosság szükséges.

## 31. fejezet: RPM, RAPTOR és rokon mutatók (PDF 305–311)

Box priorhoz shrinkage, tracking, luck adjustment és forecastminutes külön szerepe. RPM=ORPM+DRPMper100poss; 2015–16wins≈.00064*(RPM+3.15)*minutes, .016egységnyi közelítési hiba egy meglévő metric újraképletezésében, nem előrejelzési hiba. A könyv replacement−3.15pont/possession-t ír, de helyesen per100poss. Replacement és átlag0nemazonos baseline. RAPTORdescriptive/PREDATORpredictive, luck-adjustedRAPM/PIPMkorabeli változatok, aktuális működés/forrás később ellenőrizendő. A top20RPM-re szűrt korreláció (.89RPM–RAPTOR,.26RPM–RAPM stb.) tartománykorlátozott és a kiválasztás szempontjától függ; nem teljesliga-validáció. Szöveg top10, ábrafelirat top20: eltérés. GOAT összevetés eltérő megfigyelt szezonokból/karrierszakaszokból nem fair univerzális rangsor, és személyesWiltválasztás nem mérési eredmény.

## 32. fejezet: lineupok (PDF 312–318)

Warriors2A288perc162margin→27per48; átlagellenfél+2.67HCAadjusted→29.67lineupérték. Nagyon kevés effektívmeccs, selection és opponentbecslési hiba. Hawks426perc−2.4vs126perc+28 példára normálσ12per48 és függetlenség feltételezve SEdiff=sqrt(144/8.875+144/2.625)=8.4310,z3.605. A szöveges Excel1−NORMDIST(0,lineup1−lineup2,σ,TRUE) előjele fordított: magasabblineup2 esetén NORM.CDF(0,μ1−μ2,σ) vagy1−CDF(0,μ2−μ1,σ) ad≈.99984-et. A táblázat.9999289 szintén nem egyezik a kiírt30.4/8.4310számokkal. Normal-sampling SE önmagában nem ad P(trueμ2>μ1) posteriorállítást: külön priort/likelihoodot vagy frequentisttesztértelmezést kell rögzíteni. Függetlenség kétséges közös játékosok/szezonosbecslések mellett; sokszáz lineup kiválasztása winner's curse.

Chemistry=observedadjustedlineup−sumplayerAPM maradék, nem izolált biológiai/pszichológiai szinergia. Ugyanazonadaton képzett predictor/target és kölcsönösfüggés miatt bizonytalanságot együtt kell kezelni. Egyszerű normal-priorshrinkage analog:60perc12.5 és100perc0prior→4.6875;1perc288→2.8515. A100pseudominute nem azt jelenti, hogy100perc után az átlaglineup „biztos0”; priorprecision megadás, kalibrálandó. Pontos posteriorhoz variancia/priorparaméterek szükségesek. A Hawkslineup később120percet mutat126helyett: adatváltozat eltérés.

## 33. fejezet: matchupok (PDF 319–325)

2006Harris/Parker/Nash és2011MavsCardinal/Barea lineupváltoztatások érdekes esettanulmányok. Player-vs-player és team-vs-team hatás nem feltétlen tranzitív; ezt stílusinterakció teheti lehetővé, de külön szezonok kis mintás eredménye nem bizonyít stabil körkörös dominanciát. A2006táblában4–45perceslineupok ±180per48 értékei szélsőséges kis-mintás extrapolációk. Megfigyelt teljescsapatmargin nem izolált páros matchup. Egyes meccsek utólagos kihagyása és „meglepetés” magyarázat kiválasztási torzítás lehet. Lineupváltás utáni győzelmi sorozat nem randomizált oksági hatás; fatigue/injury/rotation/ellenfélalkalmazkodás korlátja nélkül nem „játsszuk mindig a legjobb ötöst”.

## 34. fejezet: NBAfizetések és draft (PDF 326–329)

2017–18hivatkozott93Mpayroll,96pace,105.6PPG mind korabeli, pontos év/pénzügyi definíció később ellenőrizendő. ReplacementRPM−3.1/−3.11/−3.15 keverve;5*3.1*.96=14.88margin, scoringratio≈.86, exponent14→winfraction≈.108,9win/82. Margin önmagában nem határozza meg scoringratiot: a for/against átlagot hogyan változtatjuk külön feltétel. 81M/32win≈2.53Mwin, egyszerűsítve2.5M;20WAR→50M pluszminsalary kérdése, salarycap/max/rookiescale és marginálispiac nincs modellezve. Surplusmúlt és várhatókövetkezőszezon különbségét a szerző helyesen hangsúlyozza.

Draftcurve1985–2010első5évWinShares: grafikán−7.542lnpick+31.945, szövegben−7.5542. 2#8kb#1 várhatóösszeg, de rosterkorlát, superstarupside, salary, pickprotection és bizonytalanság számít. Elsőötév „rookiecontract” általánosítása szabályfüggő, nem univerzális. Teamwinshez additívWSnem automatikus individuálisigazság.

## 35. fejezet: játékvezetői bias tanulmány (PDF 330–335)

Történeti empirikus módszertani példa, nem mai bírókra vonatkozó állítás. Ideális referee-call szintűadat hiányában weightedplayer-game foulrate,minutesweights és crewcomposition*playergroupinterakció. Foulrate=5.10+.182B*W−.763B−.204W. A B=1 csoport Wmeredeksége−.022, B=0-é−.204; fontos a különbségváltozás, nem egyszerű „minden oppositegroup több foul”. Interakció .182,groupgap−.763→−.581. .182/.763≈23.85%, a23% kerekített. P<.001nem P(változóhaszontalan)<.001. Megfigyelési eredmény, kontrollok, crew/player/game clustering és context kell; hívottfoul nem groundtruthfoul.

Szintetikus1000game táblázat első35foul szövege whiteplayer-t mond, az oszlop szerint blackplayer;1.423értéket per-minute-ként nevezi, pedig per48. A folytatótanulmány 2003–06 adatát nemátfedőnek mondja az eredeti1991–92…2003–04-gyel, ami időben részben átfed; később primer cikkellenőrzés.2007–10nemszignifikánsinteraction nem teljesbiaseltűnés bizonyítása; awarenesshatásnak más időtrendalternatívái is vannak.

## 36. fejezet kezdete (PDF 336–339, részleges)

Synergyplaytype manualcharting kategóriák és protocolváltozás. Synergy„possession” chance: ORBújchance, nem standardteampossession, ígyPPPnem csereszabatos. BallhandlerPNR tartalmazhat sikertelen rollpassbólTO-t, míg sikeresrollpass arollmanhoz kerül; .93BH/screeneraggregate és magasabbrollmanPPP emiatt nem egyszerű akcióválasztási utasítás. DerivedPNRpassout nem mind része publicfeednek. Trackingdefensetype, pop/roll/slip és role további leírójel.20%→30% relatívnövekedése50%, nem34%, hacsak a kezdőérték más; az alapszámok tisztázandók. A fejezet folytatása még hátra.

## Első célzott vizuális ellenőrzés — 20 PDF-oldal

A B08-critical-01…05 kontaktlapok ténylegesen megtekintve; manifest: B08-critical-visual-manifest.json. Oldalak202,203,214,215,224,225,246,256,257,264,274,276,278,285,291,292,293,314,315,316. Ez még nem a könyv teljes ábraellenőrzése.

Vizuálisan megerősített nyomtatott eltérések:202előjelváltásV28;214a kevertstratégia nevezője2(m+k) és mindkétdefensevalószínűségnélplusz;246v(n) exponensében−a és a=−.148;256sacktimecoef3.3 miközben257ábrán t2p≈1%,t6p≈50%, tehát nem a táblázat logitja;264completiontáblafej„Pass Air Yards”,LL−2677.1ésAIC5340.1;285shootingexample2.3 a tényleges.1267helyett;291duplikált játékosok az említett sorokban;293összegfelsőhatár14 a20meccses példában;314lineupvalószínűség.9999289 nem a kiírt SE/diff eredménye;315ExcelCDFelőjelcsere.

A gyökjelek a314/315oldalon helyesen szerepelnek; azok csak a szövegkinyerésből tűntek el. A224/225oldali two-point táblák kiemelései láthatók: q=.5 táblában down14,F3=.015; ez összhangban van az adaptív ágakból kapott≈.01497-tel és nem a narratíva.0133értékével. A két chart eltérőq miatti döntéseit nem szabad univerzális chartként átvenni. A274defreboundoszlop ténylegopponentORB(.24körül), és278regressionintercept59.04678; az eltérődefiníció magyarázata továbbra is hiányos.

## 36. fejezet folytatás (PDF 339–343)

A védelem által megszakított PNR/cut nem kerül feltétlen a befejezőplaytípus statisztikájába. Ez a magascutPPP-t felfelé szelektálja. Postup.89 és isolation.87PPP ligaátlag mellett Houstoniso1.06,Harden1.11; szereplőspecifikus és policyfüggő eltérés. Post-passshot1.14 vs postupchance.89 külön nevezőjű lehet, turnover és shotkimenet eltérő adat. A fejezet helyesen hangsúlyozza: a kategória létrehozásának szabályai nélkül nem szabad akciót optimalizálni. A36.1 táblázat TOT_FREQ≈14.5–29.7%, BH+screenchancecounts más számsor; a szöveg league>30% állítása külön összeegyeztetést igényel.

## 37. fejezet: NBA tracking (PDF 344–363)

KorabeliSportVU/SecondSpectrum,25fps,playerXY/ballXYZ és derivedmetadata. A mérés konzisztens definíciót ad, nem teszi a kiválasztott mutatót automatikusan objektív értékítéletté. Convex hull area használható spacing-proxy, de skill és advantage nem kizárólag area. Defensiveghost: expectedbehavior imitation és optimalEPminimizing defense különböző cél. Franks/Miller/Bornn/GoldsberryHMM matchuptracking: expecteddefenderposition=.62offender+.11ball+.27hoop. Ennek guardassignment bizonytalanságát tovább kell vinni, nem minden nearplayer a valódi assignment. Disruption ratio observedpoints/sum(shooterhistoricaleff*shotvalue), <1kedvező; történeti idővágás,shooter/shotmix és allocation kell.

DeepHoops rövidrablak terminalaction valószínűségek, pastTtrajectory input. Π=[.21,.06,.03,.04,.66],V=[.91,1.5,1.5,0,1.09]→EPV1.0455 (könyv1.045 kerekítés); nullbranch1.09leagueátlag csak közelítés, aktuálisshotclock/teamstatefüggő folytatási értéket kíván. Nonshootingfoul1.5csakbonushelyzetben; shotvalue és offensive-reboundcontinuation kezelését nem szabad elhagyni. EPV(t2)−EPV(t1) a pass állapotváltozása, nem feltétlen izolált oksági passhatás; minden köztes defender/teammatemoZGás része. Backcourtpass≈0,assistopp≈.2,otherpass≈.05korabeli leíró eredmény. Bhostguster imitation valószínű reakciót ad, nem optimálisat.

2016–17shotlocations: rim.631*2,3PT.358*3,midrange≈.8pont/shot; foul/TO/ORB/clock és hozzáférhetőalternatíva nélkül nem teljespossessionEV. Corner.388vsabovebreak.347 különbség.123pont/shot; táblában.387vs.347,.354vs.338modelprediction,6.4vs5.9feetdefenderdistance. Assistrate>90%vs>70%, potentialassistet is számolni kell, különben made-shotselection. A distance-onlylogit shooterösszetételmi torzítását a szerző jelzi. A FIBAline mindenholazonos távolság állítása hivatalospályaméret-ellenőrzést igényel, és a ligák összevetése önmagában nem exogén treatment. Véletlen pályakiosztás a pályageometria hatását vizsgálná, nem izoláltan az assist oksági hatását.

Cornerchoreography600meccs,4secpre-shottraj10cluster,~fele stationedsarok. Egyszerű játékelmélet drivevskickout ésdefenderdistance: szöveg d1…22, ábrán1…21; a22−d nevező d22-nélszinguláris. Drivefaktor 1−1/[α(22−d)] a leírásban és37.2payofftábla konkrét megfelelése további ellenőrzést igényel. A végpontok közötti kevertpolicy szélsőséges optimálisvédekezést ad ezen a modellen, de passTO,closeouttime,mozgás,specifikusabilityhiányzik. Átlagd13≈observed12.3egyezés nem eloszlási egyezés. Clustering„matematikailagillposed” általános mondata túl tág: eltérő cél/axioma/hasonlóság mellett különböző feladatok és megoldások vannak. Injuryriskmegfigyelés kutatási lehetőség, nem ittvalidált injuryforecast.

## 38. fejezet: NBAmeccsvégi döntések (PDF 364–374)

Down2,lastshot,nofoul: win2=p2*pOT,win3=p3. .52*.5=.26 vs.36; POT feltételesen a konkrét OTállapotban értendő, függetlenség nem bizonyított és nem is kell ha helyesen feltételes. p2határ.72,p3határ.26, de pOTküszöb .36/.52=.6923077, nem a könyv.36/.5=.72-ja. BernoulliSEsqrt(.36*.64/60000)=.00196=0.196százalékpont, nem1–2%; a ligaszintűsamplinghiba és konkrétdobásszituáció/parameteruncertainty eltérő. Négyazegyelleni fastbreakp2magas lehet, a könyv helyesen példázza a kontextusfüggést.

Up3foulvsdefend: régi41/205utolsó3PT=.20,pOT.5→.10loss; foulmakefirst*.14ORB*.3three≈.03outcome egy leszűkített eseményfában. .77helyett.75kerül a számpéldába; szövegbenmissedthree szerepel missedFT helyett. A többbirtoklás, szándékosmissszabályossága, foul-on-3, inboundTO, clock,timeout és foulpolicy hiánya lényeges. Történeti NBA27foul/260nofoul éscollege52/391 megfigyelés nem randomizált; nemszignifikancia nem stratégiai ekvivalencia. SajátMC10k: pFT.77,p2.52,p3.36,ORB.23,pOT.5→.073lossfoul vs.18nofoul. A nofoulág.36ligap3-ra emelése a régi clutch.20helyett más feltételezés; plusz4abszorpció és további szabályok nem bizonyítottan minden helyzetben „worstcaseleadingteam”. SimulationMCSE és paraméter/modeluncertainty különválik; a rajzoltWPhisztogram replikációs protokollja nem egyértelmű.

2for1 kb32secremaining: megfelelőshotquality mellett több esély, de a32nem univerzális. A clockstopNBA/FIBAállítás period/remainingtimefüggő és hivatalosszabályból ellenőrizendő. Endquarterintentionalfoul:2*.79−.9=.68várhatóhátrány vs1.0nofoul feltételezett, de ORB és további foul/clockdöntések módosítják. Hackstrategyben ORBcontinuation nélkül2pFT nem teljesEV. Történeti71%college1&1küszöb,DeAndre68%,Elamendingaktuálishelyzet nem 2026osadat. Pontvárhatóérték és meccsWP cél ne keveredjen.

## 39. fejezet: labdarúgás (PDF 378–395)

A „shots differential és goal differential nemkorrelál” univerzális állítást a mellékelt szöveg nem bizonyítja; kell primeradat, definíció, időtáv és mintavétel. xGlogitMLS2016,1114shot:const−1.1,foot+1.1vsheader,angle+.02,distance−.19,x+.11,y−.01,assistéssetpiecekontroll. Koordinátaegység/tengelyirány és angledefiníció nélkül nem hordozható. A gépi Bernoulliaccuracy helyett reliability/Brier/logloss fontos; 45fokvonaltól nemszignifikánseltérés nem bizonyít kalibrációt ritkabinekben. Egy constantbase-rate modell is kalibrált lehet gyengefelbontással; out-of-timeproper-scoreésstratifiedchecks kellenek.

ShotxGsum=Egoal lineárisvárhatóérték miatt függetlenség NÉLKÜL is igaz feltételes rögzítettshotsetre; Poisson-binomial distribution és variance=sum p(1−p) viszont függetlenséget kíván, különben covariancekell. Reboundshot/goalutánmegváltozóshotset miatt a posthocshotset nem generatívpregamemodell. A39.2efficiency(defense)=(GA−xGA)/GA esetén negatívjó, miközben a grafika pozitívértékeket„Complete”ként jelöl: előjel/névkonzisztencia vizsgálandó. GT,i jelölés a defensiveformula számlálójában gyanús typo. ZeroG esetén osztásundefined, ratiozajos.

Possessionvalue,passvalue ésVAEPszerű rövidkakciós scoringprobváltozás a nemshooterek értékelését tágítja; concedingriskkomponens is fontos, a könyv rövidleírása ezt nem vezeti végig. Gyakorlatban no-shotpossession és sikertelenakciók is kellnek. Előrevárakozó off-ballOBSO és xG mechanikus összeadása duplaelszámolást okozhat. Risknek a passcompletionprobabilityt nevezi; a riskgyakranfailurep, az irány pontos tisztázást kíván. Rewardshotwithin10sec nem azonos goal/WP/EV.

AbsorbingMarkov: (I−Q)^{-1}R, ha minden átmeneti állapotból abszorpció majdnem biztos ésρ(Q)<1. „Minden nemabsorbingstate transient” általában hamis: zárt többelemű recurrentosztály is létezik. Gameruinp.6,states0…4 korrektabsorption [0.415385,.692308,.876923]success 1/2/3kezdőtőkére. ExcelMINVERSEszövegH24:J26 vs ábraF24:H26: tartományhiba. Soccer24state,nextoursgoal/nextoppgoal időkorlát nélkül; nem remainingmatchWP és no-more-goalstatehiányzik. Tablevalue midfield.693−.307=.386; openoffmid.779−.221=.558→passdelta.172, nem a narratíva .72−.22=.50 és.12-ja. Receiver/passerjóváírás további attributionmodell, nem aMarkovból automatikusan következik.

Penaltygame3x3 payoff,heterogénkicker/keeper,mixedNashkérdés. μthreshold=(πLπR−PLPR)/(πL+πR−PL−PR) meghatározottpayofffeltételek mellett. Empiricalcenter81%selection,payoffbecslési hiba. „Nemutasítjukelazoptimalitást” nem igazoljaNash-t. Másikstudykeepercenter6.3%vscenterkick29%: probabilitymatchingnemáltalánosoptimalitás, a sajátboxisleírja,hogyfixpayoffexogénbase-rate mellett jobbbestresponse-ra mindigválasztani. Stratégiaiellenféllelkevertpolicyt payoffból kellmegoldani, nem frekvenciákat egyeztetni. Normtheorymagyarázat lehetséges hipotézis, nem egyetlenbizonyítottok.

## 40. fejezet: jégkorong (PDF 396–407)

Nyersplusminuscsapattárs,kapus,ellenfél,zonestart/stintösszetétel. A könyv powerplay/shorthandedmindkettőkihagyása leírást az NHLhivatalosplusminusdefinícióval kell összevetni (shorthandedgoals különösen). Corsi=allattemptsfor−against,Fenwickblockednélkül,CF%=CF/(CF+CA); 1250/2321=.53856 és179különbség helyes. >50%nyersCFnem bizonyít individuumátlagfölöttierőt. Többattempt mintgoal kisebb samplingvariance-t adhat, de tartós shotqualitykülönbség marad. 2006–07…2016–17inclusive11szezon, a fejléc10-etmond. Rawgoalshare,PTS% éswin% eltérő a ligapontszabálymiatt. +572Fenwickhez feltételezett5720totalattemptkell;2860teamfor és55%sharealapján differencia520lenne, ha2860a konkrétteamfor; pontosnormalizálás fontos.

**Kapuslehozási példa súlyos modell/számítási problémái (PDF400–402):** 60perc3600sec, μ_t=t*x/1800 faktor kétszeres, ha xteamgoalper60 és nem30percrate; a később2.77teamgoalértékhez ezt használja. PP23%conversionperpowerplay / shooting10%per-shot nem azonosnevező; ígyo*2.3 nem megalapozott6v5goalrate. PK.84/save.92 arány ráadásul magasabbPK esetén nagyobbdefgoalsallowedfaktort ad, rosszirány.6v5empty-net nem5v4PP, goalieésicingkülönbséggel. ENG5%goalshare / exposureτ25sec ráta becslése lehetne, de numerator/denominator azonosuniverzum és exposurekell.

Egoals=1 alapján kb5perc választása nem WP-optimum. Kétfüggetlen exponentialhazard a,b esetén P(trailingelső ÉS t-ig)=a/(a+b)*(1−exp(−(a+b)t)); a/(a+b) csak végtelenhorizonton/azelsőeseményigfeltételesen. Könyv .003 és.006rátára arány1/3, nem.375; a pontoskirakott rátákból≈.368 körüli lehet, de az időcsonkítás akkoris hiányzik. .42expectedgoals*.375nem korrekt eseményvalószínűség; lambda nem P(atleast1). Egymásutánigólok, retakegoalie, penalties, rinkpossession és akárENGutániscomeback lehet; a példát nem szabad kész NHLpredictionként átvenni.

Faceoffskill/luckvariance:255játékos≥40faceoff,observedvar38.27percentagepoint²,nullcoinflipvar12.65→33.05%nullnoise/66.95%residual. Ez megfigyelt játékosarányok közti varianciafelosztása a feltételek alatt, NEM faceoffok67%-a skill. A residual opponent,handedness,zone,usage,selectionhatásokat is hordoz. HeterogénNi éspi miatt E[p_i(1−p_i)/Ni] kell; a.5nullmásbecslés, egyMCreplicaterandomerroros. Jointopponentfaceoffok összekapcsolják a játékosokat. BinomialSEfügg a skillp-től, luckéskillfüggetlenség erős feltétel; hierarchikusmodellalkalmasabb következtetéshez.

CarryoverPPstudy2014–15:14.3/15.8/16.2/16.0vs19.1%,p=.02/.07/.08/.05. A könyv„almostallrejectat.05” nem igaz: csak.02egyértelmű,.05határeset,kettő>.05. TöbbszörösnestedS30/60/90/110teszt, exposure és carryoverclassification censoringfontos: haPPkorán góllal megszűnik, a ténylegesenperiodotátívelők kiválasztása outcomealapúsurvivorbias lehet. A kategoriaszabályt ellenőrizni kell. Átlagmanszámnemszignifikanciája nem zárjakiamanszámkonfoundálást. Short-handedgoalp.81/.94/.75/.96 nem ekvivalenciabizonyítás. Two-proportionz pooledp korrektfüggetlenbinomialmintára, de team/gameclusteringésoverlapkezelendő.

## 41. fejezet: röplabda (PDF408–413)

GreekleaguePythagoreanαpoints10.8,αsets1.4; heldout2018–19seasonjobbsetmodell. Endseasonpredictionés in-seasonforecast különbségét kezeli, de runningfinalwincél már részbenmegvalósultmeccseketis tartalmaz, így remaininggames különmérendő; baselinecurrentwin%/scheduleadjustmentkell. BTsetlogit=c+rhome−raway,mean0constraint/anchorazidentifikációhoz. Tie-break15 vsregular25 különfolyamat,24tiebreaks7%adatritka. First15roundfit,16–22test hasznos idővágás; .3933homeintercept→.5971, a szöveg59%kerekített. AEK−.4983vsIraklis−.2615→logit.1565,p.539046. MatchsetMChelyett zártbestof5formula is használható megfelelőfeltételekkel; setekfüggetlensége,rotation/fatigueésinjury nem automatikus.

15/24homeTB.625nem erőskülönskillbizonyíték, és ha a24 teljesévesadat, testperiodbevonásveszély.72.5%accuracyés5binreliability kis mintán kevés; binCI/count/proper-scoreésbaseline kell. A könyv megemlíti a calibrationplot korlátait és a Bayesiantruncated-pointmodelalternatívát.

## 42. fejezet kezdete (PDF414–415, részleges)

Golfstrokesgained a kezdőhelyzettel korrigál, nyersputtszám nem összevethető. Broadie/ShotLink15Mshot történetiadat, nem sajátletöltöttforrás. Távolságfüggő expectedstrokes táblázat kezdete elolvasva; folytatás hátra.

## 42. fejezet folytatás: golf (PDF415–420)

Strokesgained=E(strokesbefore)−1−E(strokesafter), holedoutE=0; puttkörrebaselineexpectedputts−actualputts. Távolság/lie/pálya/versenyerő és weather adjustment kell. Tiger táblatotal−1.002, a felirat mégis„GainsOneStroke”: előjelhiba.394yardtee példában400ra és280yardutánihelyzetbenTEEval3.65-re kerekít, pedig fairway3.69; a megfelelőlie fontos. A komponensek teleszkopikusan totalSG-vé összeadódnak, nem különfüggetlen playerképességek. Topgolfers20/45/20/15%share korabeli leírófelosztás, nem garantált fejlesztésiROI. 1degree/60feet slope60*tan(1°)=1.0473feet, nem1.5; stimp egység/szabvány később ellenőrizendő. Longergainvsaccuracy döntéshez veszélyek teljeseloszlása és targetutility szükséges. A0.1stroke/hole*18=1.8feltételezi, hogy mind18lyukugyanaztdöntési lehetőséget adja.

## 43. fejezet: e-sport (PDF421–428)

Dota40kgame,rawRadiant.525,PudgeRadiant.536/Dire.463,18,395Pudgegame logisticintercept−.17,level.004,side.32,interaction−.003. A dataset„level1–25” alapjánnoviceplayerekkövetkeztetése gyanús: el kell különíteni in-matchhero level,accountlevel ésMMR/skill. A snapshotidőpont/patcheltérés,provsrankedjáték,side/pickselection és playeridentity fontos. A raw52.5 nem okságimapedge, és a nemszignifikánslevelkülönbség nem egyenlőeloszlásbizonyíték.

HeroAPMridgebinarytargetintercept.523, Riki+.072→.595; a szerző jelzi, hogy linearscore[0,1]-enkívülmehet éscalibrationkell.58.5%testaccuracy vs52.5%sidebaseline kisjavulás, de teljesprotocol/uncertainty/proper-scorehiányos. Ritkánpickelt hero nem automatikusan replacement-quality. Stablepatch/role/timing-featureadás szükséges. NBA2K2019passingopportunity nőijátékosoknál eltérő példa: opportunity és conditionalperformance külön mérése fontos. Aktuálisteamcount/hero113/revenue/vieweradatok korabeliek, nem mai specifikációk.

## 44. fejezet: sportfogadás alapjai (PDF432–442)

Spread settlementhez margin=ownscore−opponentscore kell; a könyv ownpoints−3>0 írása hibás. Spread/total nem szükségképpen mean vagy mode, és diszkrétmarginpushszámít. −110break-even conditionalresolved11/21=.5238095 korrekt;57%win,10profit/11risk→.0881818ROI. Push-salunconditionalEV=10pwin−11ploss, és hányadot csak a resolvedesetekkel helyes összevetni.

**Vig/hold/overround külön:** azonos−110odds és2*11dollarstake mellett a book1dollartkeres22handle-en=4.54545%, nem a könyv1/21=4.7619%-a. Az impliedprobösszeg1.047619 túlára4.7619%, ezmásnevező. A könyvstake és to-winunit keveredik, később10.50átlagstake sem valódi előrelekötött11risk. Moneyline−240/+200break-even .705882ésdog.333333; fairp a kétthresholdátlaga nem bizonyítotttruep, a proportionaldevig is csakfeltételezés. Onefavorite240stake+dog100stake outcomehouse0/40, p=.69feltételezve12.4/340=.03647 helyes ezen a betmixen. Symmetricbeliefnem garantálequaldollars, azonos számúbetternél is eltérőstake.

MLBlistedpitcher-szabály nem univerzális, action/listed és actualplatformelszámolás kell. SuperBowl2017MLitt−120/+100,47fejezetben−150/+130: eltérősnapshot vagy következetlenség, nem keverhető. Arbitrage−122/+125:122<x<125, x123.50minprofit1.2295max1.5. Egyenlőprofitx=225*122/222=123.64865,profit1.35135. Threewayodds2/4/5inverseösszeg.95,100stakepayoff105.2632, allocations52.6316/26.3158/21.0526. Centsroundingutánnem pontosanazonosprofit. Minoutcomeprofitlinearizálható z≤payoff_i korlátokkal, nem kellGRGmultistart; limits,fees,slippage,void/rulematching nélkül nem gyakorlatiriskfree.

Parlaypush„bármelylegpush→teljesrefund” nem általános, gyakranlegremoved; teasernélszinténrulespecifikus, loss+push különösen. nindependent50%leg és payout2.6/6/12/25/35profit alapján houseedge10/12.5/18.75/18.75/43.75%korrekt, de máskétszorzósoddsra nem univerzális. Különmeccsek nem szükségképpen teljesfüggetlenek; sharedmodel/league/weatherkockázat isvan. Samegamecorrelation70%conditionaltoyexample; „mostbooksnemfogadcorrelatedparlay” aktuálisüzletiállításkésőbbellenőrizendő. Teaser7point examplepwin.706²=.498436,panypush.029775,ploss.471789 csakazontermékszabályalatt aholpushmindentvoidol. EV100*.498436−135*.471789=−13.847915; stake135reedge10.2577%, nem10.1. A„100bet”felvezetés valójában100to-win/135risk.

## 45. fejezet: bookmaker és bettorbias (PDF443–445)

Levitt2001bookdatafavoritbetsharehome56.1/away68.2: betdarabszám vs dollarmennyiség pontos különbsége fontos. Az idézett49.1/57.7/47.8/50.4coverarányok súlyozottbettoradatok lehetnek, nem automatikus komplementgamefrequenciák. Bookpwin49.45%→profit.6155per11stake vs.5, relatív23.1%profitnövekedéskorrekt, a szöveg.6156kerekít. Bettermix és linebias alapjánbookkockázatot vállalhat, nem mindigbalancedbook; ez nem bizonyít ma isarbitrázsmentesdefaultcontrarianprofitot.

2002–16road+10dogs53.08%373game,road−8favorites44.52%155game: utólagos küszöbválasztás, pushes,prices,CI,multiple-testing és időbenkülöntestkell. Line1/2.5ponteltolás50%hitre calibratinginsamplemedian, nem a book„valódihite”vagyándékára közvetlen bizonyíték. ROI−110mellett kiselőny nehezenkülöníthetőmintazajtól.

## 46. fejezet: saját csapatrating (PDF446–469)

Odds nélküli baseline: μmargin=HCA+rhome−raway, OLSgamemarginból,meanr0azonosítással.2016NFLHCA2.57; korabeli3/4pontállandók nemörökparaméterek. Least-squaresmean nem szükségképpenmedianfairspread; diszkrétkeynumbersésaszimmetria számít. Meanresidual0 fittedintercept és trainingminta alatt, nem általánostestgarancia. Londonhomekéntkezelve torzíthat. Schedule-strengthopponentratingátlag,16osztóseasonfüggő. Spreadsheettypos: ColumnFvsLmargin; awayVLOOKUPleírásH6/DenverhelyettI6/Carolina. „MindenregresszióSSE”nem igaz a korábbi logisticMLE-kre.

LAD/MAE robustabb outlierekre, de nem feltétlen egyedi; ridge a coefficientstabilitásra, nem ugyanazmintrobustloss. Sagarin„LSjobb”eredmény itt nem reprodukált. A szerző helyesen week1…Ktrain/K+1testet javasol. Offense/defenseadditive: home=μ+.5h+oh+da,away=μ−.5h+oa+dh; átlagoff/def0,defpozitívrossz,overall=o−d. TotalHCA ígykinullázódik, ami modellfeltétel, nem természeti törvény. Covariance/pace és scoringdistribution nélkül ez csakmeanforecast.

BTlogitwinMLE: **tie likelihood .5*p+.5*(1−p)=.5 konstans, ezért a döntetlen semmi információt nemad**, nemhalfwin-halfloss. Softlabelcrossentropy .5logp+.5log(1−p) adhat fél-félcreditet, de azsem valóditieprobability; Davidson/ordered/discretescore szükséges tiepiacra. NE1.85–ATL.96logit=.89→p.70889,nem.79. Separationundefeatedteam esetén divergensMLE, pseudo1W1Lagainstfictitiousteam vagyprior/ridgevégesíthet, nem feltétlen objektív erő. Recencyλ.95NFL/.9college historikusajánlás,tuningcsakpastvalidation. Brady4meccseltávolítás és playoff2xweight kézi policy,előredeklaráltablationkell.32.43+30.43=62.86, nem62.5aSuperBowltotal, spread2helyes. Eredményhezutólagigazítottparaméter nem forecastbizonyíték.

Soccer multiplicativegoals μ*h*oh*da illetveμ/h*oa*dh,meanoff=meandef1; positivityloglinkkelbiztosítható, h=0bounddivisioninvalid. SSE helyett countlikelihoodkoncepciófontos. SpreadsheetIcol helyettH-baírtsquarederror circular lenne;AVERAGE(H5:N24)helyettN5:N24kell. Chelsea2.38Reading.916ból „2–1”roundedmean nem bizonyítottmodalexactscore. Poissonpéldaλ1.4: P0=exp(−1.4)=.24659696, a könyv1.4exp(−1.4)/2!=.242sem számításkéntsem P0-ként helyes (az.172618, P2=.241665). Drawsumalsóindex0kell,nem1;0–0kihagyása jelentős. Tail>10levágás után„otherwin=1−draw−win”rosszhelyreteszmaradéktömeget, kontrollkell. A táblázatdraw.174feltehetően0–0tisbelveszi, képletettől eltér.

Skellam PMF exp(−λ1−λ2)*(λ1/λ2)^(z/2)*I_|z|(2sqrtλ1λ2). Kinyertkönyvbenpozitívexponent, vizuálisellenőrzéskell. „BármilyenkorreláltPoissonkülönbségeugyanaz”túláltalános. KözössháttérkomponensesbivariatePoisson X=U+W,Y=V+W esetén Wkiesik, SkellamU/Vintenzitásokkal; nem az X/Ymarginalmeanekkel. Negatívvagyegyébdependenciára nem általános. Directmarginlikelihoodfittelhetmásλ-t, de nem biztosítja, hogy atieproblem mindenkor megoldott. EgyetlenChelseaReading2–2realization nem modellvalidáció.

## 47. fejezet: ratingből valószínűség (PDF470–486)

HistoricσNFL14,NBA12,NCAA10,collegefootball16; residualσoutoftimeésstatefüggés, nemautomatikusnormaldistribution. Diszkrétmargincontinuitycorrection[a−.5,b+.5]; NFLkeynumbers célzotttömegetkívánnak. Coltsμ7,line−4:pcover≈.571,ppush≈.028,pcover|resolved≈.587; pushnemveszteség, azEVdenominátora azösszesbetlekötöttrisk. Finalmarginmodellvsregulationmarginmodell szétválasztandó: ha márOT-valfiteltfinalscores, mégOTtiekülön hozzáadása duplaelszámolás.

Patriotsμ3OTképlet másodikCDF-benμ7szerepel, typo; μ3mindkétCDFbenkell.−150/+130break-even .60ésdog.43478, favoritetiltottintervallum.565217….6. FootnoteML≤100helyettnegativebranchML≤−100értelmes. Coltsμ8teaser−1nélregulationtie +.5*pOTcover csak haOTwinmargin>1, ésOTtie/failureelszámolás szabályfüggő. Ligaminden7pointteaser70.6% és egyμ8matchup70.9% egyezés nem modellvalidáció.

NBA2017μhome11.46,μaway5.46,sigma12; szövegawayCavs-marginnek nevezi a pozitívWarriors-margin-t. SzövegHHAAHAH,47.2táblázatHHAAAHH (régi2–3–2)eltérés. Függetlenrögzítettp-késazonos4home3away mellett sorozatgyőzelemvalószínűség sorrendtől független, de serieslength/gameprop/orderadaptationhelytelen lehet. Fix7szimulációlegalább4win helyes serieswinneralgoritmus ezen feltételekkel.1000MCiter .95p-nél~.0069MCSE, nem paraméterbizonytalanság. Injury/matchup/lineup/pacingadaptationvagysharedlatentstrength miatt iidgamefeltétel gyenge.

NCAAbrackethelyesrouting,pre-tournamentrating ésneutralrules fontos. RatingsrangeA3:AL4csak38oszlop, a64teamhoznemelég, továbbiworksheetkontextuskell. Finalsfigure−4.645 vs szöveg10margin. Leggyakoribbroundwinner szerinti tippsor nem feltétlen koherens bracket/nem optimalizál mindenpoolpayoutot: scorerules/opponententrieskell. Egy99percentilebracket és bejöttPatriots nem bizonyít tartósedge. NFL2017hatteamconference csakakkoriformátum; QBnévDavidCarrhelyettDerekfeltehető, primerellenőrzéskésőbb.

Platt scaling: p'=1/(1+exp(Af+B)); rawscore ésprobability/logitinputnemazonos. A könyv A=−1.17,B=2.74 eseténf∈[0,1]→p'≈[.0607,.174], nem[.27,.8]. Valószínűintercept/slope/signkeverés, ábraésforrásellenőrzéskell. A week4…16trainnextweekelőrejelzéssor jóoutoftimealap, de ugyanazonoutoffoldpredictionsrecalibratortfitelniésottlogloss.67→.64értékelni optimista; különcalibration/test vagy rollingnested. Logistic„probabilistic”output nem automatikusan jólkalibrált distribution-shiftben. Calibrationnem szükségképpenconfidencecsökkentő; isotonicisoverfitelhetkicsimintán. Tiedgameskihagyása feltételesnontiewinprob-t ad, nem fullwinprob.

## 48. fejezet: NET (PDF487–490)

ÖtkomponensesNETleírás történeti, aktuálisNCAAruleskésőbbellenőrizendő. Margin10capvsuncappedefficiency eltérőösztönzés; TVImódszerre„szerintünkhasonlóregression”szerzői feltételezés, nem dokumentáltalgoritmus. PossestimateFGA+TO−OREB+.475FTA: Pitt61.125 vsGT71.825ugyanazonmeccsen feltűnőeneltér,teamrebound/adat/definícióellenőrzéskell;119.43−89.11=30.32nem azt jelenti,hogy9pontténylegesmargin ugyanazon100possra30pont. Comparable/averagedpossessiondenominator szükséges. Azadjustedwinexample1.2/(1.2+1.6)=.42857korrekt. RPI/NETSpearman.95all353vs.45top10rangkorrelációnem jövőbelipredikciósminőség.

## 49. fejezet: Kelly (PDF491–496)

A könyv„EdwardKelley1956” névvel hivatkozik; a történeti szerző/írásmód primerellenőrzésre jelölve, nem ennek alapján névadás. Generalizedwinprofit a,lossmult b: max p ln(1+af)+q ln(1−bf),f*=(pa−qb)/(ab)=p/b−q/a; longonlyf≥0,solvencyf<1/b, zeroedgef0. A könyv spreadnél a1,b1.1,to-winunitot használ, ezért f=.14545 p.6 esetén **riskfraction=.16**, nem standardriskfraction. Standarddecimal1.90909nél a=.90909,b1→f=.16. Grossgrowth exp(expectedlog), százalékosgrowth exp(log)−1; nem arithmeticexpectedwealthgrowth. p.6nálgeo≈1.181%, a nyitó1.8%typo. Expectedarithmeticgrowth nagyobb és más célfüggvény.

Táblázatos f.03… .7182 a könyvto-winbasefeltételeire helyes, de mindig riskegységre kelláttérni. pmodelerror,korreláltnapifogadások,uncertainstakes,fees/minimumok ésbankrollconstraints nincsenek részletesen kezelve ebben a fejezetben. FullKellyt nem szabad bizonytalanságkezelés nélkül automatikus1–5dollártippre ráhúzni. Negatívedge-nélno-bet, nem negatívtét vagy kötelezőfogadás. A felhasználó1–5dollárkorlátja csak elfogadottbetre vonatkozhat, nem minden napijelöltre.

## 50. fejezet kezdete (PDF497, részleges)

Calcuttaaukció, pot és kimeneti súlyok bevezetése. A teljes folytatás alább.

## 50. fejezet: Calcutta (PDF497–499)

A pontosan 1…6 győzelemhez tartozó 2/5/15/20/25/33%-os potrészeket a kategóriába kerülő csapatok számával osztja: 16/8/4/2/1/1. Csapatonként a nyereményrészek .00125/.00625/.0375/.1/.25/.33. A csapat fordulóeloszlásával súlyozott potrész az értékelés alapja; a példa Kentucky .200471, Wisconsin .08754425, Kansas .02270375. A várható teljes pot becslése külön bizonytalan: egyetlen aukciós ár nem bizonyítja a teljes piac helyes árszintjét, és a saját licit is növeli a potot. Ha mások befizetése C, a saját licit b, a várt részesedés f, a nettó EV=f(C+b)−b; ezért a nullszaldós saját licit fC/(1−f). A könyv egyszerű f×pot korlátját csak tisztázott potdefinícióval lehet használni. Extra 5%-os különdíjnál az összes kifizetési rész újranormalizálása szükséges. Közös bankroll és egymást kizáró csapatkimenetek korrelációja nincs részletesen kezelve.

## 51. fejezet: adatgyűjtés és megjelenítés (PDF502–512)

API végpontok, paraméterek, HTML-feldolgozás, nflscrapR és Excel-makrók bemutatása. A megadott NHL statsapi-végpont, PFR Game Finder, szolgáltatói elérés és ingyenes tárhelyállítások történetiek; jelenlegi működésük nincs ellenőrizve. Makrót nem futtattam. A későbbi forrásellenőrzésben verzió, licenc, séma, lekérdezési korlát és időbélyeg is szükséges. A vizualizáció alapelvei: arányos vizuális kódolás, oszlopdiagramnál nulla kiindulópont, színtévesztők számára is értelmezhető színek, a döntési határok bemutatása.

A Texans play-by-play példában a pass/sack szövegminta szerinti besorolás minden mást futásnak vesz; ez a büntetéseknél, térdelésnél, scramble-nél és érvénytelen play-nél hibázhat. Strukturált eseménytípus és kizárási szabály szükséges. A 167 passz/285 futás 452 play, EPA-átlag .044/−.142, SD1.605/.725: a különbség önmagában nem oksági passzelőny. A downside átlag E[min(EPA,0)] nem a veszteséges play-ek feltételes átlaga és nem szórás; minden play a nevezőben marad. A kockázat célfüggvényfüggő, nem kizárólag ez a negatív rész.

## 52. fejezet: Bayes (PDF513–521)

Posterior ∝ likelihood × prior; partíció felett normalizálni kell. Az orvosi példa csak hipotetikus matematika: .004 prior, .8 érzékenység, .1 téves pozitív mellett egy pozitív utáni valószínűség .031128. A táblázat egészséges negatív sorában .996×.8 helyett .996×.9 ad8964-et. Ismételt eredmények odds-szorzása csak feltételes függetlenségnél jogos; a harmadik pozitív utáni pontos érték kb. .6728, nem .666. Monty Hall 2/3-os váltási előnye a műsorvezető ismert viselkedési protokolljától függ.

Normális prior önmagában nem garantál normális posteriort: normális likelihood és megfelelő feltételek kellenek. A normális–normális modell posterior precizitása 1/τ²+n/σ²; kis mintás becsült σ nem ismert σ. Beta(5,2) átlaga5/7, módusza.8;16/20 után Beta(21,6), újabb16/20 után Beta(37,10). Átlagból nem azonosítható a prior erőssége α+β. A megfigyelt játékosszázalékokra illesztett prior összekeveri a valódi heterogenitást a binomiális mintazajjal, ha a próbálkozásszámokat figyelmen kívül hagyjuk. A faktorális béta-sűrűség csak egész paraméterekre ilyen alakú; általában gammafüggvény. A posterior nem minden egyes adat után szükségképpen közelít az igazsághoz; modell, azonosíthatóság és prior-tartó szükséges. Garoppolo63 passza és utólagos sikere nem bizonyítja egy csere pénzügyi értékét.

## 53. fejezet: nemnegatív mátrixfaktorizáció (PDF522–530)

S≈WH, S játékos×zóna, W játékos×komponens, H komponens×zóna; a mintázatok H soraiban vannak, nem oszlopaiban. A közös célfüggvény nem konvex, helyi optimum és inicializáció számít. A komponenssorrend és skála nem egyértelmű; W D és D⁻¹H ugyanazt adja, így súlyokat normalizált komponensekkel szabad összevetni. Nyers darabszám a stílust a játékidővel keveri. Az adatok 2014–15-ösnek vannak leírva, az egyik ábrán 2015–16 szerepel; ellenőrzendő.

348 játékosból a≥50 dobású288 szerepel12 zónában; finomabb47×50 rács2350 cellával térbeli simítást igényel. A k≤min(m,n) kompressziós választás, nem általános matematikai kényszer. k=12-nél W=S,H=I már pontos rekonstrukciót adhat; nem kell12 fölé menni. A tréninghiba csökkenése és az elbow nem bizonyít predikciós értéket: elkülönített időszak, stabilitás és felhasználási cél szükséges. A térbeli intenzitásbecslés és bázisillesztés is kizárólag múltbeli adaton történhet.

## 54. fejezet: hálózatok (PDF531–546)

Irányított, súlyozott passzhálózat; a teljes passztömeg60%-át megtartó küszöb befolyásolja a kapott kapcsoltságot. L=D−A és λ₂ algebrai kapcsoltság az ismertetett formában nem tetszőleges irányított gráfra érvényes. A PDF535 mátrixa látszólag aszimmetrikus (A15=1,A51=0), a fokszámösszeg15 páratlan, miközben az ábra irányítatlanként kezeli; vizuális ellenőrzés szükséges. A csapatkémia λ₂-korrelációja.59, de kis mintán nem szignifikáns; ezt a szerzők sem tekintik bizonyítéknak. Tempó és possessionszám is mozgatja a súlyokat.

Passzmotívumok ABAB/ABAC/ABCA/ABCB/ABCD, véletlenhálózati összehasonlítás és klaszterezés stílust jellemez; ettől még nem nyerési előrejelzés. A nullmodellnek meg kell őriznie a releváns fokszám-, idő- és labdabirtoklás-szerkezetet. **A kovarianciamátrix sajátértékeiből a PCA magyarázott variancia aránya Σλᵢ/Σλ, nem Σλᵢ²/Σλ².** Négyzet a szinguláris értékeknél indokolt. A könyv fogalmai itt összekeverednek.

Az Ewing/Braess példa a rossz helyi döntések és a közös optimalizálás különbsége: az elérhető közös optimum nem romolhat attól, hogy egy további opció választható, hiszen elhagyható. Az empirikus usage–hatékonyság görbe nem automatikusan oksági, távoli extrapolációja veszélyes.

SportsNetRanknél az élirány és a mátrix sor/oszlop definíció változik: vesztestől a győzteshez, táblázatban sor a győztes. PageRanket normalizált átmeneti mátrixszal, dangling csomópontokkal és teleporttal kell definiálni: π=αPᵀπ+(1−α)v. A könyv D(D−αA)⁻¹1 alakja és a nulla kifoknál önkényes D=1 nem magától értetődően ugyanaz. Rangból külön kalibrált eredményvalószínűség szükséges. A 2009–15 évi hét SNR-accuracy átlaga.6143, a W%-os sor átlaga.5429, nem a szöveges52%; eltérő súlyozást dokumentálni kellene. Más években/más mérkőzéseken kapott FPI/Cortana-számokkal nem korrekt közvetlen összevetés. Teljes szezon gráfjának múltbeli meccsekre használata adatszivárgás.

## 55. fejezet: Elo (PDF547–553)

EA=1/(1+10^((RB−RA−h)/400)); ez döntetlenes sportban várható pontarány P(win)+.5P(tie), nem P(win or tie). Frissítés RA+=K(SA−EA); az ellenfél ellentétes változása azonos K mellett zéróösszegű. h≈56 azonos erejű csapatok.58 hazai várható eredményéhez, nem tetszőleges nyers hazai győzelmi arányhoz. K, szezonközi visszahúzás és expanziós kezdőérték történeti modellezési választás, időrendben validálandó.

**A szezonfogadások piaci win line-jából javasolt inicializálás nem felel meg a felhasználó elsődleges saját modellre vonatkozó feltételének.** A line ráadásul nem feltétlenül várható győzelmi átlag. A PDF550 idegenbeli tagjában az előjel a másik csapat hazai győzelmét adja; i csapat idegenben helyesen 1/(1+10^((Rj−Ri+h)/400)). Az átlag1500-hoz32 csapatnál az összeg48000, nem1500. Glicko rating deviation az inaktivitási és becslési bizonytalanság kezelése; a±3RD normális feltételezéshez kötött, a könyv nem ad teljes Glicko-algoritmust.

## 56. fejezet: korszakok (PDF554–560)

APM_it=állandó játékoserő_i−korszakerő_t illesztés rögzített bázisévvel. Az életkor és kiválasztási/túlélési torzítás erősen összekeveredhet a korszakhatással; az APM becslési szórásai eltérnek. A −.78-as eltérés intervallum nélkül nem elég erős következtetés. Az életkor–időszak–kohorsz szétválasztása további feltevéseket igényel. A történeti Bayes- és peak-age eredmények nem mai univerzális konstansok.

A home run átlaggal normalizált Ruth/Bonds összevetés relatív korszakdominanciát mér, nem azt, ténylegesen hány HR-t ütött volna más korszakban. AB/PA és ligadefiníció is számít. A NFL1977→1978 szezonpasszyard1976→2541 példájánál a14→16 meccses szezonváltozás miatt a meccsenkénti növekedés kb.12.5%, a szezonösszeg28.6%-a félrevezető szabályhatásként. A történeti szabálydátumokat később hivatalos forrásból kell ellenőrizni.

## 57. fejezet: fáradtság (PDF561–565)

2018–19 NBA291 egyoldalú back-to-back meccs összreziduum−551.1, átlag−1.8938, feltettσ12 esetén SE.70345,z−2.69; független normális közelítéssel95%-os CI kb.[−3.27,−.515]. A szezonvégi Sagarin-ratings használata utólagos elemzés, nem valódi előrejelzési teszt. Utazás, időzóna, keret, sérülés, meccsenkénti és csapaton belüli függés hiányzik. A nyitó általános „nem árt” állítás nem illik ehhez az NBA-eredményhez.

NFL136 bye utáni meccs átlag+.35,σ14 mellett SE1.2005,z.292,CI kb.[−2.0,2.70]. A nem szignifikáns eredmény nem bizonyít nullhatást/ekvivalenciát; az urban legend minősítés túl erős. A logitbye−.02,p.89 feltételes asszociáció. Ha egy meccs két csapatsora is szerepel, azokat nem szabad függetlennek tekinteni. A fáradtság a varianciát, sérülést és szélső kimeneteket is érintheti, nem csak az átlagot.

## 58. fejezet: egyetemi futball (PDF566–573)

A négycsapatos CFP történeti formátum; aktuális ellenőrzés nélkül nem mai szabály. Négy csapatból két elődöntő és egy döntő három meccs/két forduló, nem két meccs. Egyes BCS-dátumok későbbi történeti ellenőrzést igényelnek. A célváltozó végső AP-rang a szavazók ítélete, nem valódi csapaterő vagy nyerési valószínűség; a9×25 rangsorolt csapatból álló minta kiválasztott.

Stepwise p<.05 változóválasztás többszörös keresés; Power5 proxy konferenciapreferenciát és ellenfélerőt kever. TOP időegység (Excel-nap vs perc) tisztázandó. Ordinális logit: P(Y≤i)=logistic(θi−w·x), rendezett küszöbök és proportional-odds feltevés. A75/25 bontás szezonszintű időrendje nincs tisztázva. A top3 accuracy14% véletlen alapja12%, **nem a top1-hez tartozó4%**; top10 véletlen40%, a72% más metrika. A .83 rangkorreláció nem valószínűségi kalibráció. Néhány táblázatos SE/csillag gyanús, oldalképen ellenőrizendő. Az y=x nullhipotézis nem elvetése nem bizonyít jó közelítést.

## 59. fejezet: összeomlások és ritka események (PDF574–581)

Warriors3–1 előny elvesztése: (.25)(.42)(.34)=.0357 a Cavs három győzelmének közelítése. A kinyert szövegben 1−P(Cavs)=1−szorzat=.035 ellentmondás; komplementer.9643. A későbbi meccsek záró line-jai nem voltak mind ismertek3–1-nél, ezért ezekből utólag számolt valószínűség nem akkori ex ante prognózis. A feltételes kimenetek szorzata elvben helyes, ha az információs időpont és feltételek is helyesek.

Időarányos drift és σ√(hátralévő idő/teljes idő) normális folyamatmodell: állandó független növekményeket feltételez. Hiányzik labdabirtoklás, időkezelés, pontrács, stratégiai fault, score-függő taktika, OT. A könyv folytonossági korrekciót és50%-os OT-esélyt alkalmaz. A PDF576 nyomtatott NE-képletének pontos újraszámítása (μ−1.13,σ8.5 az Atlanta-nézőpontból; CDF−25.5/−24.5 átlaga) .002528323=.2528%, kb.1/396, nem.15%. A PDF578 Dallas-képletében a Raptors1.5 pontos pregame favorit; a Dallas-nézőpontú hátralévő drift−.45,σ6.6. A CDF−30.5/−29.5 átlaga4.009405e−6=.00040094%, kb.1/249414. Tehát a könyv százaléka itt helyes közelítés, az „1 in2500”100-szor hibás. A korábbi jegyzetben szereplő1/514427 az előjel/nézőpont téves azonosításából és a folytonossági korrekció elhagyásából adódott; visszavonva. Ezek modellfeltételes számítások, nem a valódi comeback-esély állításai.

A különböző élő modellek1/20…1/1000 számai önmagukban nem döntik el, melyik jobb: azonos állapot, időpont, input és ritkaesemény-kalibráció kell. Mets1986: a9. és10. inning bottom állapot csak azonos szabályok és becslési feltevések mellett azonos; mai extra-inning szabályra nem vihető át automatikusan. 0 comeback415 esetből nem nulla valódi valószínűség; add-one (0+1)/(415+2)=.002398, Beta(1,1) priorral posterior prediktív átlag. A prior választás és bizonytalansági intervallum számít, a simítás nem „bizonyított” esély.

## 60. fejezet: daily fantasy (PDF582–591)

Bináris játékosválasztás, költségkeret,8 rosterhely,5 alap pozíció és G/F/U flex. A kifejtésben az SF/PF egyenletek helyesen fSF/fPF-et használnak, az összesítőben gSF/gPF szerepel, noha ezek nincsenek definiálva. Kizárólag egy pozícióra jogosult játékosok példája; több pozíciónál játékos×hely hozzárendelés szükséges, különben ugyanaz a játékos többször számolható. A legalább2 csapat/2 meccs szabályt a modellből elhagyják és utólag ellenőrzik; az általános megoldásban feltételként vagy ellenőrzött iterációként szükséges.

MaxΣpᵢxᵢ a várható pontot maximalizálja, nem a versenynyertes várható nettó nyereményt. A floor/ceiling összege és tetszőleges60/40 súlya nem a csapat ponteloszlásának kvantilise; korreláció és közös meccshatás szükséges. A277.9 FP/50k keret megoldás csak az adott inputokra optimális. Az ábrán az összes pozíciószám látszólag7, a táblázat8 játékos és2PF; ellenőrizendő. A floor/ceiling ábra kinyert oszlopai ismét az eredeti inputot mutatják, vizuális ellenőrzés kell.

11000 versenyzőből2235 díjazott aránya20.318%, nem pontosan20%. A versenytárs260±30 normális közelítéséből a80. percentilis285.25; ez a kifizetési küszöb közelítése, nem a saját csapat eloszlása. A könyv1−NORMSDIST(285,260,30,1) alakja rossz függvényszignatúra (NORM.DIST kell), és rögzített konstans, ezért nem optimalizálható a kiválasztott játékosok szerint. A saját csapat ponteloszlása, ellenfelek közös játékosai, díjstruktúra, nevezési díj és osztozott helyezések szükségesek. A pozitív kifizetés sem azonos pozitív nettó hozammal. A DFS nem elsődleges felhasználói cél; a feltételes eloszlás és korreláció tanulsága átvihető.

## Teljes szöveg olvasási határa

Az összes60 fejezet, előrészek, bibliográfia és index elolvasva:1195992/1195992 karakter,609 PDF-oldal. A bibliográfia későbbi primer ellenőrzési irányokat ad (Glickman, PageRank, Miller–Sanjurjo, tracking, kalibráció), de a hivatkozások puszta felsorolása nem jelenti a hivatkozott cikkek elolvasását. A vizuális ellenőrzés lezárva, lásd alább.


## Záró oldalképes ellenőrzés és pontosítások

A B08-critical-visual-manifest.json20 oldala után a B08-audit-visual-manifest.json159 további oldala mind ténylegesen megtekintve (40 áttekintő kép). Külön nagyításban535,550,572,578; utóbbi új oldal, összesen180 különböző oldal. Nem minden609 oldalt rendereltem: a teljes szöveg minden oldala, az összetett vagy kérdéses képletek/táblák és az ábrák kiválasztott oldalai együtt alkotják a feldolgozást.

- PDF535: a gráfon tényleges nyilak vannak, az1–5 kapcsolat egyirányú. Az A aszimmetriája ezért önmagában **nem nyomdahiba**; a korábbi irányítatlan/páratlan fokszám érv itt nem alkalmazható. D az adott sormegállapodás szerinti fokszám. A könyv általános λ₂-állításához viszont továbbra is tisztázni kell az irányított gráfra használt Laplace-operátort és kapcsoltságfogalmat; az irányítatlan tétel nem vihető át magyarázat nélkül.
- PDF537: a kovarianciamátrix sajátértékeinek négyzete a PCA arányában valóban nyomtatott hiba; nem kinyerési probléma.
- PDF550: a hazai Elo-tag Rj−Ri−He alakja helyes. Az idegenbeli tag Ri−Rj−He és az összeg1500 viszont valóban a könyvben szerepel; a fenti javítás érvényes.
- PDF572: a TOP/G−.04(.0002) csillag nélkül, illetve5|6 küszöb−5.24(.404),6|7−4.95(.03) a nagyított eredetiben is ilyen. Ezek gyanús standardhiba/csillag minták; helyes értéket az illesztés nélkül nem találok ki.
- PDF575: a3–1 sorozatvesztés komplementerképlete hibásan van nyomtatva. PDF576/578 gyökjelei viszont helyesek, a szövegkinyerés vesztette el őket. Az újraszámítás a fenti59. fejezetben korrigálva.
- PDF585: SF/PF sorokban gSF/gPF szerepel fSF/fPF helyett. PDF588 összesítőben2,2,1,1,1=7, miközben a kiválasztási összeg8 és a játékostábla két PF-et mutat; a képek nem egy következetes megoldássnapshotot ábrázolnak. PDF590 ábrája valóban a régi oszlopokat ismétli, nem floor/ceiling adatokat. PDF591 rögzített normál-CDF célfüggvénye nem ad döntésfüggő optimalizálást.
- PDF468: a Skellam-képletben ténylegesen pozitív exponens szerepel, helyesen exp(−λ1−λ2). PDF466 döntetlenösszeg0 helyett1-től indul, ezért kihagyja a0–0-t; a nyomtatott példabeli „1.4 gólos átlagból0gól” .242 értéke valójában2gól valószínűsége.
- PDF484–485: a kalibrációs görbe és szigmoid a szövegben felsorolt A/B paraméterekkel nem egyezik. Az ábra alapján az intercept−1.17 és slope+2.74 standard logistic alakja valószínű lehet, de ezt nem állítom ellenőrzött forrásjavításnak.
- PDF28/29 baseball MAD2.17%vs2.05% valódi eltérés. PDF32 NFL.051vs.0639 **külön mintákra** is vonatkozik (2015ábra vs2008–2015szöveg), ezért ezt önmagában nem minősítem hibának. PDF46 az Absolute Error oszlop előjeles hibákat mutat.
- PDF387 átmeneti mátrix és fundamentális mátrix rendben látható; az ábra MINVERSE(F24:H26), a szöveg későbbi H24:J26 hivatkozása eltér. PDF479 döntőmargin−4.645 a szöveg10-es értékével ellentmond.
- PDF498 a0 győzelmű csapatnak nincs kifizetés; az1…6 győzelemhez tartozó potrészeket az50. fejezet jegyzetében javítottam.

A feldolgozás a mellékelt könyv olvasását és kritikai értelmezését jelenti. Nem jelenti a könyv minden külső adattáblájának/munkafüzetének reprodukálását, a bibliográfia összes cikkének elolvasását vagy egy nyereséges modell igazolását.
