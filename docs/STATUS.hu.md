## 2026-10-03 — D38: Liga és fogadástípus szerinti modell- és rendszereredmény

Éles kód `e310fb1`. Statistics → Engine and Model Performance by Segment: liga, scope × piaccsalád és liga × scope × piaccsalád bontás. Az eredeti döntések pénzügyi eredményei külön táblában; mellettük eredeti valószínűségből számolt becsült/tényleges arány, Brier, log loss, meccsenként egyenlő súlyú Brier, értékelt szerződések/meccsek, kizárási okok és pontos szegmenshez kapcsolt meglévő fejlesztési feladatok. A duplikált jegyek nem növelik a modellmintát. Az oldal szűrői ugyanazt a Statistics-pillanatképet használják; a fejlesztési találatok teljes történeti hatóköre külön jelzett. CLV/piaci összehasonlítás a meglévő Model Performance nézetben, saját lefedett mintával.

Teljes teszt: **6322 Python passed, 1 skipped, 5 subtests; 275 frontend passed**. Független Codex review: nincs blokkoló hiba, célzott10/10 teszt. Helyi és Tailscale éles HTML/JS hash-egyezés és tényleges API-adatból DOM-render: **78 liga/scope/család, 5 liga, 58 scope/család** sor, mindkét táblában az API-val egyezően. Pillanatkép 10:45:29–30 UTC:377 lezárt eredeti döntés,44 meccs. A megjelenítési hiány javítva; nincs módosított funding, méretezés, ledger vagy modell, és nincs állított WR/ROI-javulás. A meglévő outcome-review53 diagnosztikai találatot mutat, attention_required állapottal; feladat létrehozása nem egyenlő javítás élesítésével.

Bizonyíték: `data/releases/root-segment-performance-20261003/{live.json,review.json,smoke.mjs,segment-performance-python.log,segment-performance-web.log,segment-performance-sizing.log}`. Következő érdemi munka: a meglévő vesztes szegmensek kísérleteinek és fejlesztéseinek lezárása, prospektív ellenőrzéssel. Piacmodell-lefedettség e UI-kiadásban változatlan.

## 2026-10-03 — Régi adatmásolatok tényleges eltávolítása és időzített megőrzés

Kód `63339c0`; korábbi napló/COW kiadás után külön archiválási javítás. 14 használaton kívüli, legalább 48 órás backup/teszt SQLite-példány: 37 458 870 272 bájt helyett 4 915 016 745 bájt gzip maradt; az eredeti kibontott másolatokat teljes visszaolvasási SHA256-ellenőrzés, fsync, nyitott-fájl- és WAL/SHM/journal-ellenőrzés után eltávolítottuk. Az éles adatbázisok nem kerülhetnek az allowlistbe.

A fejlesztési takarítás 399 régi, felülírt retry/interrupted munkapéldány adatkönyvtárát választotta ki. 10:30 UTC-kor 125 feldolgozott: 79 070 348 433 bájt eredeti adat helyett 18 197 421 860 bájt ellenőrzött tar.gz. A futás ekkor még tart; hat pillanatnyilag megnyitott munkapéldányt változatlanul kihagyott. Kód/docs, runnaplók, accepted-files, aktuális/hivatkozott/legutóbbi feladatok, candidate_ready és a modell-/döntési archívumok megmaradnak. Minden eltávolítás előtt/után ellenőrzött fájlleltár, tartalom, aktuális hivatkozások és tényleges processzhasználat; a megváltozott vagy használatba vett példány visszakerül. Már létező archívum csak teljes egyezés után használható újra.

Telepített időzítés: `hu.belavarga.betting-workspace-retention`, óránként legfeljebb 20 jogosult munkapéldány, háttér-prioritás/Nice10/LowPriorityIO. Közös kizáró zár; az éles próba helyesen `busy` állapotban kilépett a folyamatban levő nagy köteg mellett. A saját stdout/stderr naplók is 16 MiB-os, hét gzip-archívumos rotációt kapnak. Az aktuális fejlesztési folyamatokat nem kell leállítani; célmunkapéldány-szintű védelmek maradnak. A tömörített bemeneti archívumokat ez a szabály nem törli; a művelet a felesleges kibontott másolatok megőrzését korlátozza.

Elfogadás: 6322 Python-teszt sikeres, 1 skip, 5 subtest; 273 frontend-teszt; független Codex review és root végső review. 740 korábban megőrzött eredeti döntési payload hash-e változatlan. Helyi/Tailscale health OK, engine error=null. Szabad hely a friss méréskor 262 346 674 176 bájt; a teljes rendszer párhuzamos lemezaktivitása miatt ez nem kizárólag a takarítás megtakarítása. Bizonyítékok: `data/releases/root-storage-bounds-20261003/{offline-databases.jsonl,workspace-retention.jsonl,retention-live-check.json,schedule.json,python-final-retention.log}`. A munkapéldány-köteg előrehaladását a JSONL élőben rögzíti; a 399 példány teljes befejezése nincs ebből a köztes állapotból készre jelentve.

## 2026-10-03 — Tárhelyfelszabadítás és supervisor-naplórotáció

Éles kód: `93eb414` (`cbae22d` naplórotáció). A supervisor teljes állapota továbbra is a meglévő `data/supervisor.json` fájlban marad; stdout rövid összefoglaló. A `supervisor.log` és `supervisor-actions.jsonl` 16 MiB fölött gzip-archívumba kerül, naplónként hét archívum megőrzésével. Az archívum fsync-je megelőzi az append-deszkriptorral azonos inode rövidítését. A JSONL-akciórekordok teljes tartalma megmarad a megőrzési időn belül.

Az engedélyezett operátori eszköz 24 óránál régebbi fejlesztési másolatok három rögzített JSON-bemeneti útvonalán APFS copy-on-write deduplikációt végez. A fejlesztési/kiadási worker leállítását és folyamatmentességet ellenőrzi; mindkét szolgáltatás visszaindult. 1115 fájl cseréje, nulla hiba; minden csere SHA256-azonosság-, metaadat- és változatlanság-ellenőrzés után történt. Sem fájlútvonal, sem tartalom nem veszett el; későbbi írások függetlenek. A szabad hely a művelet alatt 14 538 698 752-ről 48 905 412 608 bájtra nőtt (+34 366 713 856 bájt; az egyidejű naplórotációt és egyéb lemezaktivitást is tartalmazó megfigyelés). A workspace könyvtár 756 GiB logikai du-értéke APFS-megosztás mellett nem azonos a kizárólagos fizikai foglalással. A 92 GiB shadow- és 15 GiB engine-adatbázist nem módosította ez a karbantartás.

Validáció: 5763 tracked és 540 meglévő untracked Python-regresszió sikeres, 1 skip, 5 subtest; 273 frontend-teszt sikeres. Független Codex review a napló- és deduplikációs változtatáson; valódi APFS-, xattr-, független-írás-, symlink- és hibaút-tesztek. Éles rotáció: 1 769 097 683 és 37 392 596 bájt eredeti napló visszaolvasható a gzip-archívumokból. Helyi/Tailscale HTTP200, engine error=null, egészséges feed, publikáció 6689. A supervisor időzített futása exit0; development/release ismét fut.

Bizonyíték: `data/releases/root-storage-bounds-20261003/{python.log,python-existing-untracked.log,web.log,web-sizing.log,deduplication.log,live-check.json}`. A régi workspacemegosztás egyszeri, ellenőrzött karbantartás; az automatikus rotáció minden supervisor-futás része. Más naplók/adatbázisok megőrzési szabálya nem változott.

## 2026-10-03 — Napi40 cél és eredményalapú tétezés élesben (D37)

- Éles kód: `7624940`; aktív `daily_pick_target=40`, `outcome_sizing_policy=historical_game_weighted_v1`. Nincs számláló-reset, liga- vagy piaccsalád-szűkítés. A régi 20-as űrlap nem csökkentheti vissza az aktív40 célt.
- Új tétek: eredeti, lezárt rendszer-döntések liga × scope × canonical allocation family szerinti, meccsenként egyenlő súlyú ROI-ja és oddsokhoz mért WR-többlete módosítja az alapméretezést. Pontszám/meccs: `min(1, ROI/0.20, (WR−break_even_WR)/0.10)`; szorzó: `1+max(0, sum(score)/(n+20))`, pozitív szegmens-ROI és WR-többlet mellett. Kis minta folytonosan zsugorított, vesztes bizonyíték nincs nullára vágva. Ez dokumentált heurisztika, nem valószínűség- vagy profitigazolás. Bankroll esetén az eredeti jóváhagyott méretezési korlát köt.
- A ciklus as_of időpontja előtti eredményekből készülő összegzés guarddal lezárt, publikáláskor az immutable táblából újraellenőrzött. Alapösszeg/szorzó/korlát/végösszeg/hash megőrzött. A USD1–5 sáv, max5, daily_risk_limit=null és minden kitettségi/funding guard változatlan; nincs automatikus fogadás vagy eredeti tétek utólagos emelése.
- Független Codex-review és root végellenőrzés; Claude Opus5.5, agy és Grok külön tanácsadói ellenőrzése hash-hez kötve. Teljes teszt: **6290 passed, 1 skipped, 5 subtests; frontend273 passed**. A negatív vegyes meccs és kis mintás túlemelés ellenpéldái javítva, tesztelve. A korábbi hibás tesztfutás megőrzött.
- Az első rollout dashboard-méretkorlátba ütközött; átmeneti legacy-visszaállítás után a megjelenítési projection javítva. Teljes ellenőrzés továbbra is az eredeti bizonyítékon fut; a böngésző összegzést és szakirodalmi hivatkozásokat kap. A 64MiB korlát változatlan; későbbi éles6664 válasz **49.83MiB**, hiba nélkül. Az eredeti503 pontos teljes pillanatképe nem reprodukált; a hiba és helyreállítás megőrzött.
- Helyi és Tailscale6662: **13/13 Ready to Bet;14/40 napi első kiadás,26 hiány**. A következő6664 publikáció is13/14/40. 3218 jelölt teljes guard- és eredményforrás-visszaellenőrzése0.878s.377 lezárt eredeti döntés,72 szegmens;63 aktuális read-only tétértékelésből41 használ1 feletti szorzót. Ezek nem ráfogadási utasítások. Új pénzes első kiadás ebben az ellenőrzött ciklusban0.
- Lefedettség előtte/utána:411 katalógussor,157 támogatott kimenetű és150 ténylegesen kiértékelt/guardon átment sor mindkét oldalon. Új piacmodell nincs e kiadásban.741 eredeti szelvény,740 eredeti döntés,733 kiadás,378 meglévő eredmény és a checkpoint változatlan. Következő munka: a napi26 hiány és a meglévő modell/input hiányok megoldása; a cél nem tekinthető40/40 teljesítettnek.
- Bizonyíték: `data/releases/root-outcome-sizing-20261003/release.json`, `live-check.json`, `live-evidence.json`, `live-response.json`, `coverage.json`, `ledger-after.json`. Mért későbbi WR/ROI-javulást ez a kiadás még nem állít.

