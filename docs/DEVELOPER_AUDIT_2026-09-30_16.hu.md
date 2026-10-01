# Cél- és fejlesztői audit — 2026-09-30, kiadás 16

A kiadás a „Recheck Required” valódi döntési hibáit és a főoldali végrehajtási folyamatot javítja. **A teljes engine még nem kész, a tartós nyereség nem igazolt.** A fő cél változatlan: saját sportadatokból képzett, mérhető döntések, friss pontos Stake-kontraktusokkal és indokolt 1–5 USD téttel. A napi minimum 15 külön első pénzes kiadás nem ad felmentést hiányos adat vagy kitalált edge használatára.

A teljes korábbi követelményjegyzéket áttekintettük; az alábbi táblázat a kiadás15 bizonyítékaira épül, és a kiadás16 új eredményével módosítja R09/R13/R19/R30-at. Ez nem minden könyv újraolvasása, és nem az összes hiányzó piacmodell elkészülésének állítása.

| ID | Felhasználói cél | Auditált állapot és bizonyíték |
|---|---|---|
| R01 | Saját sportmodell, utána piacösszevetés | Működik: `models.py`, `governance.py`, oddsfüggetlen feature- és provenance-ellenőrzés. A professzionális modellek teljes piaccsaládos validációja még D18. |
| R02 | Könyvek, alapos tudástár és felhasználható tudás | Részleges: 28 leltári forrásbejegyzés, olvasási napló, 12 módszertani kártya, hivatkozások a guardban. A fájlleltár, a teljes olvasás és a módszer implementálása külön állítás; az új audit nem olvasta újra valamennyi könyvet. |
| R03 | Minden kért liga, ma és +48h | Folyamatos felderítés/értékelés működik; NBA, WNBA, MLB, NHL, NFL és NCAA-futball/férfi-női kosár modellek. További Stake NCAA-sportok felderítettek lehetnek, önálló modell nélkül nem támogatottak. |
| R04 | Friss pontos Stake-piac, azonnali kiírás | Működik a támogatott scope-oknál: exact event/outcome/line/odds, frissesség, publikáció és SSE. A teljes weboldali kínálat lefedettsége nem bizonyított. |
| R05 | Napi legalább 15, lehetőleg több | Budapest-napos külön első kiadások mérése működik, nincs felső darabszámkorlát. Egy nap teljesítése nem bizonyít folyamatos teljesítést. Hiány esetén fejlesztési feladat keletkezik; kutatási jelölt és újraárazás nem számít kiadásnak. |
| R06 | 1–5 USD, nincs napi összkeret | Működik, `daily_risk_limit=null`. Új tét újraszámolható; az 5 USD plafon nem nő automatikusan. Nem ismert bankrollból nem készül személyre szabott Kelly-állítás. |
| R07 | A rendszer minden kiadott fogadást sajátjának tekint | Működik: változatlan `system_decisions`, veszteségek és eredeti modell/odds/tét megmarad. |
| R08 | Minden kiadott ajánlást manuálisan megfogad a felhasználó | Működik a standing instruction alapján feltételezett végrehajtásként. Nem Stake-fiókból igazolt leadás. Jelentett duplikált szelvény külön pénzmozgás, nem új külön döntés. |
| R09 | Angol, Stake-nyelvű UI és stabil checklist | Angol UI, pontos Stake terminológia; főoldalon csak aktuálisan végrehajtható, jelöletlen utasítás. A jelölés a Bet Trackerbe teszi a tételt, visszavonható, az elszámolást nem módosítja. Új DOM-regressziók. |
| R10 | Web, Tailscale, később Telegram | Web/Tailscale működése élő kiadáskor ellenőrizendő; Telegram a felhasználó által későbbre kért, nincs bekapcsolva. |
| R11 | Autonóm elemzés és modellfejlesztés | Kontrollált újratanítás és kísérlet működik. Nem egyenlő bizonyított javulással; elutasított modellek megmaradnak. |
| R12 | Minden piac és Swish is legyen értelmezve | Teljes kapott válasz archiválása és leltározása működik. A felismerés nem önálló modell és nem igazolt fogadhatóság. |
| R13 | Konkrét utasítások; új meccsen belüli ráfogadás | Kiadás16: csak friss, igazolt konkrét utasítás és 1–5 USD. Történeti/újraellenőrzést igénylő/research jelölt nem főoldali bet. Külön minősített további kontraktus megengedett; eredeti azonos ticketet nem írjuk át vagy ismételjük. |
| R14 | Tartós, külön tanuló munkás | Működik: feladatok, kísérletek, backoff, újraindulás és outcome review. |
| R15 | Ha nem javul, keressen más modellt | Előre rögzített modellreceptek keresése működik időbeli elkülönítéssel. Azonos adatok végtelen újrakeresése és final-test tuning nem megengedett; tetszőleges új ötlet külön kódfejlesztés. |
| R16 | 24/48h meccs/piac/értékelt/fogadott számlálók | Azonos publikációból számolt bontás működik. Napi kiadás, jelenlegi ajánlás, nyitott rekord és feedkimenetel eltérő szám. |
| R17 | Kutatás, önellenőrzés, helyreállítás folyamatosan | Külön piaci/tanuló/fejlesztő folyamat és perces supervisor; napi elsődleges szakirodalmi keresés. A cím/absztrakt felfedezése nem teljes tanulmányfeldolgozás vagy implementált javulás. |
| R18 | Bet gomb vigyen a konkrét fogadáshoz | Pontos Stake-eseménylink működik, mellette piac/választás/tab. Előre kitöltött konkrét szelvénylink nincs igazolva; eseménynavigáció a tényleges képesség. |
| R19 | Teljes döntési flow, guard és frissesség | Kiadás16: kötelező input-readiness és tényleges SHA-256 döntési pecsét; minden liga teljes véges feature-vektor, MLB nyers forrásig ellenőrzött starteradatok. Aktuális policy és támogatott eredeti tét külön ellenőrzött. A modell/input-változás elkülönül az árromlástól. Teljes modellminőségi és profitbizonyíték továbbra sem kész. |
| R20 | Ligahiány és hibás modellezés automatikus javítása | Tartós diagnózis, adatjavító feladatok és immár fejlesztői intake működik. A sorba állítás nem kész javítás; az új éles szoftverkiadás továbbra is külön átvétel. |
| R21 | MLB inningpiacok saját modellel | Két inning-scope önálló megfigyelt targettel, időrendi kapukkal és scope szerinti elszámolással. Az összes inningpiac teljes támogatása nem igazolt. |
| R22 | Stake-tabok/megnevezések egyezése | Megfigyelt feed group/tab és pontos szerződés bizonyítéka megőrzött; inning Total nem helyettesíthető teljes meccses Total-lal. |
| R23 | Minden fogadástípus, player props, specials, SGM | **Részleges, jelentős nyitott munka.** 31 szakaszmodell létezése nem minden család jóváhagyása. Props/részvétel/eseménysorrend/special/SGM teljes éles támogatása hiányzik. |
| R24 | Hivatalos API, összes endpointválasz és NCAA | Fixture+odds teljes válaszai időbélyeggel/hashsel archiváltak, dinamikus NCAA-leltár működik. Az API-ban megfigyelt kínálat nem az összes weboldali piac bizonyítéka. |
| R25 | Határidős, felügyelt valódi fejlesztés | Működő izolált Codex-implementáció, teljes regresszió, külön Codex-review, tartós hibák. A tesztelt jelölt nem éles képesség. |
| R26 | Codex az agy; Claude/agy/Grok segíthet | Új kiadás: Codex elsődleges fejlesztő és végső felügyelő; Claude másodlagos, agy/Grok harmadlagos korlátozott tanácsadó. Minden felhasznált segédválasz hashhez kötött Codex-értékelést kap. |
| R27 | Párhuzamos munka; Claude Opus5.5; agy CLI | Három izolált Codex-fejlesztési sáv; integráció és union-regresszió soros. Claude `claude-opus-5-5` tényleges modellazonosítója próbahívással igazolva. Google `agy`, modell-visszaigazolással. |
| R28 | Minden komponens ismerje a célt, ne tűnjön el a hiány | Új perces engineering intake: közös cél, tartós hibaazonosító, eredeti határidő, regresszió-újranyitás, ellenőrzött lezárási bizonyíték. Nincs pusztán eltűnt horizonból „megjavult” állítás. |
| R29 | Winner/Handicap/Total hangsúly, valódi 1–5 sizing, tiszta hedge | A három fő család előbb kap méretezést; a családon belüli jobb érték és modellezési kapu megmarad. Külön core-market audit minden meccsre. Teljes közös kitettségű hedge/portfólióoptimalizáló még D16, nem kész. |
| R30 | Végén teljes cél- és fejlesztői audit | Ez az újraellenőrzött R01–R30 jegyzék, a kiadás16 regressziói, független Codex-review, nyers bemeneti hibareprodukció és telepítés utáni élő bizonyíték. |

