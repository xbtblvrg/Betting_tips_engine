# K06 — NFL: drive, irányító, állapot és diszkrét pontok

Típus: sportági szintézis és jelölthipotézisek. Forrás: [B06 1–6.fejezet](../reading_v2/B06-notes.hu.md), [B08 18–27.fejezet](../reading_v2/B08-notes.hu.md), [B07 teljes mellékelt22.fejezet](../reading_v2/B07-notes.hu.md), [B18 12–16.fejezet](../reading_v2/B18-notes.hu.md), [B21 PDF131–136](../reading_v2/B21-notes.hu.md). Állapot: történeti alapok; aktuális szabály/feature definíció ellenőrzése szükséges.

## K06.1 — Erősség és környezet

Net passing hatékonyság, sacks, EPA jellegű állapotérték, ellenfélerő, kezdő QB és pótló, támadófal, védelem, speciális egység és várható tempó külön tényező. Turnover és return TD ritka/zajos lehet; lezárt szezonból illesztett marginregresszió nem automatikusan pregame predictor. A szél/időjárás, helyszín, utazás és sérülésértékek feltételes becslések, nem örök Walters-ponttáblák.

Adat: play-by-play cutoffig, befejezett meccsek, roster/depth chart/injury publication time, forecast időjárás az előrejelzés pillanatából. Lezajlott meccs végső időjárása történeti pregame feature-ként szivárgás. Teszt: drive/passing jel hozzáadása a csapatratinghez, ellenfél- és QB-váltások szerinti külön hibaelemzés; kevés szezon adatával a bizonytalanság maradjon látható.

## K06.2 — Ponteloszlás és kulcsszámok

Touchdown, PAT, two-point, field goal, safety és szabályfüggő OT strukturális ponttömegeket okoz. Negatív binomiális pontmargók független szorzata egyszerű baseline lehet, de nem modellezi ezeket vagy a közös possessiont. A3/7körüli spreadérték az aktuális margineloszlásból jön; könyvi push-tábla nem mai paraméter. Döntetlen, regular season és playoff külön elszámolás lehet.

Teszt: score/margin diszkrét gyakoriság, push/tie mass, total és győzelem együttes konzisztencia; csonkolt score-tartomány tailtömege. A plusz1pont nyeresége az átlépett eredmények valószínűsége, nem lineáris cent/pont konstans.

## K06.3 — EP, EPA és WP célváltozó

Next-score EP, hosszú távú relatív pontérték és win probability eltér. Birtoklási perspektíva/előjel hibája megfordíthatja a döntést. A fourth-down go, punt és kick leíró eredményeit a választási szelekció torzítja; nagyobb átlag EPA nem minden állapotban nagyobb győzelmi esély. Pregame modellben a taktikai politika bizonytalansága is része a jövőbeli eloszlásnak. Teszt: ismert egyszerű állapotok, perspektívacsere, lejárt idő, timeout és szabályverzió. A régi OT-elemzés aktuális szabályként való átvétele kizárt.