## 2026-10-03 — Eredményvisszacsatolás prioritásjavítása élesben (D36)

- Kód: `bbae923`; `bounded-learning-fairness-v2`. A legalább 300 másodperce futtatható outcome/closing feladat előresorolódik a source refresh/shadow/background munkák elé. A tényleges model/segment/period restore, retry-backoff és egyetlen OS-lock megmarad; ez prioritásküszöb, nem 300 másodperces befejezési garancia.
- Független Codex ACCEPT, 64 célzott teszt. Teljes Python: 6242 passed, 1 skipped, 5 subtests; frontend: 272 passed. Egy korábbi CLI-startup teszt időtúllépése megőrizve; külön6/6, majd teljes6242/6242 sikeres, változatlan tesztekkel. Az átmeneti hiba pontos külső oka nincs megállapítva.
- Biztonságos worker-újraindítás 08:44:57 UTC: OS-lock megszerezve, 0 futó/megszakított job. V2 élő admission 08:45:17 UTC, valódi1768 attempt sikeresen completed. A teljes meglévő háttérforgás működik; ekkor nem volt overdue feedback, annak előresorolását regressziós teszt igazolja.
- Helyi/Tailscale publikáció6609:13/13 végrehajtható kártya,14/20 napi első kiadás, error=null. 741 eredeti szelvény,740 döntés,733 kiadás,378 eredmény változatlan. Bizonyíték:`data/releases/root-feedback-fairness-20261003/release.json`.
- A helyreállított377 eredeti lezárt döntés outcome_review-ja 08:21:27 UTC-kor befejeződött (ef6845202c7307a5e913cbca). Ez eredménydiagnosztika, nem automatikus modellpromóció. D27/D29 megkapta a rögzített NHL-total diagnózist: az egyetlen16-gólos szélsőség nélkül nincs általános átlag- vagy szóráshiba-igazolás; exact-line tail-kalibráció és korabeli kapusinput célzott kísérlete következik. Új modell/WR/ROI-javulás nincs állítva.

## 2026-10-03 — Kvalifikált családválasztás éles javítása (D35)

- Éles kód: `edc850d`; aktív `family_selection_policy=qualified_probability_v1`. Ugyanazon meccs/család és finanszírozási szint jelöltjei között a végső, horgonyzott/kalibrált tényleges nyerési esély az első, a stresszelt EV a holtverseny-felbontó. A push/no-loss esély nem nyerési esély. A funding, CLV, kitettség és korábbi döntések változatlanok.
- Független Codex review: ACCEPT, 341 célzott teszt. Teljes Python: 6236 passed, 1 skipped, 5 subtests; frontend: 272 passed. Az első izolált futásból hiányzó tudáskártyák okozta hibák és pótlásuk megőrizve; guardot/tesztet nem lazítottunk.
- Éles publikáció: 6583, 08:18:15 UTC; helyi és Tailscale DOM/API: 13/13 végrehajtható utasítás, 14/20 napi első kiadás, engine error=null. A következő 6584 publikáció 3208 őrzött jelöltjének policy/seal ellenőrzése és 35 megfelelő család nyertesének újraszámítása sikeres. Nincs mesterséges új kiadás.
- 741 korábbi szelvény, 740 eredeti rendszer-döntés, 733 kiadási rekord és 378 elszámolás hash-ellenőrzéssel változatlan. Bizonyíték: `data/releases/root-qualified-selection-20261003/release.json`.
- A rögzített 36 órás diagnosztikában Winner +19,2004 USD, Handicap +19,0539 USD; periódus/inning Winner–DNB −4,98 USD. NHL teljes meccses Total: −14,4499 USD, 12 meccs. Ez kutatási diagnózis, nem automatikus családtiltás vagy igazolt modellfejlődés. Részletek az eredeti `docs/audits/20261003-performance-36h-after-recovery.json` kibővített családbontásában.
- A korlátozások utáni 47 döntés / 46 meccs az összevetéskor még nyitott; a korábbi pozitív eredményből nem állapítható meg a szűkítés hatása. Következő fókusz D27/D29: NHL total kalibráció és prospektív, meccscsoportos eredménymérés.

## 2026-10-03 09:47 CEST — teljes eredménykövetés és 36 órás mérés

D34 éles javítás: `9d02880` (integráció), `7fb10ab` (pontos ESPN eredmény-visszapótlás). A rolling naptárból kieső négy NCAAF-meccs és az elavult WNBA-sor 16 eredeti fogadása az éles engine normál elszámolásával lezárult: 13 win / 3 loss, +$30.0409. Most **378 lezárt ticket / 378 matching outcome**, **363 jövőbeli open**, **0 elkezdődött open**. 741 ticket, 740 eredeti döntés, 733 issuance és minden korábban lezárt outcome változatlan hashű; a helyreállítás kizárólag új eredményeket csatolt.

Teljes release-validáció: **6216 Python + 5 alteszt sikeres, 1 skipped; 272 webteszt; 168 független review-teszt**. Az izolált kör első futásából hiányzó meglévő sportfixture-t pontos archívummal pótoltuk; az eredeti hibalog megmaradt. Független Codex-review: ACCEPT, diff SHA256 `cb65a82fd640060348e9ba5eff85861bc3d15dd6d36481d477a277f89a868a8c`. Root kontrollált kiadás; automatikus fejlesztői jogosultság nem bővült.

Helyi és Tailscale élő publikáció **6552**, `2026-10-03T07:46:50.055361Z`: **13 kártya = 13 API utasítás = 13 fejléc**, engine error null; ESPN recovery healthy, unresolved_count 0. A 2026-10-01 21:46:49 – 2026-10-03 09:46:49 CEST ablakban lezárt 251 eredeti döntés: **123 win / 122 loss / 6 push; WR 50.2041%; settled stake $291.07; P&L +$43.021; ROI +14.7803%**, 23 meccs. Meccsklaszteres 95%-os ROI-intervallum **−15.3042% – +43.7613%**. Ez a hiányzó elszámolások helyreállítása, nem modelljavulás bizonyítéka. Az ugyanebben az ablakban kiadott 535 döntésből 188 lezárt: WR46.7391%, ROI+9.3154%; 347 még nyitott.

Bizonyíték: `data/releases/root-result-recovery-20261003/release.json`, `ledger-before.json`, `ledger-after.json`, `live-check.json`; [mért 36 órás eredmény](audits/20261003-performance-36h-after-recovery.json), [javítás előtti audit](audits/20261003-072230-performance-36h.json). Az adatok eredeti ajánlások/feltételezett vagy felhasználó által jelentett megjátszás alapján számoltak, Stake-számlával nincsenek egyeztetve; működési költségek külön.

## 2026-10-03 09:15 CEST — publikáció és végrehajtható lista helyreállítva

Kód: `536e761` (végleges batch napi családkorlátja és tétezése), `a262983` (a már kiadott, bejelöletlen single tippek beszámítása; hibás kezdésű importrekord kezelése). A korábbi 10 tervezett tipp helyett a tényleges végleges batch adja a napi arány nevezőjét; a tranzakciós guard változatlan.

Élő helyi és Tailscale ellenőrzés: publikáció `6519`, `2026-10-03T07:14:46.165056+00:00`; **13 kártya = 13 fejléc = 13 API végrehajtható utasítás**, 13 külön meccs, össztét $13.00; napi első kiadás 14/20. A helyreállítás óta 5 új első kiadás; az engine hibája null. HTTP-n letöltött oldal és JavaScript élő adatú DOM-ellenőrzése, fogadási/írási művelet nélkül.

Validáció: 5598 Python-teszt + 5 alteszt sikeres, 1 skipped; 272 webteszt sikeres; két független Codex-review pontos diff-hashhez kötve. A javítás előtti 736 bet-, 735 system_decision- és 728 issuance-rekord eredeti mezői hash-azonosak. Bizonyíték: `data/releases/root-publication-family-20261003/release.json` és `live-check.json`. A D32-höz tartozó konkrét incidens lezárva; a D32 teljes, tágabb feladatköre nem lett automatikusan készre állítva.

# Legutóbbi ellenőrzött kiadás: 2026-10-02.21

## 2026-10-02 — minden megfigyelt, de nem árazott piachoz ok és következő lépés

Commit **17454f8**: a már elmentett régi publication snapshotok is kapnak `no_candidate_evaluation` vagy pontos okkódot és végrehajtható következő lépést a dashboard-olvasáskor; ezért az élő katalógusban nincs megfigyelt, modell nélkül maradt sor üres indoklással. Ez nem tesz piacot pénzes executable ajánlássá: a pontos scope-, identitás-, settlement- és modellfeltétel továbbra is kötelező.

Újraindítás után a helyi és Tailscale dashboard egyaránt **HTTP 200**, friss publication `version=6320`; a 397 market-type sorból **0** maradt üres okkal. A közös execution snapshot továbbra is **389 / 0 / 9 / 0 / 416** (first issued / current executable / marked / started-expired / no-longer-qualifying). A célzott operations/API/instruction tesztek **97 passed**, a frontend korábbi futása **272/272**.

## 2026-10-02 — bounded execution list, shared counters and Asian/DNB anchor fix

Az urgent execution-list javítás commitja **4f17225**. A kiadási útvonal és a dashboard most ugyanabból a napi issuance snapshotból kapja a számláló alapját: az `issuance_rows` a szűrt horizon/league/market nézethez használható, a napi checkpoint és a naptári összesítés külön, explicit mező. A fő UI-lista csak friss, aktuálisan actionable, finanszírozott sorokat ad át; a korábbi immutábilis rekordok és a már nem kvalifikáló szerződések a Bet Tracker kontextusában maradnak. A korábbi tippek minden dashboard-ciklusban új current review-t kapnak.

