# B14 — Swain Scheps: Sports Betting For Dummies (2020)

Állapot: teljes rendelkezésre álló szöveg elolvasva, 851882 / 851882 karakter; mind a 36 egység és mind a 46 képfájl megtekintve. Fejezetenkénti kritikai jegyzet és reprodukálható számszaki audit elkészült. A hivatkozott külső weboldalak és tanulmányok ettől még nincsenek ellenőrizve.

## Forrás és hatókör

EPUB, 36 egység, 149030 kinyert szó. A bevezető kifejezetten NFL, NCAA football/basketball, NBA és MLB fogadásra koncentrál; NHL/WNBA-specifikus modellt nem ígér. 2020-as jogi, piaci, szolgáltatói és egészségügyi állítások történeti könyvállítások, jelenlegi érvényességük nincs ellenőrizve. A copyrightoldal gyógyszerekről és orvosi tanácsról szóló sablonos felelősségkizárása szerkesztési anomália.

## Előszó, bevezető és 1. fejezet: cél, készség, zaj

- Norm Hitzges előszava: hibás döntés és peches eredmény különválasztandó. Az állítás, hogy minden balszerencse kiegyenlítődik, nem jelent garanciát egy véges bankroll vagy időhorizont mellett.
- Scheps saját gimnáziumi csapatának meglepetésgyőzelme, majd veresége motivációs/szituációs hipotézis kiindulópontja. Egy visszaemlékezés nem bizonyítja a letdown hatást; az eredmény alapján elmondott történet könnyen utólagos magyarázat.
- Fundamentális (személyzet, séma, körülmény) és adatalapú elemzés kombinálható. A fogadó választhat, mely piacot hagyja ki; a könyvüzemeltetőnek sok piacot kell áraznia. Ez lehetőség, nem automatikus információs előny.
- A nyertesek aránya egy szolgáltatónál vagy éves tippligában nem azonos a tartósan pozitív várható értékkel bíró fogadók arányával. Mintanagyság, oddsok, költség, versenyformátum és túlélési/szelekciós torzítás számít.
- A 52.38%-os nullszaldó kizárólag −110 áron, push nélküli vagy csak eldőlt fogadásokra vonatkozó arány. Nem univerzális cél minden piacra. A 55%-nál felmondhatsz típusú mondat nem következik önmagában a találati arányból: tét, volumen, költség, bizonytalanság is szükséges.
- A sharp/square elnevezés önbesorolásként gyenge bizonyíték; tényleges, teljes eredménynapló kell. Bankrollnál pénzösszeg mellett feltöltési időhorizontot is rögzít a szerző. A kis tét sem helyettesíti a teljes kitettség számbavételét.

### Ellenőrzési megjegyzések az 1. fejezethez

- A könyv a 4.5% körüli holdból 96 cent nyereséget vezet le 1 dollár tétre. Ez hibás összekapcsolás: −110 mellett nyereség = 100/110 = 0.90909 dollár; 50%-os nyerési esély mellett átlagos veszteség = 0.0454545 dollár. A 0.96 nyereségű érmejáték holdja csak 2%, nullszaldója 1/1.96 ≈ 51.0204%. A következő fejezet helyesen számol 9.09 dollár nyereséget 10 dollárra, majd ismét tévesen 96 centes érmeanalógiát használ.
- 1-2. táblázat: 181−1.1×131=36.9; 164−1.1×148=1.2; 156−1.1×156=−15.6. Itt az egység a megnyerhető 1 dollár, a kockáztatott összeg 1.1 dollár. Nem 1 dollárnyi állandó tét profitjai.
- A nulla várható értékű érmejáték sem teszi végtelenül biztonságossá a véges bankrollt; az átlagos hozam konvergenciája és a teljes pénzegyenleg pályája külön fogalom.
- Az 1984–2018 közötti 87 milliárd dollár a táblázat szövege szerint megfogadott összeg (handle), a cím revenue szava nem nyereséget jelent. Időtartama sem 25 év, bár a bevezető így jellemzi.
- A jogtörténeti rész éven belül is 2006/2002 UIGEA megjelölést kever; nem tekinthető mai jogi útmutatónak. A szerző által használt egészségügyi kategóriák és számok önálló, aktuális ellenőrzést igényelnek, itt nem kapnak diagnosztikai státuszt.

## 2. fejezet: fogadási szerződés és elszámolás

- Külön adatok: esemény, fogadási piac, kiválasztott kimenetel, időszak, line, odds, tét, szabályverzió. A kifizetésnél különválasztandó visszajáró tét és nettó profit. A szerző kezdeti kétkimeneteles leegyszerűsítését később push/void kivételekkel egészíti ki.
- A fogadás elfogadásakor rögzített odds/line eltérhet a későbbi ajánlattól. Az új ajánlat nem írja át a meglévő fogadás feltételeit. Az overtime, listed pitcher, statisztikai korrekció, megszakítás és prop definíciója platformfüggő ellenőrzési tétel.
- Amerikai odds: A>0 esetén decimal=1+A/100; A<0 esetén decimal=1+100/abs(A). A kifizetés stake×decimal, nettó nyereség stake×(decimal−1). Az implied p=1/decimal nullszaldós ár, nem bizonyított valódi valószínűség.
- Spread: score_selected−score_other+line; pozitív nyer, negatív veszít, nulla push az egyszerű kétoldalú piacon. A félpont nem pontszerzési lehetőség, hanem elszámolási küszöb. Az oddsot és a spreadet nem szabad összekeverni.
- A total a két csapat pontösszegére szól; korrelálhat a győztessel/spread-del. A standard ±1.5 run/puck line ismertetése nem jelenti, hogy más handicap soha nem létezik. A könyv implicit −110 konvencióját gépi adatkezelésben nem szabad hiányzó ár automatikus kitöltésére használni.
- Parlay: az összes nem érvénytelenített lábnak nyernie kell; függetlenül árazott lábaknál a decimal odds szorzata a kombinált ajánlati ár. A joint valószínűséget csak függetlenség esetén szabad p-k szorzataként kezelni. Push/void kezelés szolgáltatófüggő, nem globális konstans.
- Teaser: a line javul a fogadó javára, az odds romlik. Azonos ponteltolás nem azonos érték különböző sportban, line-nál vagy ponteloszlásnál. Props, futures, részidős és alternatív line piacok eltérő célváltozók.

### Számszaki és fogalmi hibajelöltek a 2. fejezetben

- Három −200 láb: 1.5³=3.375 bruttó kifizetés 1 dollárra, tehát **2.375**, nem a szövegben szereplő 2.27 nettó profit.
- A −110 parlay táblázat fair szava az ajánlott láboddsok összeszorzását jelenti, már viggel. Nem valódi margó nélküli fair odds. Egyes kerekítései pontatlanok: 13/5 = +260, nem +264; a pontos kétlábas szorzat nettó 2.644628… .
- Mantle prop példa: +425 és −300 ugyanazon teljes kétoldalú piac esetén implied összeg = 1/5.25+3/4 ≈ 0.940476 < 1. Ez underround, azaz nem illusztrálja a normál pozitív árrést.
- A jövőbeli bajnok példák fiktívek, a szereplők és évszámok nem mai tények. A több negatív árú kizárólagos bajnoki opció igen nagy árrést is eredményezhet, a példa csak oddsnotációt mutat.
- A két fogadó megállapodott ára nem azonos mindkettő valószínűségi becslésével; eltérő vélekedés vagy hasznosság is a kereskedés oka lehet. A 40%-os példában a feltétel Burrow legalább annyi TD-je, a későbbi szöveg pontatlanul több TD-re rövidít.
- A bookmaker egyenlő pénz mindkét oldalra modellje leegyszerűsítés; eltérő oddsoknál a kiegyenlített kifizetési kötelezettség nem is egyenlő téteket jelent. B15/B19 jóval árnyaltabb információs és kockázati megközelítést ad; a B14 további fejezeteinek olvasása még folyamatban.

## 3. fejezet — fogadási működés

Elolvasva a teljes fejezet: bukmékerpiac története, személyes fogadás helyszínei, oddsboard, rotációs azonosítók, multiway/futures/prop listázás, személyes és online fogadás, magánbookmaker, állami/kanadai lottórendszer. A rotációs szám a könyvben napi, nem örök globális eseményazonosító; több szolgáltatónál hiányozhat. A nyomtatott oddslap időközben elavulhat. Digitális feldolgozásnál időbélyeg és tényleges elfogadott ár szükséges. A szolgáltatói hozzáférés, kifizetés, állami lottó és jogi állapot 2020-as leírása nem jelenlegi jogosultság vagy integrációs specifikáció. A ticket fotó/azonosító az elszámolás bizonyítéka lehet, nyilvánosan közzétéve visszaélési kockázatot jelenthet.

