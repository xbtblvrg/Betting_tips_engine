# Fejlesztési kapacitás – 2026-09-30.25

A felhasználó a Mac erőforrásainak ésszerű kihasználását kérte, 70–80% körüli terhelést is engedélyezve, a gép válaszkészségének megtartásával. Ez a kiadás a fejlesztési végrehajtást módosítja. A fogadási döntések, modellek, tétek, eredeti szelvények és napi számlálási ellenőrzőpont szabályai változatlanok.

## Megmért gép és szűkület

MacBook Pro Mac16,8, Apple M4 Pro, 12 CPU-mag (8 teljesítmény + 4 hatékonysági), 16 GPU-mag, 48 GiB egységes memória. A kezdeti mérésben 35–46% CPU-terhelés, normál OS-memórianyomás és nulla swap látszott. A foglalt RAM önmagában nem memóriahiány: a cache és a kompresszor külön mérendő. A tárhelyből körülbelül 394 GiB maradt szabadon. Pontos időbélyeges adatok: `data/resource-capacity-20260930/hardware-baseline.json`.

A development/release szolgáltatás korábban launchd Background besorolást kapott. A helyi Apple dokumentáció és az izolált exportmérés szerint ez jelentős visszafogást okozhatott. A Standard + Nice 5 profil a kért munkának több futási lehetőséget ad, miközben a HTTP szolgáltatás magasabb, Interactive besorolása megmarad. A mért exportgyorsulás egy részfeladat eredménye, nem a teljes engine sebességének ígérete.

## Új működés

- Explicit bekapcsolással legfeljebb hat izolált Codex-fejlesztési feladat. Bekapcsolás nélkül a háromfeladatos korlát megmarad.
- Legfeljebb öt másodpercenként natív mintavétel: CPU-tick különbség, OS-memórianyomás, memóriaösszetevők, swap növekedés, NSProcessInfo hőterhelési állapot, szabad tárhely.
- 75% gördülő CPU-cél, 80% pillanatnyi magas küszöb. Memórianyomás, gyors swapnövekedés, hőterhelés, alacsony tartalék vagy hiányzó/elavult mérés megállítja az új munkák kiosztását.
- Három külön egészséges minta után legfeljebb egy új hely nyílik meg. Ez puha feladatindítási szabály, nem gépszintű CPU-kvóta; a már futó munkákat a kapacitásvezérlő nem állítja le.
- A CPU és a távoli AI-válaszra várakozás különböző korlát. Haszontalan munkával nem termelünk mesterséges 75%-os terhelést. A felhős AI nem a helyi GPU-n fut.
- A helyi NumPy Accelerate BLAS-t használ. OMP, OpenBLAS és VECLIB indulási szálkorlát egy, így az új párhuzamos munkák alatt a numerikus könyvtárak nem kapnak külön korlátlan szálengedélyt.
- A development státusz megőrzi a mérési időt, engedélyezett/foglalt helyeket és a várakozás okát. A szándékos erőforrás-várakozás nem hamis dispatcher-elakadás.

## Elfogadás és bizonyíték

A tartós feladat: `data/resource-capacity-20260930/root-task.json`, határideje 2026-09-30 22:15 UTC. Az implementált, tesztelt, telepített és élőben igazolt állapotot külön tartjuk nyilván. Teljes Python- és DOM-tesztek, pontos fájlhashhez kötött független Codex-review, ellenőrzött telepítés és legalább két friss helyi/Tailscale-publikáció szükséges. A bizonyítékjegyzék a `verification-index.json`; a tényleges végső állapotot a `live-verification.json` és `FINAL_AUDIT.hu.md` tartalmazza ebben a mappában.

A negatív tesztek magas/elavult/hibás mérést, CPU-számlálóátfordulást, alvás utáni mintaszakadást, memória/swap/hő/tárhely-nyomást, a fokozatos helyreállítást és futó munkák megőrzését vizsgálják. Az élő gépet nem terheljük mesterséges memóriakimerítésig vagy kritikus hőállapotig.

## A teljes parancs újraellenőrzése

Az új erőforrás-követelmény R37-ként került a követelményjegyzékbe. R01–R36 fogadási és mérési szabályai megmaradnak. Nincs új fogadási modell vagy piaccsalád aktiválva, nincs új nyereségességi állítás. A meglevő lezáratlan modellezési/elszámolási/fejlesztési feladatok és eredeti határidejük tovább élnek; a nagyobb kapacitás nem teszi őket automatikusan készre. Az általános, védett magot önállóan javító és kiadó autonóm kör teljes lezárását ez a célzott kapacitáskiadás nem igazolja.
