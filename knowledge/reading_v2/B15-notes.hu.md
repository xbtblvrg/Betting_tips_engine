# B15 — Ed Miller, Matthew Davidow: The Logic of Sports Betting (2019)

Olvasás: teljes kinyert szöveg, PDF 1–241. A hosszú tool-válaszokból kieső
részeket külön újraolvastam (22–48, 75–97 és 205). Vizuális ellenőrzés:
39., 129., 161., 215. PDF-oldal táblázatai/képletei. Nyomtatott oldal = PDF − 4
a főszövegben. Ezek kutatási jegyzetek, nem kész engine-terv.

## Fejezetenkénti jegyzetek

| PDF-oldal | Fejezet | Lényeg és értelmezés |
|---|---|---|
| 11–14 | Bevezetés | A fogadás versengő információs piac; a kötet kifejezetten nem tanít teljes adatmodellezést. Önmagában nem engine-recept. |
| 17–22 | Multiplayer game | Az elérhető árat már más fogadók is vizsgálták. A nyitóár, érett ár és a limit emelése eltérő információs állapot. Saját modell és piaci eltérés önmagában még nem bizonyított előny. |
| 23–33 | Break-even, hold | Amerikai „cent” helyett valószínűségben és hozamban kell összehasonlítani. A hold nem garantál nyereséget a booknak. Push esetén a visszatérített tét csökkenti az effektív veszteséget/előnyt. |
| 34–36 | Multiway | Többkimenetelű és döntetlent külön kimenetellé alakító piacokon könnyen elbújik a nagyobb margin. A hasonló című két- és háromutas piac nem ugyanaz a fogadás. |
| 37–43 | Parlays | Teljes decimal payout szorzódik független lábaknál. A rollover megnöveli az implicit forgalmat, a short-pay további veszteség. A variance és a közös kimenetel eloszlása nélkül a magas EV nem elég. |
| 44–55 | Market making | Nyitóbecslés → kis limitekkel price discovery → emelt limitek; ügyfélprofil és fogadási információ befolyásolja a mozgatást. Több másoló book nem több független vélemény. |
| 56–66 | Üzleti modellek | Market maker: kis margin/nagy forgalom/információhasznosítás. Retail: másolt árak, marketing, margin és ügyfélkorlátok. Spektrum, sportonként eltérhet. A bookmaker nézőpont nem pusztán meccsmodell. |
| 67–70 | Public money | A publikus ticket/money százalék önmagában nem árelőny. A „sharp oldal” csak azon az áron volt jó, amelyen megjátszották. Kivételes tömeges események nem általános napi stratégia. |
| 71–82 | In-play és összegzés | Game-state, csapaterő és pricing-modell; hibaforrás a feed, kihagyott friss információ és számítás. Timeout és játék közbeni végrehajtás különbözik. A 2019-es latency számok nem mai specifikációk. |
| 85–94 | Best price | Több book legjobb oldalai csökkenthetik a szintetikus overroundot. Related market átváltás modellfüggő, a biztos arbitrage és a valószínűségi middle nem azonos. Steam chasing piaci eredetű stratégia, nem a kért saját sportmodell. |
| 95–112 | Előnyök/hátrányok | A book csapatot, modellt, ügyféladatot és margint használ; a fogadó specializálhat és kihagyhat. A bet-delay szelektíven a legjobb ajánlatokat utasíthatja el, ezért papír-PnL ≠ realizált PnL. Kis előny mellett egy hiányzó sérülés sok jó becslést semlegesíthet. |
| 113–119 | Agreement/resistance | CLV diagnosztika likvid, valódi price-discovery piacon; kis mintán nem bizonyíték. Alacsony aktivitású, nem mozgatott propban a változatlan záróár nem cáfolat. Kedvezőtlen mozgásra automatikus ráfogadás helyett az információs eltérés vizsgálata. |
| 120–125 | Strong/weak markets | Piackészítők száma, likviditás/limitek, információhozzáférés, mozgások és margin. Heurisztikák, nem örök liga-besorolások. Kis tét önmagában nem teremt előnyt, de kisebb kapacitású lehetőségeket is használhatóvá tehet. |
| 126–131 | Related markets | Eredményeloszlásból következetesen származtatható moneyline/spread/total. Push és döntetlen tömeget meg kell őrizni. NFL-szabályváltozás miatt a történeti push-arány nem rögzített konstans. |
| 132–138 | Betting menu | Sok összefüggő piac fenntartása nehéz; egy változó frissítése a kapcsolt kimenetelek teljes eloszlására hat. A szerző ezek árazási inkonzisztenciáit keresi, a főpiacot referenciának használva. |
| 142–143 | 60% vs 54% | A találati arány nem optimalizációs cél; sok kisebb, igazolt pozitív EV-jű fogadás többet érhet néhány nagy előnyűnél. Ez nem jelenti, hogy bizonytalan kis edge-et kötelező megjátszani. |
| 144–156 | Derivatives és MLB-példa | Időszakok nem egyszerűen a teljes meccs arányos részei. Kezdő possession, opener pitcher, batting order, park és platoon együtt módosíthatja az első inningt. Előbb mechanizmus és nagyság, aztán árhoz mérés. |
| 157–162 | Props | Precíz szöveg („kettő vagy több” vs „több mint kettő”), játékoselérhetőség és matchup; kis főpiaci hír nagy prop-hatású lehet. A „három egymást követő score” táblázat a szerző korabeli modellje, nem átemelhető igazság. |
| 163–174 | Parlays részletesen | Pozitív/negatív korreláció, bankroll a szolgáltatók között, implicit rollover. Baseball hazai győzelem és under kapcsolata a kihagyott alsó 9. inning miatt. Közös modellhiba több meccs kockázatát is együtt mozgathatja. |
| 175–184 | Futures/multiway | Teljes kimeneteltér, field és eloszlásfarok; események utáni újraosztás nem egyenletes. Tőkekötés és függőségek külön problémák. Sok látszólag jó jelölt egy magas marginnal árazott mezőnyben modellhibára utalhat, de a szerző kategorikus állítása nem matematikai tétel. |
| 185–196 | Angles/trends | Mechanizmus: előrejelezhető, számszerűsíthető, még nem árazott hatás. Sok önkényes adatfilter túlillesztésre utal. „Glide”: környező küszöbértékeknél logikusan fennmaradó hatás mint robusztussági vizsgálat; nem helyettesít out-of-sample tesztet. |
| 197–215 | In-play részletesen | Három hibatípus: rossz állapot, kihagyott kontextus, modellhiba. A kezdődobó ereje nem maradhat benne extra inningben, ha már lecserélték. NBA pace/rotáció és NHL kapuslehívás/periodus eltérő hatások. Az olcsóbb live ár önmagában nem value, mert romolhatott a nyerési esély. |
| 216–223 | Marketing | Bonus, turnover, cash vs free-play és settlement szerint más a payoff. A nagy várható pénzbeli nyereséghez nagyon nagy bukási esély társulhat. Nem átvehető a napi kis tétes feladatra a szerző „go broke” bónuszstratégiája. |
| 224–230 | Hold csökkentése | Több gyenge jel együtt is csak akkor informatív, ha nem ugyanazt a hatást számoljuk többször. A szerző százalékos heurisztikáit nem tekintjük kalibrált probability update-nek. |
| 231–236 | Nyerünk-e? | Fogadásnapló, végrehajtás, piaci megfigyelés, folyamatmagyarázat; hosszú táv sokkal hosszabb, mint néhány jó nap. A könyv szerint a valódi végrehajtás mutatja meg a piac reakcióját; saját következtetés: ettől a paper teszt még hasznos első szűrő. |
| 237–241 | Köszönet/függelék | Odds-átszámítási tábla. Kerekített fractional értékek helyett pontos számítás szükséges. |

