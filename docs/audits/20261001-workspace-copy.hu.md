# Fejlesztői tárhely és élő működés — 2026-10-01

Az élő Stake-keresés az ellenőrzés alatt folyamatosan működött. Budapesti nap szerint 40 pénzes első kiadás jelent meg 20 külön mérkőzésre. A legutóbbi új kiadás 18:05 CEST-kor történt; a későbbi friss publikációkban az aktuális 23 értékelt meccs már korábbi kiadással rendelkezett. A frissítés és az új pénzes első kiadás külön mérőszám.

18:28 UTC-kor a fejlesztői új munkák indítását a 20 GiB-os lemeztartalék védelme megállította. A mért szabad hely 15–16 GiB volt, a történeti munkapéldányok logikai mérete körülbelül443 GiB. 18:44 UTC-re a szabad hely külső, az auditból meg nem állapítható okból458 GiB körülire emelkedett. A felügyelő újra engedélyezte és ténylegesen indította a munkát. Az előkészített történeti klónozó próba ezért0fájlt módosított. A felszabadulást nem tulajdonítjuk ennek a programnak vagy az akkor még nem telepített javításnak. Nem történt történeti törlés.

A megelőző javítás öt fájlból áll: védett natív APFS-másoló, három adat-előkészítő hívó és új regressziós tesztek. A másolat külön inode-on marad; tartalma, metaadatai és forrásazonossága ellenőrzött. Nem támogatott klónozás esetén ellenőrzött hagyományos másolás használható. A csak ellenőrzött adatbájtot exportáló útvonal nem vihet át további, nem ellenőrzött attribútumot. Forrás- és célváltozás, sérült másolat vagy jogosultsági hiba nem írhatja felül észrevétlenül a meglévő célt.

- Teljes validáció:4885 Python-teszt,5 alteszt,216 DOM-teszt; a256 meglévő tesztfájl változatlan.
- Független ellenőrzés:55 másolási,93 telepítési és2 függőségi telepítési/visszaállítási eset.
- Ellenőrzött telepítés:19:10:43.632696 UTC. Az új függőség a hívói előtt került a helyére; az eredeti konfiguráció pontosan visszaállt.
- Aktív munka nem lett leállítva. A betting runtime és a tanulási szolgáltatás végig futott; csak az üres fejlesztői és kiadási szolgáltatások indultak újra.
- Éles natív próba:119692614 bájt valódi adat klónozása, azonos hash, külön inode, változatlan forrás. Ez működési bizonyíték, nem teljes gépszintű tárhelynyereség-becslés.
- Telepítés utáni publikáció:19:11:10.538940 UTC, helyi és Tailscale-health200, egészséges feed.
- Új éles fejlesztői futás:20261001T191109836843-F0038; az előkészítést befejezte, az implementációs fázisig eljutott, mind az öt telepített fájl pontos hashével.
- A195 eredeti rendszer-döntés változatlan maradt. A telepítő az összes védett eredeti naplómezőt is összehasonlította; elszámolás és új kiadás közben továbbhaladhatott.

Az eredményesség nem készre jelenthető. A19:03 UTC-s eredeti döntéskimutatás126 lezárást tartalmazott:50 nyert,73 vesztett,3 push;144.13 USD lezárt tét és−31.8731 USD P&L,−22.11% ROI, működési költség nélkül. Ezek a rendszer eredeti döntéseinek nyilvántartási adatai, nem ellenőrzött számlaegyenleg. A21 meccs/3 meccsnap mintából tartós előny nem igazolt.

A teljes profitból tanuló kör, teljes piaclefedettség és automatikus modellpromóció továbbra is részleges. A D25 időtúllépése és a D31 kiadási tesztadat-hiánya külön, bizonyítékkal megőrzött következő munka; a mostani másolási javítás ezeket nem zárja le. A41 projektkövetelmény újraellenőrzése külön kimutatja a megőrzött, élőben ellenőrzött és részleges állapotokat. A korábbi hibákat, veszteségeket és határidőket nem módosítottuk.

Bizonyítékok: `data/status-checks/disk-recovery-20261001/requirement-reaudit.json`, `current-performance.json`, `profit-learning-status.json`, `release-31/release-completion.json`, `release-31/release-review.json`, `release-31/install-manifest.json`, `release-31/install-journal.json`, `release-31/live-native-probe.json`, `release-31/live-workspace-source-proof.json`.