## Azonosított okok és javítások

1. **Hiányzó MLB kezdődobó mégis pénzes ajánlást eredményezett.** Az átlag-prior hiányzó bemenetként nem zárta ki a publikációt. A Padres–Cubs első öt inning Under 3.5 modellvalószínűsége 75,8624%-ról 39,8922%-ra változott a dobó megérkezésekor; Under 2.5 esetén az odds változatlan 2,85 volt. A friss state-ben az eredeti hiányzó dobót visszaállítva az eredeti valószínűség reprodukálható. Az új gate minden MLB-scope előtt mindkét startert, a tényleges gameLog forrásokat, identitást, időrendi elérhetőséget és a hét starter feature reprodukcióját ellenőrzi. Minden ligánál teljes véges feature-vektor szükséges. Részletes bizonyíték: `docs/INPUT_READINESS_AUDIT.hu.md`, `data/clean-picks-20260930/readiness-stage/audit/input_readiness_probe.json`.
2. **A régi aktuális ellenőrzés csak egy passed flaget vett figyelembe.** Most ténylegesen újraellenőrzi a numerikus/szerződéses pecsétet, readiness-t, frissességet, aktuális minimum-ROI-t és mind az öt tétbeállítást. A modell/input-változás külön ok az oddsromlástól. A determinisztikus ellenőrzés nem külön LLM-jóváhagyás.
3. **Eredeti és aktuális értékek keveredtek.** Az egyetlen, még nem jelölt, feltételezetten megfogadott döntés eredeti tétje csak friss, azonos kontraktushoz tartozó elemzés és aktuális kitettség mellett jelenhet meg. Az eredeti tét nem emelkedik és nem íródik át. Ha már nem támogatott, a rekord megmarad a Trackerben. Manuális/dupla rekord sosem új főoldali utasítás.
4. **A részletablak régi esélyt és árat tarthatott meg.** Frissült értékeknél bezáródik; a rendezés is aktuális eséllyel/ranggal történik. A főoldali és részletablakos Stake-link végrehajtáskor ellenőrzött. A helyi lejárat hálózati válasz nélkül is eltávolítja az elavult instrukciót. Aktív utasítás a friss igazolt eseménylinket használja.
5. **Az egész felület következetes.** Főoldal: Ready to Bet, pontos piac/választás/line, aktuális odds/esély, egyszeri USD-tét. Tracker: eredeti fogadások, oddsok, tétek, jelölések és veszteségek. Research: minden kutatási és nem minősített jelölt, lefedettség és hibák. Nincs átnevezett bizonytalan jelölt vagy elrejtett veszteség.

