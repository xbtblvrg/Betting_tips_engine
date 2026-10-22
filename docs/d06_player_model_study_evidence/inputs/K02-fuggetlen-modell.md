# K02 — Független sportmodell, rating és eloszlás

Típus: könyvek közötti módszertani szintézis és felhasználói követelmény. Állapot: modelljelöltek; egyik sem igazolt éles előny. Hash és kiadás: [leltár](../reading_v2/inventory.json).

## K02.1 — A modell és a piac szerepe

Forrás: [B02 21–22.fejezet](../reading_v2/B02-notes.hu.md), [B04 PDF53–73](../reading_v2/B04-notes.hu.md), [B21 PDF48–118](../reading_v2/B21-notes.hu.md). A kért sorrend: sportadatok és aktuális állapot → saját prediktív eloszlás → kifizetési szerződés → aktuális árhoz mért érték. Bookmaker closing line, implied p, steam, ticket százalék, market-derived rating nem lehet rejtett inputja az önálló alapesélynek.

Ellentmondó irány: B21 Benter Boost és több B25 példa a piaci esélyt javító inputként használja. Ez létező módszer, de a felhasználó követelménye miatt külön benchmarkként őrizhető. Piaci adat nélküli tanítás és változatlan sportinput mellett különböző oddsokhoz pontosan azonos sporteloszlás legyen a teszt. A profit és a tipp kiválasztása ettől még jogosan függ az ártól.

## K02.2 — Csapaterő és azonosíthatóság

Forrás: [B21 PDF48–109](../reading_v2/B21-notes.hu.md), [B08 46–47. és55.fejezet](../reading_v2/B08-notes.hu.md). Bradley–Terry: p_home=sigmoid(h+r_home−r_away). Additív ratingnél referencia/centerezés, szeparációnál regularizáció szükséges. Külön támadás/védelem és hazai előny más modellfeltevés. Elo vagy pontkülönbség-rating átlátható alapmodell lehet, de hiperparaméterei ligánként és időben validálandók. A szezon végi rating nem korábbi meccs előzetes inputja.

Adatigény: kezdési és befejezési idő, eredmény, helyszín, szezon/szabály, csapatazonosítók; az adott vágáskor ismert meccsek. Új csapat/játékos, visszatérő sérült és korai szezon priorral/bizonytalansággal kezelendő. Teszt: időrendi előrejelzések, hazai alaparányhoz és egyszerű ratinghez mért javulás. Nem elég az in-sample R² vagy Solver-konvergencia.

## K02.3 — Átlagból nem következik fogadási esély

Forrás: [B21 NFL/NHL példák](../reading_v2/B21-notes.hu.md), [B25 4. és11.fejezet](../reading_v2/B25-notes.hu.md), [B09 PDF64–118](../reading_v2/B09-notes.hu.md). Azonos várható marginhoz különböző szórás, aszimmetria és döntetlen-tömeg tartozhat. Két marginális eloszlás szorzata függetlenséget állít; nem automatikusan helyes. A win/spread/total és korrelált propok ugyanabból a konzisztens közös eloszlásból származzanak.

Feltételek: Poissonnál megfelelő eseménymechanizmus/exposure; negatív binomiálisnál az adott parametrizációban Var>mean; normál közelítésnél a farok és diszkrét tömeg külön ellenőrzése. Score-mátrix csonkolt tömegét mérni kell. Teszt: OOS proper score, prediktív intervallum lefedése, score-frekvencia és farokkalibráció, szimulációs és analitikus speciális eset egyezése.

## K02.4 — Ensemble és becslési bizonytalanság

Forrás: [B21 PDF93–118](../reading_v2/B21-notes.hu.md), [B09 Staking és Bayes](../reading_v2/B09-notes.hu.md). Ugyanazokon az adatokon illesztett sok modell nem sok független bizonyíték. Stackinghez időrendben mintán kívüli alapmodell-predikciók kellenek; a végső teszt nem lehet a súlyok vagy EV-küszöb tanítóadata. Az esemény véletlensége, paraméterbecslési hiba és modellstruktúra bizonytalansága külön fogalom. Teszt: komponensenkénti ablation, külön év/szezon és ligaszegmens; az összetettség csak mérhető többlettel indokolható.
