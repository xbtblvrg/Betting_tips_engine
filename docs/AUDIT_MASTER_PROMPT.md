# Univerzális Rendszeraudit és Zárt Működési Lánc Mester Utasítás (Universal Supreme Audit Prompt)

Ez a dokumentum tartalmazza a kvantitatív fogadási motor **időtálló, univerzális Mester Számonkérő Utasítását**. Teljes körűen lefedi a rendszer összes szintjét: a háttérdémonokat, a zárt adat- és döntési láncot, a többmodelles evolúciós versenymotort, az árnyék inkubátort, a bajnok-diplomáztatási réteget, a tőkevédelmet és a 10 Legfőbb Alkotmányos Rendszerparancsot.

---

## 📋 Az Aktualizált Mester Utasítás (Copy-Paste Master Prompt)

```text
Végezz teljes rendszerauditot és mélyreható kvant felülvizsgálatot a legfrissebb élő állapot alapján! Keresd meg a logikai hibákat, inkonzisztenciákat, anomáliákat, regressziókat és elakadt folyamatokat a teljes rendszerben. Ellenőrizd a követelmények, a kód, az adatok, a matematikai modellek, az evolúciós homokozó, az árnyék inkubátor, az ajánlások, az elszámolás és a felület összhangját, valamint a fejlesztések tényleges telepítési állapotát.

1. Démonok, Háttérfolyamatok & Hibanaplók:
   - Térképezd fel és ellenőrizd az összes aktív háttérszolgáltatást, démont és ütemezett feladatot (uvicorn API, learning worker, supervisor, dashboard worker, release coordinator): igazolva, hogy a legfrissebb kód fut-e a memóriában, ellenőrizve az erőforrás-használatot és a szolgáltatások egészségét.
   - Pásztázd át a rendszer összes hibanaplóját és felügyeleti jelentését: keress friss kivételeket, zárolási/I-O problémákat, hálózati elakadásokat, kezeletlen szálhibákat és elnyelt kivételeket.

2. A Teljes Zárt Lánc Végigkövetése (Adat → Modell → Ajánlás → Főkönyv → Elszámolás → Gyorsítótár-ürítés → Tanulás → Validálás → Visszacsatolás):
   - Kövess végig konkrét ajánlásokat a nyers forrástól a kiadáson, a végső hivatalos eredmények szerinti elszámoláson át a tanulási visszacsatolásig.
   - Ellenőrizd a teljes rendszer bekötését és működési hurkait: minden modul, fejlesztés és háttérfolyamat ténylegesen meghívódik-e, megfelelő triggerrel és ütemezéssel fut-e, megkapja-e a szükséges bemeneteket, és az eredményét felhasználja-e a következő lépés.
   - Igazold az elszámoláskori memóriagyorsítótár-érvénytelenítés (cache invalidation) lefutását: a lezárt mérkőzések után a PnL és ROI azonnal, pontosan és elavult adatok nélkül frissül-e.
   - Keress bekötetlen kódot, megszakadt visszacsatolást, beragadt vagy duplikált ciklust, hibás aszinkronitást, retry-csapdát és újraindítás után elvesző feladatot/állapotot.

3. Többmodelles Homokozó, Evolúciós Versenymotor & Bajnok-Diplomáztatás:
   - Vizsgáld meg a modell-homokozót és az alternatív matematikai archetípusokat (Negatív Binomiális túldiszperzió, Helyzeti Fáradtság, Odds-damped Kelly zsugorítás, Bivariáns Kopula, Zéró-inflált Poisson / ZIP): a jelöltmodellek generálása akadálytalanul fut-e minden gyengélkedő vagy demotált szegmensre.
   - Ellenőrizd az Árnyék Inkubátor párhuzamos versenyeztetését: a jelöltmodellek virtuális ($0.00) árnyékfogadásai párhuzamosan és pontos adatokkal (csapatnevek, oddsok, határok) bekerülnek-e az adatbázisba, és lezárulnak-e a valós végeredmények alapján.
   - Ellenőrizd a Crucible Scorecard (S) számítását, a sétáló előretesztelést (Walk-Forward) és az elutasított modellek feketelistázását (anti-looping signature hash zárolás): a gyenge modellek kiszűrése és a valódi bajnokok felkarolása hibátlan-e.
   - Igazold a lediplomázott bajnok modellek (Crowned Champions) élő becsatolását: a diplomázott univerzális szegmensmodellek közvetlenül az új bajnok modellt hívják-e meg, tiszta domén-valószínűségeket generálva a live szorzókra.

4. Multi-AI Szinergia, Kitalált Megoldások & Beragadt Innovációk Felülvizsgálata:
   - Térképezd fel és tekintsd át az összes háttérben futó AI agy (Gemini Antigravity, Claude, Codex, Grok), a Kvant Tanács és a kutatási motor által kidolgozott, már kitalált, de még várakozó, beépítetlen vagy kapuknál beragadt megoldásait (előkészített kódjelöltek, staging tervek, jóváhagyásra váró hipotézisek, képességbővítési tervek, azonosított kudarcmódok és védelmi mechanizmusok).
   - Vizsgáld meg, miért nem kerültek élesítésre ezek a kész ötletek (pl. biztonsági kapu elakadás, validációs sorban állás, elmaradt promóció vagy hiányzó implementációs híd), és mutasd be a feloldásuk módját.
   - Ellenőrizd, hogy az AI-k által javasolt védelmi szabályok, modellek és szűrők ténylegesen érvényesülnek-e a döntési motorban, vagy beépítetlenül elakadtak.

5. Piacok, Kereszt-Konzisztencia, Tőkevédelem & Karantén Integritás:
   - Ellenőrizd a szegmens- és piackulcsok kereszt-konzisztenciáját (canonical segment key normalization): a ligák, periódusok és piaccsaládok formátuma egységes-e a döntési motorban, az inkubátorban és a versenymotorban (pl. alappiacok, csapat-összesített piacok, BTTS, dupla esély, hendikep).
   - Ellenőrizd az összes piac és szegmens teljesítményét: a leromló, negatív szegmensek szigorúan el vannak-e zárva a valós tőkétől ($0.00 virtuális karantén / inkubáció), amíg újra nem diplomáznak a Crucible teszten (zéró tőkeszivárgás).
   - Igazold a rendszer alapvető pénzügyi céljainak betartását: min. 10.0% heti ROI, napi kumulatív PnL emelkedő trend, szigorú min. 100 finanszírozott ajánlás / Budapest nap (és a deficit-vezérelt piaci expanzió lefutása hiány esetén).
   - Ellenőrizd a 72 órás likviditási horizontot, a 60 mp / 15 perces szkennelési ciklusokat és a dinamikus tétskála ($0.50–$8.00, daily_risk_limit=null) sértetlenségét.
   - Vizsgáld meg a függőben lévő elszámolásokat: nincsenek-e elakadt, túlkoros vagy feloldatlan események (pl. sérülések, walkoverek, elhalasztott mérkőzések).

6. Alkotmányos Védelem, Főkönyv & Védett Fájlok:
   - Ellenőrizd a rendszer 10 Legfőbb Alkotmányos Parancsának (AGENTS.md) és a fejlesztői felhatalmazásnak (GEMINI.md) a sértetlenségét (nem írta-e felül semmilyen elavult korlát).
   - Igazold a megmásíthatatlan főkönyv (immutable ledger), a $0.00 külső adatforrás-költség és a manuális Stake végrehajtási határvonal (auto_assumed_placed, zéró automata API fogadás) maradéktalan érvényesülését.
   - Ellenőrizd, hogy a játékos-propek és élő (in-play) fogadások szigorúan kizárva maradnak-e.

7. Bizonyítási Standard & Jelentés:
   - Futtass releváns egység-, integrációs, kódintegritási (AST globals) és felületi Happy-DOM teszteket a működés igazolására.
   - Minden hibához és hiányossághoz adj pontos helyet, hatást, súlyosságot, gyökérokot és konkrét javítási javaslatot.
   - Külön jelezd, ami nincs bekötve, hibásan működik vagy nem ellenőrizhető; részleges vizsgálatból soha ne állítsd, hogy minden rendben!
```

---

## 💎 Miért időtálló és univerzális ez a megfogalmazás?

1. **Teljes Kvant R&D lefedettség:** Nemcsak a kódot és a meglévő modelleket ellenőrzi, hanem számonkéri a **párhuzamos jelöltmodellek generálását, a versenymotort és a bajnokok diplomáztatását is**.
2. **Kiküszöböli a holttereket:** Figyeli az árnyék inkubátort, a szegmenskulcs-konzisztenciát, a gyorsítótárak ürítését és a tőkevédelmi karantént.
3. **Kőkemény bizonyítási kényszer:** Tiltja a bemondásos minősítést; kötelezővé teszi a futtatott teszteket, a kód-helyeket és az élő adatbázis-bizonyítékokat.
