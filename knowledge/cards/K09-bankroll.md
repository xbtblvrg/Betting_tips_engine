# K09 — Bankroll, 1–5 dolláros tétek és korreláció

Típus: matematikai alap és saját kockázati következtetés. Forrás: [B04 PDF74–167](../reading_v2/B04-notes.hu.md), [B08 49.fejezet](../reading_v2/B08-notes.hu.md), [B09 Staking és Losing](../reading_v2/B09-notes.hu.md), [B18 2.fejezet](../reading_v2/B18-notes.hu.md), [B25 17.fejezet](../reading_v2/B25-notes.hu.md). Állapot: elméleti összefüggések; bankroll és napi veszteségkeret még nincs megadva.

## K09.1 — Kelly és a feltételezései

Egy ismert p-jű, push nélküli fogadás, decimal d és nettó b=d−1 mellett f*=(bp−(1−p))/b=(pd−1)/(d−1). A cél E[ln(W_next/W_now)] maximalizálása. Nem a legnagyobb nyerési esély vagy legkisebb drawdown célja; f*=0 ha nincs pozitív előny, és korlátok módosítják az optimumot. Becsült p-re a plug-in teljes Kelly túl nagy lehet. Fractional Kelly kockázati kompromisszum, nem új előnyforrás és nem minden hasznossági függvény egzakt optimuma.

Push esetén a logcélt a win/lose/push állapotokkal kell számítani; a teljes p és a nem-push feltételes p nem keverhető. Veszteség utáni automatikus emelés nem változtatja meg az EV előjelét. Teszt: ismert analitikus speciális eset, negatív edge→0, fközel1 vesztes állapotban logcsőd, bankroll- és tétkorlátok.

## K09.2 — Közös kitettség

Több fogadásnál E[ln(1+Σ f_i R_i)] a közös kimeneteleloszlástól függ. Egyenkénti Kelly-k összeadása azonos meccs, ugyanazon játékos, közös sérülés vagy azonos modellhiba esetén veszélyes. A kitettség a maximális veszteség, laynél (d−1)stake; nem minden terméknél a kijelzett stake. B25 hétlovas példájának első portfóliója megsérti egy inaktív pozíció optimális feltételét; a könyv optimumcímkéjét nem vesszük készpénznek.

Adat: nyitott és korábban kiküldött tippek, felhasználó tényleges fogadásai és aktuális bankroll. Manuális leadásnál a kiküldés nem bizonyít megtett tétet. Teszt: állapotonkénti PnL, meccs/nap/ligaszintű veszteség és drawdown-szimuláció modellbizonytalansággal. Egymást helyettesítő tippek külön jelölendők, ne legyenek automatikusan halmozhatók.

## K09.3 — A felhasználó tétkorlátja

Frissített felhasználói cél: napi legalább 15 téttel kiadott fogadási tipp és 1–5 USD/fogadás. A [részletes menetrend](../../docs/MENETREND.hu.md) a napi rangsorolt listát és a pénzes ajánlást külön kezeli: megfelelő kínálat mellett tizenöt jelölt akkor is megjelenik, ha nem mindegyiknek pozitív a becsült értéke. A tizenötös cél elmaradását nem címkézzük teljesítésnek. Bankroll, napi keret és felhasználói pénznem nélkül a tétezés nem lehet személyre szabott. Ha a kockázatilag megengedett összeg 1 USD alatt van, azt nem kerekítjük automatikusan felfelé; a tétstátusz a rangsorban külön látható.

Nincs olyan könyvi tétstratégia, amely a negatív vagy ismeretlen edge-et bizonyított nyereséggé változtatja. A napi profit nem követelhető ki a modellből. A rangsor darabszámát, a becsült pozitív értékű jelöltek számát és a tényleges pénzes ajánlások számát külön kell mérni; a kötelező tizenötös kiválasztás teljesítményét külön kell visszatesztelni.

2026-09-28-i végrehajtási követelmény: nincs fix napi összegkeret; a maximum továbbra is 5 USD/fogadás, automatikus emelés nélkül. A napi 15-ös számláló kizárólag különálló, téttel kiadott ajánlásokat számol, megfigyelési jelölteket nem. Bankroll hiányában az alapegység 1 USD. Ez a frissítés felülírja a korábbi tízes cél és a puszta jelöltszámlálás követelményét.

## K09.4 — Differenciált tétek, 2026-09-29

Az 1–5 USD tartományon belüli differenciálás új felhasználói pontosítás. A `stress-kelly-unit-v1` a stresszelt p-ből és az exact oddsból számol nem-push feltételes Kelly-erősséget. Ismert bankroll esetén a korábbi rész-Kelly és bankrollkorlát marad. Ismeretlen bankroll esetén az alapegység szorzója `sqrt(max(1, full_Kelly / 0.02)) / sqrt(1 + related_contracts)`, legalább 1 USD, legfeljebb 5 USD, cent lefelé kerekítve. Az összefüggés itt saját, verziózott tétezési heurisztika; a 2%-os referencia és a négyzetgyök nem bizonyított optimum, nem becsült bankroll és nem empirikusan igazolt profitstratégia. A stresszforgatókönyv nem konfidenciaintervallum. A kapcsolódó fogadások teljes korrelációs portfólióoptimalizálását nem helyettesíti.

A bankrollalapon megengedett 1 USD alatti összeget továbbra sem kerekítjük felfelé. A kiadott fogadás tétét későbbi újraszámítás nem módosítja. Matematikai háttér és a becsült p kockázata: [Chu, Wu, Swartz: Modified Kelly Criteria](https://www.sfu.ca/~tswartz/papers/kelly.pdf), bevezetés és 2. fejezet ellenőrizve; a teljes cikk módszerét ez a heurisztika nem implementálja.
