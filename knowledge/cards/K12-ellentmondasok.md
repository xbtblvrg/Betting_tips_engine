# K12 — Ellentmondások, hibák és bizonyítéki súly

Típus: kritikai forrásszintézis. Állapot: az olvasott könyvek alapján; nem teljes eredeti-cikk reprodukció. A pontos hely és részletes levezetés mindig a hivatkozott olvasási jegyzetben van.

## K12.1 — Egyetértés és eltérő célok

[B04](../reading_v2/B04-notes.hu.md), [B09](../reading_v2/B09-notes.hu.md), [B18](../reading_v2/B18-notes.hu.md) és [B24](../reading_v2/B24-notes.hu.md) közös alapja az árhoz viszonyított esély, a nyilvántartás és a variancia. A szerzők személyes tétpreferenciái eltérnek; ebből nem következik egyetlen univerzális bankrollszázalék.

[B02/B07](../reading_v2/B02-notes.hu.md) saját ratingből indul, [B21](../reading_v2/B21-notes.hu.md) több példája nagyrészt piacra súlyoz, [B23](../reading_v2/B23-notes.hu.md) számos piaci/trend alapú stratégiát tárgyal. A felhasználó oddsfüggetlen modellkövetelménye választ köztük, nem az, hogy melyik szerző ígér nagyobb nyereséget.

[B26](../reading_v2/B26-notes.hu.md) rövid távú ármozgásból és pozíciólezárásból indul; ez más előrejelzési cél, mint a meccs nyertese. [B25](../reading_v2/B25-notes.hu.md) több lóverseny/lottó tanulmánya piaci preferenciát vizsgál, nem ötligás sportképességet. A könyvek célját a bizonyíték értékelése előtt kell rögzíteni.

## K12.2 — Történeti trend és általános szabály

[B12](../reading_v2/B12-notes.hu.md), [B14](../reading_v2/B14-notes.hu.md), [B22](../reading_v2/B22-notes.hu.md), [B23](../reading_v2/B23-notes.hu.md) sporthelyzeteket és fogadói heurisztikákat sorol. Minden ilyen szabály hipotézis: recency, ellenfél, környezet, szezon, odds és kiválasztási folyamat mellett vizsgálandó. A megfigyelt történeti profit nem mai paraméter. [B13](../reading_v2/B13-notes.hu.md) stratégiaszelekciója és [B25 20–21.fejezet](../reading_v2/B25-notes.hu.md) eltűnő előnyei külön figyelmeztetnek a mintán kívüli próbára.

## K12.3 — Ellenőrzött javítások mintái

| Hely | Probléma | Tudástári kezelés |
|---|---|---|
| B21 PDF26 | Logloss(1+y) tag | Helyesen(1−y); nem másoljuk a hibás képletet |
| B21 PDF24 | R² képlet és RSQ keverése | SSE/SST és korreláció külön fogalom |
| B15 PDF86–91 | Nulla holdból veszteségmentesség sugallata | Egyoldali ellenpélda; teljes állapotfedezet kell |
| B08 11.fejezet | Runs-próba előjel, feltételes hot-hand becslés | Nullmodell és estimator együtt vizsgálandó |
| B14 16.fejezet | Normalizált találati arány | Változóoddsos profit nem redukálható erre |
| B25 PDF78 | Minimumidő győzelmi esélyében Φ helyett1−Φ | Numerikus ellenpélda az auditban |
| B25 17.fejezet | Kis-edge növekedésből hiányzó1/2 | Kétkimenetelű ellenőrzés, korrigált közelítés |
| B25 PDF396 | Első portfólió KKT-állítása | Inaktív5.ló feltétele sérül; második portfólió ellenőrzött |
| B25 PDF489,504 | Jackpot-esély reciproka, EV-ből hiányzó/S | Kombinatorika és mértékegység javítva |
| B25 PDF544–545 | Exponenselőjel/zárójel | Egzakt és Poisson-közelítő megosztási képlet külön |
| B11 PDF145 | Retry után rossz embedding–szöveg párosítás | Azonosító és részleges hiba naplózása |

Ez célzott hibajegyzék, nem a könyvek értékének általános elutasítása. A teljes jegyzetek több tucat további számszaki, definíciós és inferenciális eltérést őriznek. Különbség van nyomtatott hiba, OCR-hiba, egyszerűsítés, vitatható feltevés és még ellenőrizetlen állítás között.

## K12.4 — Bizonyítéki sorrend és hiányok

Matematikai állítás: feltevések és levezetés/numerikus ellenpélda. Empirikus állítás: adat, időszak, minta, összehasonlítás, bizonytalanság és reprodukció. Emlékirat: tapasztalati hipotézis, független profitbizonyíték nélkül. Kódpélda: statikus olvasás nem futási teszt. A B10/B07 töredék, B06 korai kiadás, B11 kísérőanyag korlátját mind megőrizzük.

Nyitott hiány: aktuális WNBA/NHL kutatás, időbélyeges sérülés- és lineupadat, történeti végrehajtható odds, mai szabály/díj/API, valódi ötliga-adat és OOS validáció, felhasználói bankroll és kézbesítési csatorna. Ezeket a könyvolvasás nem pótolja. A következő online és mérési körnek ezeket kell tisztáznia.
