# Hivatalos Stake API és NCAA — 2026-09-29.10

A felhasználói NCAA-kör: bármely sportág, amely ténylegesen elérhető a Stake-en. A felfedezés nem korlátozódik az amerikaifutballra és a kosárlabdára. A megfigyelt kínálat, a modellezhetőség és a kiadott ajánlás külön állapot.

## Adatfolyam

A dokumentációt és OpenAPI-sémát a `data/probes/stake-api-docs.html`, illetve `stake-openapi.yaml` fájl őrzi. A kliens a hivatalos `https://odds-data.stake.com` szolgáltatást használja:

1. `/sports`: minden engedélyezett valódi sportág; további sportágaknál `/sports/{sport}/categories` ellenőrzi az USA kategóriát.
2. `/sports/{sport}/usa/tournaments`: ismert pro tornák és az aktuális NCAA-tornák. Egy sikertelen felderítés nem üres, érvényes kínálat.
3. Tornánként a `/schedule/sport/{sport}/usa/tournament/{tournament}` és `/sports/{sport}/usa/{tournament}/fixtures` uniója. A mindkét helyen szereplő meccs csak egyszer számít.
4. Minden meccsnél `/fixtures/{slug}` és `/odds/{slug}`. A teljes eredeti válasz külön időbélyeget, SHA-256 lenyomatot és tartós, tömörített fájlt kap a `data/stake_api_archive` mappában. Ezeket a korlátozott nyers gyorsítótár takarítása nem törli.
5. Az ajánlat egy ellenőrzött válaszból származik. Az elsődleges odds-válasz hibájánál érvényes fixture-válasz használható, a részleges hibát jelezve. A két válasz árait nem keverjük. A katalógus a másik válaszban egyedül szereplő szerződést külön jelöli.

A 48 órás keresés 60 másodperces célütemű. A teljes, visszaadott meccskatalógus 15 percenként esedékes tartós munkafeladat, a hosszú tanulmányok elé sorolva. Az órarendet a szolgáltatás és a források tényleges elérhetősége korlátozza. A teljes válaszok megőrzése nem bizonyítja a weboldal minden fülének vagy az egyedi SGM-árazásnak a lefedettségét.

A megfigyelt menetrendi `date` és a fixture-lista `startTime` több helyen fél órával eltér. A pontos fixture-időt használjuk, megőrizve az eltérést; egy órán túli vagy valódi csapatazonossági konfliktus kizárást okoz. A fixture/odds metaadatait és a független sportmenetrendet ezután is ellenőrizzük. Az MLB Seed-címek feloldásához a valódi csapatnevekkel egyező, aktív Winner piac két kimenetele szükséges.

## NCAA-modellek és tényleges kínálat

A 2026-09-29-i ellenőrzés 27 felderítési választ, 99 meccset, 198 teljes fixture/odds-választ és 607 ligánként/szekciónként pontos piacímet gyűjtött. A 99 meccs az MLS-leltárt is tartalmazza. NCAA: 14 amerikaifutball-meccs, mind a 14 párosítható a független menetrenddel. Ezeken 78 aktív, támogatott teljesmeccs-kimenetel árazását külön ellenőriztük. A meccsek az ellenőrzés idején a 48 órás ajánlási ablakon kívül vannak. A jelenlegi API nem adott vissza NCAA kosárlabdatornát vagy más NCAA-sportágat; a következő felderítés újra megvizsgálja a kínálatot.

A CFB történeti export csak befejezett meccseket tartalmazott, az ESPN scoreboard közvetlen elérése 403-at adott. A jövőbeli menetrend a SportsDataverse nyilvános, egyesített CFB-állományából érkezik, a csapat teljes nevét ellenőrzött csapatazonosító kapcsolja a csapatkatalógushoz. Ismeretlen csapat vagy TBD kezdés nem találgatott adat. A jövőbeli forrás nem írja át a történeti célváltozókat. A férfi/női kosárlabda aktuális szezonkiadása tartalmazza a menetrendet.

| Saját modell | Elkülönített tesztmeccs | Brier | Engedélyezett teljesmeccs-család |
|---|---:|---:|---|
| NCAAF | 719 | 0,1666 | Winner, Handicap, Total, csapattotalok |
| NCAAM | 3325 | 0,2075 | Winner |
| NCAAW | 3052 | 0,1688 | Winner, Total, hazai csapattotal |

Saját college-adaton tanított modellek, nem átvett NFL/NBA-paraméterek. A Winner kapuja legalább 300 időben elkülönített tesztmeccset és a konstans/Elo összehasonlítást igényli. A pontpiacok kapuja külön Brier- és kalibrációs követelményt használ. A score-diagnosztika modellközpontú vonalakon készült, nem történeti végrehajtható oddsokon; nem hozam-visszateszt. A college keretváltás, sérülés és játékos-szerep mennyiségi hatása még nincs teljesen modellezve. Új college sportot a rendszer nem sorol automatikusan ezek alá.

## Hibafelügyelet és adatjavítás

Minden 48 órás fixture külön sort kap: kiadott/új ajánlás, aktuálisan elégtelen edge, ismeretlen meccsazonosság, hiányzó modell, nem támogatott piac vagy forráshiba. Ez a Research nézetben és az operációs auditban olvasható. Hiányzó ajánlás esetén az ok megmarad a tartós kutatási/javítási sorban; ugyanazt a fogadást nem adjuk ki újra a darabszám növelésére.

Az önálló felügyelő már nem csak a folyamatok szívverését nézi: publikáció, sportforrások, evidence guard, liga- és szakaszmodellek, teljes API-válaszok, NCAA-felderítés, katalógus, tanuló munkás és szakirodalmi keresés kap állapotot. A hibás forrás és a még meg nem írt modell eltérő feladat. Az adatfrissítés, modell-visszaállítás, tanítás és receptvizsgálat automatikus; önálló kódírás/telepítés továbbra sincs implementálva.

Az ellenőrzés egy régi NHL/2023 exportból is kimutatta a hiányzó meccsazonosítókat. A javítás 1400 meccs hivatalos boxscore-jával egyezteti a csapatokat, kezdést és végeredményt. Öt meccsnél a kapus lövései/gólja miatt a mezőnyjátékosok összege eltért: ezeket a hivatalos play-by-play és góllista eseményazonosítóival egyezteti. Hiányzó érték nem lesz nulla; a szétlövés pluszgólja nem kerül a lövési hatékonyságba.

## Még nyitott követelmények

A teljes player-prop, eseménysorrendi, Leaders/Specials, kombinált/SGM, live és minden további NCAA-sport modell- és elszámolási támogatása nincs kész. Részletek: [ALL_MARKETS.hu.md](ALL_MARKETS.hu.md). A napi legalább 15 ajánlás célja és az 1–5 USD tét változatlan; nincs darabszám-felsőkorlát vagy fix napi pénzkeret. A pénzkereset cél, igazolt nettó nyereséget ez a kiadás nem állapít meg.

## Források

- [Stake Sports Data API dokumentáció](https://docs-odds-data.stake.com/)
- [SportsDataverse adatkiadások](https://github.com/sportsdataverse/sportsdataverse-data)
- [CFB egyesített adatok és leírás](https://github.com/sportsdataverse/cfbfastR-cfb-data/blob/main/DATASETS.md)
- [Hivatalos NHL boxscore példa](https://api-web.nhle.com/v1/gamecenter/2022020939/boxscore), ugyanazon meccs `play-by-play` és `landing` válaszaival.
