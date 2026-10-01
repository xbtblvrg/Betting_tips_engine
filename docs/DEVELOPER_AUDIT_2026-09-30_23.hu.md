# Fejlesztői audit – 2026-09-30.23

A kiadás célja két időzítésfüggő tesztadat javítása és a hozzájuk rendelt fejlesztési munkák bizonyítékhoz kötött újraindítása. A fogadási modellek, tétképzés, publikációs guard, eredeti döntések és napi számláló változatlanok.

## Reprodukált okok

- A Stake Sizing jövőbeli quote tesztje a modul betöltésekor rögzítette az időt; 6 másodperces eltelt idő mellett a tesztadat a tényleges 5 másodperces óratűrésbe került. Az eredeti elutasítási állítás helyes, a tesztadat időpontja hibás.
- Az NHL period presentation teszt importkor létrehozott quote-ja 301 másodperc múlva már elavult. Az éles 300 másodperces frissességi ellenőrzés helyesen utasította el.
- A fejlesztési felügyelet a pontosan diagnosztizált védett futtatókód-változásokat figyelte, de a meglévő regressziós tesztek root által javított generációját nem. Így az új ellenőrzésre képes feladatok a korábbi visszavárakozásban maradhattak.

## Elfogadási feltételek

Az eredeti tesztállítások és éles ellenőrzési küszöbök megmaradnak. Az újraindítás csak a sikertelen futás hiteles diagnózisához, megőrzött manifestjéhez és forrásbyte-jaihoz kapcsolt tényleges tesztváltozás alapján történhet. Az eredeti feladat, függőségek, határidő és teljes elfogadás megmarad. A szolgáltatói hibák visszavárakozása és a változatlan bizonyítékból történő ismétlés elleni védelem megmarad. Az újrafuttatás nem teljesítésigazolás.

A teljes Python- és DOM-tesztcsomag, a független Codex-review, a kontrollált kiadás, a megőrzött eredeti fogadási rekordok és a későbbi helyi/Tailscale-publikáció bizonyítékai: `data/loop-unblock-20260930/`. A végleges élő audit ebben a könyvtárban rögzíti a ténylegesen telepített hash-eket és feladat-újraindításokat.

## Teljes projektparancs felülvizsgálata

Ez a kiadás a belső teszt- és felügyeleti akadályok célzott javítása. Nem jelenti új piaccsalád engedélyezését, nyereség igazolását vagy a teljes önfejlesztő kör elkészültét. A ténylegesen hiányzó historikus/prospektív források, modellvalidáció és védett modellek root-integrációja nyitva marad; eredeti határidőik és hibáik nem törölhetők. Az eredeti fogadási kiadások és kimeneteik változatlanok, minden aktív ajánlásra továbbra is a jelenlegi EvidenceGuard és USD1–5 szabály vonatkozik. A rutin fejlesztések elvégzéséhez nincs új felhasználói engedélyre szükség.
