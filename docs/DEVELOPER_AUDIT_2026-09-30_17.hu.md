# Fejlesztési elakadások auditja — 2026-09-30, kiadás 17

A javítás célja a tényleges fejlesztési előrehaladás: a rendszer észlelje a hibát, azonosítsa a hiányzó bizonyítékot és felelőst, indítsa a szükséges munkát, majd ellenőrizze az eredményt. A működő heartbeat, egy újraindítás, egy sorba állított feladat vagy egy passzív modul telepítése nem bizonyít teljes modell-lefedettséget vagy profitot.

## Megállapított okok

1. A javítható, konkrét Codex-review megállapítások ugyanazt az 1–8 órás visszalépést kapták, mint a forrás- és szolgáltatóhibák. Négy konkrét feladat (D15, D16, D20, F0029) emiatt órákig várt.
2. A kiadó 1368 próbát archivált: 814 `profile_required`, 551 `file_not_allowed`, 3 `acceptance_missing`. Egyik sem jutott tényleges tesztelésig/review-ig. Több profil fájlneve eltért az elkészült modulétól; a három acceptance-hiány közben már bizonyíthatóan megszűnt.
3. A fejlesztői hiányfigyelő nem vette át a 25 kiadási akadályt. A friss folyamatjelzés elfedte a tényleges előrehaladás hiányát.
4. A fejlesztő minden feladathoz több mint 41 ezer engineering-bizonyítékfájlt másolt egy közös zárolás alatt. Egyetlen előkészítés több mint két percig tartott.
5. A fejlesztői környezet kapott piaci leltárat, de a hivatkozott teljes Stake-válaszokat, a meglévő 33 szakaszmodellt és az eredeti döntések ellenőrzött olvasási másolatát nem. Ez valóban rendelkezésre álló adatokat tett látszólag hiányzóvá.

## Javított működés

- A konkrét implementációs/review-hibák rövid, bizonyítékhoz kötött javítási kört kapnak, az eredeti feladat teljes elfogadási feltételeivel. Az eredeti próbák, hibák, futások, késleltetések és határidők megmaradnak; nincs végtelen új wrapper-feladat vagy sikerre átírt hiba.
- Az adatfüggő munka konkrét upstream adatigényt rögzít. A releváns új helyi forrásgeneráció egyszer ébreszti a feladatot; puszta heartbeat, publikációs idő vagy oddsmozgás nem. A szolgáltatóhiba külön késleltetést tart.
- Három független Codex-sáv használható a függőségek megtartásával. Az előkészítés csak az adott feladathoz tartozó hash-ellenőrzött engineering-bizonyítékot másolja.
- Az új exporter teljes Stake-forrásokat, egyező meglévő szakaszmodelleket és eredeti döntés-/outcome-JSON-t ad át. Nincs élő írható adatbázis, fióktitok, kitalált történeti elérhetőség vagy új modell-jóváhagyás. A fájlútvonalak, hash-ek, időpontok és méretkorlátok ellenőrzöttek; hiány/hiba kifejezett.
- A kiadó azonos változatlan strukturális akadályt nem próbál újra ugyanazzal az eljárással. Az akadály eredeti határideje, pontos forrása, felelőse és következő lépése bekerül a fejlesztési felügyeletbe.
- A védett root-policy engedélyezi az új passzív segédmodulok konkrét javaslatának automatikus vizsgálatát. Aktuális production-alap, exact acceptance/fájl/policy hash, teljes teszt, független Codex-review, külön root-átvétel, változatlan eredeti fogadási rekordok és későbbi élő publikáció szükséges. Ez nem módosíthat meglévő nem menedzselt runtime-kódot, konfigurációt vagy modellengedélyt. Dokumentációs JSON csak új, korlátozott, érvényes véges JSON-adat lehet.
- A webes fejlesztési nézet külön mutatja a futást és az előrehaladást: indítható munka, adatfüggés, késleltetés, root-kiadási feladat, felelős és következő lépés. A supervisor a bizonyíthatóan elakadt üres dispatchert javítja; valódi forráshiány miatt nem indít haszontalan restart-hurkot.

