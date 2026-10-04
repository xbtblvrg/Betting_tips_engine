# AI Sports Betting Quant Engine — tartós tudástár

A tudástár saját szavas, forráskritikai olvasási jegyzeteket, képleteket, feltételezéseket, ellenpéldákat és ellenőrzött javításokat őriz. Ezek fájlokban maradnak meg, későbbi munkamenetben is visszakereshetők. Ez külső projektmemória, nem a nyelvi modell súlyainak újratanítása.

**A helyi könyvanyag feldolgozva.** Mind a 26 könyv-/tanulmányfájl rendelkezésre álló tartalma feldolgozott vagy ellenőrzött duplikátummal lefedett. Ez részleteket és kísérőanyagokat is tartalmaz, nem 26 különálló teljes könyv. A két online forráskatalógus helyi tartalma szintén elolvasva. A könyvek közötti szintézis és 12 témakártya elkészült; az új rendszerterv az online kiegészítést követi. A külső kutatás megkezdődött: az R04 NHL-tanulmány teljes feldolgozása elkészült, a többi külső forrás feldolgozása még nincs lezárva.

- [Közös tudásszintézis és a 12 témakártya](SZINTEZIS.hu.md)
- [R04 — NHL lövő- és kapusképességgel korrigált xG](online/R04-notes.hu.md)
- [Külső tanulmányok tényleges olvasási naplója](online/reading_ledger.jsonl)
- [Aktuális projektállapot](../docs/STATUS.hu.md)
- [Aktuális készültség és felügyelt fejlesztés — napi legalább 15 pénzes ajánlás követelménye](../docs/READINESS.hu.md)
- [Forrásjegyzék, fájlazonosítók és SHA-256](reading_v2/inventory.json) — kinyerési állapot, nem olvasási bizonyíték
- [Tényleges olvasási napló](reading_v2/reading_ledger.jsonl) — időrendben hozzáfűzött bejegyzések; forrásonként az utolsó állapot az érvényes
- [Feldolgozási protokoll és tudáskártya-sablon](READING_PROTOCOL.hu.md)

## Keresés

A `search_knowledge.py` a saját jegyzeteket és tudáskártyákat indexeli helyi SQLite-adatbázisba. A találathoz a jegyzet fájlja, sorszáma, fejezetcíme és olvasási státusza tartozik. A könyv oldalszámát a hivatkozott jegyzet tartalmazza; a fájlsorszám nem könyvoldal. Változás után az index automatikusan frissül. Nem küld könyvszöveget külső szolgáltatónak.

```sh
cd /Users/belavarga/Betting_tips_engine
.venv/bin/python knowledge/search_knowledge.py "Kelly"
.venv/bin/python knowledge/search_knowledge.py "kalibr"
.venv/bin/python knowledge/search_knowledge.py "Poisson"
.venv/bin/python knowledge/search_knowledge.py "lineup"
.venv/bin/python knowledge/search_knowledge.py --rebuild
```

Több keresőszó együtt szerepeljen a szakaszban; a rövid szótövek is működnek. Ez szöveges keresés, nem jelentésalapú kereső. A találatot a teljes fejezet és az auditjavítások összefüggésében kell használni.

## Tematikus belépőpontok a már feldolgozott anyaghoz

