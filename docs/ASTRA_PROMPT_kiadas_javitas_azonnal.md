# UTASÍTÁS – Astra: ha nem jönnek az új tippek, az HIBA – azonnal javítani

Jelenleg sok guardon átjutott, magas nyerési valószínűségű jelölt van, és egyik sem kap tétet („Ready now: 0”). A napi kiadás messze a kötelező 100 alatt van. **Ez nem elfogadható állapot és nem riportálandó eredmény, hanem hiba.**

A rendszer kötelező feladata, hogy a legjobb tudása szerint folyamatosan kiadja a legjobb fogadási ajánlásokat. Ha nem jönnek új tippek, akkor vagy valami túl szigorú, vagy valami rossz. Mindkettőt azonnal meg kell találni és javítani, és élőben igazolni, hogy a tippek ténylegesen jönnek.

## 1. Kötelező diagnózis – most

Élő adatból, minden jelöltre és minden ligára készíts tölcsért:

guardon átjutott jelölt → funding tier → családon belüli kiválasztás → meccs-preferencia → limitek → tét ≥ 1 USD → kiadás

Minden lépésnél legyen meg, hány jelölt esik ki és milyen okkóddal. A legnagyobb kiejtő okokat egyenként javítsd. A `not_selected`, `probability_pick_suppressed`, `event_limit`, `price_below_threshold`, `already_held` és `period_limit` kódok mögötti szabályokat külön vizsgáld meg.

## 2. Kötelezően felülvizsgálandó és javítandó szabályok

A következő szabályok a kódban és a `config/project_contract.json`-ban vannak, és együtt nullára fojtják a kiadást. Mindegyiket igazítsd a felhasználói célhoz: a legesélyesebb fogadások kiadása, napi minimum 100, dinamikus meccsenkénti limit.

1. **Élettartamra szóló meccs- és tierkorlát.** Ma meccsenként és tierenként csak egy első kiadás lehet a meccs teljes élettartama alatt, és eseményenként és ciklusonként csak egy új fallback. Ezt meg kell szüntetni. Egy meccsen annyi különböző pénzes fogadás mehet ki, amennyit a dinamikus meccsenkénti limit enged, bármelyik ciklusban.
2. **Meccsszintű elnyomás.** Ma ha egy meccsen van pozitív EV-s jelölt (akár már kiadott is), az az egész meccsen elnyomja a probability pickeket. Ezt meg kell szüntetni. A jelöltek egymás mellett versenyeznek, és a limitig mindegyik kiadható.
3. **Rangsorolás.** A mostani sorrend (tier → stresszelt ROI → valószínűség) ellentmond a kötelező kiválasztási elvnek. Az új sorrend:
   - elsődleges szempont a nyerési valószínűség;
   - ezt súlyozza a tanult liga × piactípus teljesítmény (WR, ROI) és az ár.

   A családon belüli kiválasztás (`family_selection_priority`) és a meccsek közötti allokáció is ezt kövesse.
4. **Probability pick tétje.** Ne legyen fix 1 USD-os vagy alacsonyabb plafon. A tét 1–5 USD, a meglévő tanult, magabiztosság-alapú tétezéssel. Egy fogadás ne essen ki azért, mert a számolt tét 1 USD alá csúszik: a minimum 1 USD.
5. **„Jobb ár szükséges” / `price_below_threshold`.** Ez a címke ma szinte minden jelöltön ott van. Vizsgáld meg, hogy a minimum odds küszöb indokolatlanul szigorú-e (stresszlevonás, minimumlevonás, fix 15/85 modell–piac súly). A küszöb a tanult kalibrációból jöjjön, ne fix levonásból. Ha a modell rosszul kalibrált, a modellt javítsd.
6. **Shadow-only / CLV-kapu.** Ha bármelyik tier vagy piactípus shadow-only módban fut, vagy CLV-mintára vár, akkor élesben adjon ki tétet. A mérés közben párhuzamosan menjen tovább a tanuláshoz. Mérésre várni nem ok a kiadás leállítására.
7. **`event_limit` és `period_limit`.** A dinamikus meccsenkénti limit élőben bővüljön, ha sok az erős jelölt. Igazold, hogy ténylegesen bővül. Ha nem, javítsd.
8. **Napi ütemfigyelés.** Ha a napi kiadás elmarad a 100-as ütemtől, a rendszer automatikusan adja ki a következő legjobb jelölteket. Ne várjon, és ne csak jelezzen.

Ha egy szabály a felhasználó korábbi utasítása alapján került be, és ellentmond a fentieknek, akkor a **mostani utasítás az érvényes**. Írd át a szabályt a `project_contract.json`-ban, a kódban és a tesztekben is, verziózva.

## 3. A 10/10 bekötési mérés javítása

- A „Learning” elem ne legyen „partial” csak azért, mert az aznapi jelöltek még nincsenek elszámolva. A tanulási elem akkor kész, ha a visszacsatolási út működik: az elszámolt fogadások ténylegesen frissítik a teljesítményt, a kalibrációt és a súlyozást. Ezt mérd és igazold, ne az el nem számolt jelölteket számold hiánynak.
- A `not_observed 0/x` állapotú típusoknál (inning- és eseményspeciálisok, játékos-propok, `activity_unverified`) le kell fejleszteni a hiányzó adat-, parser-, modell- és elszámolási részt. Ez kötelező, nem várakozási állapot.

## 4. Kötelező eredmény – élőben igazolva

- Minden ciklusban van téttel kiadott ajánlás, ha van guardon átjutott jelölt. A „Ready now” nem lehet 0, amíg van ilyen jelölt.
- A gördülő 24 órában legalább 100 különálló pénzes ajánlás születik, a legesélyesebb jelöltekből, 1–5 USD tétekkel, a dinamikus limiten belül.
- A tölcsér (1. pont) a dashboardon élőben látszik.
- A felügyelő agent ezt élő adaton ellenőrzi. Addig nem fogadja el késznek a munkát, amíg a kiadás folyamatosan nem jön, és a napi 100 nem teljesül.

## 5. Riport (magyarul, röviden)

- a tölcsér előtte és utána;
- melyik szabályt módosítottad és hogyan, commit hash-sel;
- az első élő ciklusok kiadott tippjei ligánként;
- ha még mindig nem teljesül: a konkrét kiejtő ok és a folyamatban lévő javítás.