## 4. fejezet: ár, várható érték, bookmaker kitettség

- EV = p_win×nettó_nyereség − p_loss×tét, a push nyeresége nulla. A nyitó szóképlet pluszt ír Amount Risked előtt; a példában a negatív előjelet már helyesen beilleszti. A kockáztatott összeg mint pozitív mező mellett a levonást explicit kell elvégezni.
- Handle = megfogadott összeg; realized hold = (beszedett tétek−bruttó kifizetés)/handle. Nem tiszta vállalati profit, nem azonos az implied valószínűségek összege mínusz eggyel. Mix, eredmények, ügyfélkör és árazás befolyásolja, így pusztán realized holdból nem következik egy szolgáltató árainak minősége.
- A későbbi rész árnyalja a kezdeti 50/50-könyvmodellt: informált fogadók pénzét másképp értékelheti a book, kifizetési és teljes portfóliókitettséget kezel, kereskedési limiteket, időzítést és más szolgáltatónál fedezést is alkalmazhat. Az informált oldal ellen több public pénzt elfogadhat, ha a becsült valószínűség ezt indokolja.
- Market maker korai alacsony limittel nyit, informált fogadásból tanulhat, később emel; retail book követheti. A könyvben megnevezett cégek akkori szerepe nem mai besorolás. B15/B19 külön részletes feldolgozásával összekapcsolandó.
- Opening, current és closing ár eltérő információhalmazra vonatkozik. Steam követésekor az eredeti előny már eltűnhet; mozgásból nem következik automatikusan annak oka. A könyvben szereplő közönségarányok adatforrásának lefedettsége és időbélyege külön kérdés.
- Élő fogadás: adás késése, adat késése és ajánlat frissítése külön késleltetés. Az eseményállapothoz illeszkedő ár kell. Korai és késői előrejelzés eltérő bizonytalanságát szemlélteti a 4-2. ábra; ez sem bizonyítja, hogy minden konkrét új hír csökkenti a feltételes varianciát.
- A 4-1. ábrán Utes −3.5 oldalon nagyobb sharp zsák, Ducks +3.5 oldalon nagyobb public zsák van. Ez szemléltetés, nem empirikus adatsor. A 4-2. ábra függőleges tengelyének Variance felirata laza: a negatív tartományt is tartalmazó sáv inkább lehetséges kimenetek tartománya.

### Ellenőrzött hibák és korlátozások, 4. fejezet

- 110/210 = 52.38095%, nem a korai példában kiírt 52.41%.
- A Colts −3 példa 54% nyerési esélynél −120-at veszteségesnek mondja, de nem számol a pontosan 3 pontos győzelem pushával. Ha p_win=.54, akkor EV=.54×(100/120)−(1−.54−p_push)=p_push−.01. Már 1%-nál nagyobb push-eséllyel pozitív. A könyv konklúziója akkor helyes, ha 54% az eldőlt fogadások feltételes nyerési aránya, ezt azonban nem így definiálja.
- Silver/Gold $1000/$150 példa: standard −110 mellett Gold győzelménél a bruttó kifizetés 286.3636, hold 75.0988%, nem több mint 80%.
- Egyenlő $1100/$1100 handle-nél a nyertesnek 2100 jár, nem a táblázatot bevezető mondat szerinti 2000. A későbbi táblázat már helyes 2100/100 értékeket mutat.
- Skew = abs(A−B)/(A+B). Egyenlő −110 odds mellett a biztos nemnegatív hold határa skew≤1/21≈4.7619%, nem pontosan a 1/22≈4.5455% hold. Utóbbi szigorúbb feltétel szintén elégséges, de nem az egzakt határ. Levezetés: nagyobb oldali handle (1+s)T/2; legrosszabb profit/T=1−(21/11)(1+s)/2.
- A 45/55 informált példa szövegében egyszer Side B felé javasol skew-t, holott a táblázat és a következő mondat helyesen Side A felé több tétet mutat, ha B a valószínűbb nyertes. A 7310/5410 számoknál EV 578.18-ról 759.55-re nő a bookmaker oldalán.
- Egy pontbecslés (27–24) eltérése az adott line-tól még nem bizonyít pozitív EV-t. Teljes előrejelzési eloszlás, push, ár és bizonytalanság szükséges. A példabeli száz fős előrejelzéslistában a csoportok valójában 99 főre összegződnek; az alsó oldalon 34, nem 35 fő van.
- +200 átlagos odds és 35% összesített találati arány csak azonos ár vagy megfelelő együttjárás esetén ad 5% EV-t. E[p×odds] általában nem E[p]×E[odds]; fogadásonként kell számolni.
- A −3.2/−3.25 rövidítés itt amerikai árjelölési szlengre utal; nem univerzális parser-szabály. Más piacokon a negyedpontos line tényleges osztott ázsiai handicap lehet.
- O8.5 és O9.5 között a nyerési esély különbsége P(total=9); nem univerzális 10%. Az odds eltérése is szükséges az EV-összevetéshez.

## 5. fejezet: tét, célfüggvény, bankroll és napló

- Saját sportbecslés → kínált piacokkal összevetés → körülmények mélyebb ellenőrzése → végrehajtható ár → kitettség/tét → napló. A 45–28 kontra −20/70 példa Illinois+20 és over70 iránya számszakilag helyes, de a pontbecslés itt sem helyettesít valószínűséget.
- A várható pénznyereség, lognövekedés, csődesély, célszint elérésének valószínűsége és élmény nem ugyanaz a célfüggvény. A szerző több helyen összekeveri őket. A cash-out értéke sem önmagában a korábbi aznapi veszteség függvénye: állapotfüggő fair érték, díj és teljes kitettség számít.
- Több sport/esemény különböző kimenetele nem garantál független hozamot: közös játékos, csapat, időjárás, szabályrendszer vagy modellhiba összekapcsolhatja őket. Ugyanazon mérkőzés propja/sidese/totalja különösen nem független.
- Fix tét egyszerűen auditálható; az egység definícióját rögzíteni kell. A könyv 1/20, 1/50, 1–2% és to-win/tét alapú egységet is használ. A felhasználói $1–5/bet korlátot tényleges elveszíthető dollártétként kell majd kezelni, nem megnyerhető profitként.
- Az outs előnye jobb végrehajtható ár és kínálat; a költség tranzakció, idő, széttagolt cash és napló. A könyv öt szolgáltatós javaslata nem általános minimumkövetelmény. A veszteséges álcázó fogadások mint költség nem kerülnek automatikusan át a projektbe.
- Naplózott mezők: kiválasztás, sport/piac, ár és line, fogadási idő, állapot, bankroll, likvid szabad pénz, lezárt profit, ROI, kitettség. Függő tét, zárolt bónusz, készpénz, befizetés/kivét és működési költség külön kezelendő. Feltöltés nem modellprofit.
- Napi/heti/havi keretek előzetes rögzítése és rendszeres ellenőrzése értelmes működési gyakorlat. A minimum 20 fogadás a könyvben könyvelési áttekintéshez ajánlott, nem elegendő minta kis statisztikai előny igazolására.
- A futures kezelése kétféleképpen jelenik meg: ROI-ban settlementhónap; pénzügyi trackingben eleve veszteségnek írja és győzelemkor windfallnak. A projektben a cashflow és realizált eredmény külön nézet kell legyen, különben dupla veszteség vagy hamis időbeli teljesítmény keletkezik.

### Kelly-kritika — lényeges helyesbítések

- A helyes cél **E[log(W_következő)]**, nem log(E[payoff]) és nem egyetlen fogadás várható profitjának maximalizálása. Ezek felcserélése alapvető hiba.
- Push nélküli kétkimeneteles fogadásnál f*=(p×d−1)/(d−1), ahol d decimal odds. Zárójelek nélkül a könyv képlete félreérthető. −110 és p=.55 mellett pontos f*=.055, azaz 5.5%; 1.91-re kerekített d-vel .0554945… .
- Állandó p és odds mellett veszteség után a Kelly dollártét **csökken**, mert ugyanazt az arányt a kisebb bankrollra alkalmazza. A könyv larger and larger bets on the way down mondata ellentétes a képlettel.
- Végtelenül nagy induló bankroll nem előfeltétel a logoptimumhoz. Valós minimumtét, becslési hiba, korrelált párhuzamos bets, likviditás és drawdown viszont lényeges. Pozitív EV sem jelent biztos véges idejű nyereséget.
- A külön-külön kiszámított Kelly-frakciók összege nem biztosít érvényes közös portfóliót. A magas nyerési valószínűség önmagában nem jelent jobb fogadást, és nincs univerzális kevés betnek p>50% szabály.
- Fractional Kelly csökkenti a kitettséget, de nem teszi biztosan nyereségessé a hibás p-t. Kapcsolat: B04/B09/B18/B24 részletesebb korlátok és szimuláció.

