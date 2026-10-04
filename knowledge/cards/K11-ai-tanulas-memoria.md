# K11 — AI, tartós memória és ellenőrzött fejlesztési ciklus

Típus: saját szintézis a könyvek és a felhasználói követelmények alapján. Forrás: [B11 teljes melléklet, különösen PDF37–199 és287–311](../reading_v2/B11-notes.hu.md), [B09 modellellenőrzés](../reading_v2/B09-notes.hu.md), [B16 matematikai alkalmazhatóság](../reading_v2/B16-notes.hu.md). Állapot: tudáskezelési alap; nem működő önfejlesztő sportbot bizonyítéka.

## K11.1 — Mit jelent itt a megtanult tudás?

A nyelvi modell súlyai a könyvolvasástól nem módosulnak. A tartós projektmemória a forrásfájlok, SHA-azonosítók, fejezeti jegyzetek, hibajavítások, tudáskártyák, ellenőrzési napló és kereső együttese. A későbbi munkamenet innen tudja visszakeresni a módszert és korlátját. Az indexbe töltés nem elolvasás; a hasonlósági találat nem igazságbizonyíték. A legrészletesebb fejezeti jegyzet és későbbi audit elsőbbséget élvez a rövid kártya felett.

A teljes rendelkezésre álló könyvtár lefedése nem jelenti, hogy az összes idézett külső cikk, kódmelléklet és mai platformszabály is ellenőrzött. Ezek státusza nyílt. A duplikátum nem új független bizonyíték, az excerpt nem teljes mű.

## K11.2 — AI és numerikus modell

Az LLM feladata lehet forrásolt hírből strukturált esemény kivonása, ellentmondás jelzése, tudástári visszakeresés és érthető indoklás. Önbemondott confidence nem kalibrált sportvalószínűség. A p, EV, tét, settlement és metrikák reprodukálható numerikus számításból származzanak. Kitalált sérülés, lineup, odds vagy teljesítményadat tilos adatpótlásként. Hiány esetén hiányjelzés/kihagyás kell.

Teszt: forrásra visszavezethető állítás, validált séma és értéktartomány, időbélyeg, azonosító, ellentmondó hírek elkülönítése. A könyv stub eszközhívásai és kiírt sikerüzenetei nem külső műveletek; éles integráció csak tényleges válasszal és naplóval tekinthető működőnek.

## K11.3 — Önfejlesztés mint mérhető kísérlet

A napi eredményeket rögzítjük, de egy vesztett tipp nem önmagában hibás döntés, és egy nyerő tipp nem igazolja a p-t. Új feature vagy modellváltozat legyen jelölt, külön tréning/verifikációs adatokkal. A korábbi modellhez időrendi OOS és paper időszakban hasonlítandó; visszaállítható verzió és előre rögzített elfogadási feltétel szükséges. Ez saját tervezési következtetés a túlillesztésről szóló könyvek alapján.

Nem elfogadott: napi PnL alapján önkényes tétemelés, tesztminta újrahangolása, bizonyíték nélküli automatikus éles kódátírás. Az adatmodell frissítése, újratanítás, modellpromóció és szoftvermódosítás külön művelet. A napi loop lehet sikeresen lefutott akkor is, ha nincs fogadás vagy nincs új modell.

## K11.4 — Végrehajtó és tapasztalatnapló, 2026-09-29

Kiegészítő elsődleges források: [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), a workflow/agent megkülönböztetés, evaluator–optimizer és környezeti visszajelzés fejezetek; [Reflexion, Shinn és társai](https://arxiv.org/abs/2303.11366), a tanulmány absztraktja ellenőrizve. A teljes Reflexion-tanulmány és kísérleteinek reprodukciója nincs ezzel teljesítettnek jelölve.

A Reflexion visszajelzésből készített nyelvi tapasztalatot tárol a későbbi próbákhoz; ez nem azonos a modell súlyainak frissítésével. Az Anthropic a rögzített vezérlésű workflow-kat megkülönbözteti az LLM által dinamikusan irányított agentektől. Egyik forrás sem igazolja a jelen sportmodell nyereségét.

Saját megvalósítási következtetés: a tanítás és a fogadási publikálás külön folyamat. A `learning_jobs` és `learning_attempts` őrzi a célt, státuszt, próbákat, hibát és eredményt. Hiba után növekvő várakozású újrapróbálás; összeomlás után OS-zárral védett helyreállítás. A kontrollált modellkísérlet a korábbi időrendi elfogadási kaput használja, az eredményértékelés az eredeti döntéseket. A nem implementált kutatási feladat nem minősül megoldottnak attól, hogy bekerült a naplóba.

Ez most determinisztikus végrehajtó, nem a Reflexion LLM-algoritmusának implementációja és nem önálló kódíró AI. A tényleges bekötés: `src/betting_engine/learning.py`, külön launchd worker, `/api/learning`, Model Performance → Learning Execution.

## K11.5 — Konkrét hipotézisek keresése

2026-09-29-én ellenőrzött elsődleges források: [Google DeepMind: AlphaEvolve — scaling impact, 2026-05-07](https://deepmind.google/blog/alphaevolve-impact/), [AlphaEvolve eredeti bemutatása](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/), [scikit-learn: Probability calibration](https://scikit-learn.org/stable/modules/calibration.html). A DeepMind publikáció programjavaslatok gépi kiértékeléséről szól; az itteni sportmodellre nem nyújt eredménybizonyítékot. A kalibrációs dokumentáció alapján a valószínűségek ellenőrzése és a tanító/kalibráló adatok elkülönítése fontos része a kísérletnek.

Saját, AI-val megfogalmazott implementációs hipotézisek: túl erős esélyzsugorítás enyhítése; kis mintán erősebb kalibrációs szabályozás; erősebb regressziós regularizáció; több, szabályozott fainterakció; egyszerű Elo-alapú esélymodell. Ezek hat előre rögzített receptként futnak, a kontrollal együtt. A receptek az éles modell tesztidőszaka ELŐTTI adatokon versenyeznek; a kiválasztott recept egyszer kerül a végső modell-összehasonlításba. Pontkülönbség- és összpontszám-MAE legfeljebb 5%-os romlása is külön kapu. A team-total diagnosztika megjelent a tanítási riportban.

Ez a modellkereső előre definiált hipotéziseket tesztel, nem implementálja az AlphaEvolve-ot és nem generál szabadon új forráskódot. Az időrendi vizsgálatok fejlesztési eredmények: a korábbi tesztadatok már szerepeltek fejlesztői elemzésekben. Az élő profitot továbbra is előre rögzített döntésekkel kell igazolni.
