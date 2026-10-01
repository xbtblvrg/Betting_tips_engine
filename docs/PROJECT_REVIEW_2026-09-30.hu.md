# Projektellenőrzés — 2026-09-30

**Eredmény:** a szolgáltatás működik és a teljes regressziós tesztcsomag sikeres, de hat reprodukálható hiba javítást igényel. Három közvetlenül érinti a fogadási utasítás vagy a telepített modell ellenőrzését. Ez audit, nem javítás vagy új kiadás.

Az ellenőrzés a projekt követelményeire, a publikációra, a felületre, a döntés- és fogadásnaplóra, a modellezésre, valamint az élő működésre terjedt ki. Két külön Codex-részellenőrzés külön ideiglenes munkapéldányon vizsgálta a publikációt és a modelleket. A fő ellenőrzés külön megismételte a három API/napló reprodukciót, és saját DOM-reprodukcióval igazolta a részletablak hibáját. Nem teljes körű biztonsági vagy minden forrássorra kiterjedő audit.

Termelési kódot, konfigurációt, modellt és fogadási rekordot nem módosítottunk. Élő írási API-hívást, frissítést, tanítást, újraindítást vagy kiadást nem indítottunk. A háttérszolgáltatások a saját ütemükben tovább működtek. A forrás- és konfigurációhash-ek az ellenőrzés alatt változatlanok maradtak. Csak ez a jelentés és a hozzá tartozó auditbizonyítékok kerültek a projektbe.

## Bizonyított hibák, javítási sorrendben

### F01 · P1 · A dashboard naplózatlan tétes utasítást készíthet

Hely: [server.py:123](/Users/belavarga/Betting_tips_engine/src/betting_engine/server.py:123), a `dashboard()` újrarangsorolási és méretezési ága.

A GET kérés a legutóbbi publikáció jelöltjeinek új tétet számít az aktuális beállításokkal. Reprodukció: egy EvidenceGuarddal publikált, nulla tétű jelölt után a minimum ROI 0,30-ról 0,03-ra változik. A dashboard **2,64 USD** új tétet mutat, miközben az adatbázisban az eredeti tét nulla, a döntések, jegyek és napi első kiadások száma egyaránt nulla. A felület ezt új ajánlásként megjeleníti. A `decision_guard.passed=true` megmarad, de a tényleges pecsétellenőrzés hibát ad: `Publication blocked: candidate changed after evidence review`.

Ez valós beállításmódosítás után, a következő sikeres publikáció előtt jelentkezhet. A GET frissességet és megjelenítési jogosultságot ellenőrizhet, új finanszírozást viszont csak a lezárt ellenőrzéssel és naplózással közös publikációs tranzakció hozhat létre. Javítási átvétel: ugyanebben a reprodukcióban nem jelenhet meg végrehajtható új tét addig, amíg a döntés, a napi kiadás és az előírt feltételezett fogadás nincs tartósan rögzítve.

### F02 · P1 · A nyitott részletablak elavult fogadási utasítást őriz

Hely: [app.js:243](/Users/belavarga/Betting_tips_engine/src/betting_engine/web/app.js:243) és a [showDetail függvény](/Users/belavarga/Betting_tips_engine/src/betting_engine/web/app.js:255).

A részletablak tartalma csak megnyitáskor készül el. Az új dashboard betöltése frissíti a kártyákat, de nem frissíti a nyitott ablak végrehajtási utasítását. DOM-reprodukcióban az ajánlat aktuális ellenőrzése `actionable=false` lett, a fő kártya helyesen `Recheck Required` állapotba váltott, a nyitott ablak azonban továbbra is **„Issued instruction: bet $1.00 once”** utasítást mutatott. Az új elutasítás oka nem jelent meg.

A részletablak végrehajtható részeit minden frissítéskor, árlejáratkor és meccskezdéskor újra kell értékelni. A már beírt naplózási adatokat közben meg kell őrizni. Átvétel: nyitott ablak mellett romló odds, lejáró quote és elkezdődő meccs esetén se maradjon aktív fogadási utasítás.

### F03 · P1 · Az alapértelmezett tanítás megkerüli a modellcsere kapuját

Hely: [train.py:47](/Users/belavarga/Betting_tips_engine/src/betting_engine/train.py:47), a 47–55. sor, valamint a stabilitásvizsgálat 60. sortól.

A `train()` alapértelmezése `controlled=False`. Ilyenkor a már létező modell nincs betöltve összehasonlításra, és a jelölt automatikus csereengedélyt kap. Ez a parancssori belépési pontot is érinti `--controlled` nélkül; a README telepítési példája is ilyen parancsot mutat.