### Tétkezelési és könyvelési audit

- A negatív EV-nél egyszerre mindent feltenni állítás csak bizonyos célszint-elérési problémákra lehet optimális; nem általános vagyonmegőrzési vagy csődelkerülési szabály. Az a későbbi javaslat, hogy csak tegyünk úgy, mintha kis előnyünk volna és keressünk több fogadást, nem bizonyítja az előnyt.
- $1000 kezdő / $10 állandó tét / $2000 vagy nulla elnyelő határ / even-money független fogadások esetén a pontos duplázási valószínűség 1/[1+((1−p)/p)^100]. p=.45,.49,.495,.505,.51,.55 mellett rendre kb. 0.000000193%, 1.79768%, 11.91959%, 88.08041%, 98.20232%, 99.999999807%. A könyv .495 sorában 14%+76%=90%, tehát belsőleg is hibás; .505 sor 94/6 sem egyezik az egzakt feltételezett folyamattal. Futtatásszám és kód nélkül a különbség nem kezelhető validált szimulációként. A 45%-nál nulla esély szó szerint hamis, csak rendkívül kicsi.
- 5-1. ROI táblázat: vesztes fogadásnál negatív tétet ír a bruttó Result mezőbe, majd még egyszer levonja a tétet. A nyertes sorok bruttó összege 1461, a teljes tét 1000, a helyes nettó profit **461**, yield **46.1%**. A könyv 91/9.1%-a dupla veszteségelszámolásból származik.
- 5-2. profit táblázat: $10, −110 nyertes profitja 9.0909, nem 9.19. A két ilyen sort javítva nettó összeg **12.1818…**, centenként kerekítve 12.18. Még a könyv hibás sorai is 12.38-ra adódnak össze, nem a kiírt 9.38-ra.
- A jobb line egy vereséget pushra javítva 10–9–1 esetén +.1 to-win egységet ad −110-nél; a nyereményarány fogadásszámonként más, mint az eldőlt fogadásokra vetítve. A push-kat külön állapotként kell megőrizni.

## 6. fejezet: valószínűség, minta, hipotézisvizsgálat

- Egyetlen kevéssé valószínű esemény megvalósulása nem cáfolja önmagában az előrejelzést; egy favorit győzelme sem igazolja. A sok előrejelzés kalibrációját és proper scoring rule szerinti hibáját kell vizsgálni. A 2016-os választási és Super Bowl-esélyek itt a könyv történeti példái, nem ellenőrzött eredeti előrejelzések.
- Helyes fő tanulság: leíró adat nem automatikusan prediktív jel; történetek, önkényes szeletelések, időablakok, ellenfélerő és eltérő játszott meccsszám könnyen félrevezetnek. Az itt bemutatott clutch, turnover és yards-per-carry példák hipotézisek, nem minden ilyen mutató haszontalanságának bizonyítékai.
- Független ismétlésnél az előző sorozat nem változtatja meg a következő próbát. Visszatevés nélküli kártyahúzás azonban **nem független**, a könyv általános felsorolása pontatlan. A teljes 11-es red szekvencia előzetes valószínűsége és a következő spin feltételes esélye különbözik.
- A 6-1. grafikon lottószámaiban látott hullámzás nem önmagában prediktív minta. A véletlen nem egyenlő normális eloszlással: a fair érme sikerösszege binomiális, nagyobb n-nél normálközelítés lehetséges; a pont- és gólkülönbség formája külön vizsgálatot kíván.
- Binomiális számolás: E[X]=np; SD(X)=sqrt(np(1−p)); SE(X/n)=sqrt(p(1−p)/n). Szám és arány szórását nem szabad összekeverni.
- LLN csak megfelelő mintavételi/stacionaritási és függési feltételekkel indokol konvergenciát; több torz vagy elavult adat nem feltétlenül jobb. A 95%-os konfidenciaintervallum eljárásának ismételt mintavételi lefedettsége nem utólagos 95%-os posterior valószínűség az adott intervallumról.
- A Gambler's Z itt (w−n/2)/sqrt(n/4). A null 50%, nem a −110 profitküszöb. A 50%-tól kimutatható eltérés nem igazolja a 52.38% feletti valódi találati arányt. A sign eldobása kétoldalú kérdéssé teszi a tesztet; profitirányhoz az előjel is kell.
- Egy p-érték nem a véletlenség vagy a modell igazságának valószínűsége. A p>.05 sem bizonyítja a nullt. Z>3 nem garantál valódi, jövőben is működő mintát, különösen utólag kiválasztott trend, függő fogadások vagy sok próbálkozás mellett. Következő fejezetek adatbányászati kritikájával összekapcsolandó.

### 6. fejezet számszaki ellenőrzések

- (8,5,1,9,2) átlag5; populációs SD=sqrt(10)=3.16228, mintaszórás=sqrt(12.5)=3.53553. A könyv 2.9-e egyik sem. A (4,5,5,6,5) populációs .63246≈.6 helyes. A 18 focigól-megfigyelés átlaga 2.44444, populációs SD1.38332≈1.38 helyes; ebből normál 68/95/99.7%-os lefedettség önmagában nem következik.
- 23/30 = 76.6667%, nem 78%. Z=2.92119. Az egzakt kétoldalú fair-binomiális farok 0.005222879≈0.5223%; nem 100 próbából 2–3 eset. A folytonos normálközelítés más a diszkrét egzakt tesztnél, mindkettő jóval kisebb az állított 2–3%-nál.
- 13/18 példában Z=1.88562 helyes. Az ablak utólagos kiválasztása további torzítás, a teljes 50 flip 25/25 átlaga nem bizonyítja önmagában sem a függetlenséget, sem minden részfolyamat stacionaritását.
- Egy fair érme 100 flipjében pontosan 50 fej valószínűsége kb. 7.96%: modális érték, de nem a többség eredménye. A 68/95/99.7 szabály közelítő és eloszlásfüggő.

## 7. fejezet: fundamentum, matchup, játékosérték

Teljes fejezet elolvasva.

- Előbb előrejelzés, utána ár és piacválasztás; kedvenc csapat kiválasztása után keresett igazolás megerősítési torzítás. Az információ akkor releváns fogadáshoz, ha hat a célváltozóra és a fogadási árhoz viszonyítva is hozzáadott értéke van.
- A szöveg fundamentális=qualitative és technical=numbers felosztása nem szigorú: injury, időjárás, lineup, taktika is kódolható. Az új rendszernek nem kell e mesterséges kettéválasztást átvennie.
- Hazai pálya lehetséges összetevői: utazás, helyismeret, zaj/kommunikáció, roster és park illeszkedése. A könyv NFL3–4, college7 pontos nyers átlagkülönbsége nem tiszta oksági hazaihatás, különösen nem kiegyensúlyozott NCAA schedule mellett.
- Játékosérték a helyetteséhez és pozíciójához képest, játékidő/minutes/snaps, sérülésállapot, csapategység illeszkedés. A Tyron Smith→Chaz Green példa a replacement gap szemléltetése, nem univerzális hatásbecslés.
- Szerzői egyszerű roster-skála 1–5: tapasztalatlan/helytelen poszt/korlátozott állapot → átlag → átlag felett → Pro Bowl → All-Pro. Ordinalis, nem automatikusan pontkülönbségre átváltható. Injuryt kétszer beszámolni a játékosértékben és a csapatratingben kerülendő.
- Egységszintű OL/DL/secondary/bullpen modellek vagy kevés csapat mélyebb követése adatgyűjtési kompromisszumok. A tipikusan mérhető public adat is adhat előnyt jobb kombinációval/értelmezéssel, így a nyilvános információnak mindig nulla az értéke túl erős állítás.
- Scheme-matchup: futás/passz, man coverage/mesh, tempó, bal/jobbkezes ütők és dobók, park. A historikus névpéldák nem mai csapatleírások. Utólagos győzelem nem bizonyítja a feltételezett mechanizmust: előzetesen leírt köztes predikciók és utólagos mechanizmusellenőrzés is kell.

### 7. fejezet folytatása: schedule, motiváció, környezet

