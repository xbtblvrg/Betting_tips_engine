# K14 — Tenisz (ATP, WTA, Challenger): Markov-lánc adogatás-modellezés, borítás és fáradtság

Típus: sportági szintézis és tenisz kvantitatív módszertan. Állapot: zárt alakú Markov adogatási valószínűség, tiebreak dinamikus programozás, 14 játszma-kimenetel, CPI borítási sebesség-korrekció és Crucible fáradtsági szűrőkapuk implementálva.

## K14.1 — Zárt alakú Markov adogatási modell és tiebreak DP

A teniszben a pontszintű adogatás-megnyerési valószínűségből ($p$) zárt alakban számítható a gém megnyerésének esélye:
$$P(\text{Hold}) = \frac{p^4(15 - 34p + 28p^2 - 8p^3)}{1 - 2p(1 - p)}$$
A szett kimenetelek (6-0-tól 7-6-ig) és a tiebreak valószínűségei dinamikus programozással egzaktul vezethetők le a két játékos relatív adogató- és fogadóerejéből. Nincs döntetlen; a mérkőzés kötelezően két (vagy Grand Slamen három) nyert szettig tart.

## K14.2 — Borítási sebesség (CPI) és felületi korrekciók

A felületi karakterisztika (Hard, Clay, Grass, Indoor) közvetlenül befolyásolja az adogatás hatékonyságát. A Hard pálya jelenti a túraátlag bázist, míg salakon (Clay) az ász- és adogatóhatékonyság -3.5%-kal csökken (hosszabb labdamenetek, magasabb fogadóelőny), füvön (Grass) +3.5%-kal, fedett pályán (Indoor) pedig +2.0%-kal emelkedik.

## K14.3 — Fáradtság és Handicap szűrőkapu (Crucible védelem)

A teniszben a felhalmozott terhelés (hosszú, egymást követő háromszettes mérkőzések kevés pihenőidővel) aszimmetrikusan rontja a mély mínusz hendikepek (-4.5 játék vagy mélyebb) teljesülését, mivel a favorit vezetés birtokában hajlamos energiát takarítani (conservation of energy). A `hyp_tennis_fatigue_handicap_gate_v1` védelmi szabály automatikusan kiszűri a túlméretezett mínusz hendikepeket fáradt játékosoknál.
