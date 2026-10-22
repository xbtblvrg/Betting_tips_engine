# Autonomous Quant Engine — Cold Start & Disaster Recovery Guide

Ez a dokumentum a **Betting Quant Engine** teljes, önálló helyreállítási és beüzemelési kézikönyve.  
Ha egy teljesen új gépen (vagy tiszta környezetben) kell elindítani a projektet, **elegendő ezt a repository-t átadni a fejlesztőnek vagy az AI-nak**, és az alábbi lépéseket követni.

---

## 1. Rendszer Áttekintés & Alkotmány

A rendszer egy önálló, több-modelles kvantitatív sportfogadási motor, amely 100%-ban ingyenes, nyílt forrásokból (ESPN, NHL API, MLB Stats API, Open-Meteo, Polymarket CLOB, Stake odds) dolgozik, szigorú tőkevédelmi szabályok mellett:
- **Irányadó Alkotmány:** [`AGENTS.md`](./AGENTS.md) (A 10 Legfőbb Rendszerparancs)
- **Fejlesztői Irányelv:** [`GEMINI.md`](./GEMINI.md) (Szigorú bizonyítási standard, védett fájlok)
- **Univerzális Audit Prompt:** [`docs/AUDIT_MASTER_PROMPT.md`](./docs/AUDIT_MASTER_PROMPT.md) (Ezt a promptot bemásolva bármelyik AI lefolytatja a teljes körű rendszerauditot és elhárítja a szűk keresztmetszeteket)
- **Legfrissebb Audit Jelentés:** [`docs/SYSTEM_AUDIT_REPORT_20261022.md`](./docs/SYSTEM_AUDIT_REPORT_20261022.md)

---

## 2. Előfeltételek (Prerequisites)

- **Operációs Rendszer:** macOS (optimális a LaunchAgent háttérfolyamatokhoz) vagy modern Linux (Ubuntu 22.04+)
- **Python:** 3.12+ (ajánlott: Python 3.14 vagy 3.12)
- **Node.js:** v20+ és `npm`
- **Git & SSH:** Hozzáférés a távoli tárhelyhez (`git@github.com:xbtblvrg/Betting_tips_engine.git`)
- **SQLite3:** Rendszerszintű sqlite3 CLI

---

## 3. Lépésről Lépésre Történő Beüzemelés (Cold Start Setup)

### 1. Repository Klónozása
```bash
git clone git@github.com:xbtblvrg/Betting_tips_engine.git
cd Betting_tips_engine
```

### 2. Python Virtuális Környezet & Függőségek
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install pytest anyio uvicorn
```

### 3. Frontend / Web Teszt Környezet
```bash
npm install
```

### 4. Adatbázisok & Könyvtárak Előkészítése
Ha a `data/` mappa üres, hozd létre a szükséges struktúrát és indítsd el a bootstrap scriptet:
```bash
mkdir -p data/logs data/research
python3 scripts/bootstrap.py
```
*(A rendszer automatikusan felépíti az `engine.sqlite3` és `shadow_incubator.sqlite3` sémákat, valamint a záróár- és inkubációs táblákat.)*

---

## 4. Szolgáltatások Indítása (Service Execution)

A rendszer 4 fő szolgáltatásból áll:

### A) macOS LaunchAgent Mód (Termelési környezet)
A felhasználói LaunchAgent fájlok a háttérben, újraindítás-védve futtatják a démonokat:
1. `hu.belavarga.betting-quant-engine` (API & Döntési motor: port 8765)
2. `hu.belavarga.betting-quant-learning` (Háttértanuló, modellező és inkubátor worker)
3. `hu.belavarga.betting-quant-release` (Kiadási koordinátor)
4. `hu.belavarga.betting-quant-supervisor` (60 másodperces watchdog és elszámolás-felügyelő)

### B) Manuális / Linux CLI Indítás (Fejlesztői vagy VPS környezet)
Egy terminálban vagy `tmux`/`systemd` alatt az alábbi parancsokkal indítható el a 3 fő folyamat:

```bash
# 1. API Szerver & Döntési Motor (Port 8765)
python3 -m uvicorn betting_engine.server:app --host 127.0.0.1 --port 8765 --no-access-log

# 2. Tanulási & Kvant R&D Worker (külön processz)
python3 -m betting_engine.learning

# 3. Kiadási Koordinátor (külön processz)
python3 -m betting_engine.release_coordinator --root .
```

---

## 5. Működés Ellenőrzése & Verifikáció (Sanity Check)

Futtasd le a teljes körű tesztcsomagot az új környezetben:
```bash
# 1. Python egység-, modell- és invariáns tesztek
pytest tests/test_codebase_integrity.py tests/test_decisions.py tests/test_learning.py tests/test_shadow_market_incubator.py tests/test_graduated_market_execution.py

# 2. Frontend Happy-DOM tesztek
npm test

# 3. Élő motor egészség-ellenőrzés
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/component-health
```

---

## 6. Folyamatos GitHub Szinkronizáció (Backup Script)

A gépen végzett fejlesztések, modellek és dokumentációk azonnali feltöltésére használd a mellékelt automatikus szinkronizáló szkriptet:
```bash
./scripts/sync_to_github.sh
```
A szkript atomi módon rögzíti a módosításokat, létrehoz egy időbélyeges commitot, és biztonságosan felküldi az adatokat az SSH ágon a GitHub repository-ba (beleértve a git tageket is).
A háttérben futó macOS LaunchAgent (`hu.belavarga.betting-github-sync`) ezt 15 percenként magától is lefutattja.

---

## 7. GitHub Issues — Automatikus Feladat- és Kvantkutatási Tracking

A rendszer önállóan képes a GitHub Issues felületén nyomon követni:
1. **Kutatási és modell-hiányosságokat** (`research-gap`): ha egy ligának még nincs független modellje vagy funkcióbővítése szükséges.
2. **Karanténba került piacok turnaround feladatait** (`capital-defense`, `quant-council`): a `$0.00` tőkevédelmi karanténban lévő ligák AI általi megjavítását és Crucible re-graduációját.
3. **Automatikus lezárás**: amint egy karanténba helyezett ligában minden piac sikeresen re-graduál a Walk-Forward Crucible teszten, a rendszer magától lezárja a megfelelő Issue-t.

Kézi futtatás / ellenőrzés:
```bash
python3 scripts/sync_github_issues.py
```
*(Ez a lépés automatikusan bele van kötve a 15 perces `scripts/sync_to_github.sh` ciklusba is.)*

---

## 8. GitHub Releases & Tagging — Mérföldkövek és Visszaállási Pontok

Minden jelentős fejlesztési mérföldkőnél (pl. tőkevédelmi zárás, új modell diplomázása, PnL fordulat) automatikusan verziózott GitHub Release és git tag hozható létre:

```bash
# Új mérföldkő publikálása
python3 scripts/create_github_milestone.py v2026.10.23-alpha-recovery --title "v2026.10.23 — Alpha Turnaround & Live Profit Milestone" --summary "Részletes leírás az elért eredményekről és verifikációról..."

# Meglévő kiadások listázása
gh release list

# Korábbi verzióra való visszatérés / megtekintés
git checkout v2026.10.22-capital-defense
```

