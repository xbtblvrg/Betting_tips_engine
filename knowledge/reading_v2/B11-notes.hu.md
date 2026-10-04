# B11 — Prompt Engineering for Generative AI — a mellékelt fájl teljes feldolgozása

James Phoenix és Mike Taylor. **A rendelkezésre álló PDF ábra- és kódpélda-melléklet, nem a teljes magyarázó könyvszöveg.** Mind a 311 oldal kinyert szövege (185322 karakter) sorban elolvasva. A 128 ábrás oldal mindegyike vizuálisan áttekintve, 32 kontaktlapon; a 38.,120.,134.,145.,303.,305. oldalt külön nagyításban is ellenőriztem. A képernyőképek apró feliratait nem mindenhol lehet teljesen kiolvasni. A kódokat nem futtattam külső szolgáltatásokon. Ez olvasási és statikus forráskritikai jegyzet, nem mai API-kompatibilitási teszt.

## 1–2. fejezethez tartozó mellékletek, 10–36. oldal

Tokenizálás, zero/one/few-shot példák, formátum előírása, variánsok véletlen sorrendű emberi értékelése. A terméknév-generálási példa öt-öt kimenetet mér; a .2 vs .6 átlag nem elég bizonyíték általános vagy statisztikailag stabil javulásra. A promptot, modellváltozatot, teljes bemenetet és ismétléseket együtt kell nyilvántartani. A JSON-kérés nem önmagában szintaktikai garancia.

Az ábrák illusztrálják a példák és a modellméret kölcsönhatását, a promptolás és felügyelt tanítás eltérő adatigényét, valamint az embeddingek szemantikai viszonyait. Az egykori GPT-4-alapú modellértékelési oszlopdiagram relatív bírálói eredmény; nem sportelőrejelzési pontosság. A 2-2. ábra a klasszikus encoder–decoder transzformert BERT/GPT jelöléssel mutatja: ez nem a GPT pontos architektúrarajza. A 2-3. ábra minden lépésben argmaxot ír, ami csak greedy dekódolás, nem minden generálás. A 2-4. ábra SFT→preferenciás jutalommodell→PPO folyamatot mutat, történeti példaként. A Bard JWST-képernyőképe a magabiztos tévedés szemléltetése.

## 3. fejezet, 37–70. oldal — kimenetek és szövegfeldolgozás

Regex, JSON/YAML, típus- és értéktartomány-ellenőrzés; feladatbontás; mondat-, bekezdés-, téma-, összetettség-, hossz- és tokenalapú darabolás; átfedő ablakok; több válasz többségi besorolása; LLM-bíráló. Hasznos elv: a strukturált kimenetet program ellenőrizze, a hiányzó kontextust ne találjuk ki, a forrásrészek összefoglalása ne veszítse el az eredeti helyet és a kivételeket.

Konkrét korlátok:

- 3-2: a bemenet csonka szavai és a plusz c. alpont nem egyeznek a kiírt eredménnyel. A regex nem javítja ki a csonka szavakat, és nem törli a c. pontot. A PDF vizuális képe is igazolja az eltérést; a kinyerés külön a/b címkeismétlést is okozott.
- 3-3 JSON-példájában a szó szerinti `...` nem érvényes JSON. A 3-1 külön gyűjtött címei és alcímei elveszítik a hierarchikus kapcsolatot.
- 3-6 YAML-validátor: a kivételosztályok nem definiáltak a részletben; bool Pythonban intnek számít; a negatív mennyiséget és felesleges kulcsokat nem zárja ki; a darab/tucat korlátozás nem egyezik a gallon/loaves sémával. A kulcs jelenléte nem teljes üzleti validálás.
- 3-11 csúszóablak eldobhatja a végét, ha az nem illeszkedik teljes ablakba; túl hosszú ablaknál a teljes rövid szöveget is elveszíti. Nulla/negatív ablakot nem ellenőriz. Az átfedés növeli a költséget és duplikációt is okozhat.
- A tokenszámláló konkrét régi modellazonosítókhoz és üzenetformátumhoz kötött; univerzális aktuális számlálóként nem kezelhető.
- A Flask-teszt saját új alkalmazást definiál: ilyen minta nem igazolja a valódi alkalmazás működését.
- Három azonos modellből vett válasz szavazása nem három független szakvélemény. A döntetlen és az érvénytelen osztály nincs kezelve. A rövidséget mérő bírálóban az értékelendő válasz system szerepet kap; a mérendő tartalom és az utasítás összekeveredik.

## 4. fejezet, 71–127. oldal — láncok, sémák, eszközök

Prompt–modell–parser lánc, batch és konkurencialimit, Pydantic séma, eszközleírás, függőségi gráf, few-shot kiválasztás, promptmentés, dokumentumbetöltés, metaadatok, szekvenciális és független lépések, stuff/refine/map-reduce/map-rerank összevetése. A sorrend és a függőség fontosabb annál, hogy minden lépés AI legyen. A map-reduce elveszíthet összefüggést, a refine sorrendfüggő; az önbevallott confidence nem kalibrált megbízhatóság.

