# MLB kiindulóadat-ellenőrzés — 2026-09-30

A hiba a fogadási logikában is jelen volt. A hiányzó kezdő dobó numerikus ligaátlag-priorrá vált, és az általános publication guard ezt nem kezelte hiányzó, döntéshez szükséges inputként. Az inning-modellek ugyanazt a feature-vektort használták, de a hiányzó dobót még külön figyelmeztetésként sem mutatták. A modellverzió változatlan maradt a meccs kontextusának frissülésekor.

A read-only élő auditban a 19 `price_below_threshold` rekordból csak 1 volt tiszta árromlás. 18 MLB rekordnál a modell/input változása önmagában is megszüntette az előnyt. A Padres–Cubs #119, első öt inning Under 3.5 rekord eredetileg 75,8624% valószínűséget és 3,24 USD-t adott; az away pitcher megérkezése után 39,8922%-ot. A #126 Under 2.5 odds mindkét pillanatban 2,85 volt, mégis 59,8118%-ról 25,8965%-ra változott az esély. A jelenlegi csapatállapottal, kizárólag az eredeti pitcher-kontextust visszaállítva az eredeti érték 0,0002 százalékponton belül reprodukálható. A régebbi Houston-példán a fennmaradó kisebb különbség a frissült csapat-/pitcher-történettől nem választható teljesen külön; az eredeti befagyasztott state hiányát nem pótoltuk kitalált snapshot-tal.

## Javítás

`DecisionReadiness(data_dir).check(game, forecast, model_report, now=None)` nem kér hálózati adatot, és nem ír fájlt. MLB esetén kötelező:

- Az aktuális official MLB schedule archivált, SHA-256 szerint ellenőrzött raw válasza, pontos game ID / team ID / start / probable pitcher ID egyezéssel.
- Mindkét starter explicit azonosítója, `starter_known == 1`, pozitív tényleges pitcher-megfigyelés és a modell által igényelt összes starter feature.
- Official people/gameLog raw forrás, pontos pitcher/game/team/sport/season azonosság. Minden, a futó form state által elfogyasztott statisztikának szerepelnie kell a hash-ellenőrzött forrásban.
- A starter feature-ök reprodukciója a tényleges megfigyelésekből, a jelenlegi modell decay/prior szabályaival. Csak lezárt, nem resumed, a konzervatív `max(start+8h,next UTC midnight)` rendelkezésre állási időt elért meccs fogyasztható el. A következő vagy még nem elérhető eredmény nem kerülhet a számításba.
- Friss aktuális pitcher season source; hibás hash, nem egyező source snapshot, hiányzó történet, csapat/azonosító ütközés és nem reprodukálható feature kizárás.

A guard minden jelöltre `input_readiness` struktúrát készít, és a source hash-eket, feature-öket, kontextus-hash-t és input-hash-t a publikáció pecsétjéhez köti. A publikáció külön is megköveteli a jelenlegi readiness verziót. A régi issued/assumed-placement rekordok nem változnak. Az új forrás megérkezésekor a következő normál engine-frissítés újraértékel, nem marad végleges tiltólistán.

## Élő read-only próba

A `audit/input_readiness_probe.json` az actual próbát, kódhash-eket és before/after reprodukciókat őrzi. 09:05:12 UTC-kor 93 nyitott rekordból 51 már elkezdődött, 21 aktuálisan kvalifikált, 18 edge-vesztett, 3 exact quote nélkül volt. Egy korábbi 09:00:41-es snapshotban a megoszlás 51 / 21 / 19 / 2 volt; egy további exact selection időközben kikerült a feedből. Az aktuális 21 megoszlása NHL 9 / NFL 6 / WNBA 4 / MLB 2. Ez nem 21 új fogadási utasítás, hanem már kiadott rekordok aktuális ellenőrzése.

8 közelgő MLB-eseményből 5-nél igazolt a szükséges pitcher input, 3-nál hiányoztak a kezdők. A mai érintett két meccs már teljesíti ezt a feltételt; a korábbi helytelen kontextus nem. A kész pitcher-inputtal rendelkező MLB-eseményeknél a vizsgált pillanatban 2 konkrét outcome felelt meg az eredeti ár/edge feltételnek, mindkettő már korábban kiadott rekord. Nem keletkeztettünk új tippeket a darabszám emelésére.

## Határok

Minden ligánál kötelező a tanított report összes feature-kulcsának jelenléte és véges numerikus értéke; logikai érték nem helyettesíthet mért számot. 151 aktuális full-game és scope-előrejelzés vizsgálatakor 0 ilyen feature-hiányt találtunk.

Ez szükséges-input ellenőrzés, nem az összes modellhibát kizáró vagy profitot igazoló új tanúsítvány. Nem tanítottuk át a modellt, nem módosítottuk az esélyeket, nem változtattunk tétet vagy árküszöböt. Nem találunk ki utólag tetszőleges pitcher-mintanagyságot: az igazolt pozitív megfigyelésszám és a meglévő modell shrinkage-je ellenőrzött; a stabilitás/kalibráció szerinti új minimumhoz külön, időrendhelyes modellbizonyíték szükséges. A teljes sérülés-/lineup-hatás és profi full-game családszintű minőségi gate továbbra is külön hiány; ez a javítás nem állítja ezeket késznek.