Éles publication-limit guard került a döntési és storage tranzakcióba: ciklusonként legfeljebb **10** új funded tipp, meccsenként legfeljebb **3** funded szerződés és **$5** össztét, legfeljebb **1** period/quarter tipp, piaccsaládonként legfeljebb **25%** napi első kiadás. A 2026-10-02 17:00–17:10 UTC közötti 158-as kiadási alakzat regressziótesztje ezeket a korlátokat reprodukálja; az immutable korábbi kiadásokat nem írja át. A megjelölt/lejárt/no-longer-recommended számlálók és a fő lista ugyanazt az aktuális snapshotot használják.

Az Asian integer/quarter vonalak és a Stake moneyline-alakú **Draw No Bet** period-piacai push-free feltételes devig anchor-t kapnak; a modell push-massza ugyanilyen feltételes tömeggel keveredik, majd visszaáll az unconditional valószínűségbe. Az 1X2/three-way továbbra is háromutas devig. NHL Over 5.0, Asian line és NFL quarter DNB céltesztek bekerültek.

Élő ellenőrzés: helyi `/api/health` **200**, dashboard **200**, Tailscale `/api/health` **200**, Tailscale dashboard **200**, friss Tailscale publication `version=6316`. A közös snapshot mért állapota: **389** napi első kiadás, **0** jelenlegi executable, **9** megjelölt, **0** started/expired, **416** no-longer-qualifying; `publication_limits_version=v1`, 10/3/1/$5/25% limitek aktívak, `daily_risk_limit=null`. A live API tracker-kompatibilitás miatt történeti sorokat is tartalmazhat; a Betting Picks főlista a current executable szűrést használja.

Validáció: teljes Python-csomag **6106 passed, 1 skipped, 25 warnings, 5 subtests**; a végső storage/API/anchor/publication-limit céltesztek **106 passed**; frontend `node --check` és **272/272** webteszt sikeres. Automatikus fogadásleadás nem történt, az eredeti ledger, tétek, oddsok és outcome rekordok változatlanok.

## 2026-10-02 20:05 CEST — piaccsalád-lefedettség, aktuális instrukciószámláló és dashboard-helyreállítás

A friss futás nem szűkült Periods/Quarter piacokra: az aktuális Stake-katalógusból **1 656** kimenetelt értékelt, a kiadásban **479** családjelölt maradt, többek között teljes meccses Winner/Handicap/Total, NHL period, NFL quarter/half, MLB inning és NCAAF főpiacok. A 48 órás lefedettség: **MLB 4 meccs/116 piac, NHL 19/611, NFL 9/810, WNBA 3/16**; NBA és NCAA kosárlabda esetén ebben a pillanatban nem volt meccs a munkablakban.

A korábban csak a tartott period/quarter tételeket dominánsan megjelenítő útvonal javítva: a nem végrehajtható kutatási és stale sorok nem kerülnek executable Betting Picks-ként a főlistába, hanem tömörített, teljesen visszakereshető kutatási sorok maradnak. Az UI most külön mutatja a **Current Instructions** számát és a historikus **Daily Instructions** első kiadás-számlálót; így a szűrés nem hamisítja meg a napi teljesítést. A legutóbbi éles dashboard: `version=6293`, **HTTP 200**, **39 696 916 byte**, az izolált 64 MiB válaszkorláton belül; `/api/health` **200**.

A mérhető kalibrációs stressz modul most csak prospektív, modell-tréningidő után lezárt eredményt és meccsszintű klasztert használ; 30 független meccs alatt fail-closed legacy stress marad, a snapshot hash és a kalibrációs beállítások az EvidenceGuard bizonyítékába kerülnek. A jelenlegi kiadásban **0** új executable instrukció látható: **23** stressz-értékű család már tartott szerződés, a többi jelölt aktuális áron nem felel meg; ez nem kutatási sorral vagy duplikációval van feltöltve. A historikus napi számláló **389/20**, az immutable rekordok és eredeti tétek változatlanok.

Validáció: **6099 Python-teszt, 1 skipped, 25 warning, 5 subtest**, **272/272 webteszt**, valamint izolált dashboard-worker méret- és kalibrációs regressziótesztek sikeresek. A launchd szolgáltatás friss kóddal újraindult, az engine refresh hiba nélkül fejeződött be, a helyi smoke ellenőrzés élő publikációból készült. A Tailscale ellenőrzést a következő hálózati smoke-körben külön újra kell mérni; ez nem zárható le helyi HTTP-ből.

## 2026-10-02 19:40 CEST — piaci horgony, kalibrációs diagram és élő guard-helyreállítás

Az elsődleges WR/ROI-fejlesztés részeként a döntési útvonal most a Stake exact condition piaci árát devigeli, majd a független modell és a piaci valószínűséget logit-térben, kezdeti 0,15-ös modell-súllyal keveri. A súly és a liga/piaccsalád szerinti későbbi újrafittelés időrendi megfigyelésekből készül; 30 külön meccs alatti minta nem léphet elő éles tanulási súlyként. A 0,30 feletti modell–piac eltérés review/shadow státuszba kerül. A Stake-handicap home −line / away +line párjait ugyanazon exact condition alatt kezeli az anchor; korábban ez külön csoportba esett és guard-mismatch-et okozott.

Az élő futás `version=6266`: **1 858** teljes devig anchor-szerződés, **2 012** EvidenceGuard-ellenőrzésből **1 648 átengedett**, **364 elutasított**, **1 648** horgonyzott jelölt és **475** aktuális, nem ismétlődő jelölt. Számítási mismatch nincs; az elutasítások mért okai: 184 hiányzó/review anchor, 53 nagy modell–piac eltérés, 109 MLB kezdődobó-adat hiány (84 away, 25 home) és 36 model-quality validációs elutasítás. Az aktuális CLV-gate miatt a friss futásban a pénzes új ajánlások száma **0**: a pénzes tier csak pozitív, stresszelt EV és legalább 30 független meccses prospektív CLV után engedélyezett. A napi cél hiányát nem töltjük fel gyengébb jelöltekkel.

Statistics kapott 10 bin-es reliability diagramot ugyanabból a befagyasztott eredeti döntési kohorszból. A jelenlegi immutable mérés: **693** tracker-sor, **278** lezárt, **113 win / 159 loss / 6 push**, **34** lezárt meccs, settled stake **$312.84**, payout **$269.9504**, P&L **−$42.8896**, ROI **−13.7098%**, win rate **41.5441%**; Brier **0.241583**, log-loss **0.677388**. Ezek tényleges mérési adatok, nem profit- vagy jövőbeli nyereségállítás.

Az `EvidenceGuard` a piaci horgony módját, súlyát és eltérési küszöbét is a döntési bizonyítékba zárja; a storage publikáció ugyanazon tranzakcióban összeveti az aktív beállításokkal. Az eredeti ledger/tétek nem változtak, automatikus fogadásküldés továbbra sincs. Teljes ellenőrzés: **6093 Python-teszt, 1 skipped, 25 warning, 5 subtest**, valamint **272/272 webteszt** sikeres.

Konkrét fennmaradó blokkolók és következő végrehajtott lépések: (1) MLB probable-pitcher, NHL goalie, NFL QB/injury, időjárási és NCAAF tempó inputok pótlása az input-adapterekben; (2) a 51 nagy eltérésű anchor-szerződés tartós shadow-review-ja; (3) legalább 30 független meccs CLV-adat gyűjtése szegmensenként a pénzes tierhez; (4) a fejlesztési worker `D31` stale állapotának lezárása és a megmaradt release-evidence blokkolók integrációs javítása. A komponensállapot ezt `attention_required`-ként és konkrét feladatként naplózza; a fogadási útvonal a horgonyzott, guardolt jelöltek frissítését közben folytatja.

A Stake exact-market anchor és a reliability diagram kódja a jelen kiadásban aktív; a teljes piaclefedettség, tartós pozitív CLV/ROI és minden speciális/player/live piac éles bizonyítása külön, mérhető fejlesztési munkaként marad a következő végrehajtási ciklusban.

# Korábbi legutóbbi ellenőrzött kiadás: 2026-09-30.18

A Stake szakaszhandicapek mezősorrend-függő kihagyása javítva: befagyasztott valós replay-ben +74 pontos kimenetel értékelhető. Ismert professzionális Winner-regresszió kötelező kiadási ellenőrzést kapott, a családhibák tartós D18-munkák. Három fejlesztési/release SQLite-olvasó kapcsolatlezárása javítva. 1796 Python + 5 alteszt és 64 DOM-teszt, kihagyás nélkül; független review, friss local/Tailscale publikáció és minden eredeti fogadási rekord megőrzése ellenőrizve. A teljes engine és a tartós profit továbbra sem igazolt. [Teljes követelményaudit](DEVELOPER_AUDIT_2026-09-30_18.hu.md).

## Korábbi kiadás: 2026-09-29.15

Részletes teljes követelmény- és fejlesztői audit: [DEVELOPER_AUDIT_2026-09-29_15.hu.md](DEVELOPER_AUDIT_2026-09-29_15.hu.md).
A friss kiadás éles, a teljes engine még nem teljes piaclefedettségű és tartós nyeresége nem igazolt. 1126 Python teszt + 5 alteszt, 40 DOM teszt és valódi izolált Codex-review. Pontos tanítási inputarchívum, ötligás player-forrás workflow, teljes player-market audit és külön felügyelt utility release controller beépítve. Az alábbi régebbi állapotbejegyzések történeti bizonyítékok.

## 2026-09-29 — kiadás 14, párhuzamos fejlesztés és teljes követelményaudit

Codex marad az elsődleges agy, fejlesztő és felügyelő. Három izolált fejlesztési sáv, soros ellenőrzött integráció, másodlagos Claude Opus5.5 és harmadlagos agy/Grok tanácsadók, perces tartós hiányfeltárás került a kiadásba. A Winner/Handicap/Total családok elsőbbséget kapnak a méretezésnél, külön lefedettségi audit mutatja a hiány okát. Minden új pénzes publikáció reprodukálható tétbizonyítékot követel; a korábbi tétek változatlanok.

A teljes követelményenkénti minősítés, fejlesztői hibák, tesztek, élő mérés és hátralévő feladatok: [DEVELOPER_AUDIT_2026-09-29_14.hu.md](DEVELOPER_AUDIT_2026-09-29_14.hu.md). A teljes engine továbbra sem kész, tartós profit nem igazolt. A lentebbi bejegyzések történeti pillanatfelvételek.

## 2026-09-29 19:43 CEST — záróár-értékelés élesben, modellvizsgálat elindítva

