# B03 — Analyzing Baseball Data with R, 3. kiadás — teljes rendelkezésre álló könyv feldolgozva

Jim Albert, Benjamin S. Baumer, Max Marchi. A fájlnévvel ellentétben az impresszum 2025-ös harmadik kiadást jelöl; előszó2023december, reprodukciós környezet R4.3.2. Tényleges szövegolvasás: 776870/776870 karakter, mind a 418 PDF-oldal, 15 fejezet, 3 függelék, gyakorlatok, bibliográfia és index. Vizuális ellenőrzés: 88 oldal 22 kontaktlapon, minden FIGURE-feliratú oldal és kiválasztott képletes oldalak. A példakódok teljes R-környezetbeli futtatása nem történt meg.

## Forrás és adatok (előszó, 1. fejezet)

A könyv reprodukálható baseball-adatelemzési tankönyv, nem igazolt fogadási rendszer.15fejezet,3függelék; új témák nagyadat,Quarto,Shiny. A könyv szerzői kód- és adatforrása abdwr3edata, onlinekönyv beanumber.github.io/abdwr3e; a nagy Retrosheet/Statcast adatok külön letöltendők. A hivatkozásokat később ellenőrzöm; nem tekintem a régi csomagverziókat aktuális API-szerződésnek.

Adatgranularitás: Lahman szezonos People/Batting/Pitching/Fielding/Teams; Retrosheet game-log161mező és külön event/PBP; történeti PITCHf/x; Statcast. Az időbeli lefedettség és részletesség kompromisszum. People.playerID/retroID/bbrefID külön azonosítók; név nem megbízható join-kulcs. Batting szezonon belül több stint, Fielding több pozíciós sor: összegzés előtt azonosítani kell az egységet, a nyers join sokszorozhat. NA nem nulla; korai IBB és egyéb hiányok mérési hiányok. IPouts harmadinningsre utal, nem tizedes inningsjelölés.

A csapat G és W+L nem szükségképpen egyenlő;1927NYY155game110+44döntés. TeamID/franchise/park-kód külön. Az év végi összesítés, finalGame és utólagos hároméves parkfactor nem automatikusan pregame hozzáférhető input. A történeti adat frissített változata és az adott napon ismert állapot szétválasztandó.

Retrosheet: game_id+inning+bat_home_id fél-inning, batter/pitcher/defense IDs, pitchsequence és eventkód; game-log és event nem felcserélhető. PITCHf/x három pozíció+három sebesség+három gyorsulásparaméterből pálya, start/end speed, px/pz, strikezone, derived break; az ember által mért zónahatár pontatlanabb. Időbélyeg és mérési definíció nélkül pitchf/x és Statcast nem összevonható automatikusan. Statcastból csak a nyilvánosan elérhető rész tekinthető forrásnak; a csapatok teljes mozgásadata nem feltétlenül publikus. Launch speed/angle/spray/distance, védekező elhelyezkedés sportadat-alapú változók, de az aktuális labda elütési eredménye csak utólag ismert.

Forráskritikai pontok:
- PDF36szöveg Cal Ripken rekorddátumát szeptember9-nek mondja, a következő táblázat19950906éscaptionszept6. Belső ellentmondás.
- PDF41FlipPlay leírásában az A's vezet1–0, de táblázat away_score1/home0ésNYAaway: a Yankees vezet. BAT_FLD_CD/BAT_LINEUP_ID a batterLongra vonatkozik, nem Giambira; BAT_ID_ID változónév is elírás a BAT_HOME_ID helyett.
- Statcast-példa szöveg plate_y-t említ, a tábla helyesenplate_z-t mutat.
- Shiftes0.231vsstandard0.281 nyers BA különbség nem oksági hatás: különböző ütők, helyzetek, dobók miatt confounding. A régi DH-, shift-, aktuális rekord és csapatnév állítások történeti kontextusúak, mai szabályt később hivatalosan ellenőrizni kell.
- A countalapú BA kérdéseknél különbség van „azon a counton véget ért” és „azon a counton valaha áthaladt” plate appearance között; a0.253/0.479érték definícióját kód nélkül nem tekintem biztosnak.
- Nyers rangefactor=(PO+A)/G változás nem bizonyítja önmagában a fielder képességváltozását; playingtime, lehetőség, park és dobóstáb is számít.
- Az1.fejezet összes gyakorlata elolvasva: választandó granuláris forrás, GibsonIPouts/WHIP, doubleheaderattendance0, tripleplaykód, knuckleballpélda. Két dobás összehasonlítása nem bizonyít teljes eloszlási tulajdonságot.

## 2. fejezet: R és adatkezelés

Tidy egység/mező, öt dplyr művelet, natív |> és ggplot+, tibble, vector/logical/factor/list, cumsum, group_by/summarize, explicitjoin, functions/scripts, read/writeCSV, split-map-bind. A kódot és a gyakorlatokat végigolvastam; nem futtattam az összes R-példát. Mean(seasonrate) és sum(numerator)/sum(denominator) eltér: Spahn57.7vs57.3. Modellhez az exposuret és megfelelő denominatort kell megőrizni; SO/AB itt könyvi definíció, nem automatikusan SO/PA.

A könyv FIP=(13HR+3BB−2SO)/IP alakja leegyszerűsített, HBP és liga/évkonstans nélkül; nem az ERA-skálájú teljes FIP. Emiatt a bemutatott0.3–1értékek nem olyan rosszul formázott ERA-k, hanem más definíció. A szezonátlagok egyszerű átlagolása eltér a teljesinningsszel súlyozott rátától. Az ERA–FIP különbségből védelem/pech csak lehetséges magyarázat, nem azonosított ok.