Izolált, mesterséges fitet használó reprodukcióban a jelölt margin/total MAE-je 100 lett a bajnok 1-es értékei helyett, mégis települt; a `promotion_gate` egyszer sem futott le. Ez a kódút bizonyítéka, nem ténylegesen telepített rossz modell állítása. Meglévő bajnoknál minden belépési ponton kötelező a regresszió-, tanításibemenet- és bajnokváltozás-ellenőrzés. Kivétel csak a valóban első modell létrehozása lehet, a szükséges kezdeti validációval.

### F04 · P2 · A mintahatárok nem tartják be az eredmények elérhetőségi idejét

Hely: [models.py:202](/Users/belavarga/Betting_tips_engine/src/betting_engine/models.py:202), valamint a [kalibrátor illesztése](/Users/belavarga/Betting_tips_engine/src/betting_engine/models.py:218).

A train/selection/calibration/test felosztás kezdési UTC-napokat használ. A jellemzők frissítésénél alkalmazott eredmény-elérhetőségi késleltetés nem zárja ki az illesztésekből a határon még nem ismert céladatokat. A jelenlegi bemenetben az `NHL:2025020626` mérkőzés 2025-12-31 23:30 UTC-kor kezdődik és a kalibrációba kerül; saját elérhetőségi szabálya szerint eredménye 07:30-tól használható. Az első tesztmeccs, `NHL:2025020627`, már 2026-01-01 00:00 UTC-kor kezdődik.

A jelenlegi `games.json` nyolc ligájának 24 mintahatárából 15-nél volt ilyen eltérés. Nem történt a telepített modellek eredeti archivált bemeneteinek visszajátszása, ezért a konkrét éles modellek teljesítménytorzításának mértéke nincs meghatározva. Az illesztési mintákat ugyanazzal az elérhetőségi határral kell szűrni, amely a jellemzőkre vonatkozik. A javítás után külön archív visszajátszás szükséges a riportokra gyakorolt hatás méréséhez.

### F05 · P2 · Utólagos bizonylat duplázhat egy már elszámolt fogadást

Hely: [storage.py:311](/Users/belavarga/Betting_tips_engine/src/betting_engine/storage.py:311).

A bizonylatimport csak nyitott `auto_assumed_placed` jegyet egyeztet. Egy már vesztesként lezárt, feltételezett 1 USD jegy egyetlen tényleges 3 USD bizonylata így új jegyet hozott létre. Az elszámolás után két jegy és **−4 USD** P&L szerepelt az egyetlen tényleges **−3 USD** helyett.

A lezárt feltételezett jegyekre is kell auditált egyeztetés, az eredeti rendszer-döntés megőrzésével és a végrehajtási eredmény korrigálásával. Új jegy kifejezetten további fogadásként jelentett bizonylatból keletkezzen. Átvétel: későn importált bizonylat, ismételt import és valódi második jegy külön esetei.

### F06 · P2 · Az 500-as listakorlát blokkolja a régebbi jegyek kézi elszámolását

Hely: [server.py:252](/Users/belavarga/Betting_tips_engine/src/betting_engine/server.py:252), [storage.py:295](/Users/belavarga/Betting_tips_engine/src/betting_engine/storage.py:295).

Az elszámoló végpont a legutóbbi 500 jegyet tartalmazó listában keres. 501 nyitott jegynél az első, létező jegy lezárása **404 / Bet not found** választ adott, és a jegy nyitva maradt. Az elszámolásnak közvetlenül azonosító szerint kell lekérdeznie. A listázásnál lapozás vagy keresés biztosítsa a régebbi rekordok elérését.

## Tényleges működési állapot

Pillanatkép: **2026-09-30 11:10 CEST**, 2898-as publikáció. Utóellenőrzés: **11:13 CEST**, 2901-es publikáció Tailscale-en.