Kiadás `2026-09-29.13`, frontend `closing-evidence-2`. [Kiadási jegyzőkönyv](RELEASE_2026-09-29_13.hu.md). A learning worker a meccskezdés után külön, befagyasztott árösszevetést készít, a saját döntéseket és forrásokat megőrizve. A hiányzó történeti árak nem válnak becsléssé; a régi öt NFL-döntés feldolgozása élesben befejeződött, öt hiányzó záróárral. Az új panel a Model Performance → Observed Closing Prices alatt látható.

A reviewer által talált két blokkmegkerülési hiba, a legacy full-game scope és a valós double-chance megnevezések kompatibilitása javítva. Az első telepítési próbán észlelt dashboard-sémaütközés is javítva, majd a teljes root Python/API-tesztcsomag és az élő felület újra ellenőrizve. Kifizetésazonos teljes meccses Winner/Handicap aliasok többé nem kérhetnek ismételt kiosztást; az NFL-döntetlen kivétel külön marad. Az eredeti fogadások nem változnak.

512 Python/API-teszt + 5 alteszt, 38 DOM-teszt sikeres; 69 éles kártya és link ellenőrizve. 75 szelvény, 74 saját döntés, 67 kiadás és 69 checklist eredeti mezői változatlanok. A napi kiadás 45/15, a publikáció 962/962 guard-ellenőrzése sikeres; helyi és Tailscale dashboard működik.

D02/D17 külön, root által ellenőrzött éles kiadásként igazolva; az eredeti elutasított próbák megmaradtak. Ez nem egy későbbi független AI-review elfogadásának állítása. A fejlesztő újra fut: D18 piaccsaládonkénti modellvizsgálat `gpt-6-astra` modellel, a nyolc telepített modell/riport ellenőrzött másolatán. A teljes engine továbbra sem kész és tartós profit nem igazolt. A lentebbi állapotok történeti pillanatfelvételek.

## 2026-09-29 19:10 CEST — teljes folyamat-audit és éles javítások

Kiadás `2026-09-29.12`, frontend `system-audit-1`. A részletes, ellenőrizhető eredmény: [AUDIT_2026-09-29.hu.md](AUDIT_2026-09-29.hu.md). Nyers modellvalószínűség-ellenőrzés és hibás piac elkülönítése, valódi hibaállapotot figyelő supervisor, eseményenként csoportosított fogadási eredmények, új elszámolásra azonnal sorba kerülő értékelés, veszteségmentes publikációtömörítés és olcsó SSE-verzióolvasás, releváns sportkutatási szűrés, tartós meccskezdés előtti árforrás-gyűjtés került élesbe. A fejlesztő strukturált eredmény után 60 másodpercen belül keres következő munkát; infrastruktúra-hibánál és az adott feladatnál a backoff megmarad.

320 Python/API-teszt + 5 alteszt, 37 DOM-teszt és 954 valódi szerződés numerikus visszajátszása sikeres. A friss élő felület 68 kártyája, 68 pontos Stake-eseménylinkje és minden részlete ellenőrizve. A korábbi 72 szelvény, 71 saját döntés és 64 kiadási rekord eredeti mezői változatlanok; közben két új MLB-ajánlás keletkezett. SQLite quick_check rendben; a tömörítés 7,28 GB payload-területet szabadított fel újrahasználatra, nem csökkentette automatikusan a fizikai fájlméretet.

19:07 CEST-kor napi 44/15 külön pénzes ajánlás (MLB 19, NFL 1, NHL 19, WNBA 5), 11 meccs 24 órán belül, 17 meccs 48 órán belül. Öt lezárt saját döntés +6,08 USD tétek utáni eredménye mind egyetlen NFL-meccsből származik; nem öt független siker és nem igazolt tartós profit. Az AI/adat/tárhely költségkeretéről a felhasználó dönt; hiánya nem blokkolja a modellfejlesztést.

D02 javított jelöltje külön AI-review-val elfogadva; D03 automatikusan elindult. 8 mérföldkő és 18 feladat, köztük D17 árértékelési integráció és D18 professzionális modellek piaccsaládonkénti validációja. A teljes prop/special/SGM/live támogatás és automatikus éles szoftverkiadás még nyitott, `fully_ready=false`. A lentebb szereplő korábbi kiadási bejegyzések történeti állapotfelvételek.

## 2026-09-29 18:38 CEST — üzemi ellenőrzés és fejlesztési elakadás javítása

Az éles motor friss publikációt ad, a helyi és Tailscale HTTPS health-végpont ellenőrzött tanúsítvánnyal elérhető. 105 forrásjelentés, nyolc ligamodell, két inningmodell és 31 szakaszmodell; nincs jelzett előrejelzési vagy betöltési hiba. A napi külön pénzes kiadás 42/15 (MLB 17, NFL 1, NHL 19, WNBA 5), ami kibocsátási darabszám, nem az éppen fogadható tételek vagy nyertes fogadások száma. A 48 órás leltár 17 meccsből 16-ot párosít, 954 kimenetelt modellez; az MLS továbbra is csak leltár.

A D02 első jelöltjét a külön AI-review két tényleges jogosultságkezelési hiba miatt elutasította. A következő kör javításokat készített, de a későbbi éles integráció hiánya miatt a munkás nem kérte a teljes elfogadási ellenőrzést. Pontosítottuk, hogy a `task_complete` kizárólag az adott, korlátozott jelöltfeladat elfogadási feltételeire vonatkozik. A feladat által megkövetelt valós bizonyíték hiánya továbbra is kizárja a teljesítést; a teljes tesztcsomag és a külön review kötelező marad. Három új teszteset igazolja, hogy a fennmaradó éles munka semmilyen ellenőrzést nem kerülhet meg, és elfogadott jelölt sem jelenti az engine készültségét.

Validáció: 294 Python/API-teszt + 5 alteszt, 36 DOM-teszt sikeres. A fejlesztő munkás auditált, előrehozott D02 újrapróbálkozása 18:37:40-kor elindult (`gpt-6-astra`); az ellenőrzés időpontjában még implementál, nincs új elfogadás vagy éles aktiválás. A korábbi hibák és kísérletek megmaradtak. Bizonyíték: `data/development/status-repair-live-check.json`, `status-repair-retry.json`, `status-repair-python.log`, `status-repair-dom.log`. A teljes engine továbbra sem kész: `fully_ready=false`.

## 2026-09-29 16:46 CEST — felügyelt fejlesztés, határidős ellenőrzések és valódi AI-implementáció

Kiadás: `2026-09-29.11`, frontend `supervised-delivery-1`. Nyolc mérföldkő, 16 induló implementációs feladat és kötelező ellenőrzési időpont került a tartós tervbe. Az aktuális készültségi feltételeket a [READINESS.hu.md](READINESS.hu.md) rögzíti; a korábbi napi tízes menetrend történetinek jelölt. A szerződés R05 követelménye napi minimum 15 külön pénzes kiadás, R25 a felügyelt fejlesztési folyamat. A hiányt nem lehet átárazással vagy kutatási sorral teljesítettnek nevezni.

Külön launchd-fejlesztési munkás fut `gpt-6-astra` modellel, az előző kísérlet után egyórás célütemben. Másolt forrás és nyilvános sportadatok, korlátozott olvasási/írási és hálózati jogosultságok, teljes Python/DOM teszt, külön csak olvasó AI-kódreview, tartós hibák és újrapróbálkozások. A felügyelő az új munkást is figyeli. A lejárt mérföldkövek láthatók és előrébb sorolódnak; az induló feladatok után a nyitott mérföldkövek új fejlesztési lépéseket kapnak. Kódfrissítésnél a nem ütköző változások átkerülnek a munkapéldányba; ütközéskor mindkét változat megmarad az izolált javításhoz. Éles fogadási naplóhoz és publikáláshoz a fejlesztő nem fér hozzá.

Az első tényleges kör lezárult: D01 `player_history.py`, pontos ismertségi idővel kezelt játékoselőzmények, 54 új viselkedési teszt, 342 sikeres teljes Python-teszt és 36 DOM-teszt, elfogadó külön AI-review. Fontos megállapítás: a 20 játékosadat-partíció sémájából hiányzik az explicit `available_at`; az öt ligából vett mintasorokat a modul bizonyíték hiányában kizárja. A letöltési idő nem válik automatikusan történeti publikálási idővé. A jelölt `candidate_ready`, nem éles prop-modell. Két kezdeti CLI-sandbox indítási hiba megmaradt a naplóban; az elérési beállítás javítása után a teljes kör valóban lefutott.

A végleges felügyeleti kód ellenőrzése: 291 Python/API-teszt + 5 alteszt, 36 DOM-teszt; a forrásfrissítéssel összevezetett D01 munkapéldány 345 Python-tesztje és 36 DOM-tesztje sikeres. A Tailscale-felületen nyolc mérföldkő és tizenhat feladat jelenik meg JavaScript-hiba és fogadási művelet nélkül. Az eredeti 65 szelvény, 64 saját döntés és 39 checklist-bejegyzés eredeti mezői változatlanok. A napi kiadás 16:45 CEST-kor 38/15; három új, változatlan modellezési szabályok szerint kiválasztott döntés született az ellenőrzés közben. Az összes szelvény ekkor 68, a saját döntések száma 67, az eredeti 65/64 rekord megmaradt. A következő önálló feladat D02, pontos szerződéshez kötött záróár/CLV értékelő.

**A teljes engine készültsége továbbra is hamis (`fully_ready=false`).** Új szoftver automatikus éles aktiválása még nincs bekötve; a candidate-ready kód nem zárja le a produkciós követelményt. Validált teljes player-prop-, speciális-, SGM- és in-play támogatás, a hiányzó szerep-/lineup-hatások, teljes üzemi átvétel és költségek utáni profitigazolás továbbra is nyitott mérföldkövek. A helyi gép/munkamenet/hálózat és a Codex-kvóta működési függőség. Telegram későbbi felhasználói szakasz.

Bizonyítékok: `data/development-release-check.json`, `data/development-live-dom-check.json`, `data/development/state.json`, `data/development/runs/20260929T143035412663-D01/`, `data/development/rebased-candidate-python.stdout.log`. Web: **Model Performance → Supervised Development and Delivery**, API: `/api/development`.

## 2026-09-29 15:45 CEST — teljes API-válaszok, dinamikus NCAA, meccsenkénti diagnózis

