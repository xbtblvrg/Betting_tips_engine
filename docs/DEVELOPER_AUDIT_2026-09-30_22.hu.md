# Fejlesztői audit — 2026-09-30.22

Felhasználói korrekció: minden ajánlás USD1–5 sávban méretezendő, általános maximum USD5; a meglévő tételeket is újra kell számolni.

## Megvalósítás és határ

- `all_tiers_weighted_v3`: megszűnik az aktív ajánlástípusok külön USD1-es plafonja. Stress Value és pozitív modell-EV esetén a korábbi valószínűség/odds képlet marad; Probability Pick a beállított alapegységgel, kapcsolódó meccskitettséggel és az opcionálisan megadott bankroll korlátjával számol. A nem pozitív EV nem kap kitalált Kelly-t vagy pozitívedge-állítást. A jelenlegi USD1 alapegység ezért adhat USD1 eredményt.
- A korábbi v1/v2 számítások és az eredeti rendszer-döntések változatlanul reprodukálhatók. Minden új v3 döntést ugyanazok a frissességi, független modell-, kontraktus-, modellminőség-, forrás-, elszámolási és webhelyellenőrzések védenek.
- A Stake Sizing nézet újraszámolja a meglévő nyitott kontraktusok teljes célösszegét a friss, pontos ajánlatból és az aktuális meccskitettségből. Az eredeti USD1 kiadási plafont nem használja. Megőrzi a rögzített összeget, a teljes célt, az eltérést és a reprodukálható számítást.
- Ez a nézet számítás, nem új pénzes instrukció. A régi főlistás utasítás összege, a fogadási nyilvántartás, a napi első-kiadási számláló és a korábbi eredmények nem módosulnak. A felhasználótól még nem érkezett válasz arra, hogy a jelenlegi ajánlásokat már ténylegesen megkötötte-e. Emiatt a teljes tét lecserélése és egy már megkötött fogadás kiegészítése még nincs végrehajtva; ezek eltérő, auditált életciklust igényelnek.
- A pipa személyes ellenőrzőlista, önmagában nem megkötési bizonylat. A számítás nem írja felül az eredeti utasítás szerinti feltételezett végrehajtást. Automatikus fogadásleadás nincs.

## Ellenőrzési bizonyíték

A kiadási csomag: `data/existing-stake-sizing-20260930/`.

- `install-manifest.json`: a pontos telepítendő fájlok hash-e, előző verziójuk és teljes teszteredmények.
- `python.log`, `dom.log`: teljes ellenőrzés. A kezdeti izolált környezetből hiányzó hét régi adatforrásteszt fixture-hibája megőrizve: `python-attempt1-missing-fixtures.log`; a helyreállítás pontos fájljai `fixture-copy-evidence.json` alatt. Tesztet nem hagytunk ki emiatt.
- `independent-review.json`: külön Codex-folyamat pontos verzióhoz kötött review-ja.
- `install-journal.json`, `activation-receipt.json`: ellenőrzött forrástelepítés és kizárólag az engedélyezett sizing mód aktiválása; a napi számláló nem resetelhető.
- `live-dom-proof.json`, `live-verification.json`: későbbi friss publikációk, helyi és Tailscale oldal, eredeti döntések megőrzése, számítások és szervizek valós állapota. A telepített/élő állapotot ezek igazolják, nem a jelen dokumentum elkészülte.

## Teljes felhasználói cél újraellenőrzése

Az 1–5 USD méretezés, az angol Stake-terminológia, a friss kontraktusok ellenőrzése, az eredeti döntések és a napi darabszám külön kezelése jelen kiadás tárgya. A meglévő tétek új számítása elkészül; a tényleges új végrehajtási instrukcióvá alakítása a megkötési állapot tisztázásáig részleges.

A projekt egészére változatlanul részleges a teljes liga/piaccsalád-lefedettség, a player props/SGM és minden elérhető piac validált kezelése. Az ismert NHL Double Chance webhely/feed eltérés továbbra tiltott pontos kontraktusonként; az NHL 2027 forrás- és root-integrációs hiányok, NCAAF injury integráció és a korábbi release-receipt feladatok nem tekinthetők ettől lezártnak. A korábbi határidők és hibák megőrzendők. Telegram nincs bekötve. A működő szolgáltatás nem bizonyít teljes készültséget vagy nyereségességet.

A napi minimum15 tényleges új kiadás továbbra cél, nem garancia. Az újraméretezés nem számít új fogadásnak. Ez a kiadás nem módosít sportmodelleket, nem bizonyít magasabb találati arányt, CLV-t vagy profitot. Az eredményeket az eredeti döntéseken és tényleges lezárásokon kell mérni.
