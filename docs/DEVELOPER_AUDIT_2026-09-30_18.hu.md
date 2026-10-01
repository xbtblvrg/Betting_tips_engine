# Kiadás 18 — több ténylegesen értékelt piac és kötelező modellminőségi ellenőrzés

Ellenőrzés: 2026-09-30, 14:15–14:18 Europe/Budapest. Éles projektkövetelmény-verzió: `2026-09-30.18`.

## Elkészült és élesben ellenőrzött változások

- A Stake handicap-parser pontos kulcsok alapján dolgozza fel a specifikációt. A `periodnr=1|hcp=-0.5` és `quarternr=1|hcp=0.5` mezősorrend többé nem rejti el a már támogatott piacot. Duplikált kulcs, ellentmondó szakasz, előjel vagy kimenetel továbbra sem elfogadható. A negyedvonalas Asian-contract nem kerül félvonalas árazás alá.
- Befagyasztott valós feed visszajátszásán 1098 → 1172 feldolgozott standard kimenetel: +66 NHL és +8 NFL handicap. Minden korábbi Market-rekord változatlan. Mind a 74 új tétel átment a korábbi scope-/input-ellenőrzéseken; a vizsgált árakon nulla teljesítette az értékküszöböt. Ez több elemzett lehetőség, nem 74 új fogadási utasítás.
- A professzionális teljes meccses Winner kapuja újraszámítja a befagyasztott tesztpredikciók Brier- és log-loss értékét, ellenőrzi a darabszámot és az egyedi meccsazonosítókat, majd összeveti a riport Elo-eredményével. A jelenlegi NBA/NFL Winner ismert regressziója megállítja a kiadást. A kifizetésazonos handicap-alias nem kerülheti meg ezt.
- A minőségi bizonyíték tartalomhashhez kötött, bekerül a már lezárt input-readiness bizonyítékba; a publikálás és az aktuális végrehajthatósági ellenőrzés is megköveteli. Az eredeti fogadási pecsétek és tétek formátuma megmaradt. Ugyanazon modellverzió megváltoztatott riportja új ellenőrzést kap.
- A Total/Handicap külön családdiagnosztikát kap; a Winner eredménye nem válik automatikusan e piacok validációs ítéletévé. A jelenlegi szintetikus tesztvonalak nem igazolják a tényleges bookmaker-vonalak nyereségességét. A hiányok ligától és aktuális meccskínálattól függetlenül, eredeti határidőt őrző D18-feladatként látszanak.
- Három csak olvasó SQLite-útvonal explicit lezárja a kapcsolatot: release-fingerprint, release-live-observation, fejlesztési publikációolvasó. A korábbi kód 128 fájlleíró-limitnél hibázott; a javított három olvasó 400 ismétlés után is 3 → 3 nyitott leíróval, változatlan adatbázissal működött.

## Validáció és élő állapot

1796 Python-teszt és 5 alteszt, 64 DOM-teszt sikeres; nulla kihagyott teszt. A hat forrásfüggő NFL-teszthez 11 ténylegesen meglévő, másolt és hash szerint ellenőrzött fájlt biztosítottunk. Külön független Codex-review a parserhez, a minőségi root-bekötéshez és a kapcsolatlezáráshoz. A review által jelzett cache-hiba javítva és regresszióteszttel lefedve.

A 3056-os éles publikáció helyben és ellenőrzött Tailscale HTTPS-en HTTP 200. Az új minőségi kapu két NFL Winner-kimenetelt utasított el; 1167 jelöltből 1165 átment a technikai ellenőrzéseken. A 3058-as publikáció tényleges DOM-renderelése 10 instrukciót, 5 meccset és 10,67 USD össztétet mutatott, pontos Stake-linkekkel, hibás Winner-kártya, `undefined`, `Recheck Required` és írási művelet nélkül.

Napi első kiadások: 47/15 (MLB 24, NFL 6, NHL 8, WNBA 9). Ez a teljes napi kibocsátás, nem az új javítás által létrehozott fogadások száma, nem független meccsek és nem nyertes tippek száma. Korábban kiadott hibás döntés sem törlődött.

Az összes korábbi 129 szelvény, 128 saját döntés és 121 első kiadás eredeti mezői hash szerint megmaradtak. Az engine, learning, development és release szolgáltatások a felügyelő szerint egészségesek. Három Codex-fejlesztési feladat fut; az összesített rendszerállapot továbbra is részleges, mert léteznek forrás-/képességhiányok. A `fully_ready` nem vált igazzá.

48 órás éles pillanatkép:

| Liga | Visszaadott Stake-meccs | Guardon átment modellezett kimenetel |
|---|---:|---:|
| MLB | 4 | 251 |
| NHL | 11 | 784 |
| NFL | 1 | 86 |
| WNBA | 3 | 44 |
| NBA | 0 | 0 |
| NCAAF/NCAAM/NCAAW | 0 | 0 |
| MLS, kiegészítő leltár | 2 | 0 |

Az NBA/NCAA nullája ebben a visszaadott feedablakban nem kihagyott elérhető meccset jelent. Az MLS-nél tényleges modell- és független menetrendi hiány van.

## A korábbi utasítások újraellenőrzése

