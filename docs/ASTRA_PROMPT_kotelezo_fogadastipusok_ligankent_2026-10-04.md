# UTASÍTÁS – Astra: kötelező fogadástípusok ligánként, teljes kiértékelés minden meccsen, a legesélyesebb fogadások kiadása

Ez végrehajtandó parancs. Ami alább szerepel, azt le kell fejleszteni, élesíteni, és élőben igazolni kell.

## Definíciók – kötelező értelmezés

- **Támogatott fogadástípus:** olyan típus, amelyre a rendszer **pénzes ajánlást ki tud adni**. Ehhez az kell, hogy:
  - le legyen töltve és értelmezve;
  - modellel árazva legyen;
  - a guardon átmenjen;
  - tétet kaphasson;
  - el legyen számolva;
  - a tanulásba vissza legyen csatolva.

  Az „elvileg van rá modell” állapot nem számít támogatottnak.
- **Kötelező fogadástípus:** az alábbi ligánkénti listában szereplő minden típus, minden felsorolt időszakra, minden vonalra és kimenetelre. A lista **minimum**. Ha a Stake egy meccsen ezen felül más típust is kínál, az is kötelező.
- Csak az számít, amit a Stake az adott meccsen ténylegesen kínál. Amit nem kínál, az nem számít lefedettségnek, és nem is hiány.

## Kötelező működés minden meccsen

1. A gördülő 48 órás ablakban az NFL, NHL, MLB, NBA és WNBA **minden meccsén** a modellnek / engine-nek **minden** kötelező fogadástípust ki kell értékelnie, ha a Stake kínálja. Ez minden vonalra és minden kimenetelre vonatkozik, minden ciklusban.
2. A kiértékelt jelöltek közül a rendszernek **a legesélyesebb fogadásokat kötelező pénzes ajánlásként kiadnia**:
   - Elsődleges szempont a nyerési valószínűség.
   - Ezt súlyozza a tanult liga × piactípus teljesítmény (WR, ROI) és az ár.
   - A típus mindegy: bármelyik fogadástípus lehet a kiadott ajánlás, ha az a legesélyesebb.
   - Favoritok engedélyezettek.
   - A tét fogadásonként 1–5 USD, a meglévő tanult tétezéssel.
   - **Dinamikus meccsenkénti limit:** a jelenlegi meccsenkénti limit az alapérték, nem plafon. Ha egy meccsen sok jó esélyes fogadás van, a rendszer automatikusan bővítse a meccsenkénti limitet:
     - a fogadások **darabszámát**, beleértve a periódus-piacok számát is;
     - a meccsenkénti **maximális összeget**.
     - A bővítés mértékét a jelöltek nyerési valószínűsége, a modell–piac egyezés, a magabiztosság és a tanult liga × piac teljesítmény (WR, ROI) határozza meg: minél több és minél erősebb a jelölt, annál nagyobb a keret.
     - Gyenge vagy kevés jelöltnél marad az alapérték.
     - Azonos kimenetelű vagy azonos kifizetésű fogadás nem duplikálható.
     - A bővítés minden kiadott tippnél legyen naplózva (mi indokolta), és visszacsatolva a tanulásba. Így a rendszer méri, hogy a bővített meccsek javítják-e a WR-t, a ROI-t és a kumulatív P&L-t.
     - A szabályok konfigurációban legyenek, egy helyen állíthatóan.
3. **Kötelező napi minimum: 100 különálló pénzes ajánlás** az öt ligában összesen. Felső plafon nincs.
4. Ha egy kötelező típus egy meccsen nem értékelődik ki, vagy egy ciklus nem ad ki pénzes ajánlást, az **hiba**. Ki kell deríteni, mi a hiba oka (parser, guard, validáció, küszöb, modell vagy adat), és azonnal javítani kell. Ha egy típushoz hiányzik a modell vagy az elszámolás, azt le kell fejleszteni.

## Közös alappiacok (minden felsorolt időszakra, ahol a Stake kínálja)

- Winner
- Draw No Bet
- 1x2
- Double Chance
- Handicap, Asian Handicapre is
- Total, Asian Totalra is
- Home Team Total
- Away Team Total
- Odd/Even
- Total (Over-Exact-Under)
- Winning Margin
- Kombók:
  - 1x2 & Total
  - Winner & Total
  - Handicap & Total

## NFL – kötelező

**Időszakok** (mindegyikre a közös alappiacok):

- Teljes meccs (Incl. Overtime)
- Rendes játékidő
- 1st Half
- 2nd Half
- 2nd Half (Incl. Overtime)
- 1st Quarter
- 2nd Quarter
- 3rd Quarter
- 4th Quarter
- 4th Quarter (Incl. Overtime)

**Időszakos kombók:**

- 1st half 1x2 & 1st half total
- 1st half handicap & 1st half total
- 1st quarter 1x2 & 1st quarter total
- 1st quarter handicap & 1st quarter total

**Speciális és meccspiacok:**

- Halftime/Fulltime
- Winning Margin
- Will There be Overtime
- Highest Scoring Half
- Highest Scoring Quarter
- Total touchdowns (incl. overtime)
- 1st field goal made (incl. overtime)
- Total field goals made (incl. overtime)
- Total turnovers (incl. overtime)
- Total sacks (incl. overtime)
- Team passing yards
- Team rushing attempts
- Minden további Team/Match és Specials piac, amit a Stake kínál

