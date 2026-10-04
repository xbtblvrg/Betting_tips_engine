# B21 — Andrew Mack: Statistical Sports Models in Excel

## Forrás és tényleges lefedettség
2019-es copyright; a mellékelt példány nyomtatási dátuma 2022-09-14 (PDF 166), ez nem új tartalmi kiadás bizonyítéka. Teljes 168 oldalas PDF, 309102 karakter végigolvasva, a kinyert képernyőképszövegekkel együtt. Képként külön ellenőrizve: PDF 22, 24, 26, 85, 90, 113, 116, 117, 128, 134, 139, 155, 156, 157, 164. A 164. oldal valójában üres; az OCR ott áttűnő/hibás zajt adott. A táblázatok többi apró cellájának hibátlan átírását nem állítom. A hivatkozott Excel-mellékleteket még nem töltöttem le és nem auditáltam.

## Fejezetenkénti tudás

### 1–4. Bevezetés és eszköztár (PDF 7–47)
A szerző kezdőknek szánt, korábban használt modellpéldákat ad; kifejezetten nem ígér mai piacot verő modelleket. A 2019-es szolgáltatói és szoftverállításokat történetiként kezeljük. Excel Solver, Analysis ToolPak, Real Statistics, opcionális EasyFit/XLStat/Solver Data Mining. Nem szükséges ezeket megvenni a módszerek megértéséhez.

A hasznos módszertani lánc: bemenet → függvény/feltevés → várható eredmény → eloszlás → ár-összevetés → érték. Egy becsült győzelmi különbség még nem fogadási valószínűség. Hazai előny, relatív csapaterő, külön támadás és védelem eltérő modellfeltevések.

Mérőszámok: nyers találati arány, R², RMSE, MAE, log loss, Brier, mintán kívüli profit. Eloszlások: normál, binomiális, Poisson, negatív binomiális, geometriai. Korrelációs mátrix és Spearman-rangkorreláció, lineáris/logisztikus regresszió. A függőség nem bizonyít okozati hatást. A Bayes-fejezet fogalmi bevezető; erősen leegyszerűsített, nem elég módszertani specifikációnak.

### 5–6. Latens rating és Bradley–Terry (PDF 48–69)
A leíró múltbeli statisztika nem automatikusan prediktív feature. A páronkénti rating kevés adattal, pusztán eredményekből is felépíthető. Ez nem bizonyítja minden box-score modell alkalmatlanságát; a hivatkozott egyetlen mélytanulásos eredményből a szerző túl általános következtetést von le.

BTM: p(home)=sigmoid(h+r_home−r_away). A ratingeket és h-t a megfigyelt bináris eredmények log-likelihoodjának maximalizálása adja. A könyv előbb valószínűségszorzatot logaritmizál, majd helyesen javasolja a logok összegét a numerikus alulcsordulás elkerülésére. Figyelni kell az azonosíthatóságra: minden ratinghez ugyanazt hozzáadva p változatlan; centerezés/egy referencia szükséges. Veretlen csapatnál vagy szeparált eredményeknél a véges MLE létezése sem automatikus; a bemutatott Solver-konvergencia nem old meg minden statisztikai problémát.

A bemutatott BTM nem súlyoz idő szerint; ezt a szerző is hiányosságként jelzi. A p-ből újabb lineáris regresszióval MOV lesz, majd normál CDF-fel új p. Ez hibrid, nem pusztán a klasszikus Bradley–Terry modell; a két p eltérhet. A nyers logisztikus p és e többlépéses változat külön validálandó. Normál residual-szórás a tanítómintából nem foglalja magában automatikusan a rating- és paraméterbizonytalanságot.

Döntetlenre a szerző ritka esetben törlést, gyakori esetben két ellentétes +1 gólos áleredményt javasol; utána ±0,5 normál határokkal becsül döntetlent. Ez ad hoc közelítés, megduplázza a meccs súlyát, megváltoztatja a MOV-eloszlást, és nem egy korrekt háromkimenetű likelihood. Nem vehető át kritikátlanul.

### 7. TOOR (PDF 70–75)
A BTM-ratingekből MOV=a+b*r_home+c*r_away; az együtthatókat SSE-minimalizálás illeszti. Ezután újabb MOV→MOV regresszió és normál CDF következik. Ugyanazon mintán, konvergált OLS mellett az utóbbi regresszió elvileg 0 intercept/1 meredekség lenne; eltérésnél az adat- és tartományazonosságot ellenőrizni kell. A külön hazai/vendég együttható eltérő feltevés a puszta ratingkülönbségtől.