Kiadás: `2026-09-29.10`, frontend `stake-ncaa-1`. A hivatalos Stake API sportág- és tornalistájából követjük az aktuálisan elérhető NCAA-kínálatot. Mindkét menetrendi/listázó forrás és meccsenként mindkét fixture/odds-válasz bekerült; a teljes eredeti válaszokat külön, hash-ellenőrizhető tömörített archívum őrzi. Katalógus: 99 meccs, 198 teljes fixture/odds-válasz, 607 pontos ligánkénti/szekciónkénti piacímtípus, 15 perces célütem. A jelenlegi NCAA-kínálat 14 amerikaifutball-meccs; mind párosítható független sportadattal. Kosárlabda és más NCAA-torna most nem szerepelt a feedben. A 14 meccs a 48 órás ablakon túl van; 78 támogatott kimenetel árazását külön, publikálás nélkül ellenőriztük.

Három külön college meccsmodell készült saját adatokkal, családszintű időrendi kapukkal. Összesen nyolc ligamodell, két MLB inningmodell és 31 szakaszmodell töltődik be. Az NHL 2023-as, meccsazonosító nélküli csapatstatisztikai exportját 1400 hivatalosan azonosított boxscore pótolja. Öt kapuslövés/gól miatti eltérést a hivatalos play-by-play és góllista eseményeivel oldottunk fel. A friss kiadás 105 forrásjelentésében nincs hiba.

Ellenőrzött éles publikáció: 1799, 15:44 CEST. A következő 24 órában 11 Stake-meccs és 730 modellezett kimenetel; 48 órában 17 meccs és 947 modellezett kimenetel. A 17-ből egy MLS-meccs csak leltár, továbbra sincs MLS-modell. Minden meccs külön döntési okot kap a Research nézetben. A napi kiadás 32/15; a kiadás során egy új, 1 USD-s NHL-szakaszfogadás jelent meg. Ez a kiadásszám, nem nyerési vagy profitmutató, és nem garancia minden jövőbeli nap darabszámára.

Éles helyi/Tailscale API: HTTP 200, friss publikáció, 947/947 evidence-ellenőrzés sikeres, modellbetöltési hiba nincs. A felügyelő, piaci motor és tanuló munkás működik; külön komponensállapot jelzi az adat-, guard-, modell-, katalógus- és kutatási munkát. A futó munkás friss kódra újraindult; a migráció közbeni régi modulok miatti hibát megszüntettük. A visszatérő forráshibák javítási sorba kerülnek; egy elkészült kutatási dosszié nem minősíti implementáltnak a hiányzó piacmodellt.

Validáció: 269 Python/API-teszt + 5 alteszt, 35 DOM-teszt; az utolsó komponens- és NHL-javítás további hét célzott ellenőrzése sikeres. Friss Tailscale-válasszal futtatott DOM: 56 instrukció/nyilvántartott kártya, nyolc meccsmodell, 31 szakaszmodell, 17 meccsdiagnózis, 607 katalógussor és 11 komponensállapot; nincs JavaScript-hiba. A 34 aktuális és a 198 teljes katalógusválasz gzip-tartalmának SHA-256 ellenőrzése sikeres. Az eredeti 61 szelvény, 60 saját döntés és 39 jelölés mezői változatlanok. Automatikus fogadásküldés nem történt.

**A teljes, minden piactípust támogató önfejlesztő rendszer követelménye még nincs teljesítve.** Validált player-prop, eseménysorrendi, összetett/SGM, live és további NCAA-sport modellek továbbra is hiányoznak; önálló forráskódírás és telepítés nincs bekötve. A nyitott követelmények megmaradtak a projekt szerződésében. Részletek és források: [STAKE_API_NCAA.hu.md](STAKE_API_NCAA.hu.md). Ellenőrzési adatok: `data/stake-ncaa-release-check.json`, `data/stake-ncaa-live-dom-check.json`.

## 2026-09-29 14:58 CEST — 31 szakaszmodell, teljesebb piackatalógus és játékos-adattár

Kiadás: `2026-09-29.9`, frontend `scope-models-1`. Az öt meglévő meccsmodell és két MLB inningmodell mellett 31 külön NBA/WNBA/NFL/NHL szakaszmodell fut: negyedek, félidők, NHL-periódusok és rendes játékidő. A 13 765 történeti meccsből vett célváltozók tényleges szakaszeredmények, külön hosszabbítás/szétlövés-kezeléssel. Családonként külön időrendi validáció engedélyezi a piacot; egy modell megléte nem jelenti minden család jóváhagyását.

Az ellenőrzött 1752-es éles publikáció 941 pontos Stake-kimenetelt értékelt: MLB 248, NHL 589, WNBA 104. NBA/NFL meccs nincs a jelenlegi 48 órás ablakban. A bővítés után 18 új döntés jelent meg (NHL 17, MLB 1), 1,00–1,44 USD téttel a megengedett 1–5 USD tartományban. A mai kibocsátás 31/15 külön ajánlás. Ez nem napi profitigazolás, és nem jelenti a jövőbeli napok mennyiségi céljának garantált teljesülését.

A szélesebb kutatási katalógus 80 meccs 549 pontosan elnevezett piacímet tartalmaz, legfeljebb 60 napra; az egyedi játékosnevek és scope-ok külön neveket eredményeznek. Ez nem 549 kész modell és nem az aznapi fogadások száma. A játékos-adattár 706 273, meccs- és játékosazonosítóval összekötött megfigyelést tartalmaz mind az öt ligából. A játékosnapló-frissítés, szakaszmodell-tanítás és katalógusgyűjtés külön napi, tartós munkafolyamatot kapott. Sikertelen tanítási adatfrissítés megtartja a korábbi modellt és újrapróbálkozást indít.

Validáció: 253 Python/API-teszt + 5 alteszt, 34 DOM-teszt sikeres. A friss publikáció 941/941 bizonyítékellenőrzése sikeres, modellbetöltési hiba nincs. A helyi/Tailscale API HTTP 200, 31 betöltött szakaszmodell. A friss Tailscale-adatokkal futtatott DOM-ban 55 kártya, 31 modellsor, öt játékosadat-sor és 549 katalógussor; JavaScript-hiba és fogadásküldés nincs. A korábbi 43 szelvény, 42 saját döntés és 39 jelölés eredeti mezői változatlanok.

Teljesítményjavítás: a szolgáltatás egyszálas OpenMP-beállítást használ a kis modellhívásokhoz. A változatlan szövegek angol fordítása korlátozott méretű gyorsítótárba kerül; a kész JSON-adat nem fut át újra a FastAPI általános konvertálóján. A tömörítés nem fogja vissza az eseményfolyamot. Az időérzékeny ajánlásjogosultság minden GET-nél újraértékelődik. A legutóbbi ciklusok publikálása kb. 60 másodperces.

**A minden fogadástípust lefedő kérés még nincs teljesítve.** Player props: az adatgyűjtés kész, a validált modell, aktuális szerep/részvétel, pontos adapter és elszámolás még hiányzik. Eseménysorrendi, Leaders, összetett/SGM, live és MLS modellek szintén nincsenek teljesen implementálva. Az SGM-hez a konkrét kombináció valós Stake-ára is szükséges; ilyen forrás jelenleg nincs bekötve. A háttérmunkások adatot frissítenek és implementált modelleket tanítanak, nem írnak önállóan új modellekhez forráskódot. A teljes lista és a konkrét függőségek: [ALL_MARKETS.hu.md](ALL_MARKETS.hu.md).

Bizonyíték: `data/all-market-expansion-release-check.json`, `data/all-market-expansion-dom-check.json`; előző adatbázis: `data/backups/before-all-market-expansion.sqlite3`.

## 2026-09-29 01:55 CEST — teljes folyamat auditja és hibajavítás

## 2026-09-29 — pontos Stake-fül a fogadási útmutatóban

A Padres–Cubs `Innings 1 to 5 - Total / Over 4.5` az adatforrás `innings 1 to 5` csoportjában szerepel, a bemásolt Main-listában nincs. A nyilvános eseménylap külön `Innings 1 to 5` fület mutat. A korábbi útmutatóból ez a navigációs lépés hiányzott; nem kell és nem szabad a teljes meccses Totalra átvinni a tippet.

Kiadás: `2026-09-29.8`, frontend `stake-market-tabs-1`. Minden pontos piachoz megőrizzük az API csoportazonosítóit és neveit; az EvidenceGuard ellenőrzi és lezárja őket a bizonyítékban. A kártya kiírja a `Stake tab` mezőt, a Bet on Stake gomb mellett és a részletekben fül → piac → választás útvonal látszik. A régi naplóbejegyzések aktuális, pontosan azonos választásának fülét is megjeleníti, az eredeti adatok átírása nélkül.

234 Python-teszt + 5 alteszt és 31 DOM-teszt sikeres. Az élő API-adatokkal futtatott DOM-ban a konkrét Padres–Cubs kártyán és részleteiben ellenőrizve az útvonal, JavaScript-hiba nincs. A 40 korábbi szelvény és 39 saját döntés eredeti adatai megmaradtak. Bizonyítékok: `data/stake-navigation-release-check.json`, `data/stake-navigation-dom-check.json`, `data/stake-navigation-ledger-check.json`.


## 2026-09-29 — Mark as placed frissítési ütközés javítva

Reprodukált hiba: a sikeres checklist-mentés után egy korábban indult dashboard-lekérés visszaírta a régi pipaállapotot. További ütközés: folyamatban levő mentés közbeni újrarajzolás újra engedélyezte a jelölő másolatait. A kliens most megtartja a függő mentési állapotot, minden azonos jelölőt letilt `Saving…` felirattal, védi a mentett állapotot az azt megelőző lekérésektől és eldobja a későn beérkező régebbi dashboard-válaszokat. Az újabb másik eszközön végzett jelölés/törlés továbbra is szinkronizálódik. Hibás mentés után az aktuális vezérlők visszaállnak és újrapróbálhatók.

A hiba az új tesztek közül hárommal javítás előtt reprodukálható volt; javítás után mind a 29 DOM- és 14 API-teszt sikeres. Statikus kiadás: `app.js?v=checklist-sync-2`, szolgáltatás-újraindítás és éles jelölések tesztcélú módosítása nélkül. A már nyitott oldalt egyszer újra kell tölteni az új JavaScript betöltéséhez. A könyveléshez, modellekhez és eredeti fogadási adatokhoz ez a javítás nem nyúl.


