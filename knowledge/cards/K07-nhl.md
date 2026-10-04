# K07 — NHL: lövésminőség, kapus, speciális egységek

Típus: sportági szintézis, kiegészítendő modelljelöltek. Forrás: [B08 40.fejezet, PDF396–407](../reading_v2/B08-notes.hu.md), [B21 17.fejezet, PDF137–140](../reading_v2/B21-notes.hu.md), [B15 élőpiaci fejezet, PDF197–215](../reading_v2/B15-notes.hu.md), [R04 teljes tanulmányjegyzet](../online/R04-notes.hu.md). Állapot: a könyvek NHL-lefedése korlátozott; R04 teljes olvasása elkészült, empirikus reprodukciója még nincs meg. Teljes mai meccsmodell még nem áll rendelkezésre.

## K07.1 — Lövés és gól

Nyers lövésszám, veszélyesség/xG, shot location, játékállapot, emberelőny/hátrány, kapus és üres kapu eltérő szerepű. Az xG lövésenkénti feltételes gólbecslés; összege nem garantált meccsvégeredmény és nem automatikusan azonos a független Poisson intenzitással. Szituáció- és scorehatás befolyásolja a lövésmennyiséget is.

Adat: befejezett meccsek és shot events, TOI, lineup, kapuskezdés megerősítése és időbélyege, pihenő/utazás, erőviszony. Bizonytalan kapus külön forgatókönyv. Teszt: szituációnkénti xG-kalibráció, góleloszlás, csapatösszegek és közös score, valamint regular-time/OT/shootout kimenetkülönbségek. Pontos elszámolás nélkül a két- és háromutas moneyline nem összevethető.

## K07.2 — A bootstrap nem új sportadat

B21 tíz meccs0.7-es gólátlagát közel10000újrahúzással ismét becsüli. A resampling Monte Carlo-ingadozása nem képességjavulás, és az átlag eloszlása nem a következő meccs góleloszlása. A10meccs kevés marad; ellenfél, TOI, szerep és sorösszetétel hiányát az újrahúzás nem pótolja. Teszt: az egyszerű zsugorított rátához mért következőmeccses logscore; a prior és exposure nyílt megadása, nem megfigyelésszám felfújása.

## K07.3 — Lövő- és kapushatás: tesztelendő kutatási eredmény

[R04, PDF 7–13](../online/R04-notes.hu.md) korábbi lövésekből becsült lövő- és kapushatással egészíti ki az alap xG-t. A közölt javulás támadóharmadbeli, kaput eltaláló, öt az öt elleni lövésekre érvényes; nem kész meccsgyőzelmi modell vagy igazolt fogadási stratégia. A képesség becslésekor az előzmények mennyiségét, a kis minták zsugorítását és a nulla nevezőt kezelni kell.

Ellenőrzés: időrendi, meccsenként csoportosított validáció; azonos mintán alapmodell, csak lövőhatás, csak kapushatás és közös modell összevetése. A cikk belső keresztvalidációjának, arénakorrekciójának és percentilisküszöbeinek időbeli helyessége a leírásból nem teljesen igazolható. Pregame alkalmazáshoz a lövésmennyiség és helyzeteloszlás, a várható játékospercek és a felállás külön előrejelzése is szükséges. Állapot: ígéretes hipotézis, nem bevezetett engine-szabály.