| Követelmény | Ellenőrzött állapot |
|---|---|
| R01 független sportmodell | Implementált; az odds utólagos összevetés. A teljes modellminőség részleges. |
| R02 szakirodalom | Tudáskártya- és forráshivatkozás működik; e kiadás nem állít új teljes könyvolvasást. |
| R03 ligák, frissítés | 60 másodperces frissítés; NCAA dinamikus leltár. Minden piac modellezése még hiányos. |
| R04 pontos Stake-kontraktus | Aktív, pontos ár/scope/outcome ellenőrzése; szakaszhandicap-feldolgozás javítva. |
| R05 napi minimum 15 | Ma 47 első kiadás; jövőbeli napi teljesítést nem bizonyít. |
| R06 tétek | 1–5 USD, nincs rögzített napi összplafon, nincs automatikus emelés. |
| R07 saját döntésnapló | Eredeti rekordok és veszteségek megmaradnak; hash-ellenőrzés sikeres. |
| R08 feltételezett manuális fogadás | Megmaradt; nincs account-verifikáció vagy automatikus megbízás. |
| R09 angol felület, checklist | 64 DOM-teszt, aktuális élő lista és időrend ellenőrizve. |
| R10 elérés/Telegram | Local és Tailscale működik; Telegram későbbi szakasz. |
| R11 modellezés/újratanítás | Külön tanulási folyamat és megőrzött elutasított kísérletek; javulás nem automatikus. |
| R12 teljes leltár | Standard/Swish leltár működik, a modellezett rész külön számláló. |
| R13 tiszta végrehajtási lista | Csak aktuális szerverellenőrzött instrukciók; régi rekordok külön. |
| R14 tartós learning munkák | Szolgáltatás és próbálkozási napló működik; kutatási hiány nem kész funkció. |
| R15 receptkeresés | Korábbi adatokon választás, külön challenger; elutasított eredmény megmarad. |
| R16 24/48 órás lefedettség | Azonos publikációból származó ligánkénti számok ellenőrizve. |
| R17 felügyelet | Négy szolgáltatás egészséges; részleges képességek miatt összesített degraded állapot helyes. |
| R18 Stake-gomb | Élő DOM-ban pontos eseménylink; nem kitöltött bet slip. |
| R19 újraellenőrzés | Frissesség, pecsét, tét és új családminőségi kapu élesben. |
| R20 hiányfeltárás/javítás | Állandó intake; 22 pontos modellcsalád-hiány D18-hoz kötve. Nem mind javított. |
| R21 MLB inning | Külön meglévő inning-modellek és scope-elszámolás; nem helyettesíti full-game eredmény. |
| R22 pontos Stake-tab | Változatlan feedbizonyíték; új parser a tabokat megőrzi. |
| R23 minden piac támogatása | Nem teljesített: props, speciális, SGM, live és egyes szakaszok továbbra is hiányosak. |
| R24 hivatalos API/NCAA | Teljes megőrzött végpontválaszok és 48 órás diagnózis; nem a teljes weboldal-kínálat bizonyítéka. |
| R25 felügyelt fejlesztés | Három sáv, fix próbálkozási bizonyíték, független review; FD-elakadás javítva, más blokkolók maradtak. |
| R26 Codex elsődlegesség | Megmaradt; segéd-AI nem hagy jóvá fogadást vagy éles kiadást. |
| R27 párhuzamos munka | Három izolált részfeladat, ellenőrzött root-integráció; éles fejlesztő három aktív sáv. |
| R28 közös cél/önfelügyelet | Több pontos piac és valós minőségi hiba felismerése éles; a profit nem igazolt. |
| R29 fő piacok/tétképzés | Winner/Handicap/Total elsőbbség megmaradt; a közös portfólió-optimalizálás még hiányos. |
| R30 záró audit | Ez a dokumentum és a hivatkozott gépi bizonyítékok. |

## Következő szükséges fejlesztések

1. D18: az NFL/NBA Winner minőségének javítása időrendileg elkülönített modellellenőrzéssel. A régi NFL/NHL eredeti fit-bemenete hiányos; javított mai történet nem helyettesítheti azt. A korábbi, javulást nem igazoló challenger nem kényszeríthető élesbe.
2. Tartós, minden értékelt pontos fogadási vonalat tartalmazó prospektív tanulási napló és scope-helyes outcome összekapcsolás. A jelenlegi hétnapos publikációk, saját fogadási napló és korlátozott Winner/three-way prospektív mérés nem teljes pontos-piac kalibrációs adatkészlet.
3. D21/props: az 550729 történeti játékosmegfigyelés mellé meccsenkénti részvétel és szolgáltatók közötti azonosítás szükséges. Adatgyűjtés önmagában nem modelljóváhagyás.
4. D06/F0023/F0024: az egymásra utalt passzív utility- és bizonyítékcsomag közös, hash-ellenőrzött root-integrációja szükséges; ettől még nem aktiválható játékosfogadás.
5. A sérülések/kezdőcsapatok teljes számszerűsítése, pontos-vonalas családvalidáció és kapcsolódó fogadások közös kockázati modellje továbbra is nyitott.

## Bizonyítékok

- `data/quality-coverage-20260930/install-manifest.json`, `python.log`, `dom.log`, `test-source-evidence.json`.
- `data/quality-coverage-20260930/live-verification.json`, `live-dom-proof.json`, `ledger-before.json`.
- `data/coverage-recovery-20260930/parser-stage/validation/replay.json` és `coverage-audit.json`.
- `data/flow-recovery-20260930/release-fd-stage/fd-reproduction.json`, `audit-and-integration.json`.
- `data/nfl-quality-20260930/stage/actual-report-probe.json` és `owned-files.json`.

Az eredmény működő bővítés és minőségellenőrzési javítás. Nem a teljes engine készültsége vagy igazolt nettó nyereség.