NA eldobás sum(na.rm=TRUE)-val torzíthat, ha numerator/denominator nem ugyanazokat az ismert megfigyeléseket fedi. Egy teljesen hiányzó csoportot nem szabad ismertnullaként kezelni. Top1slice döntetleneket elhagyhat; nyers listák sorrendjére épülő cumsum helyett explicit időrendezés. Teljeskarrier5000ABszűrés túlélési szelekció, nem pregame predikciós minta. A könyv néhol változóneveket kever és régi feliratot hagy: WS2008–17szöveghez2003–12caption,2013hétgame a kézzel bevitt táblában, gyakorlat„kilenc”futólopó de13sor; ezeket nem kell modelladatként átvenni.

## 3. fejezet: grafika

Bar/dotplot kategóriákra; jitter/histogram numericra; scatter+smoother két változóra; boxplot és stripchart csoportokra. Réteges feliratok, outlierlabel, OPS=OBP+SLG izovonalak, export és faktorok sorrendje. OPS és OBP több ábracaptionben keveredik; aes(OBP,SLG) a tényleges koordináta. Az alternatív ops_labels kódban a0.02függőleges eltolás a felirat értékébe is bekerül, így a referenciavonal helyett a labelpozíció összegét írhatja. Jitter tengelyen elmozdított pont nem új adat; csoporteloszlás és puszta átlag külön.

Életkor a szezon június30-i kor szerint: year−(birthYear+1,ha birthMonth>=7). Szezonösszegzés és explicit sorbarendezés kell cumsum előtt, stintek kezelendők. A HR-race event_cd23; date a game_id4:11karakteréből, battingeventflag megkülönbözteti a PA-t más eseménytől. Date-only rendezés doubleheaderen/belső playsequence-en kevés, élő modellezésnél game/event időrend is kell. A gyakorlati kódban mac_data vs mac.data és nrow(.) régi magrittrmaradványok; nem változtatás nélkül futtatható minták.

## 4. fejezet: runs→wins

2001–2022csapatszezonokon RD=R−RAésWpct=W/(W+L). OLS0.499985+0.000624RD, in-sampleRMSE0.0280. Ez év végi runokkal év végi eredményt magyaráz; önmagában nincs pregame teszt. A rövid2020szezont a nyers egészszezonosRD-nél külön kell kezelni, vagy per-game/exposuremodell. Linear predictor[0,1]tartományon kívülre mehet. A tízrun≈egywin162gamesésközepesrunenvironment lokális közelítés.

**Fontos statisztikai hiba PDF119–120:** az OLSreziduumok átlaga intercepttel a tanítómintán nulla, de ebből nem következik az alá-/fölébecslés azonos valószínűsége vagy általános out-of-sample torzítatlanság. Nulla átlag≠nulla medián; interceptnélkül sem általános a nullareziduumösszeg. RMSEnormálközelítéshez eloszlási és varianciafeltételek kellenek. 0.028×162=4.536win,2RMSE9.072, nem pontosan a lábjegyzet4/8win. A72.1/95.3% az adott illesztési minta aránya.

Pythagorean p=R^k/(R^k+RA^k). k2RMSE0.0265; log(W/L)~0+log(R/RA) illesztettk1.83. Ez log-oddsnégyzetveszteséget minimalizál, nem közvetlenül WpctRMSE-t vagy meccslogloss-t; a „legjobb” függ a lossfüggvénytől és mintától. R=RA=0nemdefiniált, W/Lnulláknál logprobléma, extrém predikcióknál korlátos p még nem bizonyít valósághűséget. Más sportban más exponent, nem egy univerzális állandó.

RedSox2011expected95actual90; one-runwinsésreziduumösszefüggés leíró. Az utólag alacsonyERA/nagyGF szerint kiválasztottcloserek0.88wineltérése nem oksági és nem exante előrejelzési bizonyíték, többcloserjoin csapatduplikációt is okozhat. A one-runwin nyersszám nem azonos a closegame sikeraránnyal. Regresszióazátlaghoz nem jelenti a korábbi veszteségek kötelező kiegyenlítődését.

Derivált k2esetén dW/dR=2GRRA²/(R²+RA²)², reciproka IR/W=(R²+RA²)²/(2GRRA²). Per-game r,a mellett IR/W=(r²+a²)²/(2ra²). r=a5→10run/win,r=a3→6,r=a6→12; a lokális érték pontkörnyezettől és csapaterősségtől függ. Általánosk mellett dW/dR=GkR^(k−1)RA^k/(R^k+RA^k)². A fejezet összes gyakorlata elolvasva: korszakillesztés,19század,managerresidualésmás sport; ezek önálló becsléshez még nem végrehajtott kutatások.

## 5. fejezet eleje, PDF132–135

24base-outállapot=2³×3. Runexpectancy a fél-inninghátralévő runjainak átlaga; nem winprobability. Időrendben runs_before=home+away, fél-inningazonosító game+inning+bat_home, runscored a destination>3mezőkből, maxruns=kezdeti score+fél-inningrun; runs_roi=maxruns−runs_before. Ez célértékképzésnél helyes, de maxruns és futureinningoutcome nem predictor. A state bits sorrendje first/second/third és outs. Empty runner string vs NA forrássémafüggő. A kód new_state számítása közben tart az olvasás.

