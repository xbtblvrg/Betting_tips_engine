# K05 — NBA és WNBA: tempó, hatékonyság, rotáció

Típus: sportági szintézis; WNBA-ra részben kifejezetten tesztelendő átvitel. Forrás: [B08 28–38.fejezet, PDF272–374](../reading_v2/B08-notes.hu.md), [B09 PDF97–106](../reading_v2/B09-notes.hu.md), [B14 12.fejezet](../reading_v2/B14-notes.hu.md), [B21 PDF126–130](../reading_v2/B21-notes.hu.md). Állapot: NBA módszertani alap; a könyvekben nincs teljes külön WNBA pregame modell. WNBA elsődleges kutatás a külső katalógusban vár ellenőrzésre.

## K05.1 — Pontszám mint tempó és hatékonyság

A várható pontszám possession×pont/possession, de a szorzat várható értéke általában nem a két várható érték szorzata. Four factors: dobáshatékonyság, turnover, támadólepattanó és büntetőzés; definícióik/exposure-jük egyeztetendő. Az ellenfél és helyszín szerinti korrekció, hárompontos variancia és meccsvégi faultolás eltérő módon hat a meanre és tailre.

Adatigény: játékidő, possession-adat, ellenfél, dobások és büntetők, lineup, napirend/pihenő/utazás, sérülés és visszatérés. A back-to-back vagy „motiváció” könyvi narratíva vizsgálandó változó, nem automatikus pontlevonás. Teszt: margin és total külön, de konzisztens közös eloszlás; időrendi scoring, coverage és szezononkénti stabilitás.

## K05.2 — Játékoshatás és lineup

Adjusted plus-minus csapattárs/ellenfél hatásokat igyekszik szétválasztani, de multikollinearitás, közös pályaidő és endogén rotáció miatt regularizáció és bizonytalanság kell. Box-score mutató nem izolált oksági játékosérték. A hiányzó játékos pótlása, használati arány és várható percek együtt számítanak. A teljes szezon végén becsült RAPM nem korábbi meccs feature-je.

Teszt: játékos nélküli baseline-hoz mért OOS javulás; bejelentett vs tényleges lineup eltérés elkülönítése; az összes várható játékosperc feleljen meg a szabályos csapatpercnek, OT külön eloszlás. WNBA esetén külön játékidő, roster, mintanagyság, ligaátlag és kalibráció szükséges; NBA-koefficiens változatlan átvitele nem igazolt.

## K05.3 — Szimuláció és kis minta

B21 tripla-verseny példája külön oktatási figyelmeztetés: szezonszázalék szórása nem25dobás találatszórása; normál p kimehet[0,1]-ből; moneyball, kör és döntetlen külön szabály. A több1000szimuláció nem több megfigyelt sportadat. NBA vagy WNBA meccsnél is meg kell különböztetni a játékos skill bizonytalanságát és az adott meccs dobásvarianciáját. Teszt: Monte Carlo-hiba mérése, seed és mintaszám naplózása, valamint prediktív eloszlás külső validációja.