## Független ellenőrzés során javított további hibák

A canonical engineering JSON-hash és a nyers fájlhash felcserélését; másik modul sikerével tévesen lezárható installed-source-driftet; a történeti döntés kizárólagos forrásának helyreállását figyelmen kívül hagyó generációt; credentiales forrás-URL metaadatának ellenőrzés előtti másolását; valamint a production/data téves workspace-ként elfogadását külön regresszió ellenőrzi.

## A teljes cél és korábbi parancsok felülvizsgálata

A követelményjegyzék R01–R30 valamennyi pontja továbbra is kötelező. A kiadás16 teljes táblázata (`docs/DEVELOPER_AUDIT_2026-09-30_16.hu.md`) az alap; a jelen javítás R17/R20/R25/R27/R28/R30 előrehaladási, adatátadási és kiadási részét változtatja. A többi követelményt nem jelöli automatikusan teljesítettnek.

Változatlan: független sportmodellekből induló döntés; pontos, friss Stake-kontraktus; csak tiszta konkrét főoldali instrukció; indokolt 1–5 USD, nincs napi összkeret; a rendszer minden eredeti döntést és veszteséget sajátjának tekint; a checklist nem módosítja az elszámolást. A napi minimum 15 külön első kiadás kötelező mérendő cél, nem a hibás input/gyenge jel elfogadásának indoka. Qualified Winner/Handicap/Total központi marad, további piaccsalád csak tényleges adat/modellezés/settlement mellett. Codex az elsődleges fejlesztő, reviewer és felügyelő; a segéd-AI nem írhatja felül a bizonyítékot.

Nyitott képességek: teljes sérülés/lineup-hatás, profi családonkénti teljes minőségi elfogadás, player props és részvétel, specials, joint/SGM, live, további NCAA-képességek. A passzív könyvtármodul és a sikeres teszt önmagában nem aktivál ilyen fogadástípust. Nem állítunk bizonyított nettó profitot, örök hibamentességet vagy minden piac kész támogatását. A fennmaradó munka konkrét feladatként és változatlan határidővel folytatódik.

## Kiadási bizonyíték

Staging: 1325 Python-teszt + 5 alteszt, 54 DOM-teszt. A források több Codex-agent független kereszt-review-ján mentek át. A read-only exportpróba 33 egyező szakaszmodellt, 330 exact forráshivatkozást és 268 fájlt vitt át, 68,46 MB, 1,36 másodperc, nulla forráshiba. A scheduler replay négy konkrét kódjavítást és nyolc releváns forrás-újraértékelést tett indíthatóvá, az eredeti állapotmezők és acceptance megőrzésével; a többi valódi forrás-/providerhibát nem nevezte megoldottnak.

Az éles telepítés, ledger-ellenőrzés, szolgáltatás-újraindítás és tényleges kiadási előrehaladás eredménye a záró mérésben szerepel. Bizonyítékok: `data/flow-recovery-20260930/`. A jelen staging eredmény nem éles készre jelentés.

A végső kiadási előellenőrzés 18 új passzív jelöltet talált, közülük több helyi segédmodultól/tesztmodultól függ. Az AST-alapú előfeltétel-ellenőrzés a hiányzó pontos modulokat és elfogadott előállítójukat rögzíti, előbb az előfeltétel kiadását kéri, és nem futtat újra teljes tesztet változatlan hiányra. Az ugyanazon hívásban újrahasznált forrásnézet 25 handoffot 0,26–0,33 másodperc alatt vizsgált, egyetlen production manifest olvasással; nincs hívások között elavuló cache.


## Éles workflow-eredmény és a 17.1-es követő javítás

