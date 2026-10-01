# Követelmények, döntésfelelősség és ellenőrzött tanulás

A projekt célja a bizonyítékokra épülő nettó fogadási profit maximalizálása. A teljes utasításkészlet aktuális összefoglalója a `config/project_contract.json`; a korábbi 10 tipp és 10 USD napi limit nem írja felül a későbbi 15-ös célt és a fix napi összegkorlát eltörlését. A fogadásonkénti plafon marad 5 USD, automatikus emelés nélkül. A nulla nettó veszteség kívánság, nem teljesíthető garancia.

## Futó adatút

Sportadat → saját sportmodell → pontos Stake-piac → újraszámolt érték → EvidenceGuard → rangsor és tét → atomikus publikáció, eredeti döntésnapló és feltételezett kézi fogadás → eredményértékelés → kutatási feladatok és napi kontrollált modelljelölt.

A KnowledgeAgent a módszertani tudáskártyákat betölti, kiválasztja a ligához tartozó hivatkozásokat és rögzíti azok SHA-256 azonosítóját. Ez módszertani eredetkövetés, nem állítás arról, hogy minden könyvi módszert megvalósítottunk vagy a modell súlyaiba tanítottunk. A könyvek és külső adatforrások szövege nem adhat működési utasítást a futtatónak.

Az EvidenceGuard a valós modellkimenetből ismét kiszámítja a p, push, EV és stresszelt EV értékeket. Ellenőrzi az esemény és piac azonosságát, a forrásidőket, az oddsot, a modellverziót, a kód- és tanítóadat-azonosítót, a sportfeature-listát, az oddsfüggetlenségi jelentést, a tudáshivatkozások meglétét és a tétbeállításokat. Hiány esetén nincs új ajánlás; az ok auditálható. Ez determinisztikus hibavédelem, nem a bemeneti adatok vagy modellbecslés tévedhetetlenségének bizonyítéka.

A guard a meccsnaptár letöltésére legfeljebb egyórás, az oddsra az aktuális legfeljebb ötperces érvényességi szabályt alkalmazza. A jelenlegi szezon naptár-cache ideje 15 perc. A letöltési idő nem azonos a forrás eredeti publikálási idejével; a korabeli híradat lefedettsége továbbra is hiányos.

## Saját döntések és tényleges szelvények

A `system_decisions` megőrzi az első ajánlás kiválasztását, modelljét, valószínűségét, tétjét, árát és ellenőrzési hivatkozásait. Egy frissített ár vagy törölt pipa nem írja át. A felhasználó új utasítása szerint ugyanazon meccs új, megfelelőnek értékelt vonala/piaca külön döntés lehet; a kiadási és felelősségi kulcs a pontos szerződésazonosító. Nincs fix meccsenkénti darabkorlát, de az azonos szerződés nem ismétlődik és a gyengébb alternatívák nem lépnek sorra automatikusan. Az eredmény végső sporteredményből számolódik, a veszteségek megmaradnak. A döntések P&L-je a kiadott feltételekre számított eredmény; a `bets` külön tartalmazza a bejelentett elfogadott oddsokat, téteket és többször megkötött szelvényeket. A két érték eltérhet; egyiket sem címkézzük automatikusan Stake-számla által igazoltnak.

A korábbi főlistán szereplő és a felhasználó által megjátszottnak jelentett nulla tétű jelöltek is a rendszer felelősségi körébe kerülnek. Ezek `legacy_displayed_selection_user_reported_placed` forráscímkét kapnak; az eredeti nulla tét megmarad, az eredményszámítás a bejelentett szelvényfeltételeket használja. Nem emelik visszamenőleg a téttel kiadott napi ajánlások számát. Az eredeti 17 szelvény 16 különálló döntés, ebből 9 volt eredetileg pozitív tétű ajánlás.

## Piacok és önfejlesztés

Az aktív, időablakba eső feed-események minden visszaadott piacáról leltár készül, akkor is, ha a meccspárosítás hiányzik. A leltár külön jelöli a támogatott, nem támogatott/inaktív és párosítatlan kimeneteleket. A feed nem bizonyítottan a teljes Stake-weboldal kínálata. A megfigyelés nem modellezés: a jelenlegi numerikus engine teljes meccses Winner, Handicap, Total és az adott csapat nevét plusz hosszabbítási szabályát pontosan tartalmazó Team Total szerződéseket áraz. A csapattotal a meglévő közös ponteloszlás marginálisa; külön nyereségigazolása még nincs. Az MLS naptára és a Swish-piacok leltára is bekerült, önálló MLS-/propmodell nélkül. Prop, részidő, kombináció és in-play piacra nem gyárt kitalált esélyt.

A LearningAgent minden ciklusban a teljes döntés- és költségkimutatásból, a telepített modellek tesztjeiből és a piaci lefedettségből állít elő konkrét kutatási feladatokat. Ezek a `data/learning_review.json` fájlban és a web Model Performance oldalán láthatók. A napi kontrollált tanítás külön fut; próbálkozása és promóciós eredménye újraindítás után is megmarad. A páros időrendi Brier-kapu szükséges modellminőségi ellenőrzés, de nem profitbizonyítás.

Az automatikus hírkutatásból történő új feature-fejlesztés, új piacmodellek kódolása és tesztelt automatikus szoftvertelepítése még nincs megvalósítva. A jelenlegi agentek nem LLM-fejlesztőügynökök. A teljes körű, profitot már igazolt önfejlesztő rendszer ezért nem kész; a hiányzó képességek a projektkövetelmény-fájlban és a kutatási sorban láthatók.

API: `/api/governance`, `/api/system-decisions`, `/api/market-inventory`, `/api/knowledge/K01` (és a többi kártya). A futási eredmény az angol web Model Performance oldalán jelenik meg.

## Tervezési kompromisszumok és további elfogadási feltételek

A determinisztikus guard jól reprodukálható, olcsó és tesztelhető, de nem ellenőrzi szemantikailag az összes könyvet és hírt. A teljes nyers feed leltára több lekérdezést igényel, és nem helyettesíti az események sportadatokhoz illesztését. Az ajánlási és végrehajtási napló külön tartása elkerüli az utólagos árátírást; cserébe a két P&L eltérését magyarázni kell.

Új piaccsalád csak saját esemény- és kifizetési modellel, megfelelő történeti adatokkal, előre rögzített időrendi teszttel és pontos Stake-szerződésillesztéssel léphet élesbe. 2026-09-29-től a modellkísérletek és az eredményértékelés külön, tartós munkasort használó folyamatban futnak: [Learning Worker](LEARNING_WORKER.hu.md). A sportadat-frissítés és az odds-publikálás még egy folyamatban maradt; a 60 másodperc célintervallum, nem garantált felső futási idő.
