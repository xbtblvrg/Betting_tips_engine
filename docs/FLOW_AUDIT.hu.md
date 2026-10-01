# Fogadási folyamat auditja — 2026-09-29

Az ellenőrzés tárgya az adat → független modell → pontos Stake-szerződés → értékbecslés → publikálás → manuális végrehajtási nézet → változatlan napló → eredménykövetés → modellcsere folyamat. A feltárt konkrét szoftverhibák javítva; ez nem igazolja a fogadások nyereségességét vagy a hiányzó sportmodellek elkészültét.

## Javított hibák

| Súly | Megállapítás és javítás | Kód |
|---|---|---|
| P1 | A régi, ki nem pipált utasítás akkor is „Bet $1” lehetett, amikor a jelenlegi odds már nem érte el a küszöböt. A New York Liberty +3.5 élő példában 2,47% volt a stresszelt előny a 3%-os küszöb mellett. Most külön aktuális végrehajtási ellenőrzés van; a régi rekord változatlan marad, a nem megfelelő állapot „Recheck Required”. | `instructions.py`, `web/app.js:executionReview` |
| P1 | A hosszabb frissítési periódus akár két órára nyújthatta az elfogadott árkort. Most minden útvonalon legfeljebb 300 másodperc, a publikálási tranzakcióban is újra ellenőrizve. | `freshness.py`, `decisions.py`, `governance.py`, `server.py` |
| P1 | A napi újratanítás korábban csak a Winner Brier-javulását vizsgálta a modellcseréhez, a külön receptkeresés pontszám-védelmét nem. Most közös kapu ellenőrzi a margin/total hibáját is. A tanítás közben változó történeti adatok vagy lecserélt modell esetén nincs felülírás. | `train.py:score_gate`, `train.py:promotion_gate`, `experiments.py` |
| P1 | A publikáló csak a korábbi `passed` jelzőt nézte; utána megváltozott számokat nem vette észre. Most a szerződés, odds, valószínűségek és küszöb lenyomatát ellenőrzi az adatbázis-tranzakción belül, valamint a tétet és az időbeli érvényességet. | `governance.py:verify_publication`, `storage.py:publish` |
| P2 | A Today szűrő visszahozhatott már elkezdődött meccset pregame kártyaként. Az aktuális utasítások minden időnézetben csak jövőbeli kezdésűek; a napló megőrzi a rekordot. | `web/app.js:visibleTips` |
| P2 | A már automatikusan rögzített tét árpróbája a nulla új tétet összemosta az ár megfelelőségével. A válasz külön jelzi az ár megfelelőségét és a már rögzített szerződést. | `server.py:check_price`, `web/app.js:showDetail` |
| P2 | Az „AI-jóváhagyás” értelmezése félreérthető volt. A bizonyíték és a részletes nézet most kifejezetten determinisztikus forrás-/számításellenőrzést jelöl, külön LLM-jóváhagyás nélkül. | `governance.py:EvidenceGuard`, `web/app.js:decisionEvidence` |

## Számszaki és működési bizonyíték

- A 23:42:44 UTC pillanatkép 226 kimenetele újraszámolva a betöltött sportmodellek eloszlásaiból; valószínűség- és EV-eltérés nem volt.
- 17 meccseloszlás nemnegatív, össztömege 1. Az Over/Under és push felosztás ellenőrzése több küszöbön konzisztens volt.
- Ez a számítások ellenőrzése, nem a becsült valószínűségek valódi kalibráltságának vagy fogadási hozamának igazolása.
- A javítások előtt mentett 34 fogadási rekord és 29 pipajelölés megőrzését külön ellenőriztük; a kiadási dátumokat és oddsokat nem írjuk át.
- Reprodukálható bizonyíték: `data/flow-numeric-audit.json`; tesztek: `tests/test_flow_audit.py`, `tests/web.test.mjs`.

## Felülvizsgálati értékelés

| Terület | Értékelés |
|---|---|
| Helyesség | A feltárt végrehajtási és kiadási hibák javítva; sportmodell-lefedettség továbbra is részleges. |
| Biztonság | A publikálás szigorúbb belső konzisztencia-ellenőrzést kapott. A lenyomat nem külső kriptográfiai hitelesítés és nem Stake-fiókellenőrzés. |
| Teljesítmény | A tanuló és frissítő külön szolgáltatásban fut; a lenyomatellenőrzés helyi számítás. Teljes terheléses vizsgálat nem történt. |
| Karbantarthatóság | Közös árfrissességi és modellcsere-szabályok, külön aktuális utasítás-ellenőrzés, célzott regressziós tesztek. |

## Nyitott, érdemi képességhiányok

A sérülések, kezdők és játékospercek teljes kvantitatív hatása nincs modellezve. Játékos-, részidős, kombinált és in-play piacokra nincs teljes modellkészlet. A szakirodalom-figyelő címeket és absztraktokat indexel; nem dolgozza fel és implementálja önállóan az összes új módszert. A napi minimum 15 új kiadás és az igazolt költség utáni nettó nyereség továbbra sem tekinthető teljesített követelménynek. A számszaki guard nem pótolja ezeket.

## Éles kiadási ellenőrzés — 01:55 CEST

201 Python-teszt, 5 alteszt és 21 DOM-teszt sikeres. Helyi és Tailscale API hibamentes; a frissítő és tanuló felügyeleti állapota healthy. A 2026-09-29.5 szerződéssel készült friss publikáció mind a 226 jelöltjének lenyomata és érvényessége ellenőrzött. Mind a 34 eredeti jegy teljes rekordja változatlan. 33 különböző fogadási kártya és 33 saját Stake-meccslink látható. A még ki nem pipált New York Liberty +3.5 nem kér új tétet; Recheck Required állapotú. Négy ki nem pipált rekordból három felel meg a jelenlegi végrehajtási feltételeknek, összesen $3 megjelenített aktuális tét mellett. Mai kibocsátás: 4/15, tehát a mennyiségi követelmény még nem teljesült. Bizonyíték: `data/flow-audit-release-check.json`.
