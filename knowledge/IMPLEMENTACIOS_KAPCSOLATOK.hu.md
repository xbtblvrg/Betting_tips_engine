# A tudástár felhasználása a futó rendszerben

A könyvek nem kerültek egy nyelvi modell súlyaiba. Tartósan kereshető jegyzetek és ellenőrzött módszertani döntések támasztják alá a kódot. Az implementált módszer nem azonos a könyv összes megállapításának vagy kutatásának reprodukálásával.

| Tudáskártya | Konkrét alkalmazás | Kód / ellenőrzés |
|---|---|---|
| [K01](cards/K01-valoszinuseg-ar-ev.md) | Fair odds, várható érték, push és eltérő kifizetés | `decisions.py`; EV/push tesztek |
| [K02](cards/K02-fuggetlen-modell.md) | Odds nélküli sportmodell, Elo benchmark, külön kalibráció, koherens ponteloszlás | `models.py`; ML és félpontos hendikep egyezési teszt |
| [K03](cards/K03-adat-ido-forras.md) | Forráshash, pontos ID-k, időrend, befejezési késés, megszakított meccsek kizárása | `providers.py`, `context.py`; jövőbeli/saját box score és éjféli szivárgás tesztek |
| [K04](cards/K04-mlb.md) | Dobó-game-log, outs alapú számítás, előzményprior; nincs szezonvégi stat visszatöltése | `PitchingContext`, `FormState.pitchers`; MLB kontextusteszt |
| [K05](cards/K05-nba-wnba.md) | Tempó, labdabirtoklás és hatékonyság különválasztása | `context.efficiency`; késleltetett csapatforma |
| [K06](cards/K06-nfl.md) | Pontokon túli támadási/védési mutatók; külön NFL modell | `STAT_PRIORS['NFL']`; QB/EPA rész még hiányzik |
| [K07](cards/K07-nhl.md) | Lövési és védési mutatók; sérült történeti forrás hivatalosra cserélése | NHL adapter és adataudit; R04 xG megvalósítása még nincs kész |
| [K08](cards/K08-validacio.md) | Időrendi train/selection/calibration/test, Brier/logloss, heti blokkbootstrap, előre rögzített jövőbeli becslések | `models.py`, `train.py`, `storage.prospective` |
| [K09](cards/K09-bankroll.md) | Rész-Kelly, 1–5 USD és közös keret, nulla felfelé kerekítés | `decisions.stake_size`, `storage.risk_committed`; korláttesztek |
| [K10](cards/K10-piac-vegrehajtas.md) | Ár-időbélyeg, díj, mélység, minimum, periódus és szerződés egyeztetése | `markets.py`; spread irány, leírásellentmondás, részidő, dupla esemény tesztek |
| [K11](cards/K11-ai-tanulas-memoria.md) | Verziózott modelljelöltek, mérhető promóció, audit; LLM-bemondott esély helyett numerikus becslés | `train.promotion_gate`, SQLite változási napló |
| [K12](cards/K12-ellentmondasok.md) | Megőrzött hiányok és forrásellentmondások | `data/last_run_issues.json`, kiadáskorlátok és olvasási napló |

Aktuális megvalósítás és korlátok: [IMPLEMENTATION](../docs/IMPLEMENTATION.hu.md). A 2026-09-27-i fejlesztési metrikák retrospektív eredmények; a hangolás utáni valóban új bizonyíték a jövőbeli naplózott meccsekből származhat.
