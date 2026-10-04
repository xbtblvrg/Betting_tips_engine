# K03 — Adat, időbeliség és eredet

Típus: módszertani szintézis. Forrás: [B06 3–6.fejezet](../reading_v2/B06-notes.hu.md), [B03 2.,11–12.fejezet és függelékek](../reading_v2/B03-notes.hu.md), [B11 PDF37–199](../reading_v2/B11-notes.hu.md), [B21 visszatesztkritika](../reading_v2/B21-notes.hu.md). Állapot: elfogadott kutatási követelmények; konkrét élő adatkapcsolatok még nincsenek ellenőrizve.

## K03.1 — Mit tudhattunk akkor?

Három idő különbözik: esemény ideje, forrás publikálási/frissítési ideje, saját letöltés ideje. Korábbi tipphez csak az akkori cutoff előtt elérhető információ használható. Utólag javított box score, szezonvégi statisztika, visszamenőleg frissített sérülésstátusz vagy mai weboldal nem bizonyítja a múltbeli hozzáférhetőséget. A gördülő átlagból az aktuális és későbbi meccset ki kell zárni; az eredmény befejezési ideje is számít, különösen azonos napon.

Adatigény: forrás-URL/azonosító, nyers snapshot, lekérési idő, source_updated_at ha van, tartalomhash, sémaverzió, esemény- és résztvevő-ID. Ismeretlen publikálási időnél a bizonytalanságot meg kell őrizni. Teszt: későbbi sorok hozzáadása ne változtasson a korábbi cutoffra újraépített feature-ön; jövőbeli eredmény ne kerülhessen a tanítóadatba.

## K03.2 — Join, hiány és mértékegység

A belső join sem garantáltan csökkenti a sorszámot: sok-sok kapcsolat dupláz. A csapatnév önmagában nem stabil kulcs; játékoscsere, liga és szezon a kapcsolat része. Hiányzó nem automatikusan nulla. A .2 baseball inning két outot jelent, nem tizedes .2inninget. Perc, possession, PA, drive és meccs külön exposure. Teszt: kulcsegyediség, join-kardinalitás, sorvesztés, tartomány, mértékegység és idősorfolytonosság. A nyers hibát megőrizzük, javításának indoka naplózott.

## K03.3 — Hírek és lineupbizonytalanság

Forrás: [B07 PDF7–20](../reading_v2/B07-notes.hu.md), [B14 7.,10.,12.,14.fejezet](../reading_v2/B14-notes.hu.md), [B19 22–30.fejezet](../reading_v2/B19-notes.hu.md). Sérült sztár önmagában kevés: pótló játékos, szerep, várható perc/usage és csapatösszetétel számít. A hír igazolt állítása és a belőle becsült pontérték külön réteg. Bizonytalan részvételhez forgatókönyv és súly tartozhat; az ismeretlen súlyt nem találjuk ki magabiztosan. Teszt: ellenőrzött kezdősor után a frissítés iránya/nagysága és kalibrációja; kettős beszámítás kizárása. A hír és ugyanarra reagáló oddsmozgás nem két független sportfeature.
