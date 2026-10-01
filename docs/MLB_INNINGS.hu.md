# MLB inningmodellek — 2026-09-29

## Mi készült el?

Két külön, sportadatból tanított modell: `inning_1` és `innings_1_5`. Nem a teljes meccs futásbecslésének arányos részei. Az adatgyűjtő az MLB hivatalos Stats API `schedule?hydrate=linescore` válaszainak tényleges inningeredményeit használja. A 2023–2026-os időszakból 9 832 mérkőzés azonosítója, csapatai, időpontja és végeredménye egyezik a meglévő sportadatokkal. Minden céladathoz megmarad a nyers válasz SHA-256 lenyomata, URL-je és lekérési ideje.

A név szerint illesztett Stake-piacok:

- `1st Inning - 1x2`, `1st Inning - Total`, `1st inning - Winner`;
- `Innings 1 to 5 - 1x2`, `Winner`, `Handicap`, `Total`;
- `Innings 1 to 5 - {team} Total`.

Az illesztő csak az adott modellnél jóváhagyott piaccsaládot engedi át, pontosan az API-ban szereplő névvel, kimenetellel, vonallal és oddsszal. Egész és fél vonal támogatott; a negyedes Asian-vonal megosztott elszámolása nincs engedélyezve. A 2026-09-29-i első ellenőrzött lekérés 4 MLB-meccsen 120 további inning-kimenetelt tartalmazott. Ez megfigyelt elérhetőség, nem 120 fogadási ajánlás és nem teljes honlaplefedettség.

## Modell és ellenőrzés

Mindkét modell ugyanazokat a cutoff szerint képzett, oddsmentes MLB sportjellemzőket használja, de külön inningcélra illesztett hazai/vendég Poisson-regresszióval. A túlzott szóródást negatív binomiális eloszlás kezeli. Három regularizáció × két eloszláscsalád közül csak a korábbi kiválasztási mintán választunk. A hazai és vendég runs feltételes függetlensége közelítés; teljes ütősor-, időjárás- és bullpen-korrekció még nincs.

Az időben elkülönített minták mindkét esetben: 5 832 tanító, 1 841 modellkiválasztási, 2 159 végső tesztmeccs. Eredmények (a kisebb jobb):

| Modell | Teszt joint log loss | Befagyasztott ligaátlag-alapmodell |
|---|---:|---:|
| Első inning | 1,93663 | 1,94230 |
| Első öt inning | 4,11028 | 4,12669 |

Piaccsaládonként Brier- és kontraktusonkénti kalibrációs hibakorlát is érvényesül. A háromutas osztályok előjeles hibáit nem átlagoljuk össze, mert azok tévesen kiolthatnák egymást. Az első, kiadás előtti ellenőrzési hibával készült illesztés megmaradt a `pre-release-calibration-audit` könyvtárban; nem került az élő keresésbe. A kijavított diagnosztikával készült két modell ment át a kapun.

Ezek szerény retrospektív prediktív javulások, nem historikus végrehajtható oddsokon igazolt profit. Az új `segment_prospective` tábla az első valódi előmeccses valószínűségeket változatlanul megőrzi, később a megfelelő inningeredménnyel értékeli.

## Kiadás, elszámolás, folyamatos működés

Az új modellek az eddigi 60 másodperces keresésben vesznek részt. Az EvidenceGuard az eseményen és számításon túl egyezteti a forecast, modellriport és piac időszakát. A teljes meccs modellje nem árazhat inningpiacot. A pozitív téttel kiadott ajánlás ugyanúgy 1–5 USD közé árazott, automatikusan saját döntésként rögzített, és az angol felületen Stake-eseménylinkkel jelenik meg.

A Winner döntetlennél push, az 1x2-ben a Draw nyer és a csapatválasztás veszít. A nulla hendikep és kétutas Winner, illetve a −0,5 hendikep és a megfelelő 1x2 csapatkimenetel azonos payoff-ját a rendszer nem adja ki kétszer eltérő piacnéven. Az eredeti azonosítók és naplótételek megmaradnak.

Az elszámolás kizárólag a megnevezett inningek ellenőrzött eredményéből történhet. A hiányzó alsó inning nem nulla. Félbeszakadt, rövidített vagy kivételes elszámolású meccs teljes bizonyíték hiányában nyitva marad, ellenőrzésre vár; a 4,5 inninges kivétel és a korán eldőlt Over automatikus kezelése még külön feladat. A hivatalos [Stake-szabályzat](https://stake.com/policies/sportsbook) az irányadó, a számított eredmény nem közvetlen számlaegyeztetés.

A külön learning worker naponta indít `segment_retrain` feladatot. A friss jelöltnek a diagnosztikai kapuk mellett a korábbi modellel közös időszakon, heti blokkokkal végzett páros összevetést is teljesítenie kell. Ugyanazon adatok ismételt illesztése nem írhatja felül a korábbi jóváhagyott archívumot. Sérült modellfájlt csak a jóváhagyott riporttal pontosan egyező archívum pótolhat; máskülönben a hiány látható marad.

## Ami még külön implementáció

| Terület | Hiányzó összetevő |
|---|---|
| NHL harmadok, gól-/lövéspiacok | Saját harmad-/eseménycéladat, kapus- és játékosállapot, külön validáció és elszámolás |
| NBA/WNBA negyedek, félidők, játékospiacok | Játékperc/rotáció, használat és játékonkénti céladat; önálló eloszlások |
| NFL negyedek, félidők, touchdown/yard piacok | Play-by-play, drive- és játékos opportunity modellek |
| MLB különleges piacok, játékospiacok | Ütősor/plate appearance és pitching opportunity; pl. első run, legtöbb inning, extra inning |
| Kombinált és élő piacok | Közös eseményszimuláció, valós idejű állapot és eltérő settlement |
| MLS | Önálló sportmodell; jelenleg kínálatleltár |
| Teljes önfejlesztés | A szolgáltatás újratanít és kutatást gyűjt, nem ír és telepít új modellkódot |

Egy elkészült szakirodalmi dosszié nem oldja fel ezeket a fejlesztési hiányokat. A napi minimum 15 új, minősített ajánlás és a nettó profit továbbra is mérendő cél, nem igazolt teljesítés.
