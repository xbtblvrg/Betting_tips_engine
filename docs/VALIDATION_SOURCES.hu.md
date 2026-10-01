# Elkülönített, rögzített validációs források

A `config/validation_sources.json` védett, root által felülvizsgált katalógus. Pontos tesztútvonalakat és SHA-256 értékeket köt a deklarált forrásfájlok útvonalához, méretéhez és hash-éhez. Az archív byte-ok a `data/validation_source_archives/<sha256>.bin` tartalomcímzett fájlokban vannak. A katalógus nem könyvtárbejárási felhatalmazás: legfeljebb 16 csomag, 256 forráshivatkozás, 128 MiB összes deklarált adat és 64 MiB/fájl adható át.

A `development_inputs.export_validation_sources(root, workspace)` kizárólag külön munkaterületre exportál a `data/validation_sources/<bundle>/` névtérbe. Az aktuális játékos-, esemény-, modell- és fogadási adatokat nem írja felül. Az eredeti nyers válaszok, metaadatok és megfigyelési időpontok byte-ra változatlanok. Egy csomag csak minden deklarált tag ellenőrzése után jelenik meg. Meglevő, eltérő byte-okat nem javít felül; a hiány, eltérés, tiltott útvonal vagy symlink explicit hibát ad.

Mind a `DevelopmentWorker.prepare`, mind a `ReleaseCoordinator.prepare` ezt az exportert hívja. A kész export állapotát a `data/validation_sources/export.json` rögzíti. A fejlécben a konfiguráció pontos hash-e, a csomagokban a teszt- és forráskötések szerepelnek. A `verify_validation_sources(root, workspace)` csak olvas: minden tesztfázis és a független review előtt/után ellenőrzi a deklarált byte-okat, és nem állítja helyre a törölt vagy módosított bizonyítékot. A Codex/test sandbox a teljes validációs névteret csak olvashatóként kapja.

A forrásgeneráció az archív adatok tényleges hash-ét és méretét figyeli; egy heartbeat vagy egy nem kapcsolódó árfrissítés nem jelent új forrást. A hiányzó validációs csomag a release oldalon `validation_sources_required` strukturális akadály, külön root helyreállítási teendővel. Változatlan bizonyíték mellett nem indít újra teljes tesztet. Az új forrás jelenléte nem oldja meg automatikusan a jelölt eredeti elfogadási feltételeit.

## A két konkrét helyreállítás

- `native-rush-v1`: az eredeti NFL F0027 séma-visszajátszásokhoz szükséges 11 fájl, beleértve a rögzített `20260929T192751138800Z-d0ccf522fb5d/sources.json` történeti hivatkozást. Az eredeti hat valós visszajátszás eredményfeltételei változatlanok; hiányzó csomag most teszthiba, nem kihagyás.
- `native-shots-v1`: az elfogadott F0025 jelölt eredeti, 05:00 UTC előtti NHL-forráscsomagjának pontos öt eseménye és metaadatai, 11 fájl. A rögzített eseményhalmaz, játékosszám, lövésösszegek és időbeli ellenőrzések változatlanok. A modul és a két eredeti dokumentum az elfogadott byte-okkal egyezik; kizárólag a teszt gyökérútvonala változott root felülvizsgálattal. A régi accepted archívumot és a korábbi sikertelen kiadást nem módosítottuk.

A régi sportmanifestek teljes byte-jai megmaradnak, de a deklarált csomagok csak az adott teszthez szükséges tagokat tartalmazzák. A többi manifest-bejegyzés nem állítás arról, hogy annak forrásfájlja ebben a validációs részhalmazban is elérhető.

## Root integráció

A felülvizsgált kód/tesztek/katalógus mellett a `source-origins.json` 22 különböző blobja telepítendő pontos hash-ellenőrzéssel a production `data/validation_source_archives` alá. Production könyvtárra az exporter szándékosan nem ír. A root teljes tesztkörnyezete külön stage legyen: az új kódból `export_validation_sources(production_root, isolated_stage)`, majd `verify_validation_sources(production_root, isolated_stage)`, teljes Python/DOM tesztek és független kiadási ellenőrzés. Ha a root közvetlen production teszt-visszajátszáshoz is telepíti a validációs névteret, kizárólag az ellenőrzött csomagbyte-okat és exportbizonylatot másolja; meglévő konfliktusnál álljon meg.

Ez a csomag passzív forrás- és tesztinfrastruktúra, továbbá az F0025 passzív egyeztető utility. Nem aktivál player prop modellt, nem bizonyít aktuális részvételt, önmagában nem engedélyez elszámolást vagy fogadást, és nem profitállítás.
