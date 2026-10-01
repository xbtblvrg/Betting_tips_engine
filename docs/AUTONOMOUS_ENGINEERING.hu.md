# Autonóm engineering intake

Az `engineering_loop.sync_engineering_loop` az észlelt hiányokat tartós, visszakereshető fejlesztési munkává alakítja. Közös célja a legerősebb, saját sportadatokból modellezett, friss és pontos Stake ajánlások javítása. A Winner/Moneyline, Handicap és Total értékelési hibái elsőbbséget kapnak. Más piacok, a források, a modellvalidáció, a komponensek és a korábbi fejlesztési hibák is a sorban maradnak. A minimum ajánlási mennyiség hiánya nem jogosít fel mesterséges edge-re vagy gyengébb ellenőrzésre.

## Működés

A fejlesztési koordinátor minden ciklusban, tervezetten 60 másodpercenként hívja:

```python
result = sync_engineering_loop(root, plan, state)
```

A függvény a publikációt kizárólag `mode=ro` SQLite kapcsolaton olvassa; az `operations.json`, a supervisor komponensellenőrzése, a `learning_review.json` és a fejlesztési állapot egészíti ki a bizonyítékokat. Nem indít hálózati kérést, modelltréninget, fogadást vagy kiadást, és nem írja a fogadási/development állapot-adatbázist.

A visszatérő `extra_tasks` listát a koordinátor saját állapotzárával, ID szerint egyesíti. A `task_focus` térképet az effektív feladatdefiníciókra kell alkalmazni: prioritás, eredeti határidő, gap ID-k, ligák, indoklás és bizonyítékkivonat. A részletes bizonyítékokat a koordinátor a munkaterületbe másolhatja. Az implementáció, a tesztek és a végső felülvizsgálat továbbra is a Codex feladata.

A külön `engineering.lock` folyamatok között is védi az intake saját állapotát. A JSON csere atomikus; a változási napló flush/fsync után tartós. A rendszer nem helyettesít sérült nyilvántartást üres fájllal. Sérüléskor a koordinátor látható hibát kap, a fájl és az eredeti határidők megmaradnak.

## Tartós nyomkövetés

- `data/development/engineering.json`: gap nyilvántartás, feladathoz rendelés és koordinátori prioritások.
- `data/development/engineering-evidence/<sha256>.json`: tartalom szerint címzett forráskivonat és feloldási bizonyíték.
- `data/development/engineering-events.jsonl`: létrehozás, ismételt észlelés, javulás és regresszió.

Az ID ligához és problématípushoz/piaccsaládhoz kötött; egy új meccs vagy ármozgás nem gyárt új feladatot. Az összes kapcsolódó liga/piac problémája először meglévő nyitott D-feladathoz kerül. Ha a seed csak jelöltként kész, egy integrációs feladat keletkezik. Egy véges feladatcsoporthoz egyszerre egy ilyen inkrementum tartozik. A jelölt elfogadása nem zárja le a production hiányt és nem okoz percenként új implementációt.

Az első észleléstől számított, változatlan célhatáridő kritikus megfigyelési/komponenshibánál 4 óra, kiemelt forrás/alapmodell hiánynál 24 óra, más hiánynál 48 óra. Ezek teljesítési célok; a határidő átlépése látható marad. Ismétlődés, újrapróbálkozás vagy regresszió nem tolja el a dátumot. Három külön publikációban jelentkező forráshiba kritikus prioritást kap; ugyanazon publikáció többszöri beolvasása nem számít új hibának.

## Mikor javult meg

Friss, későbbi pozitív bizonyíték kell. Egy korábban látott piac eltűnése, egy üres vagy hibás feed, egy elkészült kutatási leírás vagy egy `production_verified` szöveges állapot önmagában nem elég. Élő piaci feloldásnál minden korábban látott pontos piacnév/szekció jelenléte, aktív támogatása és tényleges értékelése szükséges; alternatív bizonyíték a később ismertetett külön, forráskötött root elfogadási receipt. Komponensjavulást friss egészséges audit igazolhat. Korábbi implementációs hibát csak a meglévő ellenőrző által igazolt, forrásfájlok hash-eihez kötött production receipt zárhat le. A nyitott tanulási/validációs követelmény nem tűnik el attól, hogy a következő learning feladatlistából kimarad.

A naponta megfigyelt fogadási hozamot továbbra is az eredeti lifecycle/outcome rendszer méri. Több kapcsolódó tipp ugyanazon meccsen nem több független eredmény. A benchmark feletti Brier eredmény, a kevés nyertes vagy az elkészült kód nem bizonyít tartós profitot. A folyamatos újraellenőrzés és a rögzített határidő nem garancia arra, hogy minden hiányzó külső forrás vagy modell validálható lesz a megadott időpontig.

## Ellenőrzés

A dedikált tesztek ismételt észlelést, valódi és hamis javulást, változatlan határidőt, regressziót, párhuzamos írásokat, sérült nyilvántartást, feladat-összevonást, core piac prioritást és learning benchmark-hiányt vizsgálnak. A fő rendszer állapotára a `status` csak megfigyelt hiányokat közöl; `full_engine_ready` értéke mindig hamis. A teljes kész állapotot külön, minden követelményt igazoló release/acceptance folyamat mondhatja ki.

Egy általános tanulási/validációs követelmény elfogadásához külön root oldali `engineering-releases/<sha256(gap_id)>.json` bizonyíték is használható. Kötelező a pontos `gap_id`, a `production_verified` állapot, a későbbi `verified_at`, sikeres ellenőrzések és az ellenőrzött fájlok SHA-256 listája. A feloldás a meglévő forráskötött receipt ellenőrzőt használja; módosított artifact, régi vagy jövőbeli receipt nem oldhat fel új hibát. A továbbra is aktuálisan észlelt hiba felülírja a completion állítást. A piaci támogatási, ligamodell- és önálló adatforrás-hiányoknál ez alternatív feloldási bizonyíték: az ellenőrzött implementáció és validáció akkor is igazolható, amikor a korábban érintett meccs már kikerült az időablakból. A puszta eltűnés továbbra sem feloldás, és az aktuálisan ismét megfigyelt hiba újranyitja a tételt az eredeti határidővel.
