# Külön felügyelt utility release

A root-owned `betting_engine.release_coordinator` külön szolgáltatás. A coding worker továbbra sem írhat production fájlokat, fogadási adatokat, konfigurációt vagy release bizonylatot. A Codex controller source-bound acceptance bizonylata szükséges; a `candidate_ready` felirat önmagában nem elegendő. A régi bizonylat nélküli jelölésekből javítási/revalidálási feladat lesz.

A `config/release_policy.json` pontos task/fájl listája kezdetben csak önálló, passzív utility modulokat enged. A D01/D03 ismert neveit tartalmazza; a további moduloknál a megadott név szerinti profil újraellenőrzése szükséges, ha a coding agent más fájlnevet választott. Nincs wildcard vagy automatikus policy-bővítés. Már létező, nem e coordinator által kezelt fájlt, core/guard/ledger modult, konfigurációt, credentialt vagy import hookot nem cserél. A D07 meglévő `economics.py` módosítása és a D13 meglévő `closing_prices.py` módosítása ezért külön root integráció marad.

A folyamat committed acceptance → archived exact bytes + accepted snapshot + production base → izolált staging → teljes Python/DOM tesztek → külön read-only Codex release review → újabb source/accepted/policy/immutable-ledger ellenőrzés → pontos reviewed fájlok atomikus telepítése → későbbi friss, guard-konzekvens publication és health ellenőrzés. A production module manifest és minden telepített fájl SHA-256 értéke ismét ellenőrzött. A utility telepítése nem változtat meglévő runtime callsite-ot, nem igényel restartot, és nem állítja, hogy új képesség aktív. A későbbi publication a meglévő szolgáltatás folytonosságának bizonyítéka, nem új modellek aktiválásának bizonyítéka.

A model/prediction/data-pipeline aktiválását igénylő review külön `real_model_gate_required` hibát és fix határidejű javítási feladatot ad. Ez a kezdeti profil akkor sem kapcsol be ilyen változást, ha a candidate tesztjei zöldek. Valódi sportadat, időrendi/per-game független értékelés, source/model/contract/settlement gate szükséges egy külön későbbi aktiváló profilhoz. A utility státusz sosem `production_ready` task receipt és sosem `all_models_ready`.

A journal a `data/releases/journal.json` és a csak controller által írható run archive között tárolja az eredeti hash-eket, a review-t, a teszteredményt és az immutable mezők lenyomatát. Félbeszakadt telepítést újraindításkor code-only rollback állít vissza. A rollback csak a pontosan telepített hash-t írja felül; egy újabb külső szerkesztést megőriz, `manual_attention` hibával. Adatbázist sem normál release, sem rollback, sem recovery nem cserél. Settled státusz és új ticket megjelenhet, az eredeti odds/stake/payload/issuance nem változhat. Journal-integritási hiba esetén nincs fájlmódosítás.

A `data/releases/state.json` minden installt és hibát őriz; `gaps.json` minden nyitott hiányt és eredeti, négyórás checkpointját. Ez checkpoint, nem automatikus sikerígéret. A coding coordinator percenként újranyitja a megfelelő eredeti taskot egy új release-hibára, az attempt történetet megőrizve. Ugyanaz a hiba nem indít végtelen extra task-fanoutot. A root policy változtatást igénylő hibák külön root feladatként látszanak, nem kérnek a workertől jogosultság-lazítást.

## Beüzemelés a root ellenőrzött integrációja után

```sh
.venv/bin/python scripts/release_service.py install
.venv/bin/python scripts/release_service.py status
PYTHONPATH=src .venv/bin/python -m betting_engine.release_coordinator --status
```

A service 30 másodpercenként vizsgál, külön OS flock mellett egyszerre egy release-t hajt végre. Egy kísérlet hibája a következő release-eket nem jelenti késznek. Ideiglenes leállítás: `.venv/bin/python scripts/release_service.py stop`. Egy `--once` futás is valódi, policy-engedélyezett release lehet, nem dry run. Külön felhasználói jóváhagyást nem kér: a projekt autonóm deploy utasítása az engedély, a fenti teszt/review/source gate-ek a végrehajtás feltételei.

A lifecycle regressziók kizárólag ideiglenes könyvtárakat és SQLite adatbázisokat használnak, fake validation/health backenddel. Nem bizonyítanak éles release-t. Az igazi backend ugyanazt az OS izolált teljes tesztfuttatást és a külön Codex read-only review-t használja, mint a felügyelt development controller.

## Ellenőrzési bizonyíték (2026-09-29)

A worker-stage `data/development/runs/release-coordinator-check-2` archívumban a valódi Codex OS sandboxban futó teljes csomag: **684 Python teszt + 5 subtest**, **40 DOM teszt**, 0 skip. A 34 release lifecycle regresszió külön ideiglenes rootokat/adatbázisokat használ. Független read-only agent review 61 célteszttel elfogadta a kódot; a policy-race, managed utility supersession és corrupt receipt izoláció javítása ellenőrzött. A teljes suite nem futtatott valódi release Codex review-t vagy éles deployt; ennek külön release evidence kell.

A CLI 15 másodperces `data/releases/worker.json` heartbeatet ír a hosszú teszt/review alatt is. A `release_health(root)` read-only felügyeleti API megkülönbözteti a friss aktív ellenőrzést a 60 másodpercnél régebbi heartbeatből vagy lejárt, legfeljebb 1560 másodperces lépésből adódó hibától. A validator részletes 5 másodperces heartbeatje külön `validation-worker.json`. Meglévő, hash-ellenőrzött root task-release receipt esetén a korábbi `candidate_ready` flag nem kér új revalidálást; ha az artifact hash megváltozik, ez a kivétel megszűnik.
