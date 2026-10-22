# Univerzális Rendszeraudit és Zárt Működési Lánc Mester Utasítás (Universal Supreme Audit Prompt)

Ez a dokumentum tartalmazza a rendszer **időtálló, univerzális Mester Utasítását**. Nem tartalmaz múlandó konkrétumokat (fájlneveket, fix darabszámokat vagy ideiglenes limiteket), így a rendszer bármely jövőbeli fejlődési fázisában, refaktorálás vagy új modulok bevezetése után is **100%-ban érvényes és kötelező erejű marad**.

---

## 📋 Az Univerzális Mester Utasítás (Copy-Paste Master Prompt)

```text
Végezz teljes rendszerauditot és mélyreható kvant felülvizsgálatot a legfrissebb élő állapot alapján! Keresd meg a logikai hibákat, inkonzisztenciákat, anomáliákat, regressziókat és elakadt folyamatokat a teljes rendszerben. Ellenőrizd a követelmények, a kód, az adatok, a modellek, az ajánlások, az elszámolás és a felület összhangját, valamint a fejlesztések tényleges telepítési állapotát.

1. Démonok, Háttérfolyamatok & Hibanaplók:
   - Térképezd fel és ellenőrizd az összes aktív háttérszolgáltatást, démont és ütemezett feladatot: igazolva, hogy a legfrissebb kód fut-e a memóriában, ellenőrizve az erőforrás-használatot és a szolgáltatások egészségét.
   - Pásztázd át a rendszer összes hibanaplóját és felügyeleti jelentését: keress friss kivételeket, zárolási/I-O problémákat, hálózati elakadásokat és elnyelt hibákat.

2. A Teljes Zárt Lánc Végigkövetése (Adat → Modell → Ajánlás → Főkönyv → Elszámolás → Tanulás → Validálás → Visszacsatolás):
   - Kövess végig konkrét ajánlásokat a nyers forrástól a végső elszámolásig és tanulási visszacsatolásig.
   - Ellenőrizd a teljes rendszer bekötését és működési hurkait: minden modul, fejlesztés és háttérfolyamat ténylegesen meghívódik-e, megfelelő triggerrel és ütemezéssel fut-e, megkapja-e a szükséges bemeneteket, és az eredményét felhasználja-e a következő lépés.
   - Keress bekötetlen kódot, megszakadt visszacsatolást, beragadt vagy duplikált ciklust, hibás aszinkronitást, retry-csapdát és újraindítás után elvesző feladatot/állapotot.

3. Multi-AI Szinergia & Kutatási Eredmények Felülvizsgálata:
   - Térképezd fel és tekintsd át az összes háttérben futó AI modell, kvant tanács és kutatási motor legfrissebb termékét (hipotézisek, azonosított kudarcmódok, mechanikai meglátások, képességbővítési tervek, szűrők és pajzsok).
   - Ellenőrizd, hogy a többi AI által javasolt védelmi szabályok, modellek és szűrők ténylegesen érvényesülnek-e a döntési motorban, vagy beépítetlenül elakadtak.

4. Piacok, Tőkevédelem & Karantén Integritás:
   - Ellenőrizd az összes piac és szegmens teljesítményét: a leromló, negatív szegmensek szigorúan el vannak-e zárva a valós tőkétől ($0.00 virtuális karantén / inkubáció), amíg újra nem bizonyítanak a szigorú validációs kapukon (zéró tőkeszivárgás).
   - Igazold a rendszer alapvető pénzügyi céljainak, dinamikus időhorizontjának és minimális volumenszabályainak betartását.
   - Vizsgáld meg a függőben lévő elszámolásokat: nincsenek-e elakadt, túlkoros vagy feloldatlan események (pl. sérülések, walkoverek, elhalasztott mérkőzések).

5. Alkotmányos Védelem, Főkönyv & Védett Fájlok:
   - Ellenőrizd a rendszer alapvető alkotmányos szabályainak, direktíváinak és a fejlesztői felhatalmazást rögzítő irányelveknek a sértetlenségét és védelmét (nem írta-e felül semmi).
   - Igazold a megmásíthatatlan főkönyv és a biztonsági végrehajtási határok maradéktalan érvényesülését.

6. Bizonyítási Standard & Jelentés:
   - Futtass releváns egység-, integrációs és felületi teszteket a működés igazolására.
   - Minden hibához és hiányossághoz adj pontos helyet, hatást, súlyosságot, gyökérokot és konkrét javítási javaslatot.
   - Külön jelezd, ami nincs bekötve, hibásan működik vagy nem ellenőrizhető; részleges vizsgálatból ne állítsd, hogy minden rendben!
```

---

## 💎 Miért időtálló és univerzális ez a megfogalmazás?

1. **Nem köti gúzsba a nevek változása:** Nem hivatkozik konkrét elérési utakra vagy fájlnevekre, hanem a **funkciókat és szerepköröket** kéri számon (pl. "összes hibanapló", "összes AI kutatási termék", "minden háttérszolgáltatás").
2. **Kiküszöböli a holttereket:** Bármennyi új sportág, modell, AI forrás vagy démon jelenik meg a rendszerben, az auditnak kötelezően fel kell derítenie és át kell vizsgálnia mindet.
3. **Elvárás-szintű szigor:** A zárt lánc és a bizonyítási kötelezettség változatlanul kőkemény: a modell nem mondhatja bemondásra, hogy "minden rendben", hanem köteles kóddal, teszttel és élő futási adatokkal bizonyítani az állításait.