## 2026-09-29 13:04 CEST — külön MLB inningmodellek kiadva

Projektverzió: `2026-09-29.7`. Elkészült és fut az MLB első inning és első öt inning külön count modellje, 9 832 hivatalos, inningenként egyeztetett mérkőzésből. Mindkét modellben 5 832 tanító, 1 841 kiválasztási és 2 159 időben későbbi tesztmeccs. A teszt joint log loss 1,9366 / 4,1103, a befagyasztott ligaátlagé 1,9423 / 4,1267. Ez prediktív ellenőrzés, nem profitigazolás.

A Stake első ellenőrzött pillanatképében 120 további inning-kimenetel vált pontosan illeszthetővé. Támogatás: név szerinti Winner, 1x2, Total, Handicap és csapatösszeg az engedélyezett scope-ban és tényleges kínálat szerint. Azonos kifizetésű Winner/0 handicap és 1x2 csapat/−0,5 handicap nem duplázhatja az új kiadást. Külön időszakazonosítók, Draw/push logika, scope-szerinti elszámolás, első élő forecast befagyasztása, napi `segment_retrain` és archívumból történő jóváhagyott helyreállítás.

Validáció: 231 Python-teszt + 5 alteszt, 25 frontend DOM-teszt. A telepített helyi és Tailscale API HTTP 200, friss publikációban mindkét modell, 8+8 prospektív előrejelzés; a live API-adatokkal futtatott DOM-ban két modellsor és nulla JavaScript-hiba. A korábbi 36 szelvény, 35 saját döntés, 35 jelölés és 28 kiadási rekord minden mezője változatlan.

**Aktuális külső akadály:** a Stake odds-data API minden liga végpontjára HTTP 502 `Maintenance` választ adott. A frissítés percenként folytatódik; a felület ezt adatforrás-kiesésként mutatja. Régi oddsot nem adtunk ki új ajánlásként. A napi számláló a kiadás ellenőrzésekor 6/15, az új modellekből még nincs érvényes friss oddsszal kiadott fogadás. A korábbi lekérés numerikus visszajátszása nem friss ajánlás és nem számít bele.

Részletes adat-, modell-, settlement- és fennmaradó fejlesztési leírás: [MLB_INNINGS.hu.md](MLB_INNINGS.hu.md). Megmaradó hiány: játékospiacok, más sportágak periódusai, kombinált/live és különleges MLB-piacok, MLS-modell, teljes lineup/perc/kapus-hatás, executable-price profitvizsgálat és önálló kódfejlesztés. Nem minden hiányzó modellt oldott meg ez a kiadás.

Ellenőrzési állományok: `data/inning-models-release-check.json`, `data/inning-models-dom-check.json`, `data/imports/inning-parser-shadow.json`; előző állapot: `data/backups/before-inning-models.sqlite3`.


Az auditban azonosított elavult végrehajtási utasítás, frissítési idővel megnyújtható árérvényesség, eltérő modellcsere-kapu, ellenőrzés utáni módosítás, Today nézetben megjelenő elkezdődött pregame és félreérthető árpróba javítva. Új `freshness.py`, `instructions.py`, tranzakción belüli bizonyítéklenyomat-ellenőrzés és egységes pontszám-romlási kapu. A modellcsere védett a közben változó történeti adatok és modell ellen. Az ellenőrzés kifejezetten determinisztikus; nincs külön LLM-jóváhagyás.

Éles helyi/Tailscale ellenőrzés: 226/226 guard-siker, 34 eredeti jegy változatlan, 33 kártya saját Stake-linkkel. A konkrét, küszöb alatti New York Liberty +3.5 nem kap Bet $1 utasítást. 201 Python-teszt +5 alteszt és 21 DOM-teszt sikeres. Napi új kibocsátás 4/15; nettó nyereség nem igazolt. Részletes megállapítások és nyitott képességhiányok: `docs/FLOW_AUDIT.hu.md`; bizonyíték: `data/flow-audit-release-check.json`, `data/flow-numeric-audit.json`.

## 2026-09-29 01:03 CEST — tartós felügyelet, szakirodalom-figyelés, Stake gombok

Telepítve és ellenőrizve: külön perces supervisor, leállt/elakadt folyamatok feltételes újraindítása, meglévő worker-hibák tartós újrapróbálása, napi arXiv cím/absztrakt-figyelés három témában. Az utolsó ellenőrzéskor 36 cikk a katalógusban, 3 téma lekérése tárolva. A fő szolgáltatás életéhez kötött `caffeinate -s` assertion működik hálózati tápon. A két felügyelt szolgáltatás egészséges állapotú.

Mind a 30 aktív kártyán Bet on Stake gomb, a rögzített Stake-meccs pontos URL-jével. A link meccsoldalra mutat; nem előre kitöltött szelvényre. Konkrét kiválasztást előre betöltő Stake-link nem igazolt, ezt a felület is közli. A gomb mellett a pontos piac és választás szerepel. Megnyitása nem módosítja a pipát, fogadási naplót és nem küld fogadást.

Ellenőrzések: 195 Python-teszt +5 alteszt, 19 DOM-teszt; élő Tailscale API és DOM; 30/30 kártya helyes rögzített href-fel, nulla írási művelet; 206/206 guard-siker az ellenőrzött publikációban. Korábbi 31 fogadási rekord változatlan. Bizonyíték: `data/automation-buttons-release-check.json`.

Határ: a szakirodalom-figyelés nem teljes tanulmányfeldolgozás, új modellek leprogramozása vagy önálló kódtelepítés. A már implementált újratanítás/modellkeresés működik automatikusan; teljes nyílt végű önfejlesztés nincs. A napi 15 új ajánlás és a nettó nyereség még nem igazolt teljesítés.

## 2026-09-29 00:49 CEST — gördülő 24/48 órás lefedettség

Telepítve: `Next 24 Hours` szűrő, főoldali rövid lefedettség, Research ligabontás. A számlálás az ajánlásokkal azonos, atomikusan publikált készletből történik. Külön érték az összes megfigyelt kimenetel, az aktív/friss standard ajánlat, az ellenőrizetlen Swish-ár, a modellel értékelt kimenetel, az értékküszöböt teljesítő legjobb piac, a különböző rögzített szerződés és a pipálás. A napi kibocsátást továbbra is a budapesti kiadási dátum határozza meg.

Élő Tailscale-ellenőrzés: 24 órán belül 5 meccs (MLB 2, NHL 1, NFL 1, WNBA 1), 1547 megfigyelt kimenetel, 930 friss aktív standard ajánlat, 118 modellezett kimenetel, 11 megfelelő legjobb piac; 17 korábban kiadott/rögzített különböző szerződés, ebből 9 nincs pipálva. 48 órára 12 meccs, 3541 megfigyelt kimenetel, 1940 aktív standard ajánlat, 196 modellezett kimenetel, 18 megfelelő legjobb piac, 30 rögzített szerződés, 12 nincs pipálva. Ezek pillanatfelvétel-számok, nem teljes weboldali lefedettség. A teljes korábbi 31 jegy és 18 pipajelölés változatlan maradt. A duplán jelentett jegy nem két különböző ajánlás.

Mai kibocsátás az ellenőrzéskor: 1/15, 14 hiányzik. A mennyiségi cél és az igazolt nettó profit továbbra sincs teljesítve. Ez a módosítás mérési és megjelenítési javítás, nem új sportmodell vagy a hiányzó prop/részidős piacok megoldása.

Ellenőrzés: 183 Python-teszt + 5 alteszt; 17 DOM-teszt; a végső aggregálási optimalizálás után 39 célzott Python-teszt. Friss élő publikáció: 196/196 EvidenceGuard-ellenőrzés sikeres, helyi és Tailscale elérés, élő DOM 24 órás nézetében 17 kártya. Bizonyíték: `data/rolling-coverage-release-check.json`. Frissítő hibamentes, külön tanulási worker monitoring állapotban.

# 2026-10-02 19:10 CEST — piaci minőségkapu, outcome-feedback és élő ellenőrzés

**Elvégzett lépés.** Az új, mérhető WR/ROI-minőségcél bekerült a követelmény- és agent-szabályzatba (`config/project_contract.json`, `AGENTS.md`). Az aktív tartós beállítások: `fallback_clv_gate=prospective_positive_clv`, `clv_min_games=30`, `max_bet=5`, `daily_risk_limit=null`. A `stress_value` útvonal változatlanul az exact Stake-kontraktus, stresszelt EV és USD1–5 sizing alapján működik. A `model_pick` és `probability_pick` jelöltek most árnyékban maradnak, amíg legalább 30 független meccsen forrás-ellenőrzött pozitív prospektív CLV nem áll rendelkezésre. Az immutable döntések, eredeti oddsok, tétek, kimenetelek és a kézi fogadás tilalma változatlan.

**Bizonyíték és teszt.** A gate és a publikációs újrajátszás 154 célzott Python-teszten átment; az outcome-feedback/coalescing javítás külön 19 teszten átment. A friss élő publikáció 2026-10-02 19:07 CEST-kor sikeresen commitálódott: 549 aktuális döntési sor, 103 stress-value tier és 445 shadow/unfunded sor; a 380 fallback sor `shadow_only=true`, a pénzes ajánlások száma ebben a pillanatképben 0, mert a jelenlegi stress-value jelöltek már rögzített szerződések. A publication failure a gate-seal javítás előtt reprodukálható volt, majd a `6f986d5` javítás után megszűnt.

**Mért teljesítmény.** A lezárt immutable döntések alapján: 278 settled döntés, 34 külön meccs, 113 win, 159 loss, 6 push; settled stake $312.84, payout $269.9504, P&L −$42.8896, ROI −13.7098%, döntési találati arány 41.5441%. A kalibráció 271 win/loss megfigyelésen Brier 0.241583, log-loss 0.677388, meccssúlyozott Brier 0.229949; ez mérés, nem profit- vagy jövőbeli nyereségállítás. A friss záróár-gyűjtés 4353 forrásrögzítést és 0 capture hibát jelez.

**Outcome tanulási kör.** A legutóbbi archivált outcome-review forrása és bemeneti hash-e megmaradt (`generation=11d66cae…`, `input_sha256=b374ce83…`), 49 diagnosztikai javaslattal; az eredeti ledger hash nem változott. A nagy evidence-feldolgozás már streamelve és korlátos budgettel fut. A learning workerben egy régi, külön `shadow_evaluation` retry maradt; ezt nem jelölöm késznek és nem engedem a fogadási döntési útvonalba.

