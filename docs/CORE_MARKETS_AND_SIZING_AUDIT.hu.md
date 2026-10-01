# Fő piacok, tétezés és reprodukálhatóság — 2026-09-29

Az audit a valódi adatbázist csak olvasta. A módosítások a release-14 staging
könyvtárban készültek; ez a dokumentum önmagában nem állít éles telepítést.

## Miért volt minden tét 2 dollár alatt?

A 2026-09-29 18:13 UTC körüli ellenőrzés 74 pozitív tétű, megőrzött
rendszerdöntést talált. Ebből 63 darab pontosan $1, a legnagyobb $1.52,
az eredeti ajánlott tétek összege $76.90. Ez nem a duplikált felhasználói
szelvények teljes pénzügyi forgalma. A 74 döntésből 32 teljes meccses
Winner/Handicap/Total, 42 más piac vagy részidős piac.

39 döntés őrizte már az újabb dinamikus tétezés részletes bizonyítékát;
ezekből 28 maradt $1. A többi 35 korábbi, fix egységes döntés. A dinamikus
döntések közül csak kettő született korábbi azonos meccses kitettség nélkül.
A meglevő azonos meccses szerződések száma 0–11 között volt.

Nem létezett rejtett $2 felső korlát. A képlet a stresszelt Kelly-erősségből
számolt egységet osztotta `sqrt(1 + related_contracts)` értékkel. Például
a WNBA `c2c6b9c133eb185665cd` Total döntésének stresszelt Kelly-értéke
0.33924 volt: más kapcsolódó fogadás nélkül $4.11, nyolc meglevő azonos
meccses szerződés mellett $1.37. Ez a korábbi döntés változatlan marad.

Az aktuális beállítás: bankroll nincs megadva; alapegység $1; maximum $5;
nincs napi összesített dollárkorlát és nincs meccsenkénti darabszámkorlát.
A tétezés bankroll nélkül deklarált heurisztika, nem személyre szabott
optimális Kelly-portfólió és nem igazolt profitnövelő eljárás.

## Javítás és mérhető viselkedés

- A minősített teljes meccses Winner, Handicap és Total elsőbbséget kap
  a tétkiosztásban. Így az ugyanabban a ciklusban felbukkanó részidős
  piacok nem pusztán a feldolgozási sorrend miatt csökkentik előbb a
  fő piacok egységét. A családon belül továbbra is a stresszelt várható
  érték választ; minden más minősített család jogosult marad tétre.
- Az érték szerinti rang és a kiosztási sorrend külön mező. Azonos
  számoknál stabil szerződésazonosító dönti el a sorrendet; a feed
  sorrendjének megfordítása nem változtatja a nyertes szerződést/tétet.
- A `stress-kelly-unit-v2` ugyanazt a számszerű tétezési görbét használja,
  de eltárolja az azonos meccses fogadások nélküli tétet, a kedvezmény
  előtti/utáni értéket, a szorzót és a kötő korlátokat. A szöveges
  indoklás konkrétan megmondja, miért lett például $3.87 helyett $1.29.
- 2.00 oddson, push és korábbi azonos meccses fogadás nélkül a stresszelt
  nyerési esély 51.5% / 55% / 60% / 65% / 75% értékén a tétek
  $1.22 / $2.23 / $3.16 / $3.87 / $5.00. Ezek képletellenőrző példák,
  nem valódi fogadási ajánlások vagy kalibrált bizalomkategóriák.
- A publikációs ellenőrzés újraszámolja a teljes tétezési bizonyítékot,
  és a bankroll/alapegység/Kelly-paraméterek nem cserélhetők ki a korábban
  ellenőrzött értékekhez képest. Hiányzó tétezési bizonyítékkal egy új
  kiosztott utasítás nem publikálható.
- Az eredeti kiadott tétek, árak, veszteségek és időpontok változatlanok.
  A duplikált payoff-alias továbbra sem második fogadás vagy hedge.

A `status.core_markets` minden ismert következő 48 órás meccshez három
audit-sort tartalmaz: kiértékelt/minősített kimenetek, kiválasztott pontos
szerződés és az esetleges hiány oka. A 2061-es eredeti publikáció
(2026-09-29T18:13:01Z) 20 meccsére számolt 60 sorból 15 `not_evaluated`,
37 `no_qualifying_price`, nyolc `already_held` volt. A `not_evaluated`
nem állítja, hogy a Stake nem kínál ilyen piacot: a forrás/modell/guard
rétegekben megkeresendő külön diagnózis. A küszöb alatti odds önmagában
nem programhiba és nem javítható kitalált előnnyel.

