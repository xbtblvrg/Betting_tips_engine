# Kiadás19.2.1 — ellenőrzött kiadások átvezetése a dinamikus feladatokra

A19.2 éles ellenőrzése után az F0023, F0024 és F0025 segédmodulok forráshashhez kötött root-kiadási bizonylata érvényes volt, de a fejlesztő a dinamikusan létrehozott feladatokat továbbra is `retry` vagy `candidate_ready` állapotban látta. Az `apply_task_releases` csak az előre felvett tervfeladatokon ment végig. Ez hibás állapotot és felesleges újramunkát okozhatott.

A javítás ugyanazzal a bizonylatellenőrzéssel kezeli a terv és a dinamikus feladatsor egyesített, azonosító szerint egyedi elemeit. Megőrzi az előző állapotot, a korábbi sikertelen próbálkozásokat és az eredeti határidőket. Ha a kiadási bizonyíték érvénytelenné válik, az előző befejezetlen állapot visszatér. Egy segédmodul kiadása nem zárja le a teljes piaci/modellezési mérföldkövet.

A változás a fejlesztési feladatállapotok átvezetését érinti. A fogadási ajánlások modell-, ár-, frissességi és tétellenőrzései, az eredeti fogadások és a napi számláló időablaka változatlanok. Az1–5USD sáv és a `daily_risk_limit=null` megmarad.

Az R01–R33 követelmények teljes auditja a `DEVELOPER_AUDIT_2026-09-30_19_2.hu.md` dokumentumban változatlanul érvényes. Ez az utójavítás az R25/R27/R28/R30/R33 végrehajtási bizonyítékát egészíti ki. A napi minimum és a nem kész piacok/modellek megmaradnak nyitott követelménynek; javulást vagy profitot ez a szoftverjavítás nem igazol.

Bizonyítékok: `data/release-receipt-followup-20260930/root-task.json`, `install-manifest.json`, `python.log`, `dom.log`, `independent-review.json`, `install-journal.json`, `live-verification.json`. A tényleges tesztelt, telepített és élesben igazolt állapotot ezek a megőrzött eredmények külön mutatják. A kiadás előtti nézet nem elég: az éles workernek is át kell vezetnie a három érintett feladatot, és friss helyi/Tailscale-publikáció szükséges. Eredeti határidő:2026-09-30 16:45 Europe/Budapest.
