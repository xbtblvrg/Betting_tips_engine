# Folyamatos ajánláskiadás — 20. kiadás

A felhasználó kifejezetten mindkét felajánlott döntési módot engedélyezte. A változás a finanszírozási feltételt módosítja; nem új modell, nem jobb kalibráció, és nem bizonyított nyereségesség. A tényleges telepítési és élő ellenőrzési eredményt a `data/continuous-delivery-20260930` könyvtár külön, időbélyegzett bizonyítékai tartalmazzák.

## Implementáció és elfogadás

- `funding_policy.py`: reprodukálható stress_value, model_pick és probability_pick. A szigorú mód marad az alapértelmezés. Az explicit éles aktiválás a `continuous_best_available_v1` beállítás.
- A model_pick nominális pozitív modellhozamból méretez, legfeljebb $1-ig. A probability_pick nyerési esély szerint választ, nyíltan megőrzi a nem pozitív modellhozamot, és legfeljebb $1-es, nem Kelly alapú egységet használ. Bankrollt egyik sem talál ki.
- `decisions.py`, `governance.py`, `storage.py`, `instructions.py`: azonos szabály érvényes a kiválasztásban, a lezárt bizonyítékban, a publikációs tranzakcióban és a még végrehajtható eredeti utasításban. Beállításváltozás után új ellenőrzés szükséges.
- Az eredeti szerződés, tét és kiadási idő változatlan. Ugyanazt a szerződést az átárazás nem teszi új fogadássá. Egy meccsen ciklusonként legfeljebb egy új fallback, és meccsenként/módonként egy első kiadás engedélyezett. A szigorúbb mód változatlanul méretezhet $1–$5 között.
- A webes kártya és részletablak megkülönbözteti a módokat. Az eredménynézet az eredeti döntések módja, ligája és piaca szerint mutatja a ténylegesen elszámolt eredményt. Korábbi, módjelölés nélküli döntések nem kapnak utólag új besorolást.
- Az automatikus fejlesztő nem módosíthatja a védett finanszírozási modult. A piaci frissítés továbbra is külön fut a tanulástól és a szoftverfejlesztéstől.

## A teljes parancs állapota

| Követelmény | Ellenőrizhető állapot és fennmaradó munka |
| --- | --- |
| 1. Jobb fogadási döntések, szakirodalom | A meglévő tudáskártyák és eredeti döntések megmaradnak. Ez a kiadás nem igazol új szakirodalmi modelljavulást. |
| 2. Főpiacok és teljes piaclefedettség | A főpiaci elsőbbség és a teljes feed leltára megmarad. Minden játékospiac, kombináció és felfedezett liga teljes modellezése továbbra sem kész. |
| 3. Független valószínűség | A sportmodell és bemeneti ellenőrzés változatlan; csak a későbbi finanszírozási döntés változik. Nem keletkezik kitalált valószínűség. |
| 4. Tétezés | Stresszelt $1–$5, két explicit legfeljebb $1-es mód. Nincs napi összegkorlát és nincs kitalált bankroll. |
| 5. Hedge és duplikáció | Az eredeti duplikációvédelem és családválasztás megmarad. A több szerződés nem jelent bizonyított hedge-et; új hedge-modell nem készült ebben a kiadásban. |
| 6. Publikáció és napló | Kötelező EvidenceGuard, friss pontos szerződés és tranzakciós ellenőrzés. Ismert webhely/feed konfliktus továbbra is kizáró ok. Nincs automatikus fogadásleadás. |
| 7. Folyamatos keresés, napi legalább 15 | A 60 másodperces célütem és az eredeti napi számláló megmarad. A tényleges teljesítést új éles első kiadások bizonyítják; archív visszajátszás nem számít teljesítésnek. |
| 8–9. Tartós javítás, határidő | Az integrációs feladat és próbálkozások a root-task és tesztnaplók alatt megmaradnak, fix 16:25 UTC határidővel. Hibás próbálkozás nem törölhető és a határidő nem tolható el. |
| 10. Felügyelt engineering loop | Izolált párhuzamos Codex munka, független review, külön telepítés és élő visszaellenőrzés szükséges. A folyamatok futása önmagában nem igazolja a hiányzó modellek elkészültét. |
| 11. Tanulás és eredmény | Módszerenként külön eredeti kiadási eredmények. Új modellek, tartós CLV-előny vagy profit javulása ebben a kiadásban nem bizonyított. |
| 12. Bizonyíték | Pontos fájlhash-ek, teljes regresszió, független review, telepítési napló, helyi és Tailscale publikáció és változatlan eredeti rekordok ellenőrzése külön állapotok. |

A korábbi webhely-elérhetőségi és release-receipt feladatok, valamint a hiányzó piaci képességek nem záródnak le ettől a finanszírozási változástól. A friss élő eredményt és a napi kiadott darabszámot a kiadás utáni audit rögzíti; a nettó profit továbbra is mérendő cél.
