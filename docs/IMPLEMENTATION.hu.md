# Működő kutatási kiadás — 2026-09-27

A kiadás saját, sportadatokból tanított ötliga-modellt és folyamatosan frissülő helyi webfelületet tartalmaz. A meccs előtti teljes mérkőzés győztese, félpontos hendikep és összpontszám piacokat értékeli. Ez működő kutatási rendszer, nem igazolt nyereséges szolgáltatás.

## Használat

Web: **http://127.0.0.1:8765** ezen a Macen. A bejelentkezéskor induló `hu.belavarga.betting-quant-engine` launchd-folyamat telepítve van. A gépnek bekapcsolva, ébren és internetkapcsolattal kell működnie. Nyilvános szerverre telepítés nem történt.

A főoldal a következő 48 órát, külön a mai és holnapi mérkőzéseket mutatja budapesti időben. Egy kártyán: liga, meccs, kezdés, pontos választás és vonal, saját nyerési esély, minimum decimális odds, javasolt USD-tét, referenciaár és frissesség. A feltételes jelölt nem egyenlő a tétet kapott ajánlással. A legalább tizenöt ajánlás napi kínálati cél; nincs mesterségesen létrehozott vagy ismételt alternatív vonalakkal feltöltött lista.

A rangsor először az érzékenységi forgatókönyvben is legalább a beállított előnyt mutató jelölteket választja. Ezen belül a stresszelt várható hozam, majd a nyerési esély rangsorol. Ciklusonként egy meccs/piaccsalád legjobb szerződését választja, a már tartottakat is beleszámítva. Új vonal vagy új piac ugyanarra a meccsre külön kiadható; nincs fix meccsenkénti darabkorlát. Ez nem teljes kovarianciamátrixos portfólióoptimalizálás.

A referencia-platform minimuma külön szerepel. Ha a javasolt 1 USD ennél kisebb, a kártya jelzi, hogy az adott referenciaoldalon nem végrehajtható ilyen tét: azonos szerződést, megfelelő minimumot és legalább a küszöbárat nyújtó másik platform kell. A tétet nem emeljük automatikusan a platform minimumára.

A részletek között másik platformon talált odds is ellenőrizhető. Ez a beírt árra számol, annak külső elérhetőségét nem ellenőrzi. A „Megtettem a fogadást” funkció kizárólag a ténylegesen, manuálisan elhelyezett fogadás naplózására szolgál.

## Adat és modell

| Liga | Saját bemenetek az általános csapatformán és Elo-n túl | Jelenlegi hiány |
|---|---|---|
| NBA, WNBA | Becsült labdabirtoklás, támadó/védő hatékonyság, eFG, labdaeladás, támadólepattanó és büntetőarány | Játékospercek, lineup, számszerű sérüléshatás |
| MLB | Hivatalos kezdődobó-azonosító, korábbi dobószereplésekből zsugorított ERA, WHIP, K%, BB%, HR%, megfigyelt ütők | Ütősor, bullpen-terhelés, park, időjárás, dobócsere forgatókönyvek |
| NHL | Lövések, lövésarány, védési és lövési arány, emberelőnyös gólok | Lövésminőség/xG, kezdő kapus és TOI |
| NFL | Yard/játék és engedett yard/játék, passz/futás, labdaeladás, harmadik kísérlet, játékszám | QB-hatás, drive/EPA, időjárás és kulcsszámok részletes eloszlása |

Források: MLB StatsAPI; NHL hivatalos API; sportsdataverse publikált ESPN/NHL adatkészletek; nflverse. A modell csak név szerint engedélyezett sportjellemzőket kap. Odds, spread, záróár és piaci rating nem kerül az esélymodellbe.

A pillanatnyi sérülésjelentés forrással és dátummal látható. Nem kap önkényes, betanítatlannak számító százalékos korrekciót. A historikus kezdődobó-kijelölések és sérüléshírek teljes korabeli verzióarchívuma nem áll rendelkezésre; ezt a retrospektív vizsgálat korlátjaként kezeljük.

A sportmodellek külön tanított, regularizált logisztikus regresszióból, gradient boostingból és Elo-becslésből álló keverékek. A keveréksúly külön időszakon, a kalibráció egy még későbbi időszakon illeszkedik. A score-modell regularizált regresszió, a ponteloszlás páros empirikus hibákból készül; egész, nemnegatív pontokra és a kalibrált győzelmi tömegre egyeztetett. Az alternatív hendikep/total kalibrációját a győztesmodell pontossága önmagában nem igazolja.

A történeti befejezési idő hiányában a formába kerülés konzervatív szabálya: a kezdés után nyolc óra és a következő UTC-napkezdet közül a későbbi. Ez időnként késlelteti a friss eredmény felhasználását, de megakadályozza az éjfélen átnyúló meccsek eredményének korai bekerülését. A megszakított, később folytatott meccsek kimaradnak ebből a teljes meccses modellből.

## Ellenőrzés és tanulási kör

1. Sportadatok és forrásállapot frissítése; box score és dobó-game-log illesztés pontos meccs/csapatazonosítókkal.
2. Sportesélyek számítása a piacok lekérése előtt.
3. Az esemény, a két csapat, kezdés, vonal, periódus, díj és elszámolás egyeztetése.
4. Stake ligánkénti tournament- és fixture-odds lekérdezés; pontos teljes meccses piac, aktív kimenetel, vonal és decimális odds ellenőrzése. A rosszul párosítható, részidős, felfüggesztett és ellentmondó piacok kizárása. Egész vonalnál a sportsbook push tétvisszatérítését számoljuk.
5. Rangsor és tét; tranzakciós publikálás; a web 3 másodperces eseményfigyeléssel értesül az új kiadásról. Alap piaci ciklus 60 másodperc; mind az öt liga tournament feedje ellenőrzésre kerül.
6. Lejárt/visszavont tippek eltávolítása, új/változott/visszavont események naplózása.
7. Tényleges fogadások és előre rögzített első meccsbecslések visszamérése.
8. Legfeljebb napi újratanítási jelölt. Közös, időben elkülönített tesztmeccseken legalább 150 megfigyelés és 12 hét, páros heti blokkbootstrap szerint kedvező Brier-különbség kell a modellcseréhez. Ez retrospektív összehasonlítás, nem profitigazolás és nem teljes független jövőbeli teszt.

