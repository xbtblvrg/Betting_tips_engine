# Kiadás 19.2 — a kutatási és fejlesztési ciklus ellenőrzése

A felhasználó a tényleges háttérmunkáról, annak felügyeletéről és az eredményesség javításáról kért ellenőrzést. A vizsgálat két konkrét, javítandó hibát talált: az izolált fejlesztő és a kiadási tesztkörnyezet nem kapta meg a meglévő regressziós tesztek rögzített forrásait; két NHL-modellkísérlet pedig azonos sportadatokat eltérő dátum- és számformázás miatt külön új adathalmaznak tekintett.

## A javítások határa és bizonyítéka

Az elkülönített, hashhez kötött `validation_sources` csomag a regressziós tesztek eredeti megfigyeléseit őrzi meg. Nem írja felül az aktuális kutatási adatokat, nem változtatja meg a források történeti elérhetőségét, és nem ad modelljóváhagyást. A fejlesztői és kiadási előkészítés egyaránt ellenőrzi a csomagot; hiányzó vagy módosított kötelező forrás esetén a hiba megmarad. Az F0027 és F0025 tesztekben a root által jóváhagyott forrásútvonal javul, az elvárt eredmények és állítások megmaradnak. A hiányzó F0027-csomag többé nem eredményez kihagyott tesztet: ellenőrzési hiba marad.

Az F0025 NHL-lövésadat-egyeztető korábban elfogadott segédmodulja és dokumentációja változatlan bájtokkal kerül az integrációba. Ez passzív adategyeztetés. A játékospiac teljes modellje, részvételi feltételei és elszámolása ettől nem válik kész képességgé.

A tanulási feladatok azonosítója az azonos UTC-időpont és az azonos egész értékű pontszám különböző szöveges alakját egységesíti. A valódi időpont-, eredmény-, statisztika- és dobóazonosító-változás megmarad külön adatgenerációnak. A korábbi tanítási archívumok és döntések változatlanok. Az átállás ligánként egy új, kanonikus azonosítójú kísérletet okozhat; ez önmagában nem javulás.

A reprodukciók, teljes tesztfutások, független review, pontos telepített hash-ek és éles ellenőrzések a `data/validation-unblock-20260930/` könyvtárban találhatók. A végső állapotot a `root-task.json`, `install-journal.json` és `live-verification.json` bizonyítja; a dokumentum vagy egy sikeres részteszt önmagában nem telepítési bizonyíték. Eredeti határidő: **2026-09-30 16:45 Europe/Budapest**. Az összevont izolált futás **2488 Python-tesztet és 5 altesztet, valamint 66 DOM-tesztet** teljesített, hiba és kihagyás nélkül. Mindkét valódi előkészítési útvonalon külön 122 archívumhoz kötött teszt sikeres; a független natív sandbox-próba a csomag módosítását megtiltotta.

## Mi zajlik ténylegesen a háttérben?

Az ellenőrzött rendszer külön piaci, tanulási, izolált fejlesztői és kiadási szolgáltatásból áll. Három fejlesztősáv működik kizárólagos feladatfoglalással. Az ellenőrzés idején a záróár-összehasonlítás, a liga/piaccsalád szerinti előnymérés és a szakmai modellvalidáció dolgozott; a közelmúltban a kombinált Stake-quote kezelése és a dashboard terhelhetősége is futott. A pontos aktuális feladatokat, futásazonosítókat és fázisokat a megőrzött élő pillanatkép mutatja. A sorba tett vagy elkészült jelölt nem jelent éles képességet.

A külső felügyelő névlegesen 60 másodpercenként ellenőriz, három egymást követő hibás állapot után újraindíthat, 15 perces ismétlési korláttal. A fejlesztési fázisoknak külön időkorlátjuk van; a lejárt vagy megszakadt munkák és a sikertelen próbálkozások megmaradnak. Ez működő helyreállítási mechanizmus, nem annak bizonyítéka, hogy elakadás soha nem történhet. A gép alvása és hosszú műveletek az ellenőrzések tényleges ütemét befolyásolhatják.