- Pihenő és utazás lehet teljesítményhatás anélkül, hogy ATS-előny lenne; a könyv 210–219 back-to-back adata önmagában nem választja el a nincs hatás és a már beárazott hatás magyarázatát. Mérhető irányok: napok, idősávok, távolság, érkezés, játékidő, rosterkor, road-trip hossza; ezeket hipotézisként kell kezelni, nem kész együtthatóként.
- Recency és valós fejlődés két külön jelenség; rövid jó/rossz eredménysor miatti pszichológiai történetnek több alternatív magyarázata lehet. A kiválasztott edzők dupla számjegyű vereség utáni 64%-os ATS-je nincs prospektíven igazolva, neveik utólagos kiválasztása torzíthat.
- Rivális/egyoldalú rivális, revenge, elimination, playoffesély, look-ahead és sandwich helyzetek: a szerző is több esetben nem talál megbízható profitot. Közös erős motivációból nem következik, melyik oldalnak lesz többletelőnye.
- Taktika/edzőválasztás és játékosminőség kölcsönhatása; coaching tree segíthet hipotézisben, de nem ad automatikus teljesítményparamétert. A szabályváltozás és a bírói végrehajtás külön rezsimváltozás; múltbeli állítások újraellenőrzendők.
- Időjárás, stadion mikroklíma, roof, közönség/zaj, bíró/umpire lehetséges kovariánsok. A weather-under mechanikai szabály túl erős: az ár és a pontos helyi állapot számít.
- A fejezet végén kontrollcsoport, összehasonlítható mérőszám és faktort tartalmazó/nem tartalmazó helyzet összevetése szerepel. Megfigyeléses kontroll önmagában nem randomizáció; strength/schedule/selection korrekció kell.
- A 42 NBA-idegenbeli meccs a szövegben a szokásos 82-es, felezett menetrenddel sem egyezik (41). A Pacific-csapatok Super Bowl-eredménye mint utazási bizonyíték különösen gyenge ökológiai következtetés.

## 8. fejezet: modellezés, rendszerek és túlillesztés

- A szerző fokozatos NFL-szimulációs példája: normál pontgenerátor → drive-szám és TD/FG/üres drive → XP/safety → mezőnypozíció/punt/kickoff → offense–defense csapatspecifikus esélyek. Ez modellalkotási szemléltetés, nem validált implementáció. Safety pontját az ellenfél kapja; idő, drive-függés, possession-váltás és véges játékhossz szükséges a koherenciához.
- A több részlet és több Monte Carlo futás nem garantál jobb valósághűséget. A szimuláció numerikus bizonytalanságát csökkenti az ismétlés, a bemeneti/strukturális hibát nem. A Jeopardy-anekdota nem bizonyítja egy univerzális tétstratégia optimalitását.
- System: sporton át alkalmazható feltételcsoport az adott ligán belül; angle: konkrét csapat/játékos/edző/helyszín. A logikai AND/OR szabályokból felállított 13–1 NFL over példa sok szűrést tartalmaz, ezért keresési múlt nélkül névleges Z-tesztje nem megfelelő bizonyíték.
- A nagyobb minta **nem tünteti el automatikusan a szelekciós torzítást**, és nem jelenti, hogy a jelenlegi hóvihar vagy lineup hatása kellően benne van az átlagban. A Chicago/Colorado közvéleménykutatás-analógia ezt tévesen sugallja.
- A múltbeli trend találati aránya nem közvetlenül a mai meccs p-je. A Kellybe betöltve a kiválasztási és becslési hibát felnagyítja. Az egymást támogató rendszerek sem független bizonyítékok, ha ugyanazon mérkőzéseken/kovariánsokon alapulnak.
- Értékes könyvbeli önkritika: a szerző MLB-rendszereinek felét elvesztette, amikor kiderült, hogy nyitó oddsokra illesztett, de a később elérhető záró árra nem működtek. Kötelező a döntési időben ténylegesen elérhető ár és sportadat használata; closing adat csak utólagos értékelésbe kerülhet.
- Rezsimváltozás, roster/coach csere és piac alkalmazkodása ronthat egy trendet. Önmagában a vesztes széria nem különíti el a romlást a zajtól. A sok ötlet naplózása, időbeli holdout és egyszeri, előre rögzített értékelés erősebb védelem, mint a plausibilis történet.
- P-hacking és túlillesztés kapcsolódnak, de nem szinonimák: előbbi a szignifikanciakeresés/adatvezérelt tesztmódosítás gyakorlata; utóbbi általánosabb generalizációs probléma. A könyv helyesen említi a véletlen szűrések csábítását, de három-négy egyszerű feltételre szorítás sem oldja meg automatikusan.
- Jó rendszer szerzői ismérvei: megfelelő statisztikai bizonyíték, hihető kapcsolat, fogadható piac, időben ismert és feldolgozható bemenet, elég gyakori esemény. Projektkiegészítés: időbeli teszt, költség és végrehajthatóság, specifikált null, változatnapló.
- SDQL példa `points>40` az adott meccs végső pontjára szűr, tehát **look-ahead leakage**, a könyv ezt helyesen figyelmezteti. A `p:points>40` már előző meccs. Viszont `p:A and A` utolsó A-jának H-ra cserélése **más játékmintát** választ (előzőleg idegenben, most otthon), nem ugyanazon minta ellenkező csapatnézetét. A szöveg 19–24–1 ↔24–19–1 tükrözési állítása így hibás. Ellenfélhivatkozás szükséges ugyanazon meccsek perspektívaváltásához.
- A JR Miller példában magas és alacsony totalnál is under szerepel; az érvelés gyanús szerkesztési hiba, eredeti forrás nélkül nem javítom biztos overre.

## 9. fejezet: saját power rating

- Rank csak sorrend, rating különbsége nagyságot is hordozhat, ha a skálát pontokra kalibrálták. Támadó/védekező komponens és tempó szükséges, ha margin mellett totalt is becslünk; egyetlen aggregált erősségszám összeadása nem általában helyes totalmodell.
- Kezdő módszer: score_A=(PF_A+PA_B)/2 és fordítva. Giants/Browns példa 26.4–20.6, margin5.8,total47. Ez egyszerű baseline, schedule-strength, tempo, uncertainty nélkül; a kis line-eltérés nem önmagában fogadási jel.
- Pythagorean: PF^γ/(PF^γ+PA^γ), γ sport- és rezsimfüggő. A 256/211 runs példa γ=2 mellett kb. .5955 és 96.47/162; a kiírt .59×162 azonban 95.58, csak a nem kerekített eredménnyel kapjuk a 96.5-öt. Ez nem konkrét ellenfél elleni meccsvalószínűség.
- Időbeli súlyozás Σwᵢxᵢ/Σwᵢ; a Michigan példa első3 w=.3,középső3 .7,utolsó3 1, súlyösszeg6, raw353.111,weighted319.1667≈319.2 (könyv319.0). A súlyozás mértékét előzetes időbeli teszt határozza meg, nem az aktuális előnyt maximalizáló keresés.
- Offseason prior: előző idény + visszatérő termelés/minutes/snaps + új roster + bizonytalanság. A könyv piacból visszafejtett alternatívája (look-ahead spreads, season totals) **piaci benchmark**, a felhasználói független modell alapjába nem kerülhet.
- A look-ahead példában feltételezett HFA3 levonása Patriots6,Cowboys3,Titans2,Jaguars0 semleges ratinget ad. Rögzített 3 pont szerzői példa, nem általános mai konstans. Külön abszolút referencia/ratingazonosíthatóság kell.
- Adatforrás: mérhető definíció, megfelelő granularitás, időszerű frissítés és strukturált formátum. Az eredeti sportdefiníció, későbbi korrekció és adatközlési idő külön mező. Rangra alakítás elveszti a távolságot, ráadásul kötött átlagos rosterlétszámtól függ; a rangok összege nincs pontskálára kalibrálva.
- A 3rd-down/points-per-drive/YPP súlyok és injury-korrekciók együtt dupla információt vihetnek be; a formula minden csapatra azonos alkalmazása önmagában nem teszi elfogulatlanná a featureválasztást.
- Számszaki hibák: Ravens24+Bengals21=45, nem44. A rangösszeg-példa Atlanta/Buffalo bemeneteit a két egymás utáni táblázat között magyarázat nélkül megváltoztatja; reprodukáláshoz ugyanazok az inputok kellenek.

## 10. fejezet: NFL — teljes olvasás

