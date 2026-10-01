# Fejlesztői tesztkörnyezet javítása

A macOS helperhatár tesztje közvetlenül sikerült, a Codex Seatbelt környezetében viszont a második `sandbox-exec` profil felvétele 71-es kilépéssel meghiúsult: `sandbox-exec: sandbox_apply: Operation not permitted`. Ez nem a production olvasási/írási védelem sikertelensége; a platform nem engedte a tesztből a második profil alkalmazását. A korábbi sikertelen futások megmaradnak.

A javított controller minden alfolyamat előtt saját, megbízható kódból végrehajt egy valódi natív ellenőrzést külön átmeneti mappán. Pozitív kontroll bizonyítja, hogy a próba CLI olvasni és írni tudja a jelzőfájlt; a tényleges, változatlan helperprofil ezután mindkettőt megtiltja, és a fájl tartalmának változatlanságát is ellenőrzi. Production fogadási adatot a próba nem olvas.

Az eredmény csak a controller run-archívumába kerül. Nonce, időpont, a helper- és ellenőrzőmodulok pontos SHA-256 hash-e, valamint mindkét kontroll eredménye köti. A sandboxolt teszt kizárólag a pontos macOS nested-sandbox hibánál támaszkodhat erre a friss bizonyítékra. Hiányzó, idegen forrású, régi, jövőbeli vagy sikertelen bizonyíték esetén hibázik. Más CLI-hibát ez nem tesz sikeressé. A normál helperhívó nem változott, és soha nem futtat védetlen helper fallbacket.

A `native_boundary.py` védett workerfájl. A controller-prooffájlt a jelölt csak olvashatja. A projekt root és a fogadási adatok olvasási/írási tilalma, a hálózati tiltás és a külön production release követelménye megmaradt. A függőségkönyvtárak symlinkjeit a worker közvetlenül a már engedélyezett, csak olvasható kanonikus célra köti; a staging dupla symlink miatti hozzáférési hibáját ezzel megszünteti, további projektmappákat nem engedélyez.

A prompt elkülöníti a kódoló saját implementációs feladatát a később futó független controller review-tól és production release-től. Ezek későbbi volta önmagában nem teszi hiányossá a jelölt implementációt. A kifejezetten megkövetelt valós forrás, availability timestamp, validációs eredmény és settlement bizonyíték hiánya továbbra is `task_complete=false` ok; szintetikus fixture nem helyettesíti.

D13 elutasítása más okból helyes volt: a jelölt ténylegesen három regressziós esetet adott hozzá a meglévő `tests/test_closing_prices.py` fájlhoz. A rebase-konfliktus csak a forrásmodult érintette, nem azt a tesztfájlt. A meglévő tesztek byte-szintű változtathatatlansága megmarad. A pontosított prompt új tesztfájlt kér a rebase-regressziókhoz is, és tiltja a már helyes upstream kód indokolatlan újraformázását.

## Ellenőrzés

A külön worker-stage valódi, controller által indított Codex sandboxában:

- Python: **650 passed**, további 5 subtest; egy meglévő Starlette figyelmeztetés.
- DOM: **40 passed**, nulla kihagyott teszt.
- Külön OS-próba: a védett controller-root jelzőfájl olvasása és írása tiltott; a natív bizonyíték átírása is tiltott; a jelzőfájl változatlan.

A részletes kimenetek: `data/development/runs/native-repair-check-4/` a worker-stage alatt. A célzott suite korábban 74 tesztet teljesített; ezután a függőség-symlink és rebase regressziókkal bővített teljes suite futott. A hiányzó staging tudáskártyákat az eredeti, változatlan kártyák másolásával pótoltuk, nem a tudásguard gyengítésével. Production deploy nem történt.