### 8. GSSD (PDF 76–83)
Csapatonként hazai szerzett/kapott és idegenbeli szerzett/kapott átlagok; ezek megfelelő párosításából lineáris MOV, Solver-SSE-illesztés, további mapping. Fontos adatszivárgási veszély: a bemutatott szezonátlagok csak a tréningvágásig ismert eredményből készülhetnek. A veszteségfüggvény tartományának levágása önmagában nem teszi tisztává a holdoutot, ha az inputátlagok már tartalmazzák annak meccseit. A könyv ezt a fejezetet nem dokumentálja elég részletesen; a táblázat jó holdoutértéke nem auditált bizonyíték.

### 9. ZSD (PDF 84–88)
Latens csapatkülönbség + helyszínkorrekció → sigmoid → standard normál kvantilis → ligaszintű pontátlag + szórás*z. Hazai/vendég pontelőrejelzés SSE-je közösen optimalizált; ezek különbsége MOV, összege total lehet. A képen ellenőrzött 85. oldali képlet mindkét lookupnál 2. oszlopot használ, miközben a próza hazai/idegenbeli ratingről beszél: a melléklet nélkül nem állítom, hogy két külön szerepratinget használ ugyanabban a hazai függvényben. A transzformáció negatív pontbecslést is megengedhet, és szélső sigmoidnál kvantilis-instabilitás léphet fel. A latens skála matematikai választás, nem empirikusan bizonyított ponteloszlás.

### 10. PRP (PDF 89–92)
Külön támadó és védő rating; a hazai várható pontszám ligaátlag+0,5*h+hazai támadás+vendég védelem, a vendégé ligaátlag−0,5*h+vendég támadás+hazai védelem. Mindkét ratingcsalád átlagát 0-ra korlátozza: itt az azonosíthatóságot expliciten kezeli. A pozitív védő rating több engedett pontot jelent, tehát rosszabb védelmet. A két pontszám hibáját minimalizálja. A további MOV-kalibráció külön lépés.

### 11–12. Ensemble (PDF 93–109)
Különböző, önmagukban használható modellek kombinálása; közös hibák esetén a puszta darabszám nem jelent sok független információt. Egyszerű valószínűségátlag, MOV-regresszió, majd logisztikus stacking. BTM és TOOR erősen összefügghet. A logisztikus linear predictor log-odds, p=exp(eta)/(1+exp(eta)).

A bemutató in-sample predikciókon is illeszt kombinálót. Tiszta vizsgálathoz időrendben mintán kívüli alapmodell-predikciók szükségesek a stacking tréningjéhez, majd külön végső teszt. A negatív t-statisztika önmagában nem teszi haszontalanná a prediktort; előjele a koefficiens előjelét követi. A p-értékek iteratív 0,05 alá faragása nem validáció. Regularizáció/PCA értéke sem az, hogy p-értéket gyártson. Az átlagolásnál a korrelált hibák csökkentik a nyereséget, de nem minden korreláció egyenlő káros regressziós multikollinearitással.

### 13. Benter Boost (PDF 110–118)
Saját modell + piac marginmentesített valószínűségeinek kombinálása, Benter munkájára hivatkozva. A szerző saját ad hoc adaptációként is jelzi. A leírt de-vig valójában hatványmódszer: q_i=(1/odds_i)^k, k úgy választva, hogy Σq_i=1. Ez becslés, nem a valódi esélyek feltárása.

A modell és piac logaritmusainak súlyozásából normalizált geometriai pooling lesz: u_i=p_model_i^a*p_market_i^b; p_i=u_i/Σu_j. A könyv LN(p)-t is „log odds”-nak nevezi; az log-valószínűség, a bináris logit LN(p/(1−p)).

A 117. oldali első képlet nevezője exp(összes súlyozott log), nem exponenciálisok összege. A későbbi G_i/ΣG_j normalizálás ugyanazon közös nevező esetén ezt algebrailag kiejti, tehát nem állítom automatikusan hibásnak a végső szimbolikus eredményt. A screenshot viszont nem konzisztens: no-vig 30,71%+66,80%=97,51%; boost 29,66%+68,22%=97,88%; a látható „Exponent” értékek és végső arányok sem egyeznek a közölt normalizálással. A Solver célcellája a képen F27, a prózában D22. A melléklet auditálása elengedhetetlen lenne.

A spread→MOV regresszió meredekségét „piaci hatékonysági százaléknak” tekinti, majd ezt ensemble-súlyként használja. Ez nem megalapozott: a meredekség skála/kalibrációs viszony, nem információs hatékonyság, nem feltétlenül [0,1]-ben van. Ezt a súlyozást nem vesszük át.

**Kapcsolat a felhasználói kéréssel:** a piaci oddsot predictorba beépítő Benter-változat nem felel meg a saját sportmodell elsődlegességének. Ismeretként megőrizzük; a saját modell helyettesítésére nem használjuk. A szerző EPL-profitpéldája ~40 fogadás, amit maga is nem bizonyító mintának nevez.