## 5. fejezet teljes folytatása: RE24

A stateváltozás vagy runs_scored>0 szűrés kizárja a semmit nem változtató rekordokat, de megtartja az üresbázisos HR-t. Csak3outos teljes fél-inningből számol RE-t; a könyv elismeri a walkofffél-inningek kizárásának szelekciós torzítását. RE2016üres0out0.498,1out0.268,2out0.106;loaded2out0.695. A runvalue=REafter−REbefore+runsscored. A thirdoutRE0 helyes, de minden joinhiány vak0-ra cserélése elfedné a sémát/hiányzó állapotot. Csak bizonyított terminális3outstatenél nulla.

Altuve2016RE24=34.7,717PA;bat_event_fl fontos a nonbattingplays kizárásához. Opportunity=sum(REstart)ésRE24 együtt értelmezhető, de exposurefüggő, nem tisztán skill. A loaded8PA részhalmazból clutchképességet nem lehet megbízhatóan becsülni. A PA>=400nem biztos pitcherfilter a kétirányú játékosok korszakában. „32outcomes” az Altuveleírásban téves a24prePAstate mellett.

PDF146positions kód group_by(bat_id,bat_lineup_id)→summarize→arrange→mutate(first) helyesen kijelölhet csoporton belüli leggyakoribb positiont, de **nem csökkenti egy sorra a battert**. A későbbi inner_join(bat_id) így több sorra sokszorozhatja run_exp_400-at. Szükséges egy explicitone-row-per-batter selection és tiepolicy; nonbattingeventekből számolt lineupfrekvencia PA helyett torzíthat.

HRmean1.38,solo1,loaded2outgrand slam4+0.106−0.695≈3.411. Singleérték a baserunningtől/error-től függ, lehet2.68vagy−0.621; batternek tulajdonított teljesrunváltozás nem tiszta ütőképesség. HR/single mintafrekvenciák és1/2/3/4SLGweights nem egyazonfogalmak.

SB2213/2926=.756;first1out498/708=.703,meanrunvalue.00723. A cél nem pusztán sikerarány: a többbaseelőrelépés, dobáshiba és más endstate is számít. A kétállapotú közelítés break-even p=(REbefore−REfailure)/(REsuccess−REfailure), de a könyvi valós mintában több végállapot van. „Megéri” csak az adott kiválasztott2016attempt-mintára leíró következtetés; nem minden játékos/helyzet politikája, nem oksági becslés. Régi szezonstateértékek nem mai konstansok. A fejezet minden gyakorlatát olvastam, köztük legalább1run valószínűségmátrixát: E[runs]≠P(runs>=1).

## 6. fejezet teljes: count, swing, pitchmix, umpire

Itt helyesen megkülönbözteti az ended-on és passed-through countokat. tOPS+ indexet nem szabad nyersOPSszázalékos változásként automatikusan értelmezni;118nemfeltétlen18%OPSjavulás. Retrosheet pseq tisztítás [.>123N+*],1–3pickoff,nopitch és marker nem pitch. ^[BIPV]elsőball,^[CFKLMOQRST]elsőstrike; Uunknown, ismétlődőfoul, eseményrekordok közötti megismételtsequence külön kezelendő. Nyers cleanedlength sem garantáltan mértvalóspitchcount minden történeti/automatikuscountesetben.

2016c01−.0394vs c10+.0371→.0765runvaluekülönbség leíró. Passedthroughindikátorok longformában12sor/PA; c00≈0normál. A chapterpbp_counts190713sor mindenplayt tartalmazhat, és nem látszik explicitbat_event_flszűrés: a PA-szintűnek nevezett elemzésnél a nonbattingevent és ismételtsequence súlyát auditálni kell. starts_with(c)széles, séma-változás újmezőt is felvehet. Nem állítom, hogy a bemutatott számok függetlenül reprodukálva vannak.

2–2höz0–2felőlérkezők−.0311,2–0felől−.0422; a szerző helyesen hangsúlyozza túlélési/szelekciós torzítást. Az aggregáltcountmodell nem feltétlenül memoryless mechanizmus: batter,pitcher és korábbi dobások információt hordozhatnak. A counton feltételhez kötött megfigyelés nem randomizált taktika.

LOESS swung~px+pz és called_strike~px+pz szemléltető felület; binárisnál0alá/1fölé mehet. Az invalidpredikciók kirajzolásból kiszűrése nem javítja a modell valószínűségi érvényességét. Sparse2–0panel gyenge bizonyíték, random500/3000sample reprodukálhatósághoz seedésmegfelelőcsoportméret. Verlander2009–12FF44.1%, leftiesCH22.8%vsright8.17%, slider2.01%vs16.9%; matchuphandéscount fontosság, nem mai pitcherparaméter. Count és pitchmix jointfeltétel kell. Umpire50%kontúrok0–2szűkebb,3–0tágabb történeti minta; eltérőbatters/umpires/pitches confounding. A képhez near.5szűrtadat és több.1szint nem pontos explicitbreaks=.5. A2016CyYoung/MVPVerlander szövegtörténeti állítást nem tekintem ellenőrzöttnek. Minden gyakorlat olvasva, pitchRV, PAlength,pickoff, handedness/pitchtypezone.

## 7. fejezet teljes: catcher framing