A 17 fájlos workflow-javítás 2026-09-30 12:01 CEST-kor települt. Az eredeti 128 ticket, 127 rendszer-döntés és 120 első kiadás immutábilis mezői egyeztek. A developer `enabled=true`; mindhárom sáv implementation fázisba jutott (D18, E5E9AD751B5, E7E4DE07846), valamennyi megkapta a 33 meglévő részmodellt és hiteles nyers forráscsomagot. Az 25 kiadási akadály ténylegesen bekerült az engineering intake-ba. Ez indult munka, nem három kész feladat.

Az E8B9BF9FBBA korábban beragadt modul 12:08:37 CEST-kor tényleges `installed_utility` állapotba jutott: 1345 Python-teszt +5 alteszt, DOM, független Codex-review, pontos forrás-/policy-/ledger-ellenőrzés és frissebb éles publikáció után. A legelső teljes kiadási átadás megvalósult. A következő hiányzó előfeltétel már konkrét dependency-handoffot kapott. A passzív player-model-study modul nem aktivál player-prop fogadást és nem igazol profitot.

A TLS-ellenőrzött Tailscale 2948-as publikációját valódi letöltött HTML/JS-sel futtatott DOM-próba ellenőrizte: 10 végrehajtható jelöletlen utasítás, 3 aktív fejlesztési sáv, nincs Recheck Required/undefined/NaN/JavaScript-hiba vagy audit által kezdeményezett írás.

A további audit két valódi, védett root-integrációra váró forráshibát is lezárt kóddal: ECDB explicit player-ID felülírása donor-névvel, és E698 fixture/endpoint/bundle hibabizonyítékainak elvesztése. Az újabb elfogadott parser szigorítása mellett 13 régi hibás esetet reprodukáltunk. A 108 fixture/216 archivált endpoint visszajátszása 0 megváltozott eredeti szerződést/oddsot/normalizálást mutatott; 1054 leltári sor kapott további forrásprovenance-t. Minden player-prop sor továbbra is `publishable=false`, önálló modellaktiválás nem történt.

A HTTP-üzempróbában 20–35 másodperces várakozások is jelentkeztek. A standalone teljes dashboard-profil 1,77 másodperc, a development status 0,3–0,6 másodperc; önmagukban nem magyarázzák a HTTP-várakozást. Konkrét reprodukált hiba volt, hogy az aszinkron SSE-generátor a fő eseményciklusban végzett szinkron SQLite-lekérdezést. Az új `asyncio.to_thread` hívás külön szálra teszi ezt, és read utáni disconnectet is ellenőriz. A lassú adatbázisos barrier teszt a régi kódon elbukott; az új kód 4 concurrency/cancel/disconnect regressziója és 18 API-tesztje sikeres. A javítás nem állítja bizonyíték nélkül, hogy minden HTTP-késleltetés oka azonos volt.

A 17.1 összevont kód végső teszt- és éles eredménye az alábbi zárásban szerepel; a fogadási valószínűségek, eredeti tétek és publication guard nem módosultak.


## 17.1 záró ellenőrzés és a következő tényleges hibakör

A 17.1-es, 11 fájlos követő kiadás 12:14:02 CEST-kor települt. Az összevont Python/API-sor 1381 teszt +5 alteszt, a DOM-sor 54 teszt volt; független Codex-ellenőrzések is sikeresek. A 2954-es lokális és TLS-ellenőrzött Tailscale publikáció már ezt a szerződésverziót adta. A későbbi 2960-as valódi webes válasz 9 konkrét, jelöletlen, végrehajtható kártyáját ellenőrizte a letöltött produkciós HTML/JS: pontos Stake-link, 1–5 USD, friss szerveres ellenőrzés, nincs JavaScript-hiba vagy ellenőrzésből eredő írás. A 2954-es később visszajátszott, elavult válasz 0 kártyája helyes lejárat volt, nem az aktuális kínálat mérése.

