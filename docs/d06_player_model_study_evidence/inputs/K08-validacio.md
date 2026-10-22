# K08 — Kalibráció, visszateszt és bizonyíték

Típus: statisztikai definíció és saját módszertani szintézis. Forrás: [B09 PDF119–237 és329–420](../reading_v2/B09-notes.hu.md), [B21 PDF119–125](../reading_v2/B21-notes.hu.md), [B18 7. és17.fejezet](../reading_v2/B18-notes.hu.md), [B25 10.,12.,20–21.fejezet](../reading_v2/B25-notes.hu.md). Állapot: vizsgálati elvek; éles előny nincs igazolva.

## K08.1 — A mérőszám jelentése

Bináris Brier=mean((p−y)²), logloss=−mean[y ln p+(1−y)ln(1−p)]. Mindkettő proper scoring rule: a valódi feltételes esély kimondását jutalmazza várható értékben. A logloss p0/1 és ellenkező eredmény esetén végtelen; a numerikus clipping dokumentált technikai lépés, nem mód a túlzott bizonyosság eltüntetésére. Multiclass Brier eltérő skálázási konvencióit egyeztetni kell.

Kalibráció: azonos becsült p mellett az eredménygyakoriság közel p; kis csoportoknál nagy bizonytalanság. A konstans ligaátlag lehet kalibrált, de kevéssé informatív. Találati arány, rangsorolás, kalibráció, ROI és drawdown külön tengely. Teszt: proper score egyszerű baseline ellen, reliability bizonytalansággal, külön sport/piac/odds és szezon. A kalibrációs modellt is csak múltbeli, mintán kívüli predikciókon szabad tanítani.

## K08.2 — Időrendi kísérlet

Train → későbbi validáció → érintetlen későbbi teszt. Walk-forward esetén minden tanítás, skálázás, imputáció, rating, ensemble és kalibráció a cutoff előtt történik. Több piac ugyanazon meccsről együtt mozog; a bootstrap/resampling egysége legalább a közös esemény, szükség szerint időblokk. Késői eredményű vagy átfedő horizontú címkékhez külön gap/embargo kellhet. Azonos adaton80fókuszcsapat-újrasorsolás nem80független történet.

Adatigény: korabeli sportsnapshot és valóban elérhető quote; az aktuális odds nem helyettesíti a múltbelit. Ha nincs hiteles korabeli odds, sportpredikció tesztelhető, profitbacktestként nem címkézhető. Előre rögzített piacuniverzum, EV-küszöb és módszercsalád; minden próbálkozás nyilvántartása. Tesztadat ismételt kézi nézegetése és finomhangolása után új holdout szükséges.

## K08.3 — P-érték, szelekció és bizonytalanság

A p-érték a null és a modellfeltételek mellett legalább ilyen szélsőséges statisztika esélye, nem P(nincs skill|adat). Nemszignifikancia nem bizonyított hatékonyság vagy nulla hatás. Változó odds, tét és függőség mellett egyetlen50–50binomiális teszt általában hibás profitnull. A pozitív rekordot kiválasztó tipsterlisták survivorship és multiple testing torzítást hordoznak.

Tesztelhető eredmény: előre specifikált OOS teljesítménykülönbség bizonytalansági intervallummal, ugyanazon eseményuniverzumon, nettó költségekkel. Szimulációból kapott keskeny CI csak a szimulációs modell feltételeire érvényes. Millió mesterséges húzás nem szünteti meg a rosszul becsült p-t. Rövid nyerő vagy vesztő sorozat önmagában nem újratanítási bizonyíték.

## K08.4 — CLV mint kiegészítő diagnosztika

Forrás: [B15 PDF113–119](../reading_v2/B15-notes.hu.md), [B09 PDF152–183](../reading_v2/B09-notes.hu.md), [B19 17–21.fejezet](../reading_v2/B19-notes.hu.md). A záróárhoz mért jobb vétel információs diagnosztika lehet megfelelően likvid, árképző piacon; nem igazolt valódi p és nem garantált ROI. Eltérő vonal/ruleset, jutalék és ritkán mozgó propok torzítanak. Oddsinput nélküli sportmodell mellett CLV utólagos benchmarkként megmaradhat; ne szivárogjon vissza az adott meccs előrejelzésébe.
