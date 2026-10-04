# Könyvtári tudásszintézis — 2026-09-27

A hozzáférhető 28 helyi fájl mindegyikének rendelkezésre álló tartalma feldolgozott: 26 könyv-/tanulmányfájl (duplikátumokkal, töredékekkel és kísérőanyaggal), valamint 2 online forráskatalógus. A 24 részletes olvasási jegyzetet 12 közös témakártya kapcsolja össze. A könyvi anyagot már az [R04 NHL-tanulmány teljes feldolgozása](online/R04-notes.hu.md) is kiegészíti. A kereső aktuális méretét a `search_knowledge.py --rebuild` parancs jelzi; az index a jegyzetek bővülésével frissül.

Ez a könyvanyag szintézise, nem kész vagy profitot igazolt engine. A kártyák a részletes jegyzetekhez vezetnek, azok a könyv fejezetéhez/oldalához; a leltár a pontos forrásfájl hashét őrzi. A hash a példányt azonosítja, az olvasási napló a feldolgozási állapotot.

## A tudás szerkezete

| Kártya | Téma | Későbbi felhasználás |
|---|---|---|
| [K01](cards/K01-valoszinuseg-ar-ev.md) | Esély, fair ár, EV, push, margin | Kifizetési szerződés és egységes számítás |
| [K02](cards/K02-fuggetlen-modell.md) | Rating, eloszlás, ensemble, oddsfüggetlenség | Önálló sportelőrejelzés és piaci benchmark elválasztása |
| [K03](cards/K03-adat-ido-forras.md) | Adat, cutoff, kulcs, hiány, hír | Történetileg helyes bemenet és reprodukálhatóság |
| [K04](cards/K04-mlb.md) | MLB | Dobó, bullpen, ütősor, park, base–out állapot |
| [K05](cards/K05-nba-wnba.md) | NBA és WNBA | Tempó, hatékonyság, lineup, játékosperc |
| [K06](cards/K06-nfl.md) | NFL | Irányító, drive, EPA, kulcsszám és ponteloszlás |
| [K07](cards/K07-nhl.md) | NHL | Lövésminőség, kapus, TOI, játékállapot |
| [K08](cards/K08-validacio.md) | Validáció | Kalibráció, időrendi teszt, szelekció, CLV |
| [K09](cards/K09-bankroll.md) | Bankroll | Kelly feltételei, korreláció,1–5USDkorlát |
| [K10](cards/K10-piac-vegrehajtas.md) | Piac és végrehajtás | Bookmaker, tőzsde, bid/ask, díj, késés |
| [K11](cards/K11-ai-tanulas-memoria.md) | AI és memória | Forrásolt hírfeldolgozás és ellenőrzött modellfejlesztés |
| [K12](cards/K12-ellentmondasok.md) | Ellentmondások és bizonyíték | Hibajavítás, történeti állítás és nyitott hiány |

## A könyvek közös tanulsága

A fogadási érték nem pusztán a valószínűbb győztes. Előbb saját sportadatból becsült, kalibrált kimeneteleloszlás kell; ezt az adott fogadás pontos kifizetésével és ténylegesen elérhető árával lehet összevetni. A legösszetettebb modell, legszebb magyarázat vagy leghosszabb szimuláció sem helyettesíti a jó adatot és a mintán kívüli ellenőrzést.

Az MLB/NBA/NFL könyvi alap részletesebb, mint a WNBA/NHL anyag. A sportok közös rating- és validációs keretet használhatnak, de eltérő mechanizmust, szabályt, exposure-t és kimeneteloszlást igényelnek. A felhasználó kérésére a piaci odds az értékellenőrzés és későbbi benchmark része; nem az önálló sportesély alapja.

A kis tét a veszteség nagyságát csökkenti, előnyt nem teremt. A frissített felhasználói cél napi legalább 10 rangsorolt jelölt; a [menetrend](../docs/MENETREND.hu.md) külön kezeli a rangsort, a pozitív becsült előnyt és a pénzes ajánlást. A kínálat hiányát és a bizonytalan értéket nem szabad elrejteni. Az önfejlesztés adatgyűjtésből, újratanítási jelöltekből és mérhető, időrendben elkülönített összehasonlításból állhat; egyetlen napi eredmény utáni szabályátírás túlillesztéshez vezethet.

## Bizonyítéki állapot

- **Elolvasott:** a mellékelt helyi tartalom, az olvasási naplóban megjelölt ábrákkal/képletekkel.
- **Számolt:** a célzott algebrai/numerikus auditok, külön reprodukciós fájlokkal. Nem minden közölt empirikus kutatás reprodukált.
- **Hipotézis:** könyvi trend, konkrét sportegyüttható, személyes tapasztalat és mai átvihetőség.
- **Külső kutatás folyamatban:** a katalógus 18 forrásából az R04 teljes szövege és kijelölt ábrái feldolgozva, forráskritikai jegyzettel. A többi forrás ellenőrzése, a további kódmellékletek, aktuális szabályok és szolgáltatások vizsgálata még nincs lezárva. A letöltés vagy egy absztrakt elolvasása nem teljes feldolgozás.
- **Megvalósult:** betanított ötliga-modell, élő referenciaár, helyi automatikus webes publikálás. **Nincs még:** hiteles végrehajtási oddsokon igazolt profit és Telegram-küldés. Lásd az [implementációs kapcsolatokat](IMPLEMENTACIOS_KAPCSOLATOK.hu.md).

A forráskritikai megjegyzés nem csökkenő készültséget jelent: a teljes olvasás után is maradhat megoldatlan empirikus állítás. Ezeket pontos státusszal őrizzük, nem kitalált igazolással zárjuk le.

## Megvalósítás és folytatás

A felhasználó fejlesztési utasítása alapján a könyvszintézisből és ellenőrzött adatmintákból működő kutatási kiadás készült. A megmaradt online olvasás, reprodukció és független jövőbeli validáció folytatandó. Az új webes engine és a régi szintetikus CLI-prototípus külön megvalósítás.