6. **A kézi oddsellenőrző sem kerülheti meg a döntési kaput.** Kötelező ugyanaz a friss readiness/seal/settings ellenőrzés; hibánál nulla tét és nincs minősített ár. Az érvényes kézi számítás is hipotetikus, nem ellenőrzött Stake-ajánlat, nem hoz létre instrukciót vagy ticketet.

## Megőrzött működés és nyitott feladatok

A normál forrásfrissítés és újraértékelés tovább működik; új kezdődobóadat után a következő futás ismét elvégzi a readiness-t. Az összegzett napi kiadás a korábbi, ma először kiadott döntéseket is tartalmazza, ezért nem azonos az éppen megtehető utasítások számával. A már kiadott hibás döntések is megmaradnak a tanulási/eredménynyilvántartásban.

A teljes sérülés-/lineup-hatás, profi full-game családonkénti teljes minőségi elfogadás, player props, specials, joint/SGM, live és további NCAA capability-k a meglévő fejlesztési sorban maradnak. A readiness javítása nem bizonyítja e modellek elkészülését. A Codex fejlesztő/reviewer és párhuzamos feladatvégrehajtás tovább működik, a korábbi határidők és sikertelen próbák megőrzöttek. A publikációs védelem új modulja védett fejlesztési felület.

## Kiadási bizonyíték

