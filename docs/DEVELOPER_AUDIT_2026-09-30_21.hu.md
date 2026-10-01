# 21. kiadás — Model Pick tétméretezés

A felhasználó engedélyezte a számított tétet a pozitív modellértékű Model Pick ajánlásokra is. Az új `model_pick_sizing_policy=model_weighted_v2` a meglévő nominális valószínűségből és oddsokból számított méretezést alkalmazza $1–$5 között, az azonos meccshez tartozó korábbi fogadások miatti csökkentéssel és a megadott bankroll korlátaival. Bankroll hiányában a már dokumentált alapegység-képlet működik; személyre szabott Kelly-optimumnak vagy validált profitnövelésnek nem nevezzük.

A régi `unit_cap_v1` szabály alapértelmezett marad a korábbi bizonyítékok reprodukciójához. Az új finanszírozási bizonyíték `funding-policy-v2`, a Model Pick tétbizonyítéka `model-pick-unit-v2`. A régi eredeti tétek nem változnak, és az új policy nem hoz létre ráemelést, új példányt vagy napi számlálóresetet. A Probability Pick továbbra is legfeljebb $1, a Stress Value méretezése változatlan.

A guard, a publikációs tranzakció, az aktuális végrehajthatóság és a webes kártya/részletablak egyezteti az aktív méretezési szabályt. Beállításváltozás után új, friss ellenőrzés szükséges. A már kiadott nagyobb tétet romló jel vagy növekvő kapcsolódó kitettség esetén a főoldal nem utasíthatja végrehajtásra; az eredeti rekordot és a későbbi eredményt megőrizzük.

## Bizonyíték és a teljes parancs állapota

- A tétképlet tesztjei lefedik az $1, $2, $3, $4 és $5 összeget, a tört oddsot, a push valószínűségét, az azonos meccs kitettségét, a bankrollkorlátot, a régi formátumot és az érvénytelen bemenetet.
- Valós guard → publikáció → eredeti döntés → friss utasítás integráció ellenőrzi a nagyobb tétet, az eredeti $1 megőrzését, a módváltási versenyt, a módosított bizonyíték kizárását és a lejárt quote-ot.
- A 17 tényleges korábbi Model Pick v1 fogadás eredeti költségszámítását az új kód olvasási művelettel sikeresen reprodukálta. Részletek: `data/model-pick-sizing-20260930/legacy-reproduction.json`.
- A teljes tesztnaplók, pontos forráshash-ek, független review, külön telepítés és beállításaktiválás, valamint a friss helyi/Tailscale-visszaellenőrzés a `data/model-pick-sizing-20260930` könyvtárba kerülnek. Egy teszt nem helyettesíti az élő bizonyítékot.
- A 12 pontos teljes követelmény auditjának alapja továbbra is a 20. kiadás auditja. Ebben a kiadásban a 4. pont tétezése, a 6. pont végrehajtási bizonyítéka és a 12. pont ellenőrzése bővül. A korábbi piaci, adatforrás-, modell-, CLV- és release-átadási hiányok állapota nem lesz kész ettől a módosítástól.
- Az ajánlásfrissítés és a háttérfejlesztés külön folyamat marad. A napi cél, az $5 felső határ, a `daily_risk_limit=null`, az eredeti rekordok, a modellek és az EvidenceGuard változatlan alapkövetelmények. Nincs automatikus fogadásleadás.

Ez a kiadás a kért tétméretezést vezeti be. Modellpontosság vagy nyereségesség javulását az új méretezés önmagában nem bizonyítja; azt az eredeti kiadások későbbi eredményeivel kell mérni.