Takenpitchkiválasztás:description balls/blockedball/pitchout/HBP, swings,fouls,calledstrike; ismeretlen/outcomeNA és koordinátahiány kezelendő. HBPmintball bevonása nem azonos borderline umpirecallcélpopulációval. 2022bőlrandom50k; történeti, új szabályrendszernél más hatásmechanizmus lehet. GAMlogitP(calledstrike)=smooth(x,z), majd handednessfixeffects; a közös smooth+additivehand nem teljes hand-specifikus térbeliinterakció. A négy feltételespredikció2százalékpontos SD-je nem paraméterbizonytalanság.

**Geometriai eltérés PDF190:** a szöveg a labda bármely részének lemezfölé érésére hivatkozik,9inchescircumference mellett a középpont vízszintes határa (17/2+9/(2π))/12≈0.8277ft. A kód(17+2*(9/π))/24≈0.9471ft a labda sugarát kétszer akkora növelésként használja. A könyv téglalapja ezért nem az indoklásából következő szabályoscenterhatár. Valódi háromdimenziós platecrossing, mért koordináta és empirikus umpirezóna külön kérdés; a vertikális1.5–3.6 is fixszemléltetés, nem személyre szabott zóna.

Framingkétlépcső: GAMstrike_prob, majd logitp=β0+β1*strike_prob+catcherrandomintercept. Második GLMMbatteréspitcherrandomintercepttel. A könyv **valószínűséget**, nem logitGAMoffsetet használ; ezt nem szabad észrevétlenül azonosnak nevezni. A generatedpredictor bizonytalanságát nem viszi tovább. AzonosmintánGAMésGLMM leíró illesztés, out-of-fold/prequential kell előrejelzés értékeléshez. Koordinátahiányfilter után predict(newdata nélkül) sorillesztése szigorúan ellenőrzendő.

Első catcherlista és bővített lista nagyot változik; a könyv jogosan mondja nem teljeselemzés, umpirehiány stb. Van további **azonosítóbuggyanú**: első transmuteid=as.numeric(levels(grp)), másodiknál helyes as.numeric(as.character(grp)). A levels rendezése és a ranef rowsorrend eltérhet, így az első rangsor nevei rosszul párosulhatnak. Csak a kód alapján gyanú, Rreprodukció nélkül nem minősítem bizonyított adatcserének. SDcatcher.209,pitcher.267,batter.251 logodds-skálán; nem a teljes strikeoutcomevariancia vagy oksági fontossági sorrend. Randomhatások shrinkage-je kis mintában hasznos, de uncertaintyésopponentadjustment szükséges.

## 8. fejezet teljes: karriergörbék

OPS=OBP+SLG;OBP=(H+BB+HBP)/(AB+BB+HBP+SF),SLGtotalbases/AB. SF/HBPNA0-ra tétele szemléltető kompromisszum, hiány≠valódi0, cross-era összehasonlítás torzulhat. MantleOPS=A+B(age−30)+C(age−30)²;peak30−B/(2C),maxA−B²/(4C), csak C<0eseténmaximum. C≈0instabilpeak,C>0minimum; intervallumon kívülivertex nem megfigyeltcsúcs. KönyvMantle27.04,1.08,curvature−.003869,R².602; nem szezonelőrejelzési skillmérték. Quadraticszimmetria, fejenkéntunweightedOLS, kevésPAévek magaszaj és sérülés/korszak/csapat/park különhatás mind korlát.

BillJames similarityscore1000minusflooredscaledcareerstatdifferenceséspositiongap. Skálák kézzel beállítottak, nem tanult valószínűség; a teljeskarrierhasonlóság pregame használatnál futureleakage. C240,SS168,2B132,3B84,OF48,1B12; LF/CF/RF címkék default0helyett rendezendők. Multi-stint előbb számláló/denominatorösszegzés helyes.2000ABtúlélésiszűrés, finalGamecutoff és teljeskarrierfőpozíció utólagosak. Játékosnév splitspace hibás lehet többrészűneveknél; idpreferred.

A27→28historikuspeaktrend és longcareerhez30körülpeak leíró, selection/exposure/era által torzítható. Aylim20–40 **eltávolíthat pontokat a smootherből is**, így nem pusztán ránagyítás; rosszvertexek különvalidációt igényelnek. Uniformcurve és ageparabola nem elég sérült/visszatérőjátékos projekcióhoz. Minden gyakorlat teljesolvasva; sokhasonlóhittervizsgálat újadat nélkül nem újvalidáció.

## 9. fejezet csaknem teljes (PDF227–256részben)

25states,24tranziens+3outabszorbeáló, nonnegativeProwsum1. Markovfeltétel a választottállapot elégségessége, a time-homogeneous/azonoscsapatparaméter külön feltételezés. A könyv sajátmaga elismeri az átlagcsapat/átlagpitcher korlátot. Completeinning és battingeventszűrés; P=T/rowsumsT. sample(prob=Trow)R-ben normalizálja a weights-et, ezért a T_matrix átadása a szimulációba itt nem önmagában hiba. A25abszorbeálósort a whilekorlát miatt nem éri el mintindexet.

Reward nemterminálisállapotnál runners_before+outs_before+1−runners_after−outs_after. Az összes3outátmenet rewardját0-ra veszi: egyszerűsítés, **nonforceharmadikoutelőttpontotérőrun is lehetséges**, ezért a valós runscored eventlabel szükséges a teljesmodellhez. Nonbattingplayelhagyás, állapotaggregation és MonteCarlozaj miatt a szimulált vs empirikusREeltérés nem tisztán a nonbattingplayoksági hozzájárulása. MonteCarlo10000mean.477, P>=5 .0098szemléltetés; minta/paraméterbizonytalanság nagyobb mint a csak RNGhiba.

