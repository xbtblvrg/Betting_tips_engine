# Teljes rendszer műszaki auditja — 2026. október 2.

**Minősítés: működő szolgáltatás, részlegesen működő háttérfolyamatok, nyitott hibák. A teljes engine nem kész.**

Az audit az adatút, az eredménynyilvántartás, a web/API, a felügyelet és a fejlesztési-kiadási kör általános szoftverhelyességét vizsgálta. Ez nem új fogadási stratégia, modelljóváhagyás vagy nyereségigazolás. Éles adatot, konfigurációt, szelvényt, forráskódot vagy futó szolgáltatást nem módosítottunk. A hibákat ideiglenes környezetben reprodukáltuk; az éles felületen csak olvasás történt.

Kiinduló verzió: `923996d74ed49db84782f3c07ffde0ac1eae3d3a`. A Git által még nem követett, telepített fájlokat is külön hashjegyzék rögzíti. A 10:58:52 UTC-kor felvett 441 fájl egyike sem változott a 11:10:15 UTC-kor elvégzett összehasonlításig. A pillanatképek eltérő időpontúak; az időket nem mossuk össze egyetlen adatállapottá.

Bizonyítéktár: [data/full-system-audit-20261002](../../data/full-system-audit-20261002). A reprodukciók és a három részletes szakági jelentés ott található; a fájlok SHA-256 jegyzékét az `audit-manifest.json` tartalmazza.

## Fontossági sorrendben

P1: ténylegesen blokkolt fontos munkafolyamat vagy hibás sikerjelzés; P2: reprodukált helyességi hiány, illetve még nem teljesen igazolt éles hatás. A részjelentések eredeti besorolása megmaradt. A cache-hibát a futási reviewer P1-nek minősítette; az összesítés P2-ként kezeli, mert az integritássérülés izoláltan igazolt, éles előfordulását nem bizonyítottuk.

### 1. P1 — Az eredmény-visszacsatolás hibás bizonyítékot is sikeres munkának könyvelt el

A visszacsatolás kilenc hivatkozott jelentése összesen **78 484 564 bájt**, az olvasó összesített korlátja viszont **67 108 864 bájt**. A fájlok hashei egyeznek, de a beolvasás `feedback_total_evidence_limit` hibával meghiúsul.

Az író előbb elmenti az új nyilvántartást, majd a hibás olvasási eredményt visszaadja. A munkafeldolgozó ezt nem ellenőrzi sikerfeltételként. A `467119fd27a6dad5cac75385` feladat 03:55:24 UTC-kor `completed` lett, miközben a saját eredményében `evidence_invalid`, `source_verified=false` és nulla javaslat szerepel. A későbbi próbálkozások ugyanabba az akadályba ütköznek.