## D18: a hiányzó fejlesztési bizonyíték egy része megvolt

A D18 izolált audit NBA/WNBA esetén reprodukált régi teszteredményeket;
MLB/NHL/NFL esetén eltérő történeti bemeneti ujjlenyomatot jelzett.
NHL esetén a Winner valószínűségek maximális eltérése körülbelül 0.000350.
Ez nem bizonyított modellfájlsérülés, hanem nyitott reprodukálhatósági ügy.

A lokális eredeti archívumok ellenőrzése szerint az MLB/NHL/NFL korabeli
`models.py + context.py` SHA-256 értéke pontosan egyezik a telepített
modellek riportjával. Kilenc korábbi receptkísérlet teljes manifestje,
receptenkénti kiértékelése, eredménye és archivált forráskódja is megvolt.
Az izolált worker eddig ezeket nem kapta meg: ez tényleges, javított
fejlesztési bizonyítékátadási hiány volt.

Az új export kizárólag megengedett neveket másol, fájlméret- és
archívumszámkorláttal, symlink- és útvonalvédelemmel, a tanítókód hashének
és a tanulmányazonosítók egyezésével. Nem másol wager-adatbázist,
bejelentkezést, tetszőleges mappatartalmat vagy kísérleti pickle-t.
A valós exportpróba `data/ai_delegation/model-source-evidence-probe-14`
alatt nyolc befagyasztott modellt, nyolc igazolt tanítókód-forrást és
kilenc recepttanulmányt (91 tanulmányfájlt) adott át, kizárás nélkül.

Az eredeti tanításhoz használt teljes játékadat-pillanatkép továbbra sincs
igazolva. Konkrét drift: a régi NHL riport boxscore-lefedettsége 76.155%,
az audit mostani adataié 100%; NFL esetén 99.8783% → 100%. Az NHL adatokat
azóta a hivatalos forrásból kiegészítették. Az új adatok megcsonkítása
az egyező hash kedvéért nem reprodukció. MLB esetén a pitcher-lefedettség
azonos, a teljes tartalmi eltérés oka még nyitott.

Következő mérnöki feladat: a `train.py::train` és
`experiments.py::run_study` a fit előtt archiválja a pontos átadott
`Game`-adatokat tartalomazonosított, veszteségmentes tömörítéssel;
a riport őrizze a snapshot hashét, a forrás/hash metaadatokat,
a kódverziót, cutoffot és futtatókörnyezetet. A worker kapja meg ugyanazt
az igazolt snapshotot. Elfogadás: későbbi `games.json` módosítása után
is reprodukálható az archivált train/selection/calibration/test bemenet;
sérült/más hashű snapshot nem tölthető be; korábbi hiányzó snapshotról
nem születik hamis pótlás. A régi NHL ügyet az elérhető korabeli nyers
forrásarchívumok összevetésével kell külön lezárni.

## A teljes követelményhez még nyitott

- A professzionális teljes meccses családok mindegyikéhez nincs még
  explicit családonkénti validációs kapu. A régi D18 riport ezt helyesen
  különbözteti meg a már futó modell technikai képességétől.
- Pontos, szerep/kezdő/játékidő adatokkal validált player props, minden
  speciális/event-order piac, in-play modellek, SGM közös végrehajtható
  ár és függőségi modell, minden elérhető NCAA sport még nem teljes.
- A meccsenkénti darabszám-diszkont nem közös payoff-/kovarianciamodell.
  Nem számolja ki egy ellenirányú ráfogadás tényleges fedezeti hatását;
  ilyen profit- vagy kockázatcsökkentési állítás nem tehető. Ez a D16
  kitettségi modell konkrét további feladata.
- Napi legalább 15 külön új szerződés folyamatos teljesítése és nettó
  profit nem következik egyetlen jó napból vagy öt, azonos meccshez
  kapcsolódó nyertes döntésből. Az eredeti outcome tracking marad a
  teljesítmény mércéje; a darabszám nem pótolhat validált előnyt.

Ellenőrzés: a főpiaci/tétezési célzott tesztcsoport 113/113 sikeres;
az exportot és guardot is tartalmazó második célzott csoport 83/83
sikeres. A teljes release Python/API, DOM és éles publikációs ellenőrzése
a release tulajdonosának külön feladata.