Forráskritika:

- 4-7 egyszerre kér puszta nevet és összetett JSON-objektumot. A `temperature=0.0` változó nincs átadva a modellnek. A pontszám leírása nem enforce-olt 0–10 intervallum; a típus nem igazolja a tartalmat. A beillesztett principles szöveg `{industry}` része nem feltétlen kap újabb formázást.
- 4-9 minden backslash törlése érvényes escape-eket és adatot is roncsolhat. Az általános except és a None mezők nem magyarázzák meg az okot. A „soha None” utasítás bizonytalan adatnál találgatásra ösztönöz.
- 4-10 részlet nem definiált score-változókat használ, korábbi eltérő típusú listákra épít. Páros LLM-preferencia nem azonos címkeaccuracy-val; diszkrét ismert címkékhez közvetlen összehasonlítás kell.
- 4-11 a naptáreszköz csak rögzített sikerobjektumot ad, semmilyen naptárkapcsolatot nem valósít meg. A bemutatott sikerüzenet nem bizonyít külső műveletet.
- 4-13/15 a tools-hívások és a régi function szerepű válaszprotokoll keverednek, az eredeti assistant tool-call üzenet és a megfelelő hívásazonosítók hiányoznak. Modern implementáció előtt külön dokumentációellenőrzés szükséges.
- 4-16 üzenetlista helyett halmaz szerepel: sorrend nem garantált. A 4-18/19 QueryPlan sémája nem ellenőrzi a gráf ciklusait, egyedi azonosítóit vagy a hivatkozott függőségek létezését.
- 4-22 országnevet kér, de fővárost ad; a példa saját specifikációja ellentmondásos.
- 4-38 a genre mezőbe teljes szótár kerül; 4-40 minden mezőre ugyanazt a teljes bemeneti szótárat engedi át. A tényleges scene és plot ráadásul hiányzik az invoke-bemenetből. A PDF 120. oldalán vizuálisan is igazolva.
- Az összefoglalás nem helyettesíti a dokumentumok teljes olvasását. A dokumentum betöltése, chunkolása és vektorizálása önmagában nem tartalmi feldolgozás.

## 5. fejezet, 128–155. oldal — embedding és visszakeresés

Embedding API, Word2Vec, TF-IDF, koszinusz/L2 hasonlóság, FAISS index, RAG, indexmentés/összevonás, Pinecone metaadat, self-query retriever. A hasonlóság a relevancia jelöltje, nem az állítás igazságának valószínűsége. A retriever által visszaadott legközelebbi szöveg is lehet elégtelen; az „I don't know” viselkedésnek indoka van. Külön megőrzendő a dokumentum helye, változata és megfigyelési ideje.

Hibák és hiányok:

- 5-2-ben a ratatouille és bus között nincs vessző: Python összefűzi őket. HTTP timeout és státuszellenőrzés nincs.
- 5-3 eredményében a rövidített bemenetben nem látható szavak is szerepelnek; a bemutatott eredmény a hiányos kódból nem reprodukálható.
- 5-4 a cake/lie második számításnál n×1 oszlopokat ad a cosine_similarity-nak. Így dokumentumpárok egydimenziós hasonlóságát átlagolja, nem a két szó dokumentumvektorának egyetlen koszinuszát. Az első cakevec számítás ezzel szemben helyesen 1×n-re alakít. A 134. oldalon vizuálisan igazolva. A self-hit kizárása indexszelettel holtversenynél sem megbízható.
- A FAISS index mentése önmagában nem menti a chunkok/id-k és a metaadatok kapcsolatát. Összevonáshoz azonos embeddingmodell és indexértelmezés is kell. K nagyobb elemszám esetén a hiányzó indexek kezelését a példa elhagyja.
- **5-13 retry-hiba:** részleges sikernél az embeds lista megmarad; az újrapróbálkozás hozzáfűz a lista végéhez. A zip miatt rossz vektor kapcsolódhat szöveghez/azonosítóhoz, és csendes adatvesztés lehet. A PDF 145. oldalán is látható. A „batch” valójában sok egymás utáni embeddinghívás. Nincs pontos részleges hibaelszámolás.
- A batch metaadatai 0,10,20… értékek, míg a szűrés példája 1-et keres. A könyvmetaadatokhoz „movie” tartalomleírás kerül. A globális warning-tiltás elrejtheti a kompatibilitási figyelmeztetéseket.

## 6. fejezet, 156–199. oldal — agent és memória

Eszközhívás→megfigyelés→következő művelet, ReAct, eszközséma, agent executor, memória, összefoglaló memória, callbacks, költség- és tokenszámlálás; BabyAGI és Tree of Thoughts szemléltetése. Az eszköz végrehajtása és a róla szóló nyelvi állítás különbözik. Az alap ciklushoz a melléklet nem ad teljes megbízhatósági modellt; korlátlan ciklus, részleges hibák és megismételt mellékhatások külön kezelendők.