### 14. Visszateszt (PDF 119–125)
Időrendi végső játékok visszatartása, in-/out-sample szétválasztás, árak alapján rögzített 100 dolláros téttel mintaprofit, EV-küszöb. A példa kb. 177 tréning/20 tesztmeccs; a szerző hangsúlyozza, hogy ebből profitképesség nem következik. A GSSD 9 bet/+356 dollár csupán bemutató. A kiválasztott legjobb rendszer p-értéke több próbálkozás mellett félrevezető lehet.

Nyitott módszertani rések: teljes transzformációs lánc tréning-only illesztése, időbélyeges tényleges ár, többes próbálkozások és EV-küszöb utólagos hangolása, függő fogadások, változó oddsok nullmodellje. A p-érték a null mellett legalább ilyen szélsőséges eredmény esélye, nem a skill hiányának esélye; „pontosan ez a rekord” valószínűségeként sem pontos. A könyv t-próbás átlagodds-közelítése 9 fogadásnál különösen gyenge. Az újramintázás nem pótol új információt.

### 15. NBA: tripla-verseny (PDF 126–130)
Opportunity × efficiency gondolat: kísérletszám és sikeresség. Négy szezon min/max 3P%-ából középérték és kétpontos szórás, majd Normál-húzás×25. 1000 kör, max kiválasztása, gyakoriságokból fair odds; piac 40/60 súlyozás. A Joe Harris-nyerés anekdota, nem bizonyított modell.

Problémák: a szélsőértékek nem a teljes mintát reprezentálják; a szezonszázalék bizonytalansága nem a 25 dobás varianciája. Normál-húzás lehet <0 vagy >1; szorzása 25-tel folytonos várható találatszámot ad, nem dobásonkénti/binomiális realizációt. A moneyball súlyozást a szerző lábjegyzetben kihagyottnak vallja; versenykörök/tiebreak kezelését sem specifikálja. A max kiválasztása döntetlen esetén sorrendtorzíthat. 14–20% × 7,80 −1 = 9,2–56%, nem egységes ~8% edge. Ezek miatt a példát mechanikus átvétel helyett kritikai szimulációs leckének kezeljük.

### 16. NFL: versengő negatív binomiális (PDF 131–136)
ZSD átlagpontok + ligaszintű scoring-variancia; külön hazai/vendég eloszlás, külső szorzatból score-mátrix. Alsó/felső háromszög a győzelem, főátló a döntetlen; más cellatartományok spread/total lehetőségeket adnak.

A képen μ=20,56, variance=112,99; B=(v−μ)/μ≈4,4966, r=μ/B≈4,572. A választott parametrizációban P(k)=Γ(k+r)/(Γ(r)k!)*B^k/(1+B)^(r+k). Csak v>μ mellett ilyen negatív binomiális; a mean/variance-feltétel szükséges, nem elegendő illeszkedési bizonyíték. A képlet sportbeli kulcsszámokat és pontszerzési eseményeket nem modellez. A margók szorzata függetlenséget feltételez, a csonkolt mátrix tömegét és a végső/regular-time elszámolást külön ellenőrizni kell. A nullához közeli vagy lehetetlen sporteredményekre kiosztott tömeg strukturális hiba lehet; a mindent felvevő eloszlás nem automatikusan jobb.

### 17. NHL: bootstrap (PDF 137–140)
Kessel tízmeccses [0,0,3,1,0,0,0,2,1,0] góllistája, 20×499 visszatevéses húzás. Soronként átlag/medián, azok összesített átlaga, majd λ≈0,7 Poisson over0,5. Az átlag eredetileg is 0,7; a 0,68–0,72 ingadozás Monte Carlo-hiba, nem bizonyíték precízebb játékoserőre. A 9980 újrahúzás nem 9980 független megfigyelt meccs. A 20 elemű resample más átlagszórást ad, mint az eredeti 10 elemű mintaméret. Nem oldja meg automatikusan a kis mintát, a skew-t, az ellenfél/TOI/lineup hiányát. A bootstrap eloszlásából kapott becslési bizonytalanság és egy új meccs prediktív eloszlása külön dolog.

### 18–19. MLB és NCAAB (PDF 141–146)
MLB-nél nincs új kidolgozott modell, más fejezetek átvitelét javasolja; lábjegyzetben külön támadás/kezdődobó/bullpen fontossága. A teljes meccs várható runját 9-cel osztani nem elég első inning becsléshez: kezdődobó, ütősorrend, nem azonos inningek és varianciaátvitel hiányzik.

