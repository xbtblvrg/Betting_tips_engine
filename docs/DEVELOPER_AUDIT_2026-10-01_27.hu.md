# Fejlesztői audit — 2026-10-01.27

Feladat: a tényleges fejlesztési elakadások, elnyelt hibadiagnózisok és felesleges újrapróbálkozások javítása; a modellfejlődés továbbítása az éles döntési folyamatba; napi minimum20 különálló pénzes első kiadás. A fő követelményjegyzék R05/R20/R24/R39 és az AGENTS.md a legújabb parancsot követi. Az eredeti fogadások, a napi checkpoint és az USD1–5 tartomány megmaradnak; a napi összegkorlát továbbra is `null`.

## Azonosított, reprodukált hibák

- A fejlesztői munkapéldányokból hiányzott a két szolgáltatásszkript és a fejlesztői erőforrás-konfiguráció. A meglévő regressziós teszt olvasta ezeket. Az audit13 konkrét teljes tesztfutásban ugyanazt a hibát találta, összesen829,16másodperc tesztidővel; a Codex munka ideje ezen felül értendő. Javítás: pontos, védett controller-fájlok exportja és ellenőrzése az érdemi AI-munka előtt.
- Egyes visszaállítási feladatok érvényes eredeti hibabizonyíték mellett sem ébredtek fel jogosult root-forrásváltozás után. Az eredeti hibát és határidőt megőrző, új forrásgenerációhoz kötött helyreállítás szükséges; sérült archívum nem minősíthető érvényesnek.
- A sandbox előellenőrzése üres stderr esetén csak `Native boundary failed:` szöveget hagyott. Most külön hibaarhívum őrzi a fázist, a folyamatállapotot, a kilépési kódot, a timeout tényét, a valós cleanup-hibákat és a korlátozott kimenet teljes hashét. Sikertelen kontroll sosem ad sikeres engedélyezési bizonylatot; archiválási hiba sem fedi el az eredeti okot.
- A model-search sorazonosítója nem tartalmazta a deklarált recept/protokoll pontos vizsgálatazonosítóját. Ezért új recept ugyanazon sportadatokon nem jutott el a tanulási sorba. A sor és a kísérlet most közös azonosítót használ; változatlan ismételt poll nem indít új kísérletet, megváltozott specifikációhoz új feladat tartozik, a korábbi próbák megmaradnak.
- A napi cél lezárási ellenőrzése15-ös számmal dolgozott. A teljesítésnek az aktuális célértékhez kell igazodnia;19 kiadás nem teljesíti a20-as követelményt. A cél átállítása nem számlálóreset, nem fogadás és nem új kiadás.

## Mért modellkísérlet

A kalibráció nélküli, előre deklarált kontrollreceptet a korábbi fejlesztési időszak eredménye alapján lehetett kiválasztani. A végső összehasonlítás nem receptek rangsorolására szolgált.

| Liga | Kiválasztott recept | Közös tesztmeccs / hét | Páros Brier-különbség95%-os intervalluma | Döntés |
|---|---|---:|---|---|
| NBA | identity_calibration |720 /22 |−0,005248…−0,000553 | A meglévő javulási és pontszám-regressziós kapu teljesült. |
| NFL | flexible_calibration |225 /19 |−0,004286…+0,002783 | Nem igazolt javulás; a jelenlegi éles modell megmarad. |

Az NBA korábbi Brier-értéke0,198951, az új jelölté0,195914; a log-loss0,584903-ról0,577137-re javult. Az egyszerű,50%-os küszöbű osztályozási találati arány70,42%-ról70,00%-ra változott, tehát ezen a mérésen nem nőtt. A promóció alapja a valószínűségi becslés pontossága és a változatlan regressziós kapu; a későbbi fogadások winrate-jét és P&L-jét külön kell mérni. Brier esetén ennél a mérőszámnál az alacsonyabb jobb. Ez archivált, időben elkülönített visszateszten mért javulás, nem új, érintetlen jövőbeli teszt és nem fogadási profitbizonyíték. A korábbi végső teszt már ellenőrzött történeti adat volt; a prospektív eredményesség külön mérendő. Az NBA-jelölt élesítéséhez külön független review, pontos modell/input/forrás-hash, aktuális champion- és sportadat-ellenőrzés, kontrollált telepítés, majd friss éles publikáció szükséges.

## Ellenőrizhető állapotok

A tényleges implementáció, teljes regressziós teszt, független review, telepítés és élő visszaellenőrzés külön bizonylat. A végleges állapotot a `data/operations-learning-20261001/verification-index.json` és a benne hash szerint hivatkozott manifestek, naplók, modellarchívumok, release-journal és helyi/Tailscale-publikációk igazolják. E dokumentum önmagában nem telepítési vagy működési bizonyíték.

A különálló pénzes első kiadásokat a meglévő immutable issuance-nyilvántartás számolja. Az aktív tartós beállítás20; a régi üres tesztadatbázisok15-ös alapértéke csak történeti visszajátszási kompatibilitás. Részleges settings-módosítás nem állítja vissza a20-at15-re. A `require_guard=True`, az aktuális pontos szerződés/ár/időbélyeg ellenőrzése és a finanszírozási politika változatlan.

## Nyitva maradó követelmények

A régi, hiteles eredeti quote- és sportforrás-archívumok hiánya nem oldható meg kitalált történettel. A ligák és különleges piacok teljes validációja, a player props és kombinációk egy része, a prospektív CLV és a tényleges nyereségesség továbbra is külön feladat és mérés. A régi határidő-túllépések és sikertelen kísérletek megmaradnak. A napi20 minimum folyamatos teljesítése és a több ligára kiterjedő profit nem állítható késznek pusztán a célérték vagy egy NBA-modell javításából.