Az új éles katalógus 12:18:13-kor 3781 sor mindegyikén őrzi a fixture/endpoint kontextust. Az eredeti 128 ticket, 127 döntés és 120 első kiadás mezői változatlanok. D04, ECDB59355F3 és E6985CA1F4D csak a külön leírt, hash-ellenőrzött forráskezelési részhez kapott root-átvételt; teljes player-modell vagy profitjóváhagyást nem. D14 12:20:01-kor második automatikus passzív modulkiadásként települt.

A frissen továbbengedett F0023 kiadás ezután tényleges teljes teszteken elbukott: hat eset ugyanarra a hiányzó D06 JSON-bizonyítékra hivatkozott. A scheduler magától újranyitotta a feladatot, de az átadásból hiányzott a pontos release-napló, és ugyanaz a változatlan jelölt ismét lefuthatott volna. Ez új, valódi workflow-hiba; külön 17.2 javítást kap. A másik F0022 kiadás élő publikációs ellenőrzése időtúllépett és a kód visszaállt; ez nem minősül sikeres telepítésnek, a fogadási adatbázist nem állítottuk vissza.

A maradó HTTP-lassulás konkrét terheléssokszorozó részét is reprodukáltuk: a kliens egy már függő adatlekérésre két poll és két SSE-jelzés nyomán összesen öt párhuzamos lekérést indított. A külön SQLite WAL-próba nem mutatott olvasási várakozást; a natív minta GIL-torlódást mutatott. A single-flight javítás és a mérhető, teljes terheléses D22 elfogadás külön kezelendő. A határidő 2026-09-30 16:26 CEST; pusztán egy gyors függvényhívás vagy a klienshiba javítása nem zárja le.


## 17.2 éles lezárás és 17.3 követő javítás

A 18 fájlos 17.2 kiadás 2026-09-30 12:46:36 CEST-kor települt, 1488 Python-teszt +5 alteszt és 64 DOM-teszt után. A pontos release-teszthibák hash-ellenőrzött naplója átkerül a javító fejlesztőhöz; változatlan determinisztikus hibát nem futtat végtelenül újra a kiadó. A kliens egyszerre egy aktív dashboard-kérést és legfeljebb egy követő frissítést enged, 20 másodperces határidővel. Az aktuális próbák szabályos befejezését megvártuk, nem írtuk át a történetüket. Az F0022 passzív modul 12:56:52-kor később sikeresen települt: összesen három korábban elakadt utility jutott át a valódi kiadási ellenőrzésen.

A további éles ellenőrzés két fennmaradó hibát talált. Az újonnan részletesebb régi release-hiba még akkor is kódjavítást indíthatott, amikor ugyanahhoz a feladathoz már újabb, pontosan elfogadott jelölt létezett. Az élő publikációs ellenőrzés átmeneti hibája is feleslegesen a kódoló sorba kerülhetett. A 17.3 javítás csak a bizonyított újabb elfogadásnak megfelelő régi hibafókuszt szünteti meg; az új kiadás ellenőrzése továbbra is kötelező. Az élő ellenőrzést a release coordinator kezeli. A hibás vagy megváltozott elfogadási bizonyíték, illetve egy újabb teszthiba nem kap felmentést.

A HTTP-késleltetés valódi szerveroldali oka is fennmaradt: nagy JSON-objektumok több párhuzamos Python-szálon versenyeztek ugyanazért a GIL-ért. A 17.3 külön, rövid életű, kizárólag olvasó folyamatba helyezi a meglévő dashboard-számítást. Egyszerre egy feldolgozás és legfeljebb 16 várakozó engedett; nincs tartós eredménycache. A child nem indít Engine-t, nem ír adatbázist, és egy SQLite-olvasási snapshotot használ. A fő HTTP-folyamat kész válaszbyte-okat kap. Az új publikáció és a közben nyugtázott checklist/beállítás változás érvényteleníti az előző választ. A már lejáró quote vagy elkezdődő meccs nem maradhat végrehajtható utasítás.