## A modelljavulás mért állapota

A csak olvasó, hashhez kötött `model-research-audit.json` elkülöníti a korábbi jóváhagyott frissítéseket, az elutasított kísérleteket és a profitbizonyítékot. Korábban NBA- és WNBA-receptváltás, MLB-újratanítás, valamint WNBA Q3/Q4 szakaszmodell-frissítés kapott jóváhagyást. Ez nem jelenti minden piac teljes validációját vagy nyereségességét; az aktuális publikációs minőségellenőrzések továbbra is érvényesek.

A legutóbbi NHL-kísérlet 754 párosított tesztmeccsen, 23 hétből nem igazolt megbízható javulást: a Brier-különbség 95%-os intervalluma körülbelül −0,000789 és +0,000310, tehát átfedi a nullát. Nem történt modellcsere. A két ismételt NHL-futás 759 megőrzött becslése és célváltozója azonos volt; az ismétlést az adatábrázolás eltérése váltotta ki.

A szakirodalmi katalógus 121 publikációt tartalmazott 12 témakörben. A dokumentált olvasási kör minden esetben cím és absztrakt; a katalógus nem 121 beépített módszer. Az eredeti pénzes döntések lezárt részének nyilvántartott, tétek utáni P&L-je az audit pillanatképében **−8,3478 USD**, 85 lezárt döntésen. Ez feltételezett vagy felhasználó által jelentett manuális végrehajtás, nem fogadási számlával egyeztetett eredmény. A működési költségek külön maradnak; igazolt profit nem állítható.

## A teljes parancs újbóli ellenőrzése

Az R01–R33 követelmények release19 és19.1 auditjának állapotai fennmaradnak, az alábbi kapcsolatokkal:

| Követelmény | E kiadás hatása és fennmaradó munka |
|---|---|
| R01–R04, R12, R16, R21–R24 | A modell- és pontos Stake-szerződéskövetelmény változatlan. Teljes játékos-, különleges-, kombinált- és élőpiaci támogatás továbbra is részleges; a piackatalógus nem kész modell. |
| R05, R13, R29 | A napi minimum15, a több meccs elsőbbsége és az indokolt további meccsfogadások megmaradnak. A reset utáni hiányt EB4AF39D5B6 és a modell/előnymérési feladatok kezelik; a legfrissebb számot az élő bizonyíték adja. |
| R06–R10, R18–R19 | 1–5 USD, nincs napi összplafon; eredeti fogadások, pontos quote, EvidenceGuard és manuális végrehajtás változatlan. A mai számlálót ez a kiadás nem reseteli újra. |
| R11, R14–R15 | Azonos sportadatok formázása nem indít felesleges új modellkeresést. A tényleges eredményességet változatlanul külön kell mérni. |
| R17, R20, R25, R27–R28, R33 | Két reprodukált hibához rögzített root-feladat, változatlan határidő, izolált javítás és független ellenőrzés tartozik. A tesztforráscsomag fejlesztési és kiadási átadását is ellenőrizni kell. |
| R26 | Codex marad a vezető és a végső felügyelő; a segédszolgáltatások nem kapnak publikációs vagy kiadási jogot. |
| R30 | Ez a teljes követelménykapcsolat és a pontos verziójú, visszaellenőrizhető kiadási bizonyíték. |
| R31–R32 | A záróár-, eredmény- és árnyékmérés folytatódik. A korábban helyreállított NHL-elszámolások megmaradnak. A teljes mérhető előny és profit még nem bizonyított. |

A teljes engine továbbra sem jelenthető késznek: a napi kiadási hiány, a nem támogatott piacok, az egyes modellek minőségi problémái és a nyitott határidős feladatok külön megmaradnak. A jelen javítás célja a bizonyított fejlesztési akadályok megszüntetése és a felesleges ismétlés csökkentése; nem a készültség vagy a nyereség állításának átnevezése.