- A helyi és TLS-ellenőrzött Tailscale API elérhető; a piaci feed egészséges. A 2898-as publikációban 1082 guard-ellenőrzés és nulla elutasítás szerepelt.
- Nyolc teljes meccses ligamodell, két MLB inningmodell és 31 periódusmodell betöltve, jelzett modellbetöltési hiba nélkül. Egy modell jelenléte nem minden piaccsalád jóváhagyása.
- A budapesti nap első pénzes kiadásai: **46/15**, ebből MLB 23, NFL 6, NHL 8, WNBA 9. Ez az adott nap pillanatnyi darabszámcéljának elérése; nem minden napra teljesített követelmény, nem 46 független esemény és nem 46 jelenleg újra megteendő fogadás. E pillanatképben 19 értéket mutató legjobb piaccsalád már nyilvántartott volt, új kiosztás nulla.
- A következő 48 órás feedben **21 meccs, 19 párosított meccs, 1082 modellezett kimenetel**. Ez megfigyelt feedlefedettség, nem a teljes Stake-webhely lefedettsége. A teljes visszaadott katalógus külön 108/108 lekért fixture-t jelzett.
- A rendszer **78 nyitott engineering-hiányt**, köztük egy lejárt határidejűt jelzett. `fully_ready=false`. Az NCAAF elérhetőségi/sérülési adat nincs modellbemenetként integrálva; emiatt a komponensállapot `degraded`, miközben a négy háttérszolgáltatás heartbeatje egészséges.
- A saját eredeti döntésekből **34 lezárt választás, öt külön meccs és két eseménynap**, **+2,757 USD tétek utáni számított P&L**. Az eredeti kiadási oddsok és a sporteredmény alapú elszámolás nem számlával hitelesített hozam. Az üzemeltetési költség ismeretlen, a tartós profit nincs bizonyítva.
- Beállítások: napi összesített limit `null`, maximum 5 USD/fogadás, 60 másodperces frissítés, feltételezett automatikus nyilvántartás bekapcsolva. Ezek egyeznek a vizsgált explicit követelményekkel.

## Tesztek és pozitív megállapítások

- Teljes izolált regresszió: **1126 Python/API-teszt + 5 alteszt**, 10,68 másodperc; **40 DOM-teszt**, mind sikeres. Egy Starlette/httpx elavulási figyelmeztetés maradt.
- Külön modellfókuszú részellenőrzés: 112 sikeres teszt; ez átfed a teljes csomaggal, nem plusz 112 egyedi teszt.
- A Tailscale-ről frissen letöltött HTML, JavaScript és dashboard adataival 42 kártya, 42 eseménylink és mind a 42 részletablak ellenőrizve. Nem volt JavaScript-hiba, `undefined`/`NaN` a kártyákon vagy auditból indított írás. A kiszolgált HTML/JS egyezett a forráspéldánnyal. Ez DOM-ellenőrzés, nem képernyőképes vizuális audit.
- A produkciós publikáció őre, az eredeti döntések megőrzése, a külön eredmény-/szakaszcélok, a tanítási bemenetek hash-archívuma és a külön munkások ténylegesen megjelennek a kódban és a tesztekben. A profitot és a befejezetlen piacképességeket a jelenlegi riport nem állítja bizonyítottnak.

| Szempont | Értékelés |
| --- | --- |
| Helyesség | Javítandó: a hat bizonyított eset hiányzik a jelenlegi regressziós lefedettségből. |
| Biztonság | A vizsgált utasítási és modellaktiválási határokon P1 hibák vannak; általános behatolási teszt nem történt. |
| Teljesítmény | Az ellenőrzött API működött. A mentett dashboard JSON 24,15 MB tömörítetlenül; ez külön optimalizálási jelölt, mért túlterhelést nem állítunk. |
| Karbantarthatóság | Erős regressziós és auditnyom, de a több belépési pont eltérő kapui és a sűrű megjelenítési kód növelik a hibakockázatot. A projekt gyökere nem Git-repozitórium; Git-diff alapú kiadástörténet itt nem ellenőrizhető. |

## Következő javítás átvételi pontjai

F01–F03 ellenőrzése legyen kötelező a következő kiadás előtt. F04 eredmény-elérhetőségi javítása és archív hatásvizsgálata előzze meg az érintett útvonal következő modellpromócióját. F05–F06 külön naplóegyeztetési regressziót igényel. Minden megállapítás **nyitott**; a jelentés létrehozása nem javítás, nem állítottunk be vagy toltunk el termelési határidőt.

Javítás után teljes Python/API- és DOM-regresszió, külön Codex-review, változatlan eredeti döntés-/jegymezők ellenőrzése, majd friss helyi és Tailscale-publikációs átvétel szükséges. Új fogadási modell aktiválását a passzív utility-release profil nem helyettesíti.

Bizonyítékok: [megfigyelések](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/observations.json), [Python-tesztek](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/python-tests.log), [DOM-tesztek](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/dom-tests.log), [élő DOM-ellenőrzés](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/live-dom-results.json), [API/napló reprodukciók](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/publication-reproduction.jsonl), [nyitott részletablak reprodukciója](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/ui-stale-detail-reproduction.json), [modell-audit](/Users/belavarga/Betting_tips_engine/reports/project-review-2026-09-30/model-review/results.json).
