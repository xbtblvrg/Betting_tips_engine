# Ligánkénti folyamatos kiértékelés és tétezés

Kiadás: 2026-09-29, szerződésverzió `2026-09-29.6`.

A felhasználói cél a Stake-en ténylegesen kiírt lehetőségek közül saját sportmodell alapján kiválasztott, konkrét és beárazott végrehajtási lista. Minden új kiadott ajánlás 1–5 USD, és automatikusan a rendszer saját, feltételezetten megfogadott döntésnaplójába kerül. Nincs fix napi összegkeret. A napi minimum cél 15 különálló, első alkalommal kiadott, pozitív stresszelt értékű szerződés. A meglévő fogadás nem lesz új ajánlás egy árfrissítéstől.

## Működő folyamat

1. NBA, MLB, NHL, NFL, WNBA és külön MLS-leltár vizsgálata minden piaci frissítéskor. A támogatott sportmodellek továbbra is az első öt ligához vannak meg.
2. Pontos Stake-piacnév és forrásrész szerinti katalógus az ajánlásokkal azonos publikációban: megfigyelt, támogatott és kiértékelt kimenetelek. A Swish-részleg nem számít igazolt elérhetőségnek.
3. Független előrejelzés, exact vonalra számolt p és push, odds szerinti EV, konzervatív stresszforgatókönyv, legjobb aktuális szerződés kiválasztása piaccsaládonként, tétképzés, EvidenceGuard, publikálás. Nincs külön LLM-jóváhagyás.
4. Ligánkénti működési vizsgálat: forráskiesés, hiányzó odds, nem párosítható esemény, hiányzó vagy sérült modell, támogatott piac kiértékelésének hiánya, nem modellezett piacok.
5. Tartós feladatsor automatikus próbákkal. A forráshelyreállítás friss sportadat-lekérést kér a piaci motortól. A hiba csak későbbi friss megfigyelés után minősül helyreállítottnak. A szolgáltatásfelügyelő ettől függetlenül ellenőrzi az elakadt folyamatokat.
6. Sérült modellnél a többi liga működése folytatódik. Csak a korábban kiadott modell riportjával pontosan egyező archivált példány állítható vissza, a sérült fájl megőrzésével. Új modell ezzel nem promotálható.
7. Napi külön elsődleges szakirodalmi keresés mind a hat ligához, továbbá modellezési, kalibrációs és tanulóagent témákban. Az arXiv-lekérések címet és absztraktot indexelnek, hash-sel és olvasási státusszal. Ez nem teljes tanulmányolvasás vagy automatikus implementáció.
8. Hiányzó piacmodellekhez végrehajtott kutatási dosszié: tényleges piackatalógus, könyvi tudáskártyák, releváns felfedezett cikkek és adat-/validációs követelmények. Új piacséma új dossziéfeladatot indít; változatlan sémát nem dolgoz fel minden percben. Napi darabszámhiánynál legfeljebb hatórás újravizsgálat, egyébként napi.

A `market_research` feladat elkészülte **nem oldja meg** a piacmodell hiányát. A működési hiány `needs_implementation` marad. Ha a piac kikerül a 48 órás kínálatból, `not_currently_observed` állapotba kerül, nem „implementálva” állapotba. Nincs szabadon működő autonóm kódíró szolgáltatás.

## Az 1–5 USD tét

A `stress-kelly-unit-v1` az adott kimenetel stresszelt valószínűségét, push-esélyét, exact oddsát és a már tartott különálló meccsszerződések számát használja. Nem a legmagasabb nyerési százalék kapja automatikusan a legnagyobb tétet.

Ismert bankroll esetén megmarad a rész-Kelly és a bankrollszázalékos korlát. Ismeretlen bankroll esetén verziózott, korlátozott alapegység-szorzó működik: `sqrt(max(1, full_Kelly / 0.02)) / sqrt(1 + related_contracts)`. Ez saját tétezési heurisztika, nem személyre szabott Kelly és nem bizonyított portfólióoptimum. A tényleges tét maximum 5 USD, cent lefelé kerekítve. Bankrollalapon 1 USD alatti megengedett összeget nem kerekítünk fel.

A publikáció újraszámolja és ellenőrzi a tét bizonyítékmezőit. A korábban kiadott vagy jelentett fogadás eredeti tétét és oddsát az új algoritmus nem írja át. Az összeg módosítása nem indít új jegyet.

Matematikai háttér: [Chu, Wu, Swartz: Modified Kelly Criteria](https://www.sfu.ca/~tswartz/papers/kelly.pdf), bevezetés és 2. fejezet. A becsült nyerési valószínűség bizonytalansága befolyásolja a Kelly-tétezés alkalmazhatóságát; a fenti saját szorzószabály nem a cikk módszerének implementációja. Forráslekérési dokumentáció: [arXiv API kézikönyv](https://info.arxiv.org/help/api/user-manual.html), [API használati feltételek](https://info.arxiv.org/help/api/tou.html).

## Ellenőrizhetőség és megmaradt hiányok

- `Model Performance → Per-League Continuous Evaluation`: tényleges ligánkénti állapot és kutatási időpont.
- `Automatic Recovery and Remaining Model Work`: tartós hiány, kiválasztott javítás, feladatstátusz és legutóbbi hiba.
- `Research → Stake Market Types`: az adott publikáció teljes, liga és forrásrész szerinti piacleltára.
- `Betting Picks`: konkrét kiadott fogadások és változatlan előzmények, exact Stake-eseménylinkkel.
- `data/league-operations-release-check.json`, `data/league-operations-dom-check.json`: az éles ellenőrzés bizonyítékai.

A teljes játékos-, periódus-, kombinált és élő piacmodellezés, a sérülések/kezdők minden hatásának számszerűsítése és az MLS-modell továbbra sincs készen. Ezeket az új loop feltárja és dokumentálja; nem helyettesíti a hiányzó implementációt. A nettó profit és a minden napon elért 15 új ajánlás nem igazolt teljesítés.
