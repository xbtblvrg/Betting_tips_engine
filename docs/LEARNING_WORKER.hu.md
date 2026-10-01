# Ellenőrzött tanulási végrehajtó — 2026-09-29

Cél: a végrehajtható tanulási feladatok tényleges futtatása, a hibák és kutatási hiányok tartós követése úgy, hogy a kísérletek ne állítsák meg az aktuális ajánlások publikálását. A nettó profit és a napi 15 ajánlás cél marad, nem bizonyított eredmény.

Adatút: sportadat és lezárt döntés → kutatási áttekintés → tartós munkasor → külön worker → mérés → elfogadási kapu → atomikus modellcsere vagy archivált elutasítás → következő publikáció. A tippszolgáltatás a legutóbbi teljes modellverziót tölti be.

## Folyamatok és adatok

- `hu.belavarga.betting-quant-engine`: sportadat, forecast, Stake, publikáció, settlement. A tanítás kikerült ebből a ciklusból.
- `hu.belavarga.betting-quant-learning`: percenként ellenőrzi a tartós sort; egy feladatot hajt végre ciklusonként. Ligánként legfeljebb napi új kontrollált kísérlet, plusz óránkénti eredményáttekintés. Az elhibázott próbák külön újrapróbálhatók.
- SQLite `learning_jobs`: feladatazonosság, státusz, következő próba, eredmény; `learning_attempts`: minden végrehajtási kísérlet és hiba. A migráció tiszteletben tartja a korábbi `training_state.json` időbélyegét.
- Az OS-fájlzár megakadályozza a párhuzamos workerek ütközését. Folyamatvesztés után a megkezdett próba `interrupted`, a munka újrapróbálható. Visszavonhatatlan mellékhatás nincs: a modellcsere atomikus és a jelölt verziózott.
- Hibánál 60 másodpercről legfeljebb 6 órára növekvő újrapróbálási idő; a korábbi hiba nem tűnik el a próbák történetéből. A modellkapu elutasítása sikeresen végrehajtott kísérlet, nem sikeres modellcsere.

`outcome_review` az eredeti döntéseket és a végrehajtási napló kimutatását külön megőrzi, ligánkénti eredménnyel. Nem talál ki költséget, lezárt eredményt vagy a kevés adatból igazolt tanulságot. A nettó költség utáni eredményhez havi rendszerköltség szükséges.

A kutatási sor a még hiányzó új piacmodelleket `needs_implementation` státuszban tartja. A meglévő Elo/logisztikus/boosting-keverék kísérletezése a kontrollált tanításhoz kapcsolódik; ez nem új architektúrák automatikus feltalálása. A hiány megszűnésekor a feladat megoldottként, újbóli előforduláskor ismét nyitottként jelenik meg.

## Elutasítás utáni modellkeresés

A `model_search` feladat automatikusan létrejön az elutasított újratanítás után. Hat előre rögzített receptet vizsgál: kontroll, rugalmasabb kalibráció, erősebb kalibrációs zsugorítás, erősebb regularizáció, összetettebb fainterakciók, Elo-alapú esélymodell. A receptválasztás az éles modell végső tesztidőszaka előtti, eredmény-elérhetőséggel is szűrt adatokon történik. Egy kiválasztott recept kap végső páros összehasonlítást. A Brier-kapu mellett a margin/total MAE legfeljebb 5%-os romlását engedő kapu is szükséges.

A `data/experiments/{league}/{study_id}` könyvtár megőrzi a hipotéziseket, paramétereket, fejlesztési eredményeket, modelljelöltet, kódot és elfogadási döntést. Azonos sportadatokra nem fut újra vég nélkül ugyanaz a keresés. Érdemi sportadat-változás új tanulmányt indít; puszta letöltési idő vagy dobómegfigyelés-sorrend nem. Végrehajtás előtt elavult adatú munkák `superseded` státuszba kerülnek. A forrásbetöltő csak a statisztikai és dobóadat-kiegészítés után adhat át új közös games.json pillanatképet.

A kontrollált napi újratanítás megőrzi az előléptetett receptet. Újraindításkor a piacértékelő a teljes atomikus modellfájlt használja; a meglévő fogadások esélyei, árai és tétei változatlanok maradnak. További új sportfeature-ök és új piacmodellek kialakítása továbbra is fejlesztési munka. A végső összehasonlítás fejlesztési bizonyíték, nem nyereségteszt és nem teljesen érintetlen kutatási holdout.