**Élő állapot.** Local API HTTP 200, friss Stake-katalógus 148/148 fixture és 296 response, EvidenceGuard 1966 ellenőrzésből 1850 átment; 8 liga-modell, 2 MLB inning-modell és 31 periódusmodell betöltve, forecast error 0. A component-health állapota `degraded`: az NCAAF/injury input még nincs integrálva, és 14 market implementation gap maradt; ezek a publikációból kizártak.

**Következő végrehajtott lépés és határidő.** A következő worker-ablakban a retry állapotot külön reprodukálom, majd a piac-horgonyzott devig/logit blend és családonkénti prospektív kalibráció árnyék-útvonalát kötöm be (2026-10-02 23:59 CEST checkpoint). Élő tétútvonalra csak izolált regresszió, független Codex-review, trusted release, local/Tailscale smoke és friss publikációs bizonyíték után kerülhet.

# Projektállapot — működő kutatási engine, 2026-09-28

Az új felhasználói követelmény alapján a megvalósítás elkészült helyi kutatási kiadásként. Nem állítjuk, hogy a teljes, éles nyereséget igazolt rendszer kész.

## Elkészült és fut

- 2026-09-29 00:23: elutasított újratanítás után automatikus, hatreceptes modellkeresés. Mind az öt ligán lefutott; NBA regularized és WNBA elo_anchor előléptetve. Páros, azonos meccseken mért Brier: NBA 0.200314 → 0.198951 (720 meccs), WNBA 0.200933 → 0.193321 (195 meccs); a pontszám-MAE kapu is teljesült. Ez retrospektív fejlesztési javulás, nem igazolt profit. Javítva a közös games.json átmeneti, statisztikákkal még ki nem egészített felülírása; szemantikailag azonos letöltés nem indít új keresést. Elavult adatú munkák külön superseded státusszal megmaradnak. 179 Python/API + 5 alteszt és 16 DOM-teszt sikeres, Tailscale és az új éles modellverziók ellenőrizve; 30 korábbi szelvény változatlan. A szeptember 29-i kiadásszámláló az ellenőrzéskor 0/15: nem lett teljesítettnek feltüntetve a korábban kiadott fogadások ismételt számolásával. Audit: `data/hypotheses-release-check.json`.

- 2026-09-29 00:08: telepítve a külön learning launchd worker. Tartós feladat- és próbálkozásnapló, összeomlás utáni helyreállítás, növekvő várakozású újrapróbálás, napi kontrollált tanítás és óránkénti eredményértékelés. Az első eredményáttekintés és egy valódi NBA-kísérlet lefutott; a 720 meccs / 22 hét összehasonlítás nem mutatott javulást, ezért nem történt modellcsere. A tippszolgáltatás közben külön fut. K11 kiegészítve az agent-szakirodalom ellenőrzött részeivel; ez nem teljes Reflexion-implementáció és nem önálló LLM-kódíró. 171 Python/API-teszt + 5 alteszt és 15 DOM-teszt sikeres. Állapot: Model Performance → Learning Execution és `/api/learning`.

- 2026-09-28 23:51: kiadva a külön fogadási utasításnézet és a Research oldal. Engedélyezett az új, külön értékelt szerződés ugyanarra a meccsre; nincs meccsenkénti két ajánlás korlát. Pontos csapattotal támogatás (NFL/NBA/WNBA: Overtime; MLB: Extra Innings), teljes meccs score-eloszlásból. MLS naptár és Swish-kínálati leltár hozzáadva. A friss ciklus 86 naptári meccsből 12-t talált 48 órán belül, 188 kimenetelt modellezett, mind átment az EvidenceGuardon. 11 új, 1 USD-os kiadás: napi 22/15, összesen 30 nyitott szelvény, 29 külön választás. A korábbi 19 szelvény, 18 döntés és a migrációkor meglévő 18 jelölés változatlan. 162 Python/API-teszt + 5 alteszt, 14 DOM-teszt sikeres. Teljes prop/részidő/MLS-modellezés és igazolt profit továbbra sincs. Részletes kiadási audit: `data/instructions-release-check.json`.

- 2026-09-28 23:27: javítva az MLB seed-neves naptárbejegyzések párosítása a tényleges, aktív Winner-kimenetel csapatneveiből, szigorú idő- és azonosságellenőrzéssel. Az első éles ciklus 144 kimenetelt modellezett (korábban 88), 12 meccsen; 2 új, egyenként 1 USD-os MLB-ajánlás jelent meg. A napi pozitív tétű kiadás 11/15; a korábbi 17 szelvény változatlan, 2 új feltételezett kézi fogadással 19 nyitott tétel. A web 18 különálló választást mutat: elöl a 2 jelöletlen, majd a 16 korábbi. Eredeti odds/esély megőrzése, konkrét Watchlist-blokkolási ok, teljes modellezett kimenetellista; archívum és egyedi szelvénytábla alapból zárt. 148 Python/API-teszt, 5 alteszt és 12 frontend DOM-teszt sikeres; élő Tailscale-nézet ellenőrizve.

- 2026-09-28: tartós projektkövetelmény-szerződés, futás közbeni EvidenceGuard, verziózott tudáskártya-hivatkozások, immutable saját döntésnapló és piaci leltár. A rendszer felelősségi körébe került mind a 16 korábbi, megjátszottnak jelentett választás, a 17 szelvény és az eredeti 9 pozitív tétű kiadás számának változtatása nélkül. A web Model Performance oldalán „Engine Accountability and Coverage” rész mutatja a döntéseredményeket, a lefedettséget és a kutatási sort. 144 Python/API-teszt, 5 alteszt és 10 frontend DOM-teszt sikeres. [Működés és tényleges korlátok](GOVERNANCE.hu.md).

- 2026-09-28 23:00 Budapest: felhasználói utasítás alapján eltávolítva a korábbi 10 USD összesített napi limit. Napi keret: nincs fix plafon; fogadásonként továbbra is 1–5 USD, jelenleg bankroll nélkül 1 USD alapegység. Nincs automatikus felsőlimit-emelés. Napi cél 15 különálló, téttel kiadott ajánlás, nem felső darabszámkorlát; 60 másodperces piaci ciklus mind az öt ligára. Az eredeti pillanatképekből rekonstruált kiadásszám 9, nem 17; a 17 bejelentett szelvény és 16 jelölés változatlanul megmaradt. Részletek: [kiválasztási audit](SELECTION_AUDIT_2026-09-28.hu.md).

- Személyes „Mark as placed” jelölő minden aktuális tipphez és alternatívához, szerveroldali mentéssel. Alaprendezés: előbb a jelöletlen, majd a megfogadottnak jelölt tippek, mindkét csoporton belül kezdési idő szerint. A jelölés visszavonható, frissítés és eszközváltás után megmarad, és független az automatikus fogadásnaplótól.

- Bet lifecycle: a felhasználó állandó utasítása alapján a pozitív téttel publikált, friss Stake-ajánlás automatikusan „assumed placed” naplótétellé válik. Nem kér megerősítést; a rögzített tét, odds és modellbecslés változatlan. Az azonos szerződés újraárazása nem új fogadás; új legjobb vonal vagy piac külön kiadható. A megfigyelési és nulla tétű alternatívák nem kerülnek pénzes fogadásként naplózásra.
- Végeredmény alapján automatikus won/lost/push és számított P&L, forrásbizonyítékkal. Ligánként/piaconként/modellverziónként Brier és log loss diagnosztika az eredeti előmeccses valószínűségből. Az automatikus tételek eredménye a javasolt ár alapján becsült, nem Stake-számlával egyeztetett profit. A manuális tételek kimutatása külön marad.

- Öt külön sportmodell, történeti eredményekből, sportstatisztikákból és MLB dobóelőzményekből; odds nélküli bemenet.
- Időrendi tanítás, külön modellkeverék-választás és kalibráció; konstans és Elo-benchmark, score-diagnosztika, verziózott modellek.
- Stake Sports Data API, külön NBA/WNBA/MLB/NHL/NFL tournament-lekérdezéssel; aktív kimenetelek, pontos teljes meccses piac, vonal és odds ellenőrzése. Polymarket nem az aktuális Stake-ajánlások forrása.
- Minden rangsorolt tipp és kiválasztott alternatíva látható a weben és tartósan naplózott. A korábbi jelölések archív kontextusban maradnak meg; a megkötött fogadások nem íródnak át.
- 48 órás és mai/holnapi webes rangsor, konkrét választásokkal, küszöboddsszal és téttel; automatikus frissítés és lejárat.
- Másik platformon talált odds számítása, napi keret és rész-Kelly, 1–5 USD korlát.
- SQLite publikációs/auditnapló, tényleges manuális fogadásnapló, rendszerköltség utáni egyenleg és előre rögzített jövőbeli becslések visszamérése.
- Tartós launchd-szolgáltatás ezen a Macen; újraindulás bejelentkezéskor és folyamatleállás után. Telegram-eseménynapló előkészítve, küldés nincs.
- Python/API és frontend DOM-funkciótesztek. A telepített modellek tényleges riportja a `data/models/{liga}.joblib` objektumokban található; a `summary.json` újabb, még nem előléptetett jelölteket is tartalmazhat. A mostani ellenőrzés: 125 Python/API-teszt és 5 alteszt, 9 frontend DOM-teszt sikeres.

Web: **http://127.0.0.1:8765**. Tailscale: **https://bela-macbook-pro-m4-pro.tail8a282e.ts.net:8443/**; HTTPS, API és automatikus eseményfrissítés ellenőrizve. Részletes működés, téthatárok és indítóparancsok: [IMPLEMENTATION](IMPLEMENTATION.hu.md).

## Még hiányzik

A folyamatos adat- és oddsfrissítés, valamint az ellenőrzött modell-újratanítás automatikus. A forráskód fejlesztése és kiadása külön munka; a háttérszolgáltatás nem programozza át önállóan saját magát. A további fejlesztés kötelező Stake-konzisztencia-szabályait a gyökérben levő `AGENTS.md` rögzíti.

Teljes játékos-, lineup-, QB-, kapus- és időjárási korrekció; több bookmaker hiteles árkapcsolata; végrehajtható történeti árakon végzett ROI/CLV; független jövőbeli profitbizonyíték; in-play modell; Telegram-bekötés; távoli 24/7 üzemeltetés. A retrospektív fejlesztési teszt nem érintetlen új bizonyíték az ismételt fejlesztői vizsgálatok után. Erre az indulás óta előre rögzített becslések szolgálnak.

