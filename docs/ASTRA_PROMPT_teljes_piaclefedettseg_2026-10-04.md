# UTASÍTÁS – Astra: teljes piaclefedettség és éles ajánláskiadás (NFL, NHL, MLB, NBA, WNBA)

Ez végrehajtandó parancs, nem tervezési feladat. Most fejleszd le, élesítsd, és élő futásban igazold, hogy működik. Ne jelentést írj arról, mi hiányzik. Javítsd meg.

## Alapelv

A rendszer feladata, hogy a legjobb tudása szerint folyamatosan kiadja a legjobb fogadási ajánlásokat. Ha egy ciklus, egy liga vagy egy meccs **nem ad új téttel kiadott ajánlást**, az hiba. Az ok csak ez lehet: valami nincs bekötve, a szűrés túl szigorú, vagy a modellezés rossz. Ilyenkor a feladatod megtalálni az okot és megjavítani, nem elfogadni és naplózni. A „nem kiadható / validáció blokkolja / nincs igazolva” állapot nem végállapot, hanem javítandó hiba.

## 1. Meccsek: minden meccs, gördülő 48 óra

- Az NFL, NHL, MLB, NBA és WNBA **minden** meccse, amely a Stake-en a következő 48 órában kezdődik, kerüljön be minden ciklusba: az alapszakasz, a rájátszás és az előszezon is.
- Ha a kapcsolt schedule-feed nem ad meccset, de a Stake-en van (például NBA), akkor a Stake-kínálat a mérvadó: a meccs kerüljön be, és a hiányzó modellbemenetet pótold.
- Hiányzó bemenet (például MLB-kezdődobó, sérülés, lineup) ne ejtse ki a meccset. Pótold forrásból, vagy a modell a legjobb elérhető becsléssel számoljon.

## 2. Piacok: minden Stake-fogadástípus bekötve

- A Stake minden lapjának (Main, Handicap, Total, Half, Quarters/Periods/Innings, Combo, Specials, Team/Match, Player Props, Leaders, Same Game Multi) minden piactípusa legyen letöltve, értelmezve, modellel árazva, elszámolással bekötve és kiadható.
- Ezt mind az öt ligára meg kell csinálni, minden időszakra (teljes meccs, rendes játékidő, félidő, negyed, harmad, inning, Innings 1–5), és a „hosszabbítással / Extra Innings / Overtime and Penalties” variánsokra is.
- Konkrétan bekötendő minden, ami most hiányos:
  - **Kombinált piacok:** 1x2 & Total, Winner & Total, Handicap & Total, Total Over/Exact/Under, Halftime/Fulltime, Winning Margin, Point Range, Correct Score. Ezek mindegyikét a meglévő modellek közös eredményeloszlásából kell árazni.
  - **Eseménysorrend:** első/utolsó gól, első TD/FG, első futás időpontja, last team to score, race piacok.
  - **Összehasonlító piacok:** highest scoring half/quarter/period/inning, team to win more innings.
  - **Hosszabbítás:** lesz-e hosszabbítás vagy extra inning.
  - **Statisztikai piacok:** csapat és meccs (turnovers, sacks, passing/rushing stb.).
  - **Játékos-propok** mind az öt ligában, időszakos propokkal együtt.
- Ami jelenleg a kombináltpiaci validáció, a player prop validáció vagy más kapu miatt nem adható ki, azt tedd kiadhatóvá. A validáció legyen futó mérés a tanuláshoz, ne kiadási tiltás.

## 3. Modellezés: legjobb tudás szerint

- A modell–piac súlyozás ne legyen fix beállított arány. Ligánként és piaccsaládonként a mért eredményekből tanulja a rendszer, és folyamatosan frissüljön.
- Ha egy piacon a modell gyengébb a referenciánál (például a Winner Brier rosszabb az Elo-referenciánál), akkor a modellt kell javítani, vagy a jobb referenciát beépíteni. A piac ne legyen emiatt blokkolva.
- A valószínűségi sávszűrés és az érték-/valószínűségi küszöbök ne ejtsenek ki teljes piactípusokat. Ha egy bekötött piac (például a „Will There be Overtime”) minden kimenetele kiesik, vizsgáld felül és javítsd a küszöböt vagy a modellt.
- A sérülés-, lineup-, kezdődobó-, kapus-, QB- és időjárás-adatok legyenek számszerű modellbemenetek, ahol elérhetők.
- Minden kiadott tipp menjen elszámolásra és outcome-feedbackre. A liga × piactípus teljesítmény folyamatosan visszahasson a kiválasztásra és a tétre.

## 4. Kiválasztás és kiadás

- Minden meccsen a teljes, kibővített piackínálatból válassza ki a rendszer a legjobb ajánlásokat, és adjon nekik tétet a meglévő 1–5 USD-s, tanult teljesítményen és magabiztosságon alapuló tétezéssel. Favoritok engedélyezettek. A jobban teljesítő liga × piac párok elsőbbséget kapnak.
- A beállított kitettségi limitek maradnak. Azokon belül mindig a legjobb jelölt kapja a tétet.
- Egy meccs korábban kiadott tippje ne akadályozza, hogy ugyanazon meccs másik piacán jobb jelölt kiadásra kerüljön, amíg a limit engedi.
- A napi minimum cél kötelező, felső plafon nincs. Ha a kiadás elmarad tőle, az hiba: keresd meg és javítsd az okát (bekötés, szűrő, modell).
- A dashboard minden meccsnél mutassa az összes kiértékelt piacot: a kiadott ajánlásokat és az alternatívákat is.

## 5. Önellenőrzés minden ciklusban

- Minden ciklus végén automatikusan ellenőrizd ligánként:
  - minden Stake-meccs bent van-e;
  - minden piactípus árazva van-e;
  - minden meccsen született-e kiadott ajánlás.
- Ha bármelyik nem teljesül, a rendszer jelezze hibaként a dashboardon és a fejlesztési hurokban, és a javítás kerüljön azonnal sorra.

## 6. Kész akkor van, ha

- Tesztek zöldek (új parser + árazás + elszámolás tesztek valós Stake-payloadokkal).
- A szolgáltatás újraindítva, és több egymást követő élő cikluson igazolt mind az öt ligára:
  - minden 48 órás meccs bent van;
  - minden Stake-piactípus árazva és kiadható;
  - a rendszer folyamatosan termeli a téttel kiadott ajánlásokat.
- Röviden, magyarul jelentsd, mit kötöttél be és mit javítottál, a commit hash-ekkel. Ha valami mégsem működik, azt is jelentsd: a konkrét okot és a javítást, amin már dolgozol.