## Képletek és fogalmi különbségek

Push nélküli decimal odds `d` esetén break-even `1/d`, nettó EV `p*d-1`.
Az amerikai negatív odds abszolút `a` értékére `a/(a+100)`, pozitív `a`
esetén `100/(a+100)`. Ezek ár által megkövetelt valószínűségek, nem
automatikusan a valós eseményvalószínűségek.

Teljes, egymást kizáró kimeneteltérben `S=sum(1/d_i)`, overround `S-1`.
Az azonos kifizetésre kiegyenlített könyv holdja `1-1/S`. Ezért a két
−110 oldalnál 4.7619% overround és 4.5455% hold két külön szám.
Nem szabad egyiket másiknak nevezni. Tényleges realizált bookmaker hold
megint más mennyiség, függ a fogadások összetételétől és az eredményektől.

Push esetén fair decimal `1+p_lose/p_win`, és a feltételes, nem-push
nyerési arány `p_win/(p_win+p_lose)`. B15 PDF129 példája: win .4725,
push .025, lose .5025; fair conditional 48.4615%, fair decimal 2.06349.

## Kritikák, amelyeket nem szabad szabályként átvenni

1. **Nulla hold nem garantálja az egyoldalú fogadás veszteségmentességét.**
   A PDF86–91 köznyelvi érvelése implicit módon feltételezi, hogy a piaci ár
   jó valószínűségi horgony, és a választás nem szisztematikusan rossz.
   Saját ellenpélda: 2.5 és 1.6667 fairnek tűnő, nulla-overround ár, miközben
   az első oldal valódi esélye .2. Az első oldal EV-je −50%. A két oldal
   azonos payout szerinti megvásárlása garantálhat break-even eredményt
   költség nélkül; egy irány kiválasztása nem. A related-market „nulla hold”
   ezen felül az átváltási modell hibáját is hordozza.
2. **Middle nem feltétlenül biztos profit.** Ha egyik irány nyerésénél a
   két fogadás nettója negatív, és csak a középértéknél pozitív, az EV
   feltételezett középérték-valószínűségből jön; nem risk-free arbitrage.
3. **Boost és free bet nem automatikusan jó.** A PDF221–222 kategorikus
   tanácsát csak payoff és valós p alapján lehet alkalmazni. Stake-not-returned
   free bet készpénzértéke `p*(d-1)*credit`; nagyobb d nem mindig jobb,
   ha p közben túl kicsi. Odds boost után is lehet negatív EV.
4. **Több jel százalékainak összeadása** double countinghoz vezethet;
   sérüléshír és annak nyomán mozduló ár nem két független bizonyíték.
5. **CLV** piacszerkezet-függő diagnosztika; a könyv „fél hold” ökölszabálya
   nem univerzális statisztikai validációs küszöb.
6. **2019-es gyenge piacok és példák** nem jelentenek ma kihasználható
   előnyt. Maga a szerző is jelzi a fejlődést és az edge eltűnését.

## Nyitott kutatási kérdések a többi könyvhöz

Hogyan becsülhető odds nélkül a teljes kimeneteleloszlás? Hogyan kezeljük a
kevés adatot és bizonytalan játékoshatást? Hogyan mérjük a modellhibát a
véletlen eredményingadozástól külön? Milyen kockázatkezelés működik becsült,
korrelált edge-ek mellett? Hogyan igazolható, hogy a megfigyelt EV ténylegesen
végrehajtható? Ezekre ez a kötet nem ad teljes választ.