**Játékos-propok** (teljes meccsre, félidőre és negyedre is, ahol kínált):

- passing yards
- passing completions
- passing attempts
- longest passing completion
- rushing yards
- rushing attempts
- receiving yards
- receptions
- longest reception
- tackles
- tackles + assists
- assists
- sacks
- touchdown scorer piacok
- minden további Player Props / Leaders piac

## NHL – kötelező

**Időszakok** (mindegyikre a közös alappiacok + Both Teams to Score):

- Teljes meccs (Incl. Overtime and Penalties)
- Rendes játékidő
- 1st Period
- 2nd Period
- 3rd Period

**Speciális és meccspiacok:**

- Correct Score
- 1x2 & Total
- 1st Goal
- Last Goal
- 1st Period – 1st Goal
- 2nd Period – 1st Goal
- 3rd Period – 1st Goal
- 1st Period – Last Team to Score
- 2nd Period – Last Team to Score
- 3rd Period – Last Team to Score
- Will There be Overtime
- Highest Scoring Period
- Minden további goals / combo / specials piac

**Játékos-propok:**

- points
- goals
- assists
- shots
- powerplay points
- plus minus
- hits
- blocked shots
- faceoffs won
- kapus: saves, goals against, shutouts, win
- minden további Player Props piac

## MLB – kötelező

**Időszakok** (mindegyikre a közös alappiacok + Both Teams to Score):

- Teljes meccs (Incl. Extra Innings)
- 1st Inning
- Innings 1 to 5
- Minden további inning-tartomány, amit a Stake kínál

**Speciális és meccspiacok:**

- Total (Over-Exact-Under) (Incl. Extra Innings)
- Highest Scoring Inning
- Team With Highest Scoring Inning
- Team to Win More Innings
- Will There be an Extra Inning
- When will the 1st Run be Scored (incl. Extra Innings)
- Maximum Consecutive Runs by Either Team
- When will the Match be Decided
- Csapat to Bat in 9th Inning
- Correct Score / Winning Margin, ha kínált

**Játékos-propok:**

- dobó: strikeouts, hits allowed, earned runs, outs recorded
- ütő: hits, total bases, home runs, RBIs, runs, hits + runs + RBIs
- minden további Player Props piac

## NBA – kötelező

**Időszakok** (mindegyikre a közös alappiacok):

- Teljes meccs (Incl. Overtime)
- Rendes játékidő
- 1st Half
- 2nd Half
- 2nd Half (Incl. Overtime)
- 1st Quarter
- 2nd Quarter
- 3rd Quarter
- 4th Quarter
- 4th Quarter (Incl. Overtime)

**Időszakos kombók:**

- 1st quarter 1x2 & 1st quarter total
- 1st quarter handicap & 1st quarter total
- 1st half kombók
- Handicap (incl. OT) & Total (incl. OT)

**Speciális és meccspiacok:**

- Halftime/Fulltime
- Winning Margin (Incl. Overtime)
- Point Range
- Highest Scoring Quarter
- Will There be Overtime
- Race to X points, ha kínált
- Minden további combo / specials piac

**Játékos-propok** (teljes meccsre és időszakra, ahol kínált):

- points
- rebounds
- assists
- 3-point field goals
- points + rebounds + assists
- steals
- blocks
- double-double / triple-double
- minden további Player Props / Leaders piac

## WNBA – kötelező

Ugyanaz, mint az NBA-nál: minden időszak, kombó, speciális piac és játékos-prop, beleértve a következőket:

- Player points (incl. overtime)
- Player rebounds (incl. overtime)
- Player assists (incl. overtime)
- total 3-point field goals (incl. overtime)
- Point Range
- Halftime/Fulltime
- Winning Margin (Incl. Overtime)
- Winner & Total (Incl. Overtime)
- Handicap (including OT) & Total (including OT)
- 1x2 & Total
- 1st Quarter – Winning Margin

## Kötelező kimenet és ellenőrzés

1. **Ligánkénti lefedettségi tábla**, a futó rendszer adataiból generálva (nem kézzel). Megjelenik a dashboardon és fájlban (`docs/`).
   - Soronként: liga, időszak, pontos Stake-megnevezés.
   - Oszlopok: Stake kínálja / kiértékelve minden meccsen / pénzes kiadásra képes / ma kiadott pénzes ajánlások / elszámolva és tanulásba visszacsatolva.
2. **Meccsenkénti ellenőrzés minden ciklusban:** minden meccsen minden Stake-kínálatú kötelező típus ki van-e értékelve, és mi lett a kiadott legesélyesebb fogadás.
3. **A felügyelő agent** ezt élő adaton ellenőrzi. Ha bármelyik sorban vagy oszlopban hiány van, vagy a napi 100 nem tartható, javítási feladatot ad Astrának. Nem fogadja el késznek a munkát, amíg:
   - minden Stake-kínálatú kötelező típus minden oszlopa teljesül;
   - minden meccs teljesen ki van értékelve;
   - a napi minimum 100 pénzes ajánlás teljesül.

## Riport (magyarul, röviden)

- a lefedettségi tábla elérési útja;
- mit kötöttél be, mit fejlesztettél le, mit javítottál, commit hash-ekkel;
- a felügyelő agent pontonkénti állapota;
- ami még nem teljesül: a konkrét ok és a folyamatban lévő javítás.