- A 2007–2019 pont/spread/total/quarter táblázatok történeti rezsimleírások; 2019 több táblában részleges. Nem mai paraméterek. A 16 regular/4 preseason, sérüléskategóriák, edzőlisták és overtime szabályok mai alkalmazását külön ellenőrizni kell.
- Diszkrét margin és total: TD/XP/2pt/FG miatt 3/6/7/10/14 körül külön tömeg. Az unconditional margingyakoriság nem helyettesíti a konkrét matchup és ár közelében becsült feltételes tömeget. Az alternatív line értéke az átlépett kimenetelek fair értéke.
- A 10-2. grafikon 2013–2019 hazai margingyakoriság, átlag2.2; a 10-3. totalátlag45.4,23–69 tartományt rajzol, a10-4. külön6–45 szeletet. Ezek levágott ábrák, nem teljes eloszlások. A12 és13 margin egyformán gyakori mondata a korábbi 13 gyakoribb állítással és a grafikon eltérő oszlopaival is ellentétes.
- Spread→moneyline táblázat nyers mintaarányai nem monotonok (−6.5:84%,−7:75%), a szerző kis mintát említ; nem tekinthetők determinisztikus átváltásnak. Total/eloszlás és push is számít.
- Preseason: starter-tervezett játékidő, QB2/3, mélység és edzőcélok külön adatok. 9.2 vs9.5 átlagos spreadhiba nem teljes piaci hatékonysági mérce, mert scorevariancia és állapoteloszlás is eltér. Korreláció .0944 nem automatikusan nulla összefüggés; mintanagyság/CI és a mért cél szükséges.
- Rest/travel kutatások csak a könyvből ismert másodlagos hivatkozások (Stanford, Kyle Waters/Boston College). A 3.5%/1000km állítás oksági jellege, modellje és skálája nincs ellenőrizve. A szerző saját keresési eredményei utólag szelektált történeti minták.
- Injury: játszik-e és mennyire egészséges/kihasznált külön kérdés. Aktiválás valószínűsége, snap share, replacement és újrasérülés összekapcsolódhat; a probable95%/questionable32% nem mai univerzális becslés.
- Weather: a könyv szerint wind erősebb passzprodukciós hatás, mint hideg önmagában; dome és csapatspecifikus reakció számít. Saját jó figyelmeztetése: melyik weather állomás, tényleges stadionadat vagy napi városi átlag, forecast vagy későbbi observation, melyik fogadási idő és total? A könyvben hivatkozott Mancuso/Houghton-Bery/numberFire kutatások külön ellenőrizendők.
- Megőrzött kutatási hipotézisek (NEM aktív szabályok): preseason2-meccs előző vesztes vsnyertes; Pacific road/Eastern home season≤9 (42–23); season≥9 home rest>8 és előzőloss, road ATS65–28; MNF homewin utáni újabbhome, rövid pihenő, line>−7 (39–13); roadfav total>42 hazaitöbbrest (39–15); emelkedőtotal a korábbi unders után (54–25); Belichick későplayoff Q1under. Mindegyiknél hiányzik független teszt, teljes keresési napló és mai érvényesség.

### 10. fejezet számszaki audit

- 13/152=8.5526%, nem7.5%. 1/77=1.2987%, nem1.2% pontosan. A 20-éves megfigyelésekre kevés kontextus kerül; nem stabil conditional p-becslés.
- −102 break-even102/202=50.49505%, nem50.2%; −110-hez képesti eltérés1.88590százalékpont, nem2.18. A +3→+2.5 eladás push→loss, nem win→push. A line javulását és odds romlását teljes kimeneteltáblával kell összevetni.
- Azonos1dolláros tét mellett +k,−110 és +(k−.5),−102 összevetésénél a különbség EV_new−EV_old=p_win×(100/102−100/110)−p_push. A break-even küszöbök egyszerű különbsége nem általános pontérték.
- Két független azonos esélyű teaser-láb küszöbe sqrt(1/d), az adott **kombinált odds** és push-szabály függvénye, nem önmagában a6/6.5/7ponté. A könyv72.3/73.8/75.1% lényegében −110/−120/−130 kétlábas példa; a2. fejezet −140 6pointNFL példájával nem ugyanaz.
- 54/(54+25)=68.3544%, nem74%. A megadott Z-k némelyike a6. fejezeti z képletnél kisebb, valószínűleg folytonossági korrekciót is használ:39–13 z_raw3.60555, korrekcióval3.46688;39–15 raw3.26599,korrekcióval3.12990;54–25 raw3.26272,korrekcióval3.15021. Eltérésüket így nem önmagában számolási hibának, hanem dokumentálatlan módszerváltásnak tekintem.
- Rest differential megoszlás7.3+14.6+62+12.3+5.2=101.4%; kerekítésnél nagyobb összegeltérés. A quarter táblázat OT oszlopának nevezője/skálája nincs tisztázva, a negyedösszegek nem egyeznek meg az összponttáblával; ebből nem emelek át OT-átlagot.

## 11. fejezet: college football és általános tanulságok

Teljes fejezet elolvasva. College nem felhasználói célbajnokság, de a módszertani megállapításokat megőrzöm.

- Szabály/schedule/roster különbség: szélesebb csapaterő, eltérő ponteloszlás, más OT és clock; a2020-as menetrend, konferenciák és playoff nem mai állapot. A fejezet helyesen korrigálja a nyers8pont home-átlagot a nagy iskolák ellenfélszelekciójával; intra-conference közel3pont, tehát raw különbség nem tiszta HFA.
- Pushfrequency: teljesliga, azonosline, vagy ±7pont környéke eltérő torzítás–variancia kompromisszum. A sajátmodell konkrét matchupra becsül; piaci line szerinti csoportosítás benchmark lehet. A205/3080≈6.66% Wong-környékminta nem univerzális hárompontos kimenetelesély.
- −3/−110 vs−2.5/−120 példában q_push=.067, többi oldal.4665. To-win10 egységnél EVrégi−.4665,új−.263; mindkettő negatív. A jobb relatív érték nem bizonyít abszolút pozitív EV-t. A nyomtatott .467+.467+.067=1.001 kerekítési eltérés. Összehasonlításhoz azonos bankrollkitettségre is át kell skálázni.
- Hibák: pontvásárláskor rosszabb árat kapunk és jobb line-t; eladáskor fordítva — a fejezet egy mondata megfordítja. Az underdog+2.5 példában előjelként −2.5,majd −3 is megjelenik, a felsorolt kimenetek azonban +2.5-re igazak. A47.5 alól 6pontos teaser under53.5, nem52.5. Az O45→O46 vesztesége nem csak a46 kimenet:45-nél push→loss is történik.
- A4lábas6.5pontos teaser+350 a6pontos+280 mellett jobb line-t és jobb árat is adna; dominancia alapján gyanús táblázatérték, élő szabályként nem használható. A parlay-tábla 6 Points oszlopcíme is téves kategória. Push-kezelés platformonként külön, a szöveg közeluniverzális jelzőjét nem veszem át.
- Két független láb p1×p2 alapján értékelhető: nem kell mindkettőnek ugyanannál a küszöbnél lennie, ezt a90%×60%=54% példa helyesen mutatja. Függő lábak/push eltérő modellt igényelnek.
- Preseason roster: returning starters definíciója zavaros lehet (pozícióváltó, sérülésből visszatérő, rotációs játékos). Returning production/snap/minutes és egységszintű tapasztalat tartalmasabb; magas returning szám rossz csapatnál nem automatikus jó jel. A könyv Simons korai-season szűrése 2019-ben nem produkált profitot, ezt is megőrzi.
- A +27.5/−15.5 middle példa két110téttel outside−10,inside+200. Inside egész margó16–27, **nem16–28**. EV>0 küszöb P(inside)>10/210≈4.7619%, feltéve végrehajtható mindkét láb és azonos elszámolás. Ez kockázatos middle, nem biztos arbitrage.
- NCAA kutatási hipotézisek, nem engine-szabályok: SEC intraconf total<53 homefav/dog≤7 under146–88; roadfav≥10 és egyenlő/1–4napextra-rest ATS465–340; roadPK/smalldog hasonlórest811–649; halftime tie+home dog+total≥48+mindkettőQ2score→2Hunder; favorite≥7előzőwin≤15pass→ATS129–88. A winnowing néven tanácsolt profitmaximalizáló továbbszűrés akkor is adatbányászat, ha csak kevés új feltételt használ.
- A quarter-scoring táblázat Home Score fejléce alatt a számok összesen kb.teljesmeccs-totalra jönnek ki, nem hazai pontra; a nevező/fogalmi címke gyanús. A turnover táblázat előjele a magas hazai giveaway→negatív margin értelmezéssel konzisztens, a szöveg viszont nettó takeawayként olvasható. A margin/differential oszlop nem ugyanaz, mint egy új turnover határhatása, és megfigyeléses együttjárásból oksági pontérték nem következik.

### 11. fejezet vége

