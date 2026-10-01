# Stake-navigáció és webes elérhetőség — 19.2.2

A két felhasználó által jelzett NHL regulation Double Chance ajánláshoz az API belső `winner` csoportját az engine tévesen Winner fülként mutatta. Mindkét tényleges eseményoldal natív Safari-vizsgálata megerősítette: ilyen fül nincs, Main/Combo/Goals keresésben a teljes rendes játékidős piac nem található; Periods alatt külön harmados piacok vannak. Megőrzött bizonyíték: `data/stake-navigation-20260930/audit.json`, `ui-observations.json`, `feed-evidence.json` és hash-ellenőrzött nyers források.

## Változtatás

- A belső és ismeretlen API-csoportból nem készül kitalált webes fül; a nyers piaci evidencia megmarad.
- A friss pontos szerződés null navigációja nem hozza vissza a régi Winner fület. Az eseménylink és navigáció ugyanabból a forrásból származik. Navigációváltozás bezárja az elavult főoldali részletablakot.
- A két konkrét esemény regulation Double Chance szerződéseit a guard, a publikációs tranzakció és a jelenlegi végrehajtási ellenőrzés blokkolja a pozitív webes visszaigazolásig. Nem kerülnek át másik piacnévre vagy időszakra.
- Az eredeti kiadások, tétek, oddsok, checklist, elszámolás, napi számláló és költségkorlátok változatlanok. Más események és piacok jogosultságát ez a célzott korlátozás nem változtatja.

## Ellenőrzés és kiadás

Az izolált regressziós futás 2549 Python-tesztet és 5 altesztet teljesített; 72 DOM-teszt sikeres. A pontos kiadott fájlhash-eket, független Codex-review-t, telepítést és a későbbi helyi/Tailscale állapotot a `data/stake-navigation-20260930/` alatti install-manifest, independent-review, install-journal és live-verification fájlok igazolják, amint elkészülnek. A dokumentum önmagában nem élő kiadási bizonyíték.

## Követelmények újraellenőrzése

R22 és a pontos végrehajtható szerződés R04/R19 követelménye javítva a fenti két eseményre. Az R01–R34 követelmények többi megvalósításához ez a célzott kiadás nem ad új teljesítési állítást: független modellezés, $1–$5 tétsáv, napi minimum15 cél, eredeti döntések és veszteségek megőrzése, automatikus betleadás tilalma fennmarad. Teljes webhely-/piac-/modelltámogatás és tartós nyereségesség továbbra sem igazolt. A legutóbbi teljes audit fennmaradó modell-, adat-, fejlesztési és release-hiányait ez a navigációs javítás nem zárja le.

A két feed/webhely-ellentmondás feloldásához pozitív, azonos eseményre és regulation Double Chance piacra vonatkozó webes bizonyíték szükséges. Ismeretlen navigáció önmagában nem jelent általános fogadási tilalmat. A korlátozás nem szűnik meg pusztán az API újabb, változatlan válaszától.