P³háromlépésállapoteloszlás; Qtranzienssubmatrix, N=(I−Q)^−1várhatóállapotlátogatás, N1várhatóPA, üreskezdet≈4.27. Ennek feltétele a majdnem biztosabszorpció/invertibilitás. A kód kerekítettNelsősorösszegét adja egyik helyen, előbbösszeg-majdkerekítés pontosabb.

**Fontos simítási kódhiba PDF241:** képlet p_est=n/(n+K)p_team+K/(n+K)p_all, ahol n az adottkezdőállapot teljescsapatmintája. K=1274csak e példa, nem univerzális. A kód n.x az egyesvégállapotok cellaszáma, így cellánkéntmásweight és sum(p_est)nemgarantált1. Az inner_join az éppnemmegfigyeltvégállapotokat eldobja, miközben a szöveg pont a0countos átmenetek javítását ígéri. Előbb engedélyezett teljesállapothalmazra reindexelés/0count, közösrowN, Dirichlet/pooling, strukturálislehetetlenátmenetek külön; K-t tanítómintából kell illeszteni.

Bradley–Terry pA=exp(TA)/(exp(TA)+exp(TB))=logistic(TA−TB), Jameslog5 T=logitP. T=.2vsnullaellenfél→.5498,nem automatikusan.55azegészütemezésen. 1968példa20teams,2×10leagues,1620games,18ellenfelenként. sT=.20normáltalentet generál, **nem valóscsapatokra tanított képességeket**. Homenév ellenére explicit homeadvantage nincs. Scheduleactualcalendar/order,pitcher,rest,lineup nincs. Groupedteamsadat10talent/grouppéldája csak e ligaszerkezetre érvényes.

1620függetlenfeltételesBernoulli, seasonwins, tie-breakuniformnoise, ligaelsőkből7iidgameWorldSeries. 7előreszimuláltgyőzelemből>3bajnok azonos a best-of-sevenbajnokeloszlással csakállandófüggetlenp mellett, nem ad valósserieshosszt/rotációt. Évente új talents:1000seasonból20kcsapatsor,20kfüggetlen megfigyelésként nem értékelhető; yeargroup és párkorreláció megmarad. Talentéswins szélesség mutatja eredményzajt;35%WScsúcscsapatpélda modellspecifikus, nem aktuális forecast. PDF250ALCSemlítés ellentmond az1968modellnek, amelyben csakligaelsőésWSvan. Nulla győzelmescsapatot group_by(winner)eldobná, általánosabbkódnál lefedést ellenőrizni kell.

A9.fejezet gyakorlatok1–4és5eleje olvasva; a befejezés még következik. Egyszerűoutchainp.7mellett átlag3/.7=4.2857PA, exactly4PA=3*.7³*.3=.3087; ezek analitikusan ellenőrizhető szimulációs alapok, nem fogadásiprofit.

## 9. fejezet lezárása és 10. fejezet teljes: streakiness

A9.fejezet5–6gyakorlata is elolvasva; BTtalent.4vs.25p=logistic(.15),egyenlőtalentfairseries. Simulationmodellfeltételei nem empirikusprofitbizonyíték.

Gamehittingstreak vs egymásutániABhitstreak külön. RLEhosszak, hit/nohit, DiMaggio56, Ichiro18outgap; könyv12gapkétszer mondja, a táblában egyszer. Hittingstreakhivatalosszabályok és egyszerűH>0mindenmeccsre nem feltétlenül egyezik0AB/walk/sacflynapoknál. Dátum+doubleheader+eventrendezés, hiányzóPAszakaszok kezelés szükséges.

**Középre igazított rollingaverage** a könyvben múlt és jövő játékát vegyíti a grafikon egy időpontjában: leíróábrához jó, pregame featurehöz kizárólag előzőbefejezettadat/rightaligned+lag kell. Ablakhossz utólagoskipróbálása többszöröshipotézis; validáció nélkül nincs bizonyított hot-handjel.

Clumpiness=sum(out-runlength²), a leading/trailinggapet is beszámítja, bár szövegben„betweenhits”. Conditionalpermutation rögzítettH/ABszám mellett egyenletes sorrend nullmodell; csak exchangeabilityfeltétellel. Eltérőellenfelek, parks, injury, rest, batterusage miatt visszautasítás nem szükségképp endogénmomentum. Ichiro1532a90%kvantilis1518fölött gyengeegyjátékosbizonyíték, nem „90%eséllyel streaky”. Troutbaloldal azt jelzi nincs nagyobbclumpiness, nem teljesindependenciabizonyítás. Sokjátékossokstatisztika multiplicitás és előregisztrált/holdoutmérés kell.

Launchspeed5gamebinSD: 250BIPszűrés, Pollock354BIP,átlag87.9,SD3.44; Conforto/Fowler2017példa. player_nameID és game_date-onlygrouping veszély: aktuálisStatcastplayer_namegyakranpitchernév, a könyvi személyesdataset definícióját külön kell ellenőrizni; doubleheaderösszemosás és0BIPnapok hiánya is módosít„5games”jelentést. KülönbözőBIPszámúbinek egyszerűátlaga nem azösszeslabdaátlaga; SDsamplezajt is mér. A végsőmaradékbinösszevonása változóablakhosszt ad. Szórás önmagában nem serialdependencia vagy előrejelezhetőforma. A teljesfejezetgyakorlatai is olvasva; wOBAgyakorlat régebbitémára hivatkozik, míg főszöveg launchspeed.