NCAAB live under-ötlet: késői nagy pontkülönbségnél motiváció/kezdősor csökkenése és over felé torzított vonal. Ez a szerző 2019 körüli személyes tapasztalata, néhány nap/profitadat; nem mai piaci tény. Óra, lineup, faultolás, timeout és végrehajtási késés nélkül egyszerű pace-projekció sem megbízható. A felhasználó cél-ligái közt NCAAB nincs.

### 20. EPL (PDF 147–158)
ZSD-pontbecslésből két Poisson, 0–15 gól score-mátrix, három kimenet. A „zero-inflated” módosítás valójában ligaszintű empirikus margók külső szorzata / ligaszintű Poisson-mátrix elemenkénti hányadosa; ezzel szorozza az adott meccs mátrixát. Ez nem szokásos zero-inflated Poisson keverékmodell és nem valódi közös score-frekvencia: a margók szorzata a gólok függőségét nem méri. A könyv maga elismeri lábjegyzetben a függetlenség vitathatóságát.

A 155. oldali táblázatban a nevező 260 meccs, nem „összes gól” (ahogy a próza mondja). Home0=65/260, Away0=74/260, szorzatuk 0,0711538; a 156. oldali 0,71154 hibás tizedeshelyű. Ugyanott a 0,57872 is gyanús tizedeshely; a hányados változatlan maradhat, de a feltüntetett valószínűségek nem jók.

A nulla empirikus tail gyakoriságokat a korrekció valódi nulla valószínűséggé teszi (PDF 157-en 7+ hazai gólsorok mind nullák). Továbbá új matchup μ értékek mellett a korrigált mátrix tömege nem garantáltan 1; explicit újranormalizálás nincs a leírt lépésekben. A szokásos ZIP-keverék pozitív inflációnál túl-, nem aluldiszperz: Var=E+π(1−π)λ²; a könyv underdispersion-indoklása ezért fogalmilag hibás. Végül a 10% saját/90% piaci ensemble e projektben nem saját oddsfüggetlen alapmodell.

### 21–22. MMA, források, zárás (PDF 159–168)
MMA eredménytípusból 1–4 ordinális pszeudo-MOV ötlet; a szerző maga tapasztalat nélküli brainstormingnak nevezi. A számok távolságának empirikus jelentése nincs igazolva. Ajánlott könyvek, statisztikai szerzők és sportadatoldalak listája külön későbbi online ellenőrzéshez. WNBA-ra nincs külön fejezet; NHL-re a példa prop, nem teljes csapatmodell.

## Képként ellenőrzött alapképlet-hibák
- PDF 24: R²-ként 1−explained/total szerepel. Helyesen 1−SSE/SST; explained/SST megfelelő feltételekkel önmagában R². Excel RSQ a korreláció négyzete, általános előrejelzésre nem azonos a prognosztikus 1−SSE/SST-vel.
- PDF 26: log loss belső képletében (1+Result) áll (1−Result) helyett. Képen is így van, nem OCR-hiba. Helyesen −mean[y ln p+(1−y)ln(1−p)].
- PDF 22 képen a négyzet ^2 helyes, az OCR *2-ként adta vissza. Nem könyvbeli szorzáshiba.
- PDF 23: a szezonvégi variancia „skill”-aránya nem azonos a meccsenkénti klasszifikáció elérhető találati plafonjával, és maradéka nem konfidenciaintervallum. A 86/79/62/62 százalékos plafonok ezért nem használhatók teljesítménycélnak. 0,25/n csak homogén 50–50 és független Bernoulli kiinduló nullközelítés; eltérő erősség/program és közös meccsek miatt a valódi varianciafelbontás összetettebb.
- PDF 66: EV=pd−1 és Kelly f=(pd−1)/(d−1) eltérő mértékegységű. A 10,21% Kelly nem „agresszívebb várható előny”, hanem elméleti bankrollarány adott feltevésekkel.

## Mit tartunk meg, mi vár igazolásra?
Hasznos: alacsony adatkövetelményű ratingcsaládok összehasonlítása; támadás/védelem szétválasztás; eredményeloszlás és ár-érték döntés külön lépése; időrendben visszatartott adat; többlépcsős modellhibák tudatosítása. Nem átvett állítás: konkrét piaci nyereség, garantált ensemble-fölény, történeti hardcode-súlyok, „hatékonységi százalék” regresszióból, illetve a demonstrációk hibás képletei.

Későbbi ellenőrzés: eredeti Benter-tanulmány, a szerző mellékleteinek fórmulái és adatszeletei, az idézett predikciós plafon alapforrásai, proper scoring rule és sportági eloszlásilleszkedés. A fenti javítások matematikai forráskritikai jegyzetek, nem elkészült engine-terv.