A meglévő modell paraméterei az értékelésben használt befagyasztott paraméterek. A csapatállapot közben frissül. A program nem írja át önállóan saját kódját, és nem emel tétet egy vesztes sorozat miatt. A modellverzió adat- és kódazonosítót tartalmaz; az artefakt és a tanítókód archivált.

A retrospektív riportok fejlesztési eredmények: több változatot vizsgáltunk. Az érintetlen jövőbeli bizonyítékot az indulás óta előre, felülírás nélkül rögzített becslések gyűjtik. A konstans és Elo-alapmodellek a weben összehasonlíthatók. Történeti végrehajtható oddsokon számolt ROI és pozitív élő hozam még nincs.

## Tét és pénzügyi eredmény

Bankroll nélkül az alapértelmezett tét 1 USD, nincs fix összesített napi keret (daily_risk_limit=null), a fogadásonkénti maximum 5 USD. A maximumot nem emeli automatikusan a rendszer. Ezek módosíthatók a weben. Bankroll esetén 1/8 Kelly, legfeljebb a bankroll 1%-a és a maradék keret korlátoz. 1 USD alatti számított tétet nem kerekítünk felfelé. Ha később a felhasználó összesített napi keretet állít be, a már ma rögzített, akár lezárt tétek és a korábbról nyitott fogadások is fogyasztják azt. Új, külön értékelt legjobb vonal ugyanarra a meccsre kiadható. Az azonos szerződés árváltozása nem indít új fogadást.

A stressz-esély az érzékenységvizsgálat része, nem a valódi esély konfidenciaintervalluma. A Stake szerinti méretezés a push tétvisszatérítését veszi figyelembe. A bookmaker-push és a Polymarket fél kifizetése külön szerződés.

A felhasználó állandó utasítása szerint a pozitív téttel publikált Stake-ajánlás automatikusan feltételezetten megkötött naplótétel lesz; a naplóban az eredeti ajánláskori odds és tét marad meg. A végeredményből automatikus won/lost/push elszámolás készül, valamint az eredeti valószínűség Brier/log loss visszamérése. Az automatikus tételek P&L-je becsült, nem Stake-számlával egyeztetett. A kézzel naplózott tételek külön kimutatást kapnak. A teljes havi rendszerköltség megadható; hiányában nincs költség utáni eredményállítás. A Polymarket részvényügyletek nem kerülnek sportsbook-elszámolásba. Az NFL forrásnál a két pontszámon felül legalább öt óra elteltét is megköveteljük a kezdéstől, mert a CSV-ben nincs külön megbízható befejezési idő. [Részletes lifecycle-szabályok](BET_LIFECYCLE.md).

## Üzemeltetés

```bash
cd /Users/belavarga/Betting_tips_engine
.venv/bin/python scripts/service.py status
.venv/bin/python scripts/service.py stop
.venv/bin/python scripts/service.py install
```

Kézi indítás: `scripts/run-web.command`, ha a szolgáltatás nem fut. Egyszerre egy példány használja a 8765-ös portot. Friss környezethez: `uv sync --extra dev` a verziózárral. Modellkísérlet: `PYTHONPATH=src .venv/bin/python -m betting_engine.train --controlled`; a `--refresh` a sportadatokat is frissíti.

Adatbázis: `data/engine.sqlite3`; modellriportok: `data/models`; nyers forrásgyorsítótár: `data/raw`; szolgáltatásnaplók: `data/logs`. A nyers történeti cache legfeljebb hét nap/1 GiB, a quote-cache harminc nap/256 MiB, a legfrissebb URL-példányok megtartásával. A tippek, változási események és tényleges fogadások megmaradnak. A korlát miatt nem minden régi teljes API-válasz marad örökre újrafeldolgozható; a döntéshez tartozó mezők és forrásazonosítók az auditnaplóban megmaradnak.

A szolgáltatás csak localhoston figyel. Telegram-küldés nincs bekötve; az esemény-outbox elkészült, de nem küldött üzenetet. A szoftver semmilyen fogadást nem ad le.

## Még elvégzendő az éles minősítéshez

- Teljes player/lineup/kapus/QB és aktuális időjárási hatás, korabeli tanítóadatokkal.
- További bookmaker-adatkapcsolat hiteles árakkal; jelenleg Stake Sports Data API az aktuális ajánlások forrása. A fiókspecifikus minimumot és elfogadott árat a kézi fogadásnál kell ellenőrizni.
- Sportspecifikus score-mechanizmusok, valódi piaci vonalakra végzett kalibráció és független jövőbeli nyereségvizsgálat.
- Záróár-rögzítés/CLV, teljes korrelációs portfólió és részvényalapú Polymarket-napló.
- A megkezdődött mérkőzésekre külön in-play modell. A jelenlegi kiadás meccs előtti; megkezdett meccset nem címkéz friss pregame ajánlásnak.
- Telegram és igény esetén folyamatosan működő távoli szerver. A Mac alvása alatt ez a helyi folyamat nem dolgozik.
- Vizuális böngészős ellenőrzés: a számítógépes Accessibility/Screen Recording engedélyezése akadályozta. API- és DOM-funkcióteszt készült, képi ellenőrzés nem igazolt.