## Üzemeltetés és ellenőrzés

Telepítés: `.venv/bin/python scripts/learning_service.py install`; állapot: ugyanaz `status` argumentummal; leállítás: `stop`. A főszolgáltatás és a tanulási szolgáltatás külön indul/áll le. Mindkettő bejelentkezéskor indul és folyamatleállás után újraindul. Kézi egy ciklus: `PYTHONPATH=src .venv/bin/python -m betting_engine.learning --once`.

Állapot: `/api/learning`; web: Model Performance → Learning Execution. Adatok: `data/learning_worker.json`, `data/outcome_learning.json`; naplók: `data/logs/learning.log`, `learning-error.log`. A státusz időbélyeges; az állott heartbeat külön jelzett.

Kompromisszum: egy worker egyszerre egy munkát futtat, így nincs párhuzamos modellcsere. A CPU-szálak korlátozottak, de ugyanazt a gépet és adatbázist használja. Nagyobb terhelésnél külön tanítógép, üzenetsor és teljes forrásverzió-archívum szükséges. A helyi gép alvása alatt egyik szolgáltatás sem halad.

## Szakirodalmi alkalmazás és fennmaradó hiány

A források és pontos alkalmazás a [K11](../knowledge/cards/K11-ai-tanulas-memoria.md) kártyán vannak. A mostani kiadás végrehajtható, determinisztikus tanulási workflow. LLM által irányított online kutatás, új modellkód létrehozása és önálló tesztelt kódtelepítés továbbra sincs bekötve. A profitcél teljesülését valódi jövőbeli eredményekkel kell vizsgálni.

## 2026-09-29: folyamatos felügyelet és napi szakirodalom-figyelés

Három külön launchd-folyamat: a piacfrissítő, a tanuló és a percenként induló felügyelő (`scripts/supervisor_service.py`). A felügyelő három egymást követő hibás állapot után kér újraindítást, 15 perces újraindítási várakozással. Elakadás: frissítő futás >15 perc, frissítő nyugalmi szívverés >5 perc, tanuló feladat >2 óra, tanuló nyugalmi szívverés >5 perc. A sikertelen újraindítás nem sikeres helyreállítás. A felügyelő állapotát és műveleteit a `data/supervisor.json`, illetve `data/logs/supervisor-actions.jsonl` őrzi.

A `config/research_sources.json` három arXiv-lekérést ütemez 24 óránként: sportmodellek, kalibrálás/drift, tanuló ágensek. Egy worker-tick legfeljebb egy feladatot hajt végre, így a kérések között legalább egy perc telik el. A lekérés az [arXiv hivatalos API-leírását](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md) követi; a konfigurált lekérés legfeljebb 12 rekordot kér, a legújabb beküldéssel kezdve.

A `research_watch` valóban végrehajtott hálózati lekérés és cím/absztrakt-indexelés; nem teljes tanulmányolvasás, empirikus reprodukció vagy automatikus modellimplementáció. Az eredeti XML és SHA-256 megmarad; az azonos cikk változatai közös azonosítón szerepelnek. Hibánál a meglévő tartós worker újrapróbálása működik. Külső szöveg nem jut végrehajtható kódba. A források sikere és időpontja külön látható; egy sikeres téma nem jelenti valamennyi forrás sikerét.

A `/api/automation` és a Model Performance nézet mutatja a felügyelőt és a forrásfigyelést. A statisztikailag ellenőrzött modellcsere továbbra is az elkülönített tanító és modellkereső folyamat feladata. Új sportpiaci modellek önálló kódolása és telepítése még nincs megvalósítva.

A frissítő szolgáltatást `caffeinate -s` indítja: a szolgáltatás futásához kötött rendszeralvás-gátlás kizárólag hálózati tápon érvényes, a kijelző beállítását nem módosítja. A tényleges PreventSystemSleep assertion ellenőrizve. Újraindítás után a szolgáltatások a felhasználói bejelentkezéssel indulnak; kikapcsolt vagy internet nélküli gépen nem keletkezhet friss ellenőrzött ajánlat.
