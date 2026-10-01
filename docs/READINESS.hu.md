# Teljes készültség és felügyelt fejlesztés

Állapotfelvétel: 2026-09-29. Az aktuális szerződés a `config/project_contract.json`, az ütemezett munka a `config/development_plan.json`. Ez a dokumentum váltja fel a korábbi, napi tíz jelöltet tartalmazó menetrendet. Az eredeti terv történeti dokumentum marad.

## Kötelező működési célok

- NBA, WNBA, NHL, MLB, NFL és a Stake USA-kínálatában ténylegesen elérhető NCAA-sportok. Az MLS jelenleg további megfigyelt kínálat; nem helyettesíti a kért ligák támogatását.
- Saját, független sportadatokra épülő előrejelzés; ezt követi a pontos Stake-szerződés és ár vizsgálata. Winner, Total, Handicap, szakaszok, játékospiacok és minden további ligaspecifikus család teljes támogatása még nyitott követelmény.
- Mai és következő 48 órás ajánlások, folyamatos frissítéssel. Napi legalább 15 külön pénzes első ajánlás Budapest naptári napja szerint, felső darabszámkorlát nélkül. A hiány teljesítetlen követelmény; nem zárható le kutatási sorokkal, duplikátummal, átárazással vagy kitalált előnnyel.
- Ajánlásonként 1–5 USD; nincs rögzített összesített napi tétkeret. Az 5 USD plafont csak új felhasználói utasítás emelheti. Meglévő meccsre új, külön, megfelelően értékelt szerződés kiadható.
- Minden kiadott ajánlás a rendszer saját, megőrzött döntése. A fogadásleadás manuális; a felhasználó állandó utasítása alapján feltételezett megtétellel követjük. Az elfogadott odds és a publikált odds külön bizonyíték. A checklist nem változtatja meg az elszámolást.
- Angol felület, Stake-nevek, konkrét vonal és kimenetel, eseménylink és megfigyelt tab, időrendi sorrend, eszközök között megmaradó jelölés, Tailscale-hozzáférés.
- Tétek utáni fogadási profitra optimalizálás, minden veszteség megőrzésével. Az opcionális külső működési költség külön kimutatás; keretéről a felhasználó dönt, hiánya nem blokkolja a modellmunkát. Napi veszteségmentességet vagy minden kiírt meccsen pozitív előnyt nem lehet garantálni. A rendszer ezt nem minősítheti teljesített tulajdonságnak.

## Ami már működik

A hivatalos Stake API teljes válaszainak archiválása, NCAA-felderítés, nyolc ligamodell, két MLB-inningmodell, 31 szakaszmodell, pontos szerződésellenőrzés, automatikus döntésnapló és eredménykövetés, külön újratanítási munkás, újrapróbálkozás és folyamat-/komponensfelügyelet. A tényleges családszintű jóváhagyás szűkebb a modellek darabszámánál.

A 2026-09-29 19:07 CEST ellenőrzéskor 44/15 aznapi külön pénzes ajánlás és 962 ellenőrzött kimenetel szerepelt. Az öt lezárt saját döntés egyetlen külön meccsből származik, tétek utáni eredményük +6,08 USD. Ez pillanatfelvétel, nem folyamatos teljesítés vagy tartós profit bizonyítéka. A 19:43-as következő kiadásban a napi számláló 45/15, D02/D17 árértékelése éles, D18 modellvizsgálata fut. [Legutóbbi kiadás](RELEASE_2026-09-29_13.hu.md), [részletes audit](AUDIT_2026-09-29.hu.md).

## Ami a teljes készültséghez hátravan