- Bowl/rematch: kevés minta, motiváció és edzőtávozás/teljesítmény kapcsolat nehezen azonosítható. Injury mellett egészséges opt-out, bejelentési idő és pótlásra rendelkezésre álló felkészülés is számít. A könyv 50meccsnyi revenge-körében nincs stabil side-előny; a60% totalismétlődést maga is gyengének nevezi.
- A támadók jövőbeli turnoverje teljesen megjósolhatatlan, csak defense definiálja állítás túl erős, a könyvben nincs hozzá elég bizonyítás. Új modellnél play/context/pressure/QB hatás külön ellenőrzendő.
- További történeti hipotézisek: week≥7 mindkétcsapat előzőhome,mosthome előzőloss+under≥5,mindkettő<7rest→under70–24; Thursday exactly4rest→dog70–45; háromkorábbi pont≤10/7/6→over25–8; hazai előzőaway+≤10,előtte≤9,road line≥−9→road137–81; home50/40/40korábbi pont,fav≥20→ATS38–13; earlyweeks1–3total≤55,abs(spread)≥.6total→over91–47. Az utólagos történet mindkét irányú tippet képes megmagyarázni; önálló időbeli teszt hiányában egyik sem igazolt szabály.
- A spread és total algebrai szétbontásából (T±M)/2 kijövő pontszám nem szükségképpen a score-eloszlás módusza vagy pontos bookmakerjóslat; a két külön piac marginja/mediánja is eltérhet.

## 12. fejezet: NBA — teljes olvasás

- Támadási rezsimváltás: 3PA-részarány, shot mix és tempó növekedése. Több possession a pontkülönbség és total eloszlására is hat; új rezsimben a régi átlagok kevésbé relevánsak. A példák2014–2020könyvidőszakra szólnak, nem mai állapotra.
- Használható alap: possession×pont/possession; offense/defense hatékonyság és várható tempó együtt, lineup/minutes és matchup függően. A113off-rating×105possession/100=118.65≈119 helyes; a szövegben105points helyett possessions értendő. Overtime és eltérő possessiondefiníció kezelése szükséges.
- Preseason: shot mix/pace, új sztár hozzáadott vsáthelyezett usage, rookieminutes és új edző; lineuplimitből fakadó különbség. Az2019Kram-cikk csak könyvben hivatkozott, külön eredeti ellenőrzés hátravan.
- Playoff más rotáció és terhelés, sorozatellenfél, elimination; a seed korábbi resume, nem aktuális teljes erősség. A magasabb seed és homeadvantage eredményének együttjárása nem tiszta hazai pályahatás, mert erősebb csapat kapja.
- Endgame intentional fouling és hárompossessionnél feladás alakíthat margin-tömegeket; a7pont csak empirikus alak, nem állandó éles törés. A margin eloszlása szélesebb lehet a várható marginok/line-ok eloszlásánál — ez önmagában nem piaci hiba.
- Rest/utazás: a könyv back-to-back side-rendszere nem profitált; over225+no-road-rest részhalmaz61%-a lehet scoringrezsim proxy, amit a szerző maga is elismer. Home/away split, idősáv, sorozatutazás és altitude kontextusba helyezendő.
- Denver/Utah roadscorecsökkenését maga a könyv sem tudja elválasztani pace/ellenfélerő/magasság hatástól. Nyers táblázat nem oksági altitude-együttható. Egységes HFA minden csapatra lehet gyenge baseline, a csapatspecifikus becslést shrinkage nélkül kis minta torzíthatja.
- Korai-season totalmozgás Borghesi2007 kutatása és2019–20 24–16undere könyvbeli másodlagos adat, nem ellenőrzött mai hatás.
- A szerző helyesen gyanakszik az éles, indokolatlan küszöbre: homefav≥12 nyertes,míg10–11.5vesztes; homefav−1.5 különlegessége,amely−1/−2/away/playoff esetén eltűnik. Ettől még javasolja őket; a tudástár a saját kételyét is megőrzi. A sok lineból kiválasztott outlier külön többszöröspróba-korrekciót kíván.

### NBA hipotézisjegyzék — egyik sem validált élő jel

- Magas totals≥225over2015–19:382–299; homefav≥12,2014–18:274–206; −1.5homefav ellen169–108, kizárólag openingline! A mai fogadásnál nem használható utólag elérhetetlen nyitó árral.
- Playoffseed2home vagyroadfav132–82; korai-seasonhome előzőOTwin,line≥−3→road55–30; Easternroad legalább2awaygame Pacific home→under163–108.
- Road előzőfav>7,total>218,előző1H<47→road83–47; total200–215,home dog/vagyfav<10,homeelőzőloss/vagywin<18,home≥rest→under,korai rész251–160.
- Egyszerű zig-zag alig50%; első3round előzőloss+aktuálisfav>9→ATS30–10. Zig-zig:first-roundseed4–8előződouble-digitloss→ellene93–62. További thresholds kiválasztása nem ingyen bizonyíték.

### NBA hibajegyzék

- A total definícióját megfordítja: over akkor nyer, ha pontösszeg **nagyobb** a line-nál; under akkor, ha **kisebb**. A12.fejezet két mondata ennek ellenkezőjét írja.
- A nincs±.5 NBAspread állítással szemben a12-6.táblában explicit−.5/+ .5sor van. A−7favorite sorban az underdog+6helyett+7lenne az azonos handicap.
- A12-1.sorok2016–17től:54+57+257=368over és37+38+218=293under, nem a szöveg346/278. A2015–16-tal együtt382/299helyes. A≥225cím és>225oszlopcím különböző szűrő.
- 2018NBAfinal7meccs állítása könyvbeli történeti hiba jelölt; eredeti eseményforrás ellenőrzése hátravan. Piaci volumenadatok sem auditáltak. A12-3.scoringévcímkék gyanúsan elcsúszhatnak a12-1.táblához képest, benchmarknak nem használhatók ellenőrzés nélkül.
- A40%egypossession/70%kétpossession spreadhiba állítását nem támasztja alá nyers lista/possessiondefiníció. Atravel-tábla~210–230ppg értékei összevont game-totaloknak látszanak, nem egyetlen roadcsapat pontjának. East→Pacific háromidősáv-eltérés, nem a szöveg kétzónás példája.

## 13. fejezet: NCAA basketball — teljes olvasás

- Sok eltérő erősségű iskola, változó roster, eltérő nonconference schedule. A rangsor és márkanév anchor lehet; a hazaiat ugyanúgy választja a schedule, ezért rawhomeátlagot óvatosan kell kezelni. Az egyszerű ranked/unranked szelet is erősen schedule- és időfüggő.
- Pace/efficiency alap ugyanaz, mintNBA, de40perc, más shot clock/3pt/foul/OT; a2020-as szabály nem mai/egyetemes (női/meccstípus) specifikáció. A könyv68-asmezőnyt kezdetben64-re egyszerűsíti, később play-int kizáró63meccsnek nevezi. A seedtáblák eltérő összmeccsszámai eltérő időablakot/hibát jelezhetnek, nem egységes kalibrációs adatsor.
- Jó felismerés: 0–9ATS múlt nem jósolja automatikusan a10.vereséget; a könyv hasonló esetekben20–16következőATS-t talál, de ez sem bizonyítja az ellenkező stabil előnyt.
- Halftime: időben ismert1Hboxscore, valódi2Hajánlat, ár, rendelkezésre álló rövid döntési ablak; teljesmeccs és2Hközti kapcsolat nem lineáris konstans. Foul/endgame és OT-elszámolás külön, a47/53megoszlás történeti átlag, nem szabály.
- Kutatási példák: favoriteelőzőawayloss≥20,rest≥2→over260–192; bigconference,total128–144,<25%tickets→alulbeteltoldal55%; road rest<6,előzőFG≥56%,total≥145→under270–207; halftimehome down≥11/vagydown16&closeline/vagyup≥22→2Hunder; homefav≥8total≥160→2Hunder56%; earlyOTloserhome→under53–24; rankedvsunrankedregularATS1372–1135; tournamentunrankedfav ellenranked36–6; erősATSstreakellen50–28; conferencetourneyelőződogwin hosszabbpihenőután→over27–10; neutral2ndgamefav≥15,total≤144→dog14/19ésover15/19. Mindezeknél többes keresés, árváltozás, korreláció és kis minta megjegyzendő.
- A szeletelés minden hónapban/line-tartományban hasonló eredménye hasznos diagnosztika, de ugyanazon tanítóadat alcsoportjai nem új holdoutok. A ranked csapat elleni eltérő rendszer cáfolata sem automatikusan igazolja a saját stratégiát.

### NCAA basketball hibák/korlátok