## 11. fejezet teljes: adatbázis és parkfactor

DBIbackend, MariaDB/MySQL, lazytbl, dplyr→SQL, selectivecollect, explicittípusok ésseasonappend. A könyvi create/drop/truncate/overwritepéldákat csak elolvastam, nem futtattam a gépen. Az ismételtappend idempotenciát nem biztosít, kelluniquekey/upsert/adatverzió. append=FALSEönmagában nem ugyanaz mintoverwrite=TRUE; a könyvi kód előbbexplicitremove-ol. A .my.cnf továbbra isplaintextcredential, nem titkosítás; bemutatóspahnpassword/GRANTOPTION nem productionbeállítás. A group[abdwr]önmagában nem szükségképp plainmysqlalapcsoport. ParserDate, DoubleHeader és parkIDfontos;attendance0single-admissiondoubleheadernél hiányzó/összevontattendance, nem üresstadion. A letöltöttadat cleanupja előtt auditrawcopyhash kellene élesreprodukcióhoz.

Coorsrunsbootstrapmezők a pontátlag bizonytalanságát, nem egyjövőmeccs prediktív intervallumát mutatják. Home/away teljesmérkőzésruns(pontmindkétcsapat)közti különbség atmoszféra/parkhatással összhangban, de nem izolált oksági humidorhatás.

HRparkfactor=100*(HR/BIPathome)/(HR/BIPaway), a csapatÉSellenfelei battedballjaira. event2,18,19,20,21,22,23; nem összesPA. COL158,LAN71.2történeti1996. A home/awayrate önmagában nem tökéletes liganeutrálisskála: ellenfélprogram, playercomposition, homeadvantage, parkcsere és különbözőinningexposure. A könyv helyesen emeli ki az actualvenue≠home_teamID éshandednesskülönbségeket. Többévesstabilizálás múltadatokra és mai stadiongeometriára kell, nem jövőbe nyúlóparkfactor.

Galarraga252home225awayBIPweightedmeanPF129,47/1.29≈36.4neutralHR. Ez homogénszorzó/exposurefeltevés, nem bizonyított11HRszemélyesokság; nem ugyanaz mintutólagmindenHR1/PFösszeg. A parkparamétert és a sajátütő/pitchermodelt nem szabad kétszer korrigálni. Minden gyakorlat olvasva,Astrodome,fieldturf,2023pitchclockduration. Stadiontörténetiállítások később ellenőrizendők.

## 12. fejezet teljes: Statcast ingesztálás és tárhely

Napi lekérés a könyvi25krowlimit miatt;717945pitches/2430games/5868HR2023sanitycheck hasznos de nem teljeshitelességellenőrzés. A valósseasonmeccsszám nem mindig2430, a könyv2020:898,2021:2429; postponed/abandoned/resumed ésgame_typekülönösen fontos.

**Napi cachehiba:** fileexistság nrow==0esetén is unconditionalreturnNULL, ellentétbenkommenttel. Nemrefreshel részleges/inprogress/stale/corruptedadatot, nem ellenőrizunique(game_pk,at_bat_number,pitch_number), HTTP/schemabug/rate-limit és retryhiány. A statcast_season80:280Julianrange nem teljesjövőbeli/historikusmenetrend; 2019nyitóJapan stb. előbbiesemény kihagyható. A2023példa évnélküli parse_date_time `%j` outputjában2024van; nap274október1a2023évben,nemnovember6. Explicit év/ténylegesschedule kell. Március30→november6regularseasonszöveg összekeveri postseasonidényt is; game_typeRszűrés külön korlát.

RDSxz tömörRállapot, CSVnyíltinterchange, Arrow/Parquetoszlopos/partícionált, DuckDBlocalSQL, MariaDBserver. EgyRhandle504byte **nem teljesprocess/RSSmemória**, enginecacheésnatívmemória létezik. collect(sc_arrow)írás előtt az egészadat RAMbakerül, így ez nemout-of-corecopy. Versions/config/query/datafüggő a speed/disksize.

**PDF317benchmark súlyos módszertani hiba:** `summary_tbl` már a mérés előtt kiszámított eagerösszegzés, `mark(tbl=summary_tbl)` csak készobjektumot kér le22nsalatt; a lazyDBágak `collect()`-je ténylegesqueryt futtat. Ebből nem következik, hogy az inmemorytblgyorsabb aggregációra. A321–322oldali read_bip_datahívások valóbankiszámítanak, összevethetőbbek. check=FALSEa korábbiban kimenetazonosságot nem ellenőriz; queryplan,cold/warmcache,index,schema és exportköltség is hiányzik. „Minden jólbeállítottserverkönnyedénverilocalt” nem általánostétel. DuckDBwholefile vs parquetpartition narratíva figyelmenkívülhagyhat pushdown/zonemapokat; csak mérés dönt.

Az estimated_woba_using_speedangle grafikon xwOBAmezőt használ, elsőlegendBA, szöveg „hitprobability”-t következtet: **xwOBA nem találatvalószínűség**, >1islehet. Utólagbecsült szolgáltatómező évverzió/label/availability külön, nem vakpregameinput. A 2020–23változatlanság ábrán nem statisztikai egyenlőségteszt. Teljesgyakorlatok olvasva.

## 13. fejezet teljes: HRmodell és forráskritika