| Téma | Olvasási jegyzetek | Mit lehet visszakeresni? |
|---|---|---|
| Valószínűség, fair ár, EV | [B04](reading_v2/B04-notes.hu.md), [B09](reading_v2/B09-notes.hu.md), [B18](reading_v2/B18-notes.hu.md) | Oddsátváltás, margó, kifizetés, eloszlások, feltételezések |
| Saját sportmodell és rating | [B08](reading_v2/B08-notes.hu.md), [B21](reading_v2/B21-notes.hu.md), [B02 teljes mű](reading_v2/B02-notes.hu.md), [B07 részlet](reading_v2/B07-notes.hu.md) | Ellenfélerő, Bradley–Terry, Elo, csapat- és játékosérték |
| Baseball / MLB | [B03](reading_v2/B03-notes.hu.md), [B08](reading_v2/B08-notes.hu.md), [B24](reading_v2/B24-notes.hu.md) | RE24, WPA, dobómodell, parkhatás, lineup, total |
| Kosárlabda / NBA | [B08](reading_v2/B08-notes.hu.md), [B09](reading_v2/B09-notes.hu.md), [B21](reading_v2/B21-notes.hu.md) | Tempó, four factors, plus-minus, lineup, ponteloszlás |
| NFL | [B06](reading_v2/B06-notes.hu.md), [B02 teljes mű](reading_v2/B02-notes.hu.md), [B07 részlet](reading_v2/B07-notes.hu.md), [B08](reading_v2/B08-notes.hu.md), [B18](reading_v2/B18-notes.hu.md) | Play-by-play, EPA, rating, sérülés, key numbers, push |
| NHL | [B08](reading_v2/B08-notes.hu.md), [B21](reading_v2/B21-notes.hu.md) | Lövésminőség, állapotfüggés, bootstrap korlátai |
| WNBA és friss külső kutatás | [B27/B28 katalógusjegyzet](reading_v2/B27-B28-notes.hu.md), [R04 NHL-kutatás](online/R04-notes.hu.md) | R04 teljesen feldolgozva; a WNBA-cikk és a többi külső forrás még nincs lezárva |
| Kalibráció, tesztelés, szerencse | [B09](reading_v2/B09-notes.hu.md), [B21](reading_v2/B21-notes.hu.md), [B23](reading_v2/B23-notes.hu.md) | Brier, logloss, időbeli teszt, szelekció, többszörös próbálkozás |
| Bankroll, korreláció, szimuláció | [B09](reading_v2/B09-notes.hu.md), [B18](reading_v2/B18-notes.hu.md), [B24](reading_v2/B24-notes.hu.md) | Kelly célfüggvény, drawdown, csőd, függő fogadások |
| Bookmaker, piac és végrehajtás | [B15](reading_v2/B15-notes.hu.md), [B19](reading_v2/B19-notes.hu.md), [B26](reading_v2/B26-notes.hu.md) | Ár és vonal, elszámolás, likviditás, jutalék, végrehajthatóság |
| Adatkezelés és AI-eszközök | [B06](reading_v2/B06-notes.hu.md), [B11 kísérőanyag](reading_v2/B11-notes.hu.md) | Időbélyeg, tisztítás, visszakeresés, eszközhívás, séma |
| Forráskritika és hibajegyzék | [B09 audit](reading_v2/B09-notes.hu.md), [B21 képlethibák](reading_v2/B21-notes.hu.md), [B16](reading_v2/B16-notes.hu.md) | Szerzői állítás, matematikai eredmény és saját következtetés szétválasztása |

A [B13 Soccermatics jegyzet](reading_v2/B13-notes.hu.md) a saját sportmodelleket, a térbeli és passzhálózati modelleket, a valószínűségi kalibrációt és a stratégiaválasztás torzításait kapcsolja össze. Teljes szöveg és 141 kép feldolgozva; [reprodukálható számítások](verify_b13.py).

A [B14 Sports Betting For Dummies jegyzet](reading_v2/B14-notes.hu.md) mind a 36 egységet és 46 képet lefedi. Részletes NFL/NBA/MLB alapelvek, döntési torzítások, odds- és eredménynaplózási hibák; [reprodukálható audit](verify_b14.py).

A [B25 Handbook of Sports and Lottery Markets jegyzet](reading_v2/B25-notes.hu.md) mind a 24 fejezetet, 561 oldal teljes szövegét és 268 oldal vizuális ellenőrzését foglalja össze. Piaci hatékonyság, függő fogadások, index- és előrejelzési piacok, Kelly-portfólió, kereslet, kombinatorika; [reprodukálható számszaki audit](verify_b25.py).

## A tudás felhasználásának szabálya

Egy könyvállítás nem válik automatikusan engine-szabállyá. Megőrizzük a feltevéseit, alkalmazási tartományát, az ellenőrzés módját és a cáfoló példákat. A sportadatokból származó előrejelzés és a piaci árral végzett későbbi érték-összevetés elkülönítése a felhasználó követelménye; a könyvek piaci benchmarkjai ezt nem írják felül.

A napi nyereség nem igazolt következménye a könyvek elolvasásának. A tartós tudástár célja, hogy a későbbi modell és minden módosítása visszakövethető, tesztelhető állításokra épüljön.