- A „google_search” csak rögzített választ visszaadó stub. Ez nem élő kutatás.
- A regex külön utolsó actiont és inputot választ; hibás formátumnál össze nem tartozó elemeket is párosíthat. Strukturált tool-call szerződés és argumentumellenőrzés szükséges tanulság.
- A karakterhossz példájában a trace másképp írja az eredeti szót, mégis 34-et állít. A trace nem önmagában végrehajtási bizonyíték.
- A 6-2 összehasonlító táblázat nem általános képességkorlát: eszközhívás köré is szervezhető iteráció és szekvenciális végrehajtás. A korabeli framework-változatot mutatja.
- SQL-mintában a modell kérés nélkül vezetéknevet, emailt és dátumot talál ki. A visszaadott szöveg nem bizonyítja az adatbázis hiteles állapotát. Statisztikai/napi sportszámításnál ilyen adatpótlás elfogadhatatlan.
- Több memóriapélda kiírt eredménye nem a bemutatott mentett üzenetekből származik (186–187. oldal). A memória összefoglalása veszteséges és nem modellparaméter-tanítás.
- Az interjúmentés a modelltől kéri az egész beszélgetés rekonstrukcióját és mindig ugyanazt a fájlt felülírja; hiteles naplóhoz az eredeti események tárolása a megfelelő alap.
- Tokenköltség-példák korabeliek, mai díjbecslésként nem használhatók.

## 7–9. fejezet, 200–286. oldal — képgenerálás

A teljes ábrasor áttekintve: forward noise/reverse denoise; szövegencoder, véletlen zaj, látens tér és dekóder; DALL-E/Midjourney/Stable Diffusion történeti példák; stílus, negatív prompt, súlyozás, képreferencia, inpaint/outpaint, karakterkonzisztencia, denoise erősség, paraméterrács, PNG-metaadat, upscaling, ControlNet Canny/Depth/Normal/OpenPose/M-LSD/SoftEdge/segmentation/scribble; SAM-maszkok; Dreambooth, SDXL base/refiner és képarányok.

A vizuális előtte/utána példák bemutatnak irányíthatóságot és hibákat, nem univerzális minőségi garanciát. A GitHub-star növekedés nem önmagában aktív felhasználószám vagy modellpontosság. A refiner preferencia-ábráján 48.44% vs 36.93% történeti összevetés látható, a tesztprotokoll teljes szövege itt nincs meg. Az adott bettingfeladathoz ennek a résznek nincs közvetlen prediktív bizonyítéka.

A Dreambooth kód oktatási, részben notebook-specifikus. Több koncepció esetén a fájlmozgató ciklus az utolsó c/uploaded változót használja a külső ciklus után. A képrács egyetlen subplot és hiányzó/más nevű könyvtárak esetét nem kezeli. A csomagok, modellek, hardverkövetelmények és API-k történetiek; nem futtattam telepítést vagy tanítást ezek alapján.

## 10. fejezet, 287–311. oldal — összetett tartalomkészítési példa

Keresési eredmények betöltése→összefoglalás→szakértői kérdések→vázlat→RAG-alapú szakaszgenerálás→illusztráció→Gradio. Ez a több lépéses munka szemléltetése, nem sportquant modell. Több saját importált modul forrása hiányzik a mellékletből. A lapletöltő nem őrzi meg az URL-metaadatot, set használatával elveszíti a keresési sorrendet; az async lista egyes elemeit egymás után várja meg. A broad-except miatti k-csökkentés nem minden hiba helyes kezelése, és üres kontextussal találgatáshoz vezethet.

A promptoptimalizálási ábra embedding-távolságot mér: a hasonlóbb szöveg nem feltétlen igazabb vagy hasznosabb. A kézi A/B példában .6 vs .45 score és kb.629 vs636 token szerepel, de a mintaméret/variancia nélkül nincs stabil következtetés. A 305. oldalon a `prompt` a szöveg literal része, nem változóinterpoláció; a PNG-válasz kezelése nem bizonyítja a kért téma használatát. A későbbi példa Stability-kulcsot kér, de OpenAI kulcsoldalt nevez meg, és a listaeredményt str-ként jelöli. Több sortörés/behúzás is javítást igényelne.

## A projekt számára megőrzött következtetés

A melléklet hasznos az adatkinyerés, visszakeresés, forráshoz kapcsolt tartós memória, ellenőrzött eszközhívás és mérhető promptvariánsok fogalmainak megértéséhez. Nem igazolja, hogy az LLM önállóan kalibrált sportesélyt becsül, hogy egy agent-ciklus magától javítja a stratégiát, vagy hogy egy megnyerő magyarázat jó fogadást jelent. A statisztikai előrejelzés minőségét sportadatokon, időrendben elkülönített mintákon kell majd vizsgálni. A mai technikai megvalósítást a teljes könyvolvasás utáni hivatalos dokumentációkutatás alapozhatja meg.
