# Fejlesztési elakadások — záró audit, 2026. szeptember 30.

A feltárt workflow- és HTTP-elakadások javítása élesben fut. A záró változat `2026-09-30.17.4`, telepítve 13:32:38 CEST-kor. Ez a fejlesztési és kiadási folyamat ellenőrzött javítása; nem a teljes fogadási modellkészlet vagy a nyereségesség készre jelentése.

## Mi okozta az elakadást, és mi változott?

- A konkrét kódhibák túl általános, órákig tartó várakozást kaptak. Most pontos hibabizonyítékhoz kötött javítás indul, az eredeti előzmények és határidők megtartásával. A tényleges szolgáltatóhiba külön késleltetést kap.
- A kiadási akadály nem jutott el teljes bizonyítékkal a javítóhoz. Most a pontos jelölt, hash és tesztnapló is átkerül; a változatlan hibát nem futtatja végtelenül újra a kiadó. Egy újabb hitelesen elfogadott javítást nem nyit vissza egy régi hibanapló.
- Tömeges, zárolás alatti fájlmásolás fogta vissza az előkészítést; más, már meglévő adatok viszont kimaradtak. A fejlesztő feladathoz kötött, ellenőrzött forráscsomagot, meglévő részmodelleket és eredeti döntések olvasási másolatát kapja.
- A felügyeletből hiányzott a tényleges előrehaladás megkülönböztetése. Most látható és felügyelt az aktív munka, az indítható feladat, az adatfüggés, a kiadási felelős és a következő lépés.
- A böngésző párhuzamos frissítései és a nagy JSON-feldolgozás túlterhelték a szervert; a macOS háttérbesorolása tovább lassította. A kliens összevonja a lekéréseket; a dashboard és a fejlesztési állapot külön, korlátozott, csak olvasó folyamatban készül. Csak a HTTP-szolgáltatás kapott Interactive besorolást.

## Éles ellenőrzés

Két külön, egyenként 120 másodperces mérés. Körönként legalább 20 kérés végpontonként, három SSE-kapcsolat, minden mintában három aktív fejlesztési sáv és valódi új publikáció. A kiadó ellenőrzése is párhuzamosan futott. Összesen 120 HTTP200, nulla kérési hiba vagy időtúllépés.

| Végpont | 1. kör p95 | 2. kör p95 | Legnagyobb idő | Hibák |
|---|---:|---:|---:|---:|
| Betting dashboard | 1.691 s | 1.821 s | 1.880 s | 0 |
| Fejlesztési állapot | 0.535 s | 0.609 s | 0.870 s | 0 |
| Health | 0.213 s | 0.226 s | 0.265 s | 0 |

Az eredeti határértékek változatlanok. A korábbi sikertelen mérés megmaradt: a fejlesztési végpont 2,023 másodperces p95 értékét nem minősítettük a 2,0 másodperces feltétel teljesítésének; további kódjavítás után futott a két sikeres kör.

A teljes, legfrissebb kódon 1674 Python-teszt és 5 alteszt, továbbá 64 DOM-teszt sikeres, kihagyott teszt nélkül. Több független Codex-review és 44 fájl pontos hash-ellenőrzése támasztja alá a kiadást. A közben települt NFL-adatsegédmodul hat történeti próbája is az eredeti, ellenőrzött forrásokon futott.

A friss 3014-es publikációt helyben és a konfigurált, TLS-ellenőrzött Tailscale :8443 címen is megkaptuk. A tényleges HTML/JS visszajátszása 12 jelöletlen végrehajtható kártyát ellenőrzött: pontos Stake-link, szerveres engedély, konkrét 1–5 USD, nincs Recheck Required, undefined, NaN vagy JavaScript-hiba. A Tailscale-próba a saját gép Tailscale-címére vonatkozik; távoli eszköz WAN-válaszidejét nem igazolja. A teszt nem küldött fogadást vagy checklistmódosítást.

Az eredeti 128 ticket, 127 rendszer-döntés és 120 első kiadás immutábilis mezői megmaradtak. Az adatbázis quick_check sikeres; régi adatbázist nem állítottunk vissza. A developer és release kapcsoló engedélyezett, az engine, learning, development és release felügyeleti állapota egészséges. A végső kiadás nem szakította meg a három kódoló munkáját.

Négy korábban elakadt passzív segédmodul ténylegesen települt: E8B9BF9FBBA, D14, F0022 és F0027. Ezek nem jelentenek automatikus új fogadástípus-engedélyt. D22 13:41:00 CEST-kor production_verified állapotban lezárult a teljes elfogadás, független ellenőrzés és hash-hez kötött root-átvétel után. Ezt a friss élő /api/development válasz is igazolta; a korábbi sikertelen kódoló próbája megmaradt.

## A teljes felhasználói cél állapota

A követelményjegyzék R01–R30 pontjai változatlanok: önálló sportmodellből induló értékelés, a Stake pontos aktuális kínálatával összevetett ajánlás, angol végrehajtási felület, indokolt 1–5 USD, napi összkeret nélkül, közelgő 48 óra, minden eredeti döntés és veszteség követése. Codex az elsődleges fejlesztő és felügyelő. A napi minimum 15 külön kiadott fogadás mérendő, továbbra is teljesítendő cél; hibás input vagy kitalált edge nem teljesíti.

Továbbra is nyitott: teljes liga- és piaccsalád-lefedettség, a hiányzó validált player-prop/live/specials/SGM/NCAA-képességek, teljes sérülés- és lineup-hatás, valamint a tényleges tartós profitabilitás igazolása. A fejlesztési sorban van futó és indítható munka, továbbá konkrét forrás- és kiadási függőség. Ezeket ez a javítás nem írta át kész állapotra. Az eredeti feladatok, felelősök, ellenőrzési feltételek és határidők tovább élnek.

Bizonyítékok: `data/flow-recovery-20260930/`; végső élő mérés: `live/http-load-final/`; a részletes előzmény: `docs/DEVELOPER_AUDIT_2026-09-30_17.hu.md`.