Érintett pontok: [outcome_feedback.py:159](../../src/betting_engine/outcome_feedback.py#L159), [outcome_feedback.py:207](../../src/betting_engine/outcome_feedback.py#L207), [learning.py:388](../../src/betting_engine/learning.py#L388), [learning.py:434](../../src/betting_engine/learning.py#L434).

Elfogadási feltétel: a mérethatárnál nagyobb, érvényes történet korlátos erőforrással feldolgozható legyen; érvénytelen visszacsatolás ne kaphasson sikeres státuszt; a pontos hiba és az eredeti történet maradjon meg. Hibainjektálás, újraindítás és friss, sikeres feldolgozás bizonyítsa a helyreállítást. A határ puszta megemelése vagy a régi bizonyíték törlése nem teljes javítás.

Bizonyíték: `accounting-lineage-and-jobs.json`, `accounting-feedback-intake.json`, `accounting-raw-proofs.json`.

### 2. P1 — A hibafelismerés nem zárul minden esetben végrehajtott javítássá

A fenti visszacsatolási hiányt a felügyelet már **339 alkalommal** észlelte; határideje 07:55:39 UTC-kor lejárt. A hozzá rendelt `E6985CA1F4D-3` legutóbbi `candidate_ready` kísérlete október 1-jei, tehát a jelenlegi, október 2-án megjelent hiba előtti. Ez nem bizonyítja a mostani probléma javítását.

A `ROOT-LEARNING-PROGRESS-20261002` feladat határideje 10:30 UTC volt. Továbbra is `diagnosed`; automatikus indítása nincs igazolva, és nincs a fejlesztői ütemező feladatai között. A határidőt nem módosítottuk.

Elfogadási feltétel: az aktuális hibagenerációhoz tartozó végrehajtás, kizárólagos feladatfoglalás, teszt, független review, ellenőrzött kiadás és friss működési bizonyíték legyen visszakövethető. Egy korábbi jelölt vagy újabb észlelési számláló nem zárja le a hibát.

Bizonyíték: `accounting-feedback-intake.json`, `runtime-review.json`, [eredeti feladat](../../data/fixture-delivery-followup-20261002/learning-progress-task.json).

### 3. P1 — A fejlesztői kör két aktuális munkája bizonyíthatóan elakadt

- **F0038:** a `20261002T100841348495-F0038` futás teljes Python-tesztelése hat hibával állt meg. A visszatartott bemeneti állományok között hash/méretegyezési probléma, valamint NCAA-regiszter-visszajátszási hibák szerepelnek. Ez nem készre tesztelt kiadás.
- **D20:** az előző futás még a tesztfolyamat elindulása előtt egy nem engedélyezett `data/engine.sqlite3` bemeneten állt meg. A javítási útvonal mégis teszthibaként kezelte, és olyan tesztnaplót követelt, amely nem jött létre (`logs=[]`). Emiatt a `20261002T101552778431-D20` futás már a megvalósítás előtt leállt. A rögzített következő próbálkozás október 3. 10:16:33 UTC; ezt a várakozást az audit nem tekinti megoldásnak.

A fejlesztés nem teljesen áll: a pillanatképben D22/D23 körüli munka és valódi AI-szolgáltatóválaszok is szerepelnek. A segédmodellek `received_unreviewed` válasza azonban még nem ellenőrzött implementáció. Az egyes futások és hibák pontos részleteit a `runtime-review.json` rögzíti.

Elfogadási feltétel: az aktuális bemenetekből izoláltan megismételhető futás, megőrzött sikertelen tesztek, sikeres teljes ellenőrzés és külön kiadási bizonyíték. A regressziós teszteket nem szabad a hiba eltüntetése érdekében gyengíteni.

### 4. P2 — A „Mark as placed” mentése határidő nélkül beragadhat

A kérésnek nincs lejárata. Egy soha vissza nem térő mentésnél a jelölés optimistán eltünteti a tételt, letiltja annak vezérlőit, és `Saving…` állapotban marad tíz sikeres későbbi frissítés után is. Ez izolált böngészőtesztben reprodukálható.

Érintett: [app.js:44](../../src/betting_engine/web/app.js#L44), [app.js:82](../../src/betting_engine/web/app.js#L82). Elfogadás: korlátos várakozás, az elveszett válasz utáni szerverállapot-egyeztetés, ismételhető mentés és több eszköz frissítéseinek tesztje. Az elveszett válaszból nem szabad automatikusan sikerre vagy sikertelenségre következtetni.

Bizonyíték: `web-stalled-placement.json`, `web-probe.mjs`.

### 5. P2 — Egy régebbi Tracker-válasz visszaírhatja a már lezárt állapotot

A `renderLedger()` nem védi a válaszok sorrendjét. Reprodukcióban a frissebb `Manually Settled` állapotot egy korábban indított, később megérkező válasz `Open` állapotra cserélte, és visszahozta a lezárógombot. Ez megjelenítési hiba; az adatbázis nem sérült, a szerver az ismételt lezárást elutasítja.

Érintett: [app.js:320](../../src/betting_engine/web/app.js#L320). Elfogadás: elavult válasz nem írhat felül újabbat; a lezárást, párhuzamos olvasásokat, elakadást és újrapróbálkozást is tesztelni kell.

Bizonyíték: `web-ledger-race.json`, `web-ledger-race.mjs`.

### 6. P2 — A forráscache tartalmát nem ellenőrzi a hozzá tartozó hashhez

A friss cache visszaolvasásakor a `Fetcher.get()` ellenőrzés nélkül visszaadja a bájtokat és a korábbi hash-metaadatot. Két független, kizárólag ideiglenes könyvtárat használó reprodukcióban megváltoztatott tartalmat fogadott el az eredeti hash alatt. Hálózati kérés nem történt. **Tényleges éles cache-sérülést ez az audit nem bizonyított.**

Érintett: [providers.py:24](../../src/betting_engine/providers.py#L24). Elfogadás: eltérő hash, méret, hiányzó vagy hibás metaadat ne válhasson hiteles forrásként továbbadott tartalommá; a hiba és a helyreállítás legyen bizonyítható.

Bizonyíték: `cache-integrity-repro.json`, `runtime-cache-integrity-independent.json`.

### 7. P2 — A történeti eredmények forrásbizonyítéka nem mindenhol teljes

A 278 explicit NHL-archívumhivatkozás 16 külön fájlra mutat; mind megvan, a hashük egyezik. Más történeti rekordok egy része azonban csak hashre vagy URL/időpontra hivatkozik. Hét hashhez, **32 szelvényhez** nem találtunk tartalomazonos forrásmásolatot a `data` alatti, hash alapján elnevezett fájlok keresésével. Nem hash-azonosítós nevű, tetszőleges fájlok teljes tartalmi átvizsgálását ez nem helyettesíti. Egy további, 21 szelvényt érintő NFL-forrásnak van megőrzött, bájtonként hash-ellenőrzött fejlesztői exportmásolata, bár a kanonikus cache-fájl hiányzik.

Ez a forrásból történő visszaellenőrzés hiánya, nem bizonyíték hibás kifizetésszámításra. A törlés okát nem igazoltuk. A [maintenance.py:6](../../src/betting_engine/maintenance.py#L6) ugyan törölhet régi cache-változatokat, de ebből önmagában nem következik, hogy ez okozta az adott hiányt.

Elfogadás: az eredeti elszámolások tartós, tartalomazonos forrásra hivatkozzanak; a bizonyíthatatlan történeti rekordok hiánya maradjon látható. Utólag letöltött adat nem kaphat hamis eredeti megfigyelési időpontot.

Bizonyíték: `accounting-feedback-intake.json`, `accounting-hash-only-raw-search.json`, `accounting-hash-only-retained-search.json`.

### 8. P2 — Két további elszámolási/importálási szélső eset hibás

Az importazonosító a numerikus normalizálás előtt különbözteti meg az `1` és `1.0` értéket. A már importált, számszerűen azonos bizonylat ilyen újraszerializálása tévesen eltérő importnak minősül. Az eredeti 17 tételes import és az auditnyoma egyébként egyezik, a szándékosan kétszer megkötött tétel két külön szelvényként maradt meg.

A kézi lezárás az utolsó 500 tétel listájában keres azonosítót. Izolált, 501 rekordos esetben a létező régi nyitott rekordra 404-et ad. A vizsgált éles számláló még nem érte el ezt a küszöböt; ez reprodukált határeset, nem megfigyelt éles incidens.

Elfogadás: jelentésazonos numerikus importok idempotensek legyenek, a ténylegesen eltérő bizonylatok maradjanak megkülönböztetve; a kézi lezárás azonosító szerinti keresést használjon listalapozás helyett. Az eredeti döntések és auditnyomok megőrzése kötelező.

Bizonyíték: `accounting-import-numeric-replay.json`, `accounting-import-verification.json`, `accounting-open-and-manual-checks.json`. Pontos forráshelyek: `accounting-review.json`.

## További hiányok és vizsgálati korlátok

- **Folyamatjelzés:** hosszabb munkák alatt nincs külön lépésenkénti előrehaladási bizonyíték. A felügyelet akár 7200 másodpercig engedi a futó munkát, miközben a frissességi ellenőrzés 180 másodpercet vár. A futó PID és az utolsó heartbeat nem igazolja a feldolgozás eredményességét. A már létező, lejárt javítási feladat fent szerepel.
- **Négy régi nyitott NCAAF-rekord:** a 11:08 UTC-kori vizsgálat két olyan meccsazonosítót talált, amely hiányzik a helyi `games.json` állományból, és a rögzített kezdése több mint nyolc órával korábbi. A külső meccsvégeredményeket ebben az auditban nem igazoltuk; ezért ez nyitott egyeztetési hiány. Ugyanilyen régi nyitott NHL-esetet nem találtunk.
- **Lefedettség:** a komponensjelentés kilenc megvalósítási hiányt jelez, és a `full_market_coverage` kifejezetten `false`. A visszaadott feed archiválása nem azonos az összes webhelypiac teljes támogatásával. A kutatási sor vagy betöltött modellfájl sem bizonyít önmagában validált képességet.
- **Hozzáférés:** az alkalmazás Host/Origin szűrést használ, külön felhasználói hitelesítést nem. A biztonsági határ a helyi/Tailscale-elérhetőség. Az audit nem bizonyított nyilvános internetes kitettséget vagy idegen hozzáférést; a tailnet tényleges jogosultsági szabályai nincsenek itt tanúsítva.
- **Erőforrás:** a kötet 84%-ban foglalt, még körülbelül 297 GiB szabad. A fejlesztési könyvtár `du` szerinti mérete mintegy 556 GiB; APFS-másolatok közös blokkjai miatt ez nem feltétlenül ugyanennyi kizárólagos fizikai foglalás. Nem bizonyított lemezbetelés, viszont a másolatmegőrzés nyitott feladat. A dashboard egy mérése 24,5 MB kitömörített választ adott 2,84 másodperc alatt; időtúllépést ennél nem reprodukáltunk.

## Amit működőnek ellenőriztünk

| Rész | Megfigyelés | Bizonyíték határa |
|---|---|---|
| Helyi oldal és Tailscale | Mindkettő HTTP 200; a governance-válasz azonos hashű | Az ellenőrzés időpontjának elérhetősége |
| Adatfrissítés | Friss publikáció; 121/121 katalóguseseményhez 242 archivált válasz | Az elérhető feed pillanatképe, nem teljes sport/piaclefedettség |
| Publikációs ellenőrzés | A production hívás `require_guard=True`; 870 ellenőrzésből 111 elutasítás | A validáció végrehajtását igazolja, nem a becslések gazdasági minőségét |
| Checklist szerveroldala | Két egymás utáni és 12 párhuzamos izolált jelölés egy rekordot tartott meg | A kliensoldali elakadási hiba ettől még fennáll |
| Nyilvántartás számtana | A pillanatképre elvégzett ellenőrzés nem talált számtani eltérést | Nem közvetlen Stake-fiókegyeztetés |
| Kód és meglévő webtesztek | 119 Python-fájl szintaktikai ellenőrzése; 296/296 kiválasztott DOM-teszt sikeres | Nem az egész engine aktuális teljes regressziós tesztelése |

A régebbi kiadás sikeres teljes tesztjeit nem használjuk a mostani teljes kódállapot készre jelentésére. Az aktuális fejlesztői jelöltek ismert hibái megmaradtak. A jelen audit új reprodukciói nem változtatták meg az eredeti teszteket.

## Történeti nyilvántartás — 11:00:16 UTC / 13:00:16 Budapest

- Összes szelvény: **409**; lezárt: **274**; nyitott: **135**.
- Lezárt eredmény: **111 nyertes, 157 vesztes, 6 push**.
- Találati arány: **41,42% = 111 / (111 + 157)**. A push nincs a nevezőben.
- Lezárt tétek összege: **306,00 USD**; rögzített visszafizetés: **261,7708 USD**.
- Lezárt nettó fogadási eredmény: **−44,2292 USD**; ROI: **−14,45%**.
- A lezárt szelvények 32 mérkőzéshez tartoznak; nem 274 független megfigyelés.

Az automatikusan feltételezett és a felhasználó által jelentett végrehajtást a rendszer külön jelöli. Ezek az értékek a helyi nyilvántartás eredményei, nem ellenőrzött sportsbook-egyenlegek. A működési költségek nincsenek levonva. Bizonyíték: `accounting-statistics-snapshot.json`.

## Készültség és átadás

**Az audit elkészült; a felsorolt javítások ettől még nem készültek el és nem lettek telepítve.** A bizonyítékbeolvasási hiba, annak sikeresként könyvelése és a feladatok tényleges végrehajtásba kapcsolása elsőbbséget igénylő műszaki problémák. A részjelentések minden reprodukált hibához ellenőrizhető elfogadási feltételt adnak.

A meglévő fejlesztői munkák futása nem jelenti azt, hogy az itt feltárt új hibák már automatikusan javítás alatt állnak. A mostani auditanyag tartós hibajegyzék, nem új, működő ütemező vagy elkészült kiadás. Új javításról csak implementáció, teszt, független review, telepítés és élő visszaellenőrzés után lehet kész állapotot jelenteni.