124199BIP/5868HR4.72%2023; logitP(HR)=s(LA,LS),105mph25°→.772fitted. **Elütés utáni feltételesmodell**, pregameHRprophoz előbbPA/exposure,contacteloszlás,pitchermatchup és ezek bizonytalansága szükséges.100–105mphesetén28°optimumfeltételes; nem globálisswingtanács. A geom_smooth(method=gam)alapértelmezettnemfeltétlenbinomial,így binárisválaszra explicittípus kell. A launchspeedgrid szöveg90–100, kód90–110.

Game-timetemp+parkjoin, DomeNo. SúlyozottlineárisHR_Rate=4.652+.041*(F−70): .041**százalékpont**/°F, nem.041abszolútp. Ábra55–90szűr, modell nemugyanaztszűri; készülőengineben predikciósintervallum/konfounderek. Forecasttempasof vs meccskezdéskoriactualtemp külön. Dome/csukhatótetőhelyzet nem pusztán stadionállandó.

Spray x=2.5(hc_x−125.42), y=2.5(198.27−hc_y), atan(x/y)°; általánosításhoz atan2 éskoordináta-definíció. Pullhandedness ésparkfalak; HR-onlydistanceplot szelekciós, inside-the-parkHRnemoutfieldcarry. Actualdistance nem egyenlő hc-koordinátaeuklidesziprojekció.

13.fejezetparkfactor **HRcount_home/HRcount_away**,nemelőzőBIPexposureratearány; program/innings/missingjoin különbség számít. Tempparkinner_joinután mutáltsc_2023kevesebbsor lehet (1459BIP1.30%-a kb112kdenominator,nem124199), későbbi sc_two_seasonsnyersforrásmáslefedettség. Joincoverage audit szükséges.

CrossedGLMMHR~randompitcher+randombatter,batterSD.523pitcher.188. Ez az adott feltételesBIPlogoddsheterogenitás, nem „a pitchert figyelmenkívüllehet hagyni”; a pitcher K/BB/contactrate csatornáját a BIPszűrés eltakarja, és park/matchup/launchconfounders hiányoznak. Fontosság≠SDsorrend.

2021vs2023binning: LA(20,40],LS(95,110],5-ösbinek,BIPshareazösszesBIPből és P(HR|bin) külön. Cutbalhatár/régebbhiányadat, üresbincompletion, meccs/playercluster és többszörösösszehasonlítás fontos. A könyv1.21HR/game valójában **csapatmeccsre**≈5868/(2*2430), teljesmeccsre≈2.415.

**Logitkritika PDF342–345:** logit nem általános varianciastabilizáló binomiálisaránynál; delta-methodVar(logitphat)≈1/[np(1−p)], széleken nagyobb. Logitdifference−.16nem „a rate0.16-tal kisebb”: oddsratioexp(−.16)≈.8521; absolutechangebaselinefüggő. 0/100ratevégpontokra nincs finite logit, smoothingésuncertainty kell. AdottLA/LSbinbenkevesebbHR összhangbanlehetcsökkentcarryvel, de spray,park,temp,balltracking,binenbelülieloszlás és battercomposition kontroll nélkül nem izolált bizonyíték „deaderball”-ra. A játékosok módosított stratégiájára ugyanez az oksági korlát. Minden gyakorlat olvasva.


## 14–15. fejezet: reprodukálható közlés és interaktív elemzés

Quarto: YAML fejléc, revealjs, Markdown címek/listák/oszlopok, futtatott R-kód és ábra, kable táblázat, LaTeX képletek; Beamer és PPTX alternatív kimenet. A számítás és a közlés közös forrásból reprodukálható. A renderelés önmagában nem garantálja a modell vagy a felirat helyességét. PDF349: a szöveg HR/csapat/meccs átlagot ígér, a kód csak szezononként sum(HR)-t számít, az ábra 0–6000 tartományú összeg, mégis „Avg HR” feliratú. Denominátor nélküli összeg nem ráta. A 2015–2022 táblázat 2020-at kihagyja; összehasonlításnál a rövid szezon kezelését explicitté kell tenni. HR/(AB−SO) könyvi kontaktarány; a sacrifice eseményeket és más PA-végződéseket tekintve nem automatikusan az összes Statcast BIP pontos nevezője.

Shiny: tiszta elemző helperfüggvények, külön ui/server, fluidPage 4/8 oszlop, slider/select/radio vezérlők, observeEvent dinamikus választék, renderPlot és brushing. selectPlayers2 teljes pályafutásközép és 2000 IP szerinti retrospektív kiválasztás, nem pregame aktívjátékos-univerzum. Stabil playerID kell név szerinti egyezés helyett; üres és többes találat, változó inputválaszték kezelendő. A szöveg minPA/midpa néven is hivatkozik az IP-korlátra; a minták minpa bemenetet használnak. compare_plot egyik vázlata kötelező fg ötödik paramétert mutat, más hívás négyargumentumos: a valódi package-függvény ellenőrzendő. A gyakorlat „Home Run” választási szövege és a Home_Run oszlop neve eltér; `.data[[type]]` előtt explicit névleképezés kell. A folytonos kimenethez folytonos színskála szükséges. A karriergörbe nem jövőbeli előrejelzés. Hosting/GitHub futtatás csak bemutatott lehetőség, semmit nem publikáltam.

## A függelék: Retrosheet adatmodell és count parser

