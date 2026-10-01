# Release26.1 — párhuzamos Statistics-lekérések

A release26 első éles, több szűrőt párhuzamosan lekérő próbája HTTP503-at talált. Az örökölt dashboard-renderer a különböző szűrőket új publikációként kezelte és érvénytelenítette a másik olvasó eredményét. A hibás éles próba bizonyítéka a `data/statistics-lifecycle-20260930/failed-live-attempt.json`; az eredeti feladat és határidő megmarad.

Az új Statistics-folyamatkezelés a szűrőket külön kezeli, azonos lekéréseket csak futás közben oszt meg, korlátozza a gyermekfolyamatokat és a várakozó kéréseket, megszakításkor lezárja a felesleges folyamatot. Eredménycache vagy DB-írás nem kerül be. Új regressziók mérik az eltérő szűrők helyes párhuzamos válaszát, az azonos kérések megosztását, a kapacitáskorlátot, a megszakítást és az egészségvégpont reagálását.

A release26 Statistics/money/decision számítása és az élesben már lefutott outcome-review, feladatátadás változatlan. Teljes Python/DOM suite, független pontos hash-review, ellenőrzött forráskiadás és ugyanaz a párhuzamos helyi/Tailscale próba szükséges; a próba sorosítása nem lenne a hiba javítása. A végső bizonyíték a két kiadás láncát őrzi. Profitjavulást ez a szoftverjavítás nem állít.