A fejlesztői review több konkrét versenyhelyzetet javíttatott: utolsó olvasó megszakítása közben érkező új kliens, új publikáció generációja, a végső await alatt bekövetkező checklistváltozás/lejárat és a gzip kifejezett tiltása. A három új megjelenítési runtime modul védett root-integrációt igényel. A readonly előpróba nyolc olvasóval 1,187 másodpercet mért, egy gyermekfolyamattal, változatlan ledger- és checklist-fingerprinttel. Ez előpróba; a D22-t csak a két teljes, egyenként 120 másodperces éles terheléses mérés zárhatja le.

A végső 17.3 összevont tesztsor 1595 Python-teszt +5 alteszt és 64 DOM-teszt: mind sikeres. A külön backend-review 124 céltesztet, a routing-review 130 céltesztet, a modulvédelmi review 89 céltesztet és 12 közvetlen tiltási próbát ellenőrzött. Az éles terhelési elfogadás és telepítési bizonyíték külön záró leletben szerepel.


## 17.3 éles eredmény és 17.4 célzott végpontjavítás

A 17.3-as runtime-javítás 13:11:37 CEST-kor települt. Az első éles child továbbra is 12 másodperces határidőbe ütközött: a launchd Background besorolást és PRI4 erőforráskorlátozást örökölte. A HTTP-szolgáltatás Interactive/PRI31 besorolása 13:14-kor megoldotta ezt: lokális 1,305, TLS-es saját Tailscale-címen 1,316 másodperc, friss 2994-es publikáció, 11 tiszta kártya. A többi négy szolgáltatás Background maradt. A telepítő plistjének memóriában futtatott, teljesen elfogott írási tesztje egyezik az éles plisttel; független review sikeres. Ez localhost/saját Tailscale-kiszolgáló mérése, nem távoli kliens WAN-teljesítményének bizonyítéka.

Az első tiszta 120 másodperces terheléses kör minden feltételt teljesített; a másodikban az /api/development p95=2,023375625 másodperc volt, az eredeti 2,0 határ fölött. Mind a 120 kérés HTTP200, mindkét körben három SSE, három aktív Codex-sáv és valódi új publikáció volt. A dashboard/health mindkét kört teljesítette; D22-t ekkor nem zártuk le, a küszöböt nem kerekítettük át és nem lazítottuk. A sikertelen korábbi terhelési bizonyítékok megmaradtak.

A fejlesztési állapot forrásellenőrzése továbbra is a szerver GIL-jén osztozott a publikációval; a külön profil ugyanazon státuszépítés CPU-érzékenységét reprodukálta. A 17.4 meglévő, védett subprocess-modulban külön DevelopmentRenderer példányt és állandó --development módot használ. A gyermek csak a delivery_status eredményét olvassa és kódolja, nem indít Engine-t és nem nyit betting DB-t. Saját 8 másodperces child/10 másodperces route határ, legfeljebb16olvasó, egy aktív feldolgozás és nincs tartós cache. A rendes dashboard-worker parancsa változatlan. A child-megszakítás és reap a már tesztelt közös mechanika; a fejlődési státuszt nem köti fogadási quotehoz vagy a dashboard lekérésének sikeréhez.

Független valós read-only előpróba: nyolc kérés, egy child, 0,3592 másodperc; a válasz azonos az időközben változó két időmező kivételével; eredeti128ticket/127döntés/120első kiadás változatlan. A kiadó aktuális próbáját megvártuk, a három kódoló feladatot nem szakítottuk meg. A teljes új tesztsor és az ismételt, változatlan feltételű éles terhelési mérés eredményét a külön záró audit rögzíti.

A végső összevont kiadási sor1674Python-teszt +5alteszt és64DOM-teszt: mind sikeres, nincs kihagyott teszt. A közben automatikusan települt F0027 segédmodul hat történeti sématesztjéhez11eredeti hash-ellenőrzött forrásfájlt (46,98MB) másoltunk a kizárt tesztkörnyezetbe; hiányzó adat miatt nem fogadtunk el skipet. A korábbi hiányos tesztlog megmaradt. A runtime delta független review-jának84céltesztje sikeres.