| Ütem | Következő ellenőrzés | Konkrét teljesítési feltétel |
|---|---|---|
| M01 – felügyelet és fejlesztési bizonyíték | indítástól 24 óra | Tartós feladatok, megszakítás utáni helyreállítás, tesztek, külön kódreview, hiba- és csúszásjelzés, ellenőrzött üzembe helyezési bizonyíték. |
| M02 – sportadatok és szerepek | 48 óra | Valós ismertségi idővel kezelt adatok; aktuális sérülés, lineup, várható perc/snap, QB, kapus, dobó/bullpen és egyéb releváns kontextus. Forráshiba és ismeretlen állapot elkülönítve. |
| M03 – player props | 72 óra | Ligánkénti modell, időrendi teszt, részvételi feltétel, pontos Stake-azonosítás, DNP/void/push és statkorrekció elszámolása. Játékosnapló önmagában nem kész prop-modell. |
| M04 – hiányzó piacok | 96 óra | Önálló célváltozóval modellezett és tesztelt szakasz-, eseménysorrendi, speciális és Asian quarter-line piacok; további felfedezett NCAA-sportok saját modellje. |
| M05 – gazdasági értékelés | 48 óra, utána folyamatos mérés | Végrehajtható historikus árak, fagyasztott döntések, pontos záróár-összevetés, tétek utáni fogadási eredmény és külön opcionális működési költségek, azonos meccshez kapcsolódó fogadások függősége, hozam/bizonytalanság/drawdown. Élő bizonyíték szükséges; öt lezárt döntés elégtelen. |
| M06 – kombinációk és in-play | 168 óra | SGM valós kombinált Stake-árral és közös kimeneteleloszlással; live külön állapotfüggő modellel és mért késéssel. Egyes oddsok összeszorzása nem SGM-ajánlat. |
| M07 – folyamatos üzem és kiadás | 72 óra | Új szoftver izolált kiadása, teljes regresszió, shadow ellenőrzés, eredeti szelvények megőrzése, rollback és mentés-visszaállítási próba. Mindig elérhető futtatási környezet és ellenőrzött helyreállítás. |
| M08 – teljes követelményátvétel | 14 napos felülvizsgálat | Minden követelményhez kód, teszt és friss üzemi bizonyíték; minden felfedezett, kért piactípus valóban modellezett és elszámolható; napi darabszám és minden hiány visszakereshető; gazdasági eredmény megfelelő bizonyítékkal. |

Az időpontok kötelező **felülvizsgálati időpontok**, nem garantált befejezési vagy profitdátumok. Pontos időpontjuk az API-ban és a weboldalon látszik. Lejáratkor a nyitott tétel látható csúszás, és tartós eseménynapló készül; a dátumot a munkás nem tolja ki automatikusan. Az adat- és modellvalidáció időigénye a tényleges bizonyítéktól függ.

További fontos részletek: a tétképzés jelenleg korlátos heurisztika, nem validált teljes portfólióoptimalizáló. A szakirodalmi tudástár megvan, de az új módszerekhez teljes releváns szöveg, reprodukált kísérlet és kód-/tesztkapcsolat szükséges; a napi cím/absztrakt-keresés ezt nem helyettesíti. A Stake-gomb jelenleg eseményre navigál; a pontos választást előre betöltő linket külön kell igazolni, ha a platform támogatja. A Telegram a felhasználó kérésére későbbi szakasz.

## A most bekötött fejlesztési folyamat

Külön launchd-szolgáltatás futtatja a meglévő hitelesítésű Codex CLI-t, `gpt-6-astra` modellel. Legfeljebb három független feladat dolgozik párhuzamosan, külön munkafában, tartós kizárólagos foglalással és függőségi sorrenddel. A jelöltek integrációja soros, ütközés esetén új alapra kerül a feladat. Nem ütköző egyesítés után a teljes közös Python/DOM-regresszió és külön Codex-review ismét kötelező. A journalból és hashhez kötött elfogadási bizonyítékból a megszakadt integráció állapota helyreállítható.

A scheduler két másodperces ciklusa feltölti az elérhető sávokat; a teljes engineering intake percenként újraértékeli a hiányokat. Egy elutasított feladat saját várakozása nem állítja le a többi munkát. CLI/auth/infrastruktúra-hiba után a védett globális backoff megmarad. A megvalósítás célkorlátja 25 perc, a Python-tesztek 10 perc, a DOM-tesztek 3 perc, a külön review 10 perc; az integrációs várakozás külön, heartbeat mellett mért, legfeljebb egyórás lépés. A supervisor minden sáv aktuális lépésének idejét és friss heartbeatjét ellenőrzi.

