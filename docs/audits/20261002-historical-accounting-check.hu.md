# Elszámolási ellenőrzés — 2026. október 2., 11:42 CEST

Az ellenőrzés a már rögzített tételek elszámolására és összesítésére terjedt ki. Fogadási ajánlás, modell, tét vagy eredeti rekord nem változott.

| Mutató | A naplóban szereplő érték |
|---|---:|
| Lezárt szelvény | 264 |
| Érintett lezárt mérkőzés | 31 |
| Nyert / vesztett / tét-visszafizetés | 105 / 153 / 6 |
| Találati arány: 105 / (105 + 153) | 40,70% |
| Lezárt tétek összege | 289,56 USD |
| Visszatérült összeg, téttel együtt | 239,0321 USD |
| Nettó eredmény, működési költségek előtt | −50,5279 USD |
| ROI | −17,45% |
| Nyitott szelvény | 145 |

A nyertes szelvények nettó eredménye összesen +117,9421 USD, a veszteseké −168,47 USD. A tét-visszafizetéseket a találati arány nevezője nem tartalmazza. A nyitott tételek nem szerepelnek a lezárt eredményben. Az azonos meccsre kötött szelvények nem független megfigyelések.

## Ellenőrzések

Egyetlen csak olvasható SQLite-tranzakcióból készült rögzített pillanatkép a `bets`, `system_decisions` és `bet_outcomes` táblákról. Az összesítést a webes statisztikától külön, Decimal-számítással ellenőriztem. Nyert tét: tét × (odds − 1); vesztes: −tét; push/void: 0. Minden lezárt szelvény státuszát összevetettem a hozzá tartozó outcome-rekorddal.

- Nem találtam tétenkénti pénzszámítási vagy kapcsolt outcome-státuszeltérést.
- A webes összesítés a rögzített pénzpontosságon egyezik az önálló számítással. A nyers, szigorú Decimal-egyezés 7,5×10⁻¹⁶ USD tárolási eltérést mutat; az eredeti összehasonlítás is megmaradt.
- 258 szelvény lezárása a megtartott bizonyítékból a meglévő ellenőrzővel reprodukálható. 6 régi szelvény korábbi teljes-meccses hivatkozást használ; ezek figyelmeztetésként maradnak láthatók. Az eredeti döntésrekordokkal együtt 11 figyelmeztetés, 0 jelzett belső elszámolási hiba van.
- A 264 lezárt tételből 247 automatikusan feltételezett végrehajtás, 17 felhasználó által jelentett fogadás. Stake-számlakivonattal való egyeztetés nem történt.
- A sporteredmények külső forrásainak teljes újraellenőrzése nem történt. Az aritmetikai és belső konzisztenciaellenőrzés ezt nem helyettesíti.

## Külön talált adatbeolvasási hiba

A visszacsatolási jelentés `evidence_invalid` állapotú, hibája `feedback_total_evidence_limit`. A hivatkozott különböző történeti bizonyítékfájlok összmérete meghaladja az olvasó 64 MiB-os keretét. Ez az adott visszacsatolási jelentés ellenőrzését akadályozza; ebből nem következik, hogy minden tanulási feladat leállt. A korlátot és a futó rendszert nem módosítottam.

## Bizonyítékok

- `data/accounting-audits/20261002T094212/source-snapshot.json`: pontos rögzített bemenet; SHA a60e2b657d08bdd6fefe6b4fc4e9043f2e82ca31510180c3c8a8b0bc614c0afb.
- Ugyanitt `statistics-api.json`: az élő statisztikai válasz.
- `report.json`: külön számítás, összevetés és korlátok.
- `report-exact-decimal-comparison.json`: a szigorú első összehasonlítás megőrzött eredménye.
- `feedback-read-diagnostic.json`: az adatbeolvasási hiba fájlméretekkel igazolt diagnózisa.

A kimutatott veszteség a naplóban szereplő tételekre vonatkozik. A magasabb találati arány önmagában nem bizonyít magasabb nettó eredményt; a kifizetések és a tétek együtt határozzák meg az eredményt.