A számítógépes hozzáférés engedélyei miatt vizuális böngészős ellenőrzés nem történt. Az API és a DOM-ban lefuttatott felhasználói funkciók teszteltek; ez nem helyettesíti a képi ellenőrzést.

## Tudástár

A könyvi olvasás és szintézis megmaradt. A [kártya–kód megfeleltetés](../knowledge/IMPLEMENTACIOS_KAPCSOLATOK.hu.md) mutatja, mely tanulság hol került be a rendszerbe. Az R04 teljes feldolgozása mellett az R02 teljes szövege elolvasott, vizuális/képletauditja még függőben. A többi külső kutatás nincs lezárva.

## Elérhető források

A `/Users/belavarga/Betting_tips_books` mappában 28 fájl van: 26 könyv-/tanulmányfájl és két új, azonos online forráslistát tartalmazó Markdown-/JSON-katalógus (B27/B28). A Downloads közvetlen olvasása továbbra is macOS-hozzáférési hibát ad, de az elérhető másolat miatt ez nem akadályozza az olvasást.

A legutóbbi két új forrás B25 Handbook of Sports and Lottery Markets (561 PDF-oldal, 231133 kinyert szó) és B26 Sports Trading on Betfair (29 EPUB-egység, 67778 kinyert szó). Mindkettő indexelve; B26 teljes szövege és 55 képe már feldolgozva, B25 teljes szövege és 268 kijelölt oldala is feldolgozva.

A korábban hozzáadott fájlok: Sharp Sports Betting két változatban; Sharper; Smart Sports Betting második példány; Statistical Sports Models in Excel; The Complete Guide to Sports Betting (Kevin Dolan); The Everything Guide to Sports Betting; Weighing the Odds in Sports Betting. Az új Smart Sports Betting bájtszinten azonos a korábban elolvasott B12-vel. A két Sharp-változat szövegét és ábráit összevetettem: B18 teljes feldolgozása B17 tartalmát is lefedi.

A két új katalógus teljes helyi szövege elolvasva. A benne felsorolt 18 külső forrás ellenőrzése megkezdődött. A hat tanulmányból R04 (Noel: Skill-Adjusted Expected Goals, v2) teljes 17 oldala és a 6–13. oldal ábrái/képletei feldolgozva, kritikai jegyzettel. Az eredmények empirikus reprodukciója még nincs meg. A többi külső forrás feldolgozása nincs lezárva; a letöltött anyag nem automatikusan elolvasott anyag.

## Tényleges olvasás

- Teljes könyv és forráskritikai jegyzet: B04 Fixed Odds Sports Betting, B12 Smart Sports Betting, B15 The Logic of Sports Betting; B21 Statistical Sports Models in Excel; B19 Sharper; B22 The Complete Guide to Sports Betting; B24 Weighing the Odds in Sports Betting; B18 Sharp Sports Betting (B17 tartalmi duplikátum); B23 The Everything Guide to Sports Betting (262 oldal, 17 fejezet, vizuális ellenőrzés és számszaki forráskritika).
- Teljes mellékelt korai kiadás és jegyzet: B06 Football Analytics with Python & R, hat fejezet. Ez nem a későbbi teljes kiadás.
- Teljes mellékelt ábra- és kódgyűjtemény: B11 Prompt Engineering for Generative AI, 311 oldal és 128 ábrás oldal. A magyarázó könyvszöveg nincs ebben a PDF-ben.
- Teljes rendelkezésre álló részlet és jegyzet: B07 Gambler 22. fejezete; B10 The Complete Book of Sports Betting töredékes PDF-je. Ezek nem jelentik a teljes eredeti művek elolvasását.
- Teljes tanulmány és jegyzet: B16 Wigner’s Puzzle.
- B05 és B20 bájtszinten azonos duplikátum, rendre B04 és B12 lefedi.
- Teljes könyv és 55 kép: B26 Sports Trading on Betfair, forráskritikai és számítási jegyzetekkel.
- Teljes könyv: B03 Analyzing Baseball Data with R, 418 oldal, 15 fejezet, 3 függelék, 88 oldal vizuális ellenőrzésével és részletes forráskritikával.
- Összesen 26 fájl rendelkezésre álló tartalma feldolgozott vagy ellenőrzött duplikátummal lefedett. Ez nem 26 teljes, különálló könyv.
- Teljes B08 Mathletics: mind a60 fejezet, bibliográfia és index elolvasva (1195992 karakter,609 PDF-oldal),180 különböző oldal vizuálisan ellenőrizve, kritikai jegyzettel és korrigált példaszámításokkal.
- B09 Monte Carlo or Bust: a teljes778539karakter/451PDF-oldal szövege elolvasva,250különböző oldal vizuálisan ellenőrizve; kritikai jegyzet és reprodukálható futamhossz-számítás mentve.
- B02 Gambler: mind a 28 fejezet és utóanyag, 661712 karakter, 30 kép feldolgozva. B01 AZW3 teljes szöveges összevetéssel és 30 kép külön vizsgálatával ellenőrzött tartalmi duplikátum.
- Teljes B13 Soccermatics: mind a 30 egység, 594495 karakter, 141 kép feldolgozva; részletes jegyzet és reprodukálható számszaki audit mentve.
- Teljes B14 Sports Betting For Dummies: 36 egység, 851882 karakter, 46 képfájl, részletes kritikai jegyzet és reprodukálható számszaki audit.
- Teljes B25 Handbook of Sports and Lottery Markets: 2292928 karakter, 561 oldal, 24 fejezet, bibliográfiák és indexek; 268 oldal vizuális ellenőrzésével és reprodukálható számszaki audittal. A hivatkozott eredeti cikkek olvasása és empirikus reprodukciója külön feladat.

Részletes, tartós olvasási bizonyítékok: `knowledge/reading_v2/reading_ledger.jsonl` és a `Bxx-notes.hu.md` jegyzetek. A `delivery_cursor.json` csak a kiadott szöveg helyét jelzi, nem minőségi készültséget.


## 2026-09-29 — Ligánkénti működési audit és differenciált tétek

Kiadva: projekt szerződés `2026-09-29.6`. A teljes leírás: [LEAGUE_OPERATIONS.hu.md](LEAGUE_OPERATIONS.hu.md). Mind az öt modellezett liga és az MLS-leltár folyamatos működési auditot kapott, a tényleges Stake-piacnevek katalógusa ugyanazzal a publikációval készül. Ligánkénti napi szakirodalmi lekérések, tartós helyreállítási feladatok, sérült modell izolálása és pontos korábbi archívumból történő visszaállítása. Nem implementált piacmodellt egy kész kutatási dosszié nem jelöl megoldottnak.

Új fogadásokra stresszelt valószínűségből, oddsokból és kapcsolódó meccskitettségből 1–5 USD tét. Bankroll nélkül kifejezetten heurisztikus alapegység-szorzó, nem személyre szabott Kelly. Minden régi jegy eredeti tét/odds/valószínűség adata megmaradt.

Éles ellenőrzés 12:25 CEST: helyi és Tailscale egészséges, 328/328 guard-ellenőrzés sikeres; 339 liga/forrás/piac név szerinti katalógussor, 6 ligasor, 30 fogadási kártya és 30 exact eseménylink. Mind a kilenc kutatási témalekérés sikeres, köztük a hat ligakeresés. A 36 korábbi jegy és minden checklist jelölés változatlan. A napi cél továbbra is 6/15 új ajánlás. A 15 aktuálisan kvalifikáló választás már korábban kiadott szerződés, ezért nem új utasítás.

Teljes játékos-, periódus-, kombinált, élő és MLS-modell, teljes sérülés/kezdőhatás, automatikus új forráskód implementálás és igazolt nettó profit továbbra sem teljesített képesség.

Validáció: 213 Python-teszt és 5 alteszt, valamint 23 frontend DOM-teszt sikeres. Az élő API adataival külön DOM-ellenőrzés is sikeres, JavaScript-hiba nélkül.

## 2026-10-02 — Élő Stake-katalógus kiértékelés és guarded engine release gate

Kiértékelés: a meglévő Stake Sports Data API archívumból 3 478 ajánlati sor és 523 piaccsalád került újraértékelésre. A `data/market_catalog/latest.json` minden sorában szerepel a 48 órás előfordulás, párosítás, scope-modell, settlement, adatforrás, döntési lánc és a lezárt CLV/log-loss állapota; a nem bizonyított mezők `not_established`/`null` értéket tartanak.

Fejlesztés: a composite `Winner & Total`, `Total (Over-Exact-Under)` és `Correct Score` szerződésfelismerés, elszámolás és piaci horgony regressziós teszteket kapott. A release policy most exact `guarded_engine` profilt és Codex-felülvizsgálati mezőket tartalmaz; a ledger, guardok, CLV-kapu, USD1–5 sáv, `daily_risk_limit=null` és automatikus fogadásleadási tiltás változatlan.

Mért célzott validáció: 92 release/recovery/guarded-profile teszt és 73 composite/anchor/market/operations teszt sikeres. A teljes Python/web release-csomag és trusted live release még a következő lépés; ezért az aktív composite capability állapota `awaiting_release`, a fennmaradó Stake-piaccsaládok `not_established`/`operating_gaps` állapotban maradnak.


## 2026-10-02 — D33 trusted live release lezárva

Élesítés: `release-20261002T194059127957-600e50e3`, `D33`, `guarded_engine`, 10 hash-azonosított fájl. A teljes Python release-suite `6119 passed, 1 skipped, 5 subtests passed`; a DOM kapu sikeres. A független Codex-review elfogadta a bounded composite parser/settlement és EvidenceGuard-gating változást; a composite családok validáció hiányában fail-closed, nincs új statisztikai modell- vagy automatikus fogadásleadási engedély.

Élő bizonyíték: a trusted release után a helyi és Tailscale `/api/health` egyaránt `ok=true`, `engine.error=null`, friss publikáció `2026-10-02T19:47:51.457743+00:00`, `guard_consistent=true`. A release coordinator új verziója `root-guarded-engine-release-v1`, az engedélyezett betting capability `true`; az immutable ledger, CLV-kapu, USD1–5 sáv, `daily_risk_limit=null` és az automatikus fogadásleadás tiltása változatlan. A katalógusban megmaradt 523 Stake-piaccsalád és minden nem bizonyított család `not_established`/`operating_gaps` állapotban van.