- 13-3.tábla homefavorite5577over/5649under mellett az51/49% felcserélt; tényleges arány közel49.68/50.32%.
- 13-4.első/ második sor `1H<=2H` és `2H>1H` nem diszjunkt kiegészítők. A home/combined összege9640, away8893; következtetés előtt az eredeti query és definíció szükséges.
- NCState+9másodikfélidő,majdDuke10–2 példában a második eseménynek **overtime**-nak kell lennie, nem újból2H-nak. Így9−8=1nettó2H+OTmargin,−4fogadás veszít.
- 5way6pontos teaser+370 a5.5pontos+365mellett jobb árat és jobb line-t adna; újabb dominanciahibás táblázatjelölt.
- Tourneytwofer cím under, setup tényleges **over** és15over/4under; címből nem szabad szabályt építeni. Unrankeddancers cím is félrevezető, kiválasztás rankedunderdog.
- A UMBC-victory év2017megjelölés történeti hiba jelölt; elsődleges ellenőrzés majd az onlinekörben. A fix számú bracketupset előírása nem következik az összesített gyakoriságból, scoring/pool/aktuálismatchup nélkül.

## 14. fejezet: MLB — teljes olvasás

- Sajátmodellhez releváns: kezdődobó és várhatóinning/PA, bullpen elérhetőség/minőség, tényleges battingorder és platoon, park és weather/roof, védelem, base/out állapot, home9.csonkulása és walkoff. A könyv e tényezők többjét tárgyalja; részletesebb B03/B08/B24 kiegészítendő.
- Moneyline találati arány nem elég: −170nullszaldó170/270≈62.963%,62%találat veszteséges; underdog <50%is lehet pozitív. Különfogadásonkénti nettó profit/valóditét és díj kell.
- Standard±1.5runline külön célváltozó, azonos ML mellett eltérhetP(winby1). Az oddsok százas-skálájú eltolása nem konstans valószínűségi eltolás. A2019favorites376egy-runwin/1489win≈25.25% történeti átlag, nem matchupkonstans.
- Listed-pitcher vsaction: a könyvben listed feltétel legalábbelsőpitch mindkétmegnevezettstartertől; változáskor void. Action példájában a book újraárazhat. **Ez platform- és szabályverziófüggő**, a korábbi fix odds általánosítás kivétele; nem feltételezhető Stake/Polymarketelszámolásként.
- F5 kevésbé bullpenfüggő, de nem csak kezdődobó: korai kivétel/opener/lineup ugyanúgy hat. Moneyline tie/push,±.5runline és fullgame eltérő. A book F5/fullgame összefüggése magas, de a F5része a fullnak: az együttjárás nem független prediktív bizonyíték.
- Teamtotal az egyoldali offense/pitching vélekedést célozhatja; NRFI/YRFI a battingorder elejére koncentrál. Az első8hét és idényvége top10 átfedése részben mechanikus, mert az évvégi mutató **tartalmazza** az első8hetet; valódi stabilitáshoz nem átfedő későbbi periódus kell.
- HFA nyers53%átlag; teamhomesplit kis mintában ingatag. Parkhatás többkomponensű: falkerület/magasság,foulterritory,air,felület/sightline; korrekciót a roster és dobó/ütőprofil kölcsönhatásával kell kezelni. Parkváltozás/csapatköltözés definícióváltozás.
- Weather: stadiumorientációhoz viszonyított szél,roofállapot,hőmérséklet,páratartalom,nyomás,forecastfrissítés. A szöveg egyszerű +0.1run/mph állítása irány/park nélkül nem általánosítható. A2009IJSSE ésNevada-Reno kutatás csak könyvbeli hivatkozás, eredeti ellenőrzés hátravan.
- Scoringrezsim2009–2019 táblája, HR/K felfutás, lineupinningeffektusok; különbséget kell tenni inningenkénti és ténylegesPA/half-inningenkénti intenzitás között, mert a9.alján gyakran nincs ütés. Hot-hand állításokat a szerző óvatosan kezeli: minőségi, egészségi és véletlen magyarázat keveredhet.
- Kutatási trendek: home többrest,total≥9→over145–105; pontosan4homewinstreak ellenroadML/RL7%ROI; home5streak ellen11%/6%; homedog,total≤7→under55%/5.6%ROI; roadfavorite≤−201:288–77/12.3%ROI; előző1–0loss,mostfav→ML11%ROI,ellenfélváltósubset56–25/18%. Eredeti tét/ár/query, nem átfedővalidáció és költség nélkül ezek történeti állítások.
- Martingale nem hoz létre előnyt, csak kitettséget alakít; egy győzelem előtt nincs garantált véges tőkével finanszírozható sorozathossz. A könyv kétlépcsős ellenstreak-progresszióját sem vesszük át automatikus tétjavításnak.

### MLB fogalmi és számszaki audit

- **FIP** nem a mezőnyjátékosok range-ével normalizál: strikeout/walk/HBP/HR alapú fielding-independent összegzés. Az IP betűk itt a névben Independent Pitching, nem egyszerűen inning pitched rövidítés. B03 részletes képlet az irányadó; a könyv K/inning−BB/9 összehasonlítása egységkeverés, egységes nevező kell.
- NRFIpélda NO fogadás0–0-nál nyer, minden másnál **veszít**; a szöveg mindkettőre wint mond. Szabályfüggő befejezés/cancel kezelés előtt nem automatikus settlement.
- −105/+100 esetén impliedsum=1.012195…,normalizedhold≈1.2048%; a5cents kifejezés az american árak távolsága, nem5%-os hold. −110/+100hold≈2.3256%helyes.
- −125nullszaldó125/225=55.5556%, nem55%. A −241átlagosárból számított küszöb nem helyettesíti a365egyedi ár EV-jét. A roadfavorite-táblázat2014-ben6fogadástmondó szövege a2015-ös5–1sornak felel meg;2014-ben12–4=16.
- A SADEképlet súlyosan megbízhatatlan: altitudeemelkedés pozitív, hőmérsékletemelkedés negatív pontot ad, miközben mindkettő csökkenti a sűrűséget. Páratartalom mindkét szélső esetre+1; a29.5/30.3 értékek mmHg egysége nagyságrendileg hibás (inHg-nak felelhetnek meg). A Chase−7 példát alacsonyabb sűrűségnek nevezi, miközben aCoors+15más irányú konvenciót sugall. Az1000vs2600láb−6.4pont lenne folytonos képlettel,−6csak kerekítéssel. **Nem implementálható hiteles fizikai modellként**; elsődleges légsűrűségi/ballisztikai forrásból kell újra felépíteni.
- A thin-airfastball4–5mph állítás nincs kalibrált kiinduló sebesség/mérési szakasz mellett alátámasztva, nem általános featurekoefficiens. Winddirection ésforecast nélkül SADEalapú total-tipp különösen nem indokolt.
- Ventura walkoffpéldájának részletes szabálymagyarázata és a2020-as resumption/listedpitcher környezet nem mai szabálykönyv. A tanulságot őrzöm: hivatalos eredmény és fogadás elszámolási szabálya dönti el a profitot, nem a közvetítésben látszó esemény.

## 15. fejezet — döntési torzítások (24. egység)

- A bizonytalanság, a sok információ és a kontroll érzése különböző dolgok. A részletes adatbázis sem szünteti meg a sporteredmények feltételes szórását. Előzetesen rögzített indoklás, teljes eredménynapló, ellenérvek és ellenhipotézisek segítenek elválasztani a döntés minőségét a végeredménytől.
- A korreláció nem általában 0 és 1 közötti magyarázott hányad: a Pearson-r −1 és 1 közé esik; megfelelő regressziós keretben az R² a magyarázott variancia aránya. Egyik sem közvetlen okozati bizonyíték, és nem mondja meg egyetlen meccs előre meghatározott részét.
- Regresszió az átlaghoz: szélsőséges zajos megfigyelés után a következő várható megfigyelés közelebb lehet a feltételes átlaghoz. Nem jelenti az ellenkező eredmény esedékességét; a valódi csapaterő közben változhat.
- Gambler's fallacy: a múltbeli független sorozat nem változtatja meg a következő p-t. A 11 fekete egymás után amerikai rulettben 0.0269388%, európaiban 0.0361226%; a szöveg 0.00269%-a tízszeresen alacsony az amerikai értéknél. A sportmérkőzések függetlenségét viszont nem szabad feltételezett szabályként átvenni a portfólióba.
- Confirmation, recency, availability, familiarity, post hoc és illusory correlation; saját sikert készségnek, kudarcot külső oknak tulajdonító aszimmetria. A technikai szabályok alkalmazása sem szünteti meg teljesen a torzítást, ha a szabályok kiválasztása utólag történik.
- A hot-hand történeti kutatásokról adott másodlagos összefoglaló és a büntetődobás-kivétel nem mai konszenzus igazolása. Az eredeti tanulmányok ellenőrzendők. A rugalmas momentumtörténet gyenge bizonyíték; ebből nem következik minden állapotfüggés hiánya.

