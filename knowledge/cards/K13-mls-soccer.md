# K13 — MLS és Labdarúgás: háromkimenetes piacok, döntetlen-modellezés, gólintenzitás

Típus: sportági szintézis és labdarúgás módszertan. Állapot: független MLS csapatmodell, empirikus döntetlen-arány és pontos elszámolás implementálva; játékos- és xG-szintű kiegészítések kutatási fázisban.

## K13.1 — Háromkimenetes (1X2) piac és döntetlen

A labdarúgásban a rendes játékidő döntetlennel zárulhat. A kétutas Winner (Draw No Bet) döntetlennél tétvisszafizetéssel (push) zárul, míg az 1X2 piacon a döntetlen külön nyerő kimenetel. A modellezett gól- és kimeneteloszlásnak meg kell őriznie a természetes döntetlen-tömeget (empirikusan ~26% az MLS-ben).

## K13.2 — Gólok és Poisson/score-eloszlás

Az átlagos csapatszintű gólintenzitás alacsony (~1.4 gól/csapat), így a kerekített diszkrét gól- és spread-eloszlások aszimmetriát mutatnak. A Spread (Handicap) és Total (Asian Total) piacok kimeneteleit a közös empirikus góleloszlásból, az 1X2 valószínűségekkel koherensen kell árazni.

## K13.3 — Formaindex, pihenőidő és hazai pálya

Az MLS-ben a jelentős utazási távolságok és a szintkülönbségek miatt a hazai pálya előnye markáns. A csapatok formaindexe (Elo- és gólkülönbség-alapú frissítés) a pihenőnapok számával és a hazai/idegenbeli szerepléssel korrigálandó.