Chadwick cwevent bináris és könyvtári útvonalfüggőség, baseballr::retrosheet_data évenként events/rosters listák, RDS cache; a régi parse_retrosheet_pbp már a könyv szerint is leváltott. A könyv környezeti változó példáit nem hajtottam végre. A Linux LD_LIBRARY_PATH példája nem Mac telepítési recept; az üres végső pathkomponens sem szándékolt könyvtár. Az alternatív retrosheet csomag más, listás visszatérési struktúrát ad.

Kulcs game_id (homekód+YYYYMMDD+doubleheaderjel) és event_id. Inning+bat_home_id adja a fél inninget; a kimeneti sor minden play, nem feltétlenül PA. Basecode bitmaszk: 0 üres, 1 első, 2 második, 4 harmadik, összegek kombinációk. A 97 alap+63 bővített mező történeti verzióleírás. BAT_EVENT_FL különíti a PA-végződést az SB/WP stb. eseményektől. Battedball típus/location lefedettsége szezon- és eseménytípus-függő, a hiány nem véletlen. Eventkódok: 2 out,3K,4SB,5DI,6CS,8PO,9WP,10PB,11balk,12advance,13foulerror,14BB,15IBB,16HBP,17interference,18error,19FC,20–23single–HR.

Count parser: eltávolítja a . > 1 2 3 + * N jeleket; ball BIPV és strike CFKLMOQRST halmaz; két strike után FR foulok nem emelik a countot. Ez a múltbeli pitch sequence-nek szánt regex-készlet nem mai szabálymotor. A c00=TRUE még hiányzó/ismeretlen szekvenciára is igaz; az unknown és automatikusan kiosztott count események kezelését ellenőrizni kell. Félbeszakadt PA-k és nem batting event sorok duplikálhatják ugyanazon PA lefedését. A c32 két regex AND kombinációja olvasva; éles alkalmazás előtt explicit állapotgéppel és esemény-fixture-ökkel ellenőrizendő, nem elegendő a könyvből változtatás nélkül átvenni.

## B–C függelék: történeti tracking és változószótár

A régi PITCHf/x Gameday XML/pitchRx megszűnésének bemutatása erős példa a források változékonyságára. A StatsAPI regisztrációjáról, publikusságáról és R-wrapper hiányáról szóló állítás a könyv idejéből származik, jelenlegi hozzáférési feltételként nem fogadom el ellenőrzés nélkül. Brooks/PitchInfo osztályozás eltérhet MLBAM-től. A történeti linkek későbbi elsődleges forrásellenőrzési jelöltek.

Chadwick Register köti össze MLBAM/Retrosheet/Lahman ID-ket; snapshot és verzió szükséges. Statcast táblák: game state, pitch, event, BIP, derived xBA/xwOBA, alignment. A „20,000 frames per second” általános trackingállítás, pfx_x/pfx_z hüvelykes egysége, effective_speed lemezátlépési sebességként definiálása, release_pos_y=50 ft állítás és a szabad szöveges leírások hivatalos szótárral ellenőrizendők. Ezeknél valószínű régi PITCHf/x és új Statcast definíciókeveredés van, ezért nem rögzítem hiteles jelenlegi adatszerződésként. Release koordináta és az initial vx0/vy0/vz0 referenciahelye nem cserélhető fel automatikusan. xwOBA nem találati valószínűség és nem egyszerű OBP. A shift mezők értelmezése szabályváltozásoktól függ.

A PDF390 képletét vizuálisan ellenőriztem: a nyomtatott x,y,z egyenlet gyorsulástagja valóban 0.5*a*t, a négyzet hiányzik; az alatta levő R-kód viszont helyesen 0.5*a*t^2. Ez nem kinyerési hiba. A pályaszámító cross_p kvadratikus gyöke csak kompatibilis koordináták, nemnegatív diszkrimináns, megfelelő pozitív időgyök és nemnulla ay mellett használható; ay→0 lineáris határesetet külön kell kezelni. seq(0,cross_p,interval) nem feltétlenül tartalmazza a lemez pontos elérési idejét.

## Vizuális ellenőrzés és lezárás

88 PDF-oldal ténylegesen megtekintve a B03-sheet01–22 képeken; manifest B03-visual-manifest.json. A grafikonok megerősítették a leírt alakokat: RE24 és lehetőség összefüggése, count- és framingfelületek, karriercsúcsok, szimulációs győzelmi szóródás, permutációs streak-eloszlások, Coors kontroll és intervallumok, launch angle/speed HR-felület, évközi logitkülönbségek. A 2–0 swingfelület instabil alakja látható; nem meggyőző önmagában játékelméleti sajátosságként. A p241 shrinkagekód n.x hibája és p190 zónaszélesség-képlete a renderen is megerősítve. A p349 rosszul feliratozott összeg és p390 hiányzó t-négyzet vizuálisan igazolva.

További p336 belső ellentmondás: a szöveg NYY>1.5 HR parkfactort állít, az ábra NYY pontja körülbelül1.3; TEX kb1.55 és CIN kb1.47. A pontos adatszámítás reprodukció nélkül csak ábra–szöveg eltérésként rögzítve.

A könyv erős adatkezelési és sportfolyamat-modellezési módszertár, de a demonstrált in-sample illeszkedések, szimulált tehetségek és retrospektív karriergörbék nem bizonyítanak fogadási előnyt. A teljes könyv feldolgozva; a könyvben felsorolt külső művek nincsenek ettől elolvasva, a külső dokumentáció aktuális ellenőrzése külön következő munkafázis.