## 16. fejezet — táblázatkezelés és képletek (25. egység)

- Relatív, abszolút és vegyes hivatkozás, nevesített tartomány, pontos kulcsegyezés, IF, IFERROR. Az adattípus, egyedi kulcs és hiányzó érték külön kezelendő; a megjelenítési hiba elrejtése nem teszi használhatóvá az ismeretlen bemenetet.
- A 16-3. ábra VLOOKUP-ja elhagyja a negyedik paramétert, így közelítő keresést használ, miközben a későbbi szöveg helyesen pontos FALSE egyezést javasol. A közelítő keresés rendezési feltételekkel működő alsó határ, nem általános legközelebbi elem.
- A 16-5/6. ábrán az adattábla A:B, a keresett név G1; a szöveges képlet H1/B1:D6 hivatkozásai nem ehhez az elrendezéshez illenek. A 16-7. ábra és szöveg együtt egyértelmű hibát mutat: B:E tartományban a játékos az 1., pont a 2., assziszt a 3. oszlop. Curry asszisztjához col_index=3 kell; a könyv 2-je a 26.4 pontot adja a 6.1 assziszt helyett.
- ROUND(32.3,0)=32 kerekítés, nem felfelé kerekítés. A Mac/VBA elérhetőségével, korlátlan IF-beágyazással és névtörlés hatásával kapcsolatos szoftverállítások nem ellenőrzött aktuális dokumentációk; a könyv alapján nincs régi verzióra visszaállítás.
- American→decimal: negatív a esetén d=1−100/a, pozitív a esetén d=1+a/100. A=0 érvénytelen ár, nem 50%-os valószínűség; a pick'em nulla spread más típus. −100 matematikailag érvényes even odds, még ha a felület +100-at használ is. A könyv validátora ezt is kizárja.
- +200 nullszaldója 1/3, nem minden második fogadás; a képlet jó, az ezt magyarázó mondat hibás. p→american: p>0.5-nél −100p/(1−p), egyébként 100(1−p)/p, 0<p<1. A nyomtatott összetett IF végén egy fölösleges zárójel van. p=.45 fair ára +122.2222; +122-re kerekítés már kis negatív EV-t jelent, ajánlati küszöbnél ezt kezelni kell.
- 16-10. ábra: risk675, net50, yield7.4074%; a gross winning400/675=59.2593% nem esemény-valószínűség. A 16-11. ábra oddsfüggően súlyozott nyerési/vesztési egységei másik ad hoc mutatót adnak, nem kalibrált találati arányt.

### A normalizált találati arány ellenpéldája

A könyv c=.5238 mellett nyerésre c/q, veszteségre −q/c súlyt ad, ahol q az ajánlati implied probability. Ha az ár fair, q=p, sok független ismétlésben a pozitív súly várható értéke c, a negatív súly abszolút várható értéke p(1−p)/c. Így a mutató határértéke c²/[c²+p(1−p)]. p=.25 vagy .75 esetén ez 59.4038%, miközben az EV pontosan nulla. Emiatt a 63.4%-os normalizált eredmény nem önmagában edge-bizonyíték, és nem jogos 52.38%-hoz mérni tetszőleges oddsösszetételnél. Helyette tényleges árhoz kapcsolódó profit, proper scoring rule és kalibráció szükséges.

### Kelly és z-próba

A 16-12. ábra helyesen ad p=.55, −110, bankroll1000, fél Kelly esetén 27.50-et: f=(pd−1)/(d−1)=.055. A raw bet amount címke itt valójában arány. Az önálló fogadás képletéhez kell p-bizonytalanság, nempozitív edge-nél kihagyás és közös kitettség kezelése; ezek nincsenek megoldva a hétcellás táblában.

A Gambler's Z első Excel-képlete hibásan zárójelezett. 50%-os binomiális null alatt a nyers z=(W−L)/sqrt(W+L); pozitív eltérésnél folytonossági korrekcióval z=(W−(W+L)/2−.5)/sqrt((W+L)/4). Kétoldali próbánál a korrekció iránya az eltérés előjeléhez igazodik; kis mintánál pontos binomiális próba előnyös. A véletlenség önmagában nem jelent 50%-os p-t, és 50%-os null nem a −110 profitküszöb. A korrekció magyarázza a korábbi trendtáblák több z-eltérését; azokat nem puszta számolási hibaként kell kezelni.

## 17. fejezet — gyakorlati hibák (27. egység)

- Veszteségüldözés kerülése; ténylegesen kapott ár és vonal naplózása; utólagos opening/closing csere tilalma. A saját információhoz illő piacot kell vizsgálni, nem szükségszerűen csak a győztest.
- A találati arány ártól függ, így a 54–57%-os célok csak meghatározott ártartomány mellett értelmezhetők. A mindig található profitábilis rendszer állítás nincs bizonyítva; a piac és a szabályok változnak.
- A passz a döntéstér része. A fejezet kis szórakoztató propot ajánló kitétele nem illik a kizárólag validált edge-re épülő engine-hez. A pickek minőségi osztályaihoz 100 fogadás nem univerzális bizonyítási küszöb; a több osztályt és utólagos szelekciót is kezelni kell.
- Átlag mellett ellenfél, kontextus és eloszlás szükséges, de a 17-1. Panthers-táblázat számtanilag hibás. A közölt score-sorozatból a 7. meccs előtti átlag23.6667 és score36; a magyarázat20/42 értékei a sorokkal sem egyeznek. A 15 tényleges előző-meccses becslésből4 esik7ponton belül; az első hétnek nincs azonos szezonbeli előzménye. A könyv5/16-os értéke hibás táblára épül. Az audit újraszámolja minden hét prior átlagát és eltérését.

## 18. fejezet, függelékek és utóanyag (28–36. egység)

- Olvasmányajánlók: Silver, Tetlock/Gardner, Chamorro-Premuzic, Manteris, Firestein, Moskowitz, Huff, Poker Joe, Lewis, Taleb és Melville. Fő témák: bizonytalanság, empirikus ellenőrzés, bukásból tanulás, statisztikai félrevezetés, piaci narratívák és megszállottság. E művek könyvön belüli ajánlása nem jelent teljes eredeti olvasást; a Sharper a külön B19-forrásban már feldolgozott.
- A függelék történeti adatforráslistája: KillerSports/SportsDatabase, Covers, PreGame, OddsShark, TeamRankings, SportsbookReview, DonBest, RotoGrinders, Action Network, Betsperts; NFL: SportsOddsHistory, FootballOutsiders, ProFootballReference, WalterFootball; NBA: NBAStuffer, BasketballReference; NCAA: mcubed, KenPom; MLB: BaseballHQ, DailyBaseballData, BaseballReference. A korabeli ingyenesség, API-hozzáférés, frissesség és újrafelhasználhatóság nincs ellenőrizve.
- Podcastok és Twitter-nevek szakmai ötletforrások, nem automatikusan hiteles adatszolgáltatók vagy validált tippek. A B függelék Bovada/5Dimes/BetOnline/Heritage/Intertops összevetése 2020-as megfigyelés; a nincs helykorlátozás, befizetési/kiutalási limitek, díjak és ajánlati minimumok nem jelenlegi jogi/platformtények. Nem Stake/Polymarket-integrációs specifikáció.
- Index, szerzői életrajz, köszönetnyilvánítás, kiadói reklámok és EULA-hivatkozás elolvasva. Az életrajz és hálaadás szerint a hoki-fejezet nem került a könyvbe: ebből a forrásból nincs részletes NHL-modell, és WNBA-fejezet sincs. Az EULA szövegét csak webhivatkozásként tartalmazza a fájl, a távoli szerződés nem része az elolvasott anyagnak.

## Vizuális és számítási lefedettség

Mind a46 képfájl megtekintve; 1–25 szakmai ábra/táblázat, a továbbiak reklámok, borító, címoldal, logók és ikonok. A 13. ábra MLB-oddsboardja elkülöníti a kezdődobót, nyitó és aktuális árat/vonalat. A 14–25. ábrák fenti Excel-ellenőrzései a képen látható cellákra is épülnek. Az áttetsző PNG-k fehér háttéren lettek renderelve; az eredeti képek megmaradtak.

Reprodukció: `knowledge/verify_b14.py`; eredmény: `knowledge/reading_v2/B14-numerical-audit.json`. Az audit számtani ellenőrzés, nem tanított modell vagy visszateszt. Kiemelt tiltott átvételek: hibás ROI, ad hoc normalizált win%, odds nélküli winrate-cél, SADE mint fizikai modell, múltbeli trend mint igazolt mai előny.