A végleges regresszió, telepítési napló, eredeti ledger hash-ellenőrzés, friss éles publikáció és TLS-ellenőrzött Tailscale DOM-próba a `data/clean-picks-20260930/` alatt található. Az alábbi záró mérés kerül ide a tényleges üzempróba után; addig ez nem kész telepítésigazolás.

Staging ellenőrzés: **1220 Python teszt + 5 alteszt, 53 DOM teszt**; egy meglévő Starlette/httpx deprecation figyelmeztetés. Független Codex-review minden feltárt módosításra. A 72 korábbi sealed auto-rekord (23 v1, 49 v2 sizing) eredeti budget-je read-only módon valid; 43 esetben a matematikailag azonos ROI 1e-16 eltérését helyesen toleráljuk, lényeges eltérést új negatív regresszió tilt. Az eredeti pénzösszeg és pecsét egyezése továbbra is kötelező.


### Éles záró mérés — 2026-09-30 11:23 CEST

A 18 fájlos kiadás éles. A 2908-as publikáció 11:20:33 CEST-kor keletkezett az új `2026-09-30.16` követelményverzióval; helyben friss governance-válasz és tanúsítvány-ellenőrzött Tailscale HTTPS is igazolta. Az éles HTML/JS hash egyezik az átvett kóddal. Read-only DOM próba: **11 végrehajtható, jelöletlen instrukció**, 11 pontos jelenlegi Stake-eseménylink, minden részletablak ellenőrzött; nincs Recheck Required, undefined/NaN, JavaScript-hiba vagy próba által kezdeményezett írás. Az 11 jelenlegi utasítás mind korábban egyszer kiadott 1 USD-s tét; nem 11 most létrehozott új ticket és nem egy egységesre átírt tétméretezés.

1082 aktuális modellezett kimenetel: MLB240/NHL718/NFL80/WNBA44, mind új readiness-bizonyítékkal. 29 kimenetel a jelenlegi értékhatár felett; ez átfedő piacokat és már kiadott szerződéseket tartalmaz, nem 29 új fogadás. 93 nyitott tracked rekordból 51 meccs már elkezdődött, 12 aktuális feltételekkel is végrehajtható (egy már jelölt), 9 korábbi tétet a friss méretezés nem támogat, 17-nél modell/input-változás, 1-nél tiszta árromlás, 3-nál hiányzó exact quote az ok. Ezek mind eredeti rekordként megmaradtak.

Napi első pénzes kiadás: **46/15**, ebből MLB23/NFL6/NHL8/WNBA9. Ez a nap eddigi kiadásainak történeti száma, a korábbi hibás döntések is részei; nem újra megteendő lista vagy igazolt nyereség. A korábbi 128 ticket, 127 rendszer-döntés és 120 elsőkiadási rekord immutábilis mezői egyeznek, SQLite quick_check ok, DB-visszaállítás nem történt.

A supervisor engine/learning/development/release szolgáltatásai healthy állapotúak. A developer üresjáratban, futó munka megszakítása nélkül újraindult az új védett modul szabályával; `enabled=true`, maximum 3 párhuzamos sáv. Az aktuális pillanatban 0 aktív kódolási sáv, 21 candidate-ready és 18 retry feladat szerepel; egy candidate-ready elem nem éles képesség, a retry eredeti késleltetése nem lett megkerülve. A teljes engine `fully_ready=false`. NCAAF injury input továbbra nem integrált, 10 operating gap marad; ezt nem jelöltük egészséges teljes modell-lefedettségnek.

Bizonyíték: `combined-python.log`, `ui-tests.log`, `manifest.json`, `release-backup/deployment.json`, `live/live-dom-check.json`, `live/verification.json`, `live/development.json`, `live/local-governance.json`. A tényleges tesztelt forrás 1220 Python + 5 alteszt és 53 DOM tesztet teljesített; a dokumentum utólagos záró kiegészítése külön hash-bizonylatot kapott.
