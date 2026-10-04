# K04 — MLB: állapot, dobó, ütősor és runs

Típus: sportági tudásszintézis, modelljelöltek. Forrás: [B03 teljes baseballkönyv](../reading_v2/B03-notes.hu.md), [B08 1–17.fejezet](../reading_v2/B08-notes.hu.md), [B15 PDF144–156](../reading_v2/B15-notes.hu.md), [B24 15–16.fejezet](../reading_v2/B24-notes.hu.md). Állapot: történeti módszerek elolvasva; aktuális feed, szabály és OOS előny még ellenőrizendő.

## K04.1 — Opportunity és skill

Csapat-run, játékos per-PA skill és várható PA más mennyiség. OBP/SLG/wOBA/RC történeti összefüggései nem automatikus holnapi runprojekciók. Recency, életkor, platoon és ellenfélerő zsugorítással kezelendő; a legnagyobb történeti split kiválasztása torzít. FIP/DIPS és Statcast mechanikusabb dobójeleket adhat, de BABIP nem tisztán szerencse és xERA nem valódi képesség megfigyelése. Paraméter és definíció forrás-/korszakfüggő.

Adat cutoffig: PA és dobóesemények, bal/jobb kéz, park, lineups, kezdődobó/nyitó, várható dobóterhelés, bullpen rendelkezésre állása, időjárás. A dobó W/L és ERA a védelemtől, offense-től, scoringtól is függ. Teszt: azonos évek és azonos előre kijelölt játékosuniverzum; jövőbeli minimum-IP feltétellel ne szelektáljuk a mintát.

## K04.2 — RE24 és meccsszimuláció

A base–out állapot24lehetőség, de a bit- és outs-sorrend könyvenként eltér. RE24 állapotérték és eseményérték nem win probability. Meccsszimulációhoz batting order, baserunner-átmenetek, harmadikout/scoring, dobócsere és terminálás szükséges. A lineup maga alakítja a PA eloszlást. Hazai vezetésnél az alsó utolsó inning elmaradhat; ez a győzelem és under függését is érinti. Extra inning szabályt az aktuális ligaszabályból kell majd rögzíteni.

Nem alkalmazható: egész meccs runs/9 mint első inning eloszlás; iid véletlen hitter mint fix lineup; történeti1966-os átmeneti százalékok mai konstansként. Teszt: állapotátmeneti invariánsok, outs és runs, terminális helyzet, ismert egyszerű eloszlás, team totals/margin/total közös kalibrációja. A ligaátlag run egyezése nem elég a tails és matchupok igazolására.

## K04.3 — Park és szelekció

Parkfactor egyszerű home/away aránya keveredhet csapaterővel, menetrenddel, időjárással és mintazajjal. Védelmi/framing hatás kontextust és ellenfelet igényel. A negyedszer ütősoron átmenő kezdődobók szelektált túlélők: a kedvező átlag nem bizonyít fáradtságmentességet. Tesztelhető hipotézis: ezek cutoffhelyes korrekciója javítja a szezonokon át mért runeloszlást az egyszerű csapatratinghez képest. Előjele/nagysága nem hardcode-olt könyvtáblázat.

## K04.4 — Implementált inningcélú modellek (2026-09-29)

Az első és első öt inning külön modellje elkészült az MLB Stats API tényleges inningeredményeiből. A `segment_data.py` azonosító-, csapat-, idő- és végeredmény-egyeztetést végez; a `segments.py` külön count regressziót és időrendi train/selection/test bontást használ. A teljes game runs/9 tiltása fennmarad. Piacspecifikus Draw/push kezelés, pontos Stake-név és elszámolási időszak kötelező. A conditional-independence közelítés, teljes lineup/időjárás/bullpen hiánya és a profitszintű validáció hiánya a riportban marad. Részletes reprodukció: [MLB_INNINGS.hu.md](../../docs/MLB_INNINGS.hu.md). Ez nem új szakirodalmi eredmény vagy nyereségbizonyíték.
