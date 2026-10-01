# Release26 — eredménystatisztika és fejlesztési visszacsatolás

A felhasználó kérésére külön angol Statistics nézet mutatja az összes rögzített jegy eredményét, az eredeti döntések minőségét és az eredményekből indított fejlesztési munkát. Követelmény: R38. A fogadási policy verziója továbbra is 2026-09-30.22; ez a kiadás nem aktivál új modellt vagy fogadási szabályt.

## Megállapítások és javítások

- A megőrzött87 lezárt jegy kimenetele és pénzügyi elszámolása független újraszámításban egyezett.39 nyert,45 vesztett,3 push; tét90,50 USD, számított visszatérítés81,7922 USD, nettó−8,7078 USD.86 eredeti döntés nettója−9,3478 USD. A különbség egy valós, felhasználó által jelentett ismételt nyertes jegy. A pillanatkép ideje és hash-e az auditfájlokban szerepel; az oldal a későbbi eredményeket is figyelembe veszi.
- Feltételezett manuális végrehajtás és felhasználói jelentés külön kategória; egyik sem közvetlen Stake-fiókellenőrzés. A megőrzött eredményforrás nem automatikusan újraellenőrzött nyers forrás. A régi full-game hivatkozások korlátja látható marad.
- Az új olvasási út nem alkalmazza a régi500-jegyes listakorlátot. Egy tranzakcióban olvassa a jegyeket, eredeti döntéseket és eredményeket. Hiányzó pénzügyi adatból nem számít hamis nullát; a duplikált jegy csak a cash-ledgerben számít külön.
- Budapest szerinti meccskezdési dátum-, liga- és végrehajtásiforrás-szűrő, ligánkénti és scope/piaccsaládonkénti bontás, nyitott tétkitettség, reprodukált kalibráció és visszakereshető lezárt jegyek készültek. A GET nem ír fogadást, tanulási feladatot vagy számlálót.
- Az NHL jövőbeli előrejelzéseinek kiértékelése most ugyanazt a pending/final eredményvédelmet használja, mint a bet-elszámolás. Függőben lévő eredmény nem tanítási cél. Már rögzített történeti eredményt nem írtunk át.
- Az outcome review korábban általános összefoglalót írt. Most pontos eredeti bemeneteket, értékelőkód-hasheket és diagnózist archivál; tartós, fix határidejű engineering gap és feladatfókusz keletkezik. Azonos bizonyíték nem generál új kísérletet vagy feladatáradatot. A hiányzó régi forrást nem találja ki.
- A13 befejezett meccsből származó veszteséges részszegmensek diagnózisok, nem megbízható oksági következtetések. Külön teszt, független review és kontrollált kiadás kell a javításhoz; a diagnózis vagy elkészült segédmodul nem bizonyított modelljavulás.

## Ellenőrzési lánc

Az implementált, tesztelt, telepített és élőben igazolt állapot külön bizonyítékot kap a `data/statistics-lifecycle-20260930/verification-index.json` fájlban. A telepítési manifest a pontos forrás- és teszthasheket, a teljes Python/DOM tesztnaplót, a független Codex-reviewt és az ellenőrzött source-only telepítést köti össze. A helyi és Tailscale API/UI ellenőrzés és a valódi outcome-review/engineering intake futás külön artifact; egy implementációs állítás nem helyettesíti őket.

A tesztcsomag lefedi a duplikált jegyeket, hiányos pénzügyi adatokat, eltérő elszámolási köröket, függő NHL-eredményt, árva outcome-rekordot, read-only snapshotot,500feletti jegyszámot, idő- és forrásszűrést, sérült/stale bizonyítékot, feladatismétlést, határidőmegőrzést, UI-hibát, XSS-t és későn visszaérkező szűrőválaszt. A régi frontend tesztfájl teljes tartalma változatlan prefix; a hozzáfűzött import kizárólag az új Statistics regressziókat veszi fel minden teljes suite-ba.

## Megmaradó korlátok

A rendszer jelenlegi rögzített eredménye veszteséges; nyereségesség és új modelljavulás nincs igazolva. A hiányzó teljes forráslefedettség, új piacok és validált modellek továbbra is nyitott engineering munka. Ez a kiadás az eredmények mérését, a lifecycle-védelmet és a bizonyítékhoz kötött fejlesztési átadást valósítja meg. A költségkeretről a felhasználó dönt; az itt közölt P&L a fogadási tétek utáni eredmény, működési költségek nélkül. USD1–5, max5, daily_risk_limit=null, az eredeti jegyek és a napi checkpoint változatlanok. Automatikus fogadásleadás nincs.