Codex az elsődleges fejlesztő és felügyelő. Claude Opus5.5 (`claude-opus-5-5`) másodlagos, Google `agy` és Grok harmadlagos, korlátozott tanácsadó. A pontos segédválasz hashéhez Codex-értékelés tartozik; a segéd nem módosít éles kódot, modellt, tétet vagy naplót. Sikertelen vagy nem archiválható tanács esetén Codex folytatja. A szolgáltatónkénti párhuzamkorlát és ritkítás csökkenti a segéd-előfizetések terhelését.

A 19 alapfeladat mellett a tartós engineering intake korlátozott új integrációs feladatot is létrehozhat. A meglévő határidő nem csúszik újrapróbálkozáskor; kutatási jelentés vagy eltűnt meccs nem bizonyít megoldást. Az összes korábbi kérést átvevő [fejlesztői audit](DEVELOPER_AUDIT_2026-09-29_14.hu.md) külön minősíti az R01–R30 követelményeket.

A munkás másolt forrásfában és másolt, nyilvános sportadatokon dolgozik. A teljes regressziós tesztcsomagot a vezérlő futtatja, külön operációs rendszerbeli sandboxban. A meglévő teszteket, a szerződést, a guardot, a tétképzést és a döntésnaplót a jelölt nem írhatja át. Független, csak olvasó AI-review követi a teszteket. Futás közben megváltozott éles forrás, sikertelen teszt/review vagy megszakítás esetén a feladat nyitott marad. Az implementáció és a hiba bizonyítéka megmarad. Az egymásra épülő, elfogadott jelöltek külön munkafában gyűlnek. A nem ütköző éles kódfrissítések automatikusan átkerülnek a munkapéldányba, új teszteléssel; ütközéskor mindkét változat megmarad, és az AI a másolaton kap javítási feladatot. Lejárt ellenőrzési idejű munka előrébb kerül. Az induló feladatok befejezése után a nyitott mérföldkövekhez további implementációs lépéseket rendel a munkás; ettől a mérföldkő még nem lesz kész.

**Az automatikus éles szoftvertelepítés még nincs bekapcsolva.** A `candidate_ready` tesztelt fejlesztési jelöltet jelent, nem kiadott piacmodellt. A működő termelési motor közben folytatja a frissítést és a meglévő kontrollált újratanítást. A későbbi éles aktiváláshoz forráshashhez kötött kiadási ellenőrzés, gazdasági/modellkapu és üzemi próba kell. Ez az M07/M08 nyitott része, nem elhallgatott kézi függőség.

Helyi működési függőség: a Macnek, a felhasználói munkamenetnek és a hálózatnak elérhetőnek kell lennie. A fő szolgáltatás hálózati tápon ébren tartási állítást használ; ez nem független szerveres 24/7 rendelkezésre állás. A fejlesztési munkás a meglévő Codex-hozzáférés kvótáját használja; kvótahiba naplózott sikertelen kísérlet, nem elvégzett feladat.

Technikai alap: [OpenAI Docs – nem interaktív Codex](https://learn.chatgpt.com/docs/non-interactive-mode), [jogosultsági profilok](https://learn.chatgpt.com/docs/permissions). A gépen elérhető CLI-t és a sandbox tényleges írási/hálózati korlátozását külön ellenőrizzük.

## Hol ellenőrizhető?

- Web: **Model Performance → Supervised Development and Delivery**.
- API: `/api/development`; folyamatállapot: `/api/automation`.
- Feladatok és kísérletek: `data/development/state.json`, `data/development/runs/`.
- Élő állapot: `data/development/worker.json`, felügyelet: `data/supervisor.json`.
- Elmaradt ellenőrzések: `data/development/checkpoint-events.jsonl`.
- Eredeti, működő kód: `src/`; elfogadott, még nem éles jelöltek: `data/development/accepted/`.

A „kész” állapothoz nem elég az, hogy a szolgáltatások élnek, az API válaszol, a fájlok léteznek vagy egy AI azt írja, elkészült. Követelményenként ellenőrzött működés és megőrzött bizonyíték szükséges. A fogadási eredmény külön, később megfigyelt, eredeti tétekből és kifizetésekből számolt állítás. A működési költség ismeretlen összege nem jelent nulla költséget, de nem feltétele a fogadási siker mérésének.
