# B24 — Weighing the Odds in Sports Betting

King Yao, Pi Yee Press, copyright 2007/2008/2011, ISBN 978-0-935926-38-5. EPUB 33 egység. **A teljes33 egység és393816 karakter elolvasva;58 beágyazott kép vizuálisan ellenőrizve.** Fejezetenkénti kritikus jegyzet; a történeti adatokat nem tekinti mai modellparaméternek.

## 6–7. fejezet

Hedge értékelése: aktuális valószínűség, az új tét saját EV-je, a meglévő és új tét együttes kifizetési fája, maradó kitettség és kockázati preferencia. Jó indok lehet a túl nagy kitettség, nemnegatív EV-jű ellenoldal, előre tervezett jövőbeli csökkentés vagy tőke felszabadítása. A két külön könyvnél tett ellenoldali fogadás általában újabb tőkét köt le, nem automatikusan szabadít fel tőkét. Cash-out/ticket eladás ettől eltér.

Az összesített middle pozitívEV-je nem jelenti, hogy hozzáadása javítja a már meglévő fogadás EV-jét. Fordítva, egy pozitívEV-jű reversemiddle-láb nem feltétlen kerülendő csak azért, mert egy szűk eredménytartományban mindkét tét veszít. A teljes eloszlás és a kitettségi limit döntsön. Könyvbeli maxwin/maxloss önmagában nem teljes kockázatmérték; a gyakoriság, függőség és bankroll is kell.

A Saints 24:1future p=.1 mellett100tétEV150,300tétEV450. A feltételes későbbi−110hedge2640/2400 EV−120 **abban a későbbi állapotban**. Ezt az indulóEV450-ből feltétel nélkül levonni és330-ként a teljes stratégiaEV-jének nevezni nem pontos: a hedgeállapot elérésének esélyével kell súlyozni, továbbá minden lehetséges útvonalat figyelembe venni. A magasabb első tétet igazoló jövőbeli hedge lehetősége nem garantált; a könyv később ezt elismeri.

USC100tét5:1, .818×.34≈.278 végsőnyerés: fair ticketérték166,80, eredeti tét feletti érték66,80. A szöveg utóbbit is market value-nak nevezi, ami az előző fejezet bruttóérték-definíciójával keveredik. Innen a realizációs változás +433,20/−166,80 helyes. Az aktuális érték és az eredeti költség elkülönítése nélkül félrevezető a könyvelés.

Orlando90bruttófuture .95→85,50, meccsvesztésután.92→82,80:−2,70 értékváltozás. A könyv ezt maximális hedge-tétként kezeli, de a szükséges hedgeprofit, tét és odds nem ugyanaz; a másik ág és a meccsesély is szükséges a pontos neutralizáláshoz. A szinte értéktelenné vált future mellé vesztett eredeti tétet „visszanyerő” fogadás nem valós hedge.

Parlay utolsó lábának tervezett negatívEV-s hedge-je helyett kevesebb láb jobb lehet azonos díjszabás mellett; promóciós kifizetés vagy eltérő információ kivétel. Félidei nagy middle látszólagos értéke gyakran a már jól álló eredeti jegyből származik, nem az új láb pozitívEV-jéből.

A Bonds-hír példája:160/100eredetitétEV10,30; háromszoros480/300 majd200tét+180ellenoldal→végső+100/−120, EV24,10 a feltételezett65,5%-on. A nagyobb kezdeti tét és későbbi ármozgás kombinációja javíthat, de a mozgás/elfogadás kockázatát is árazni kell. A „hedge általában hiba” szerzői álláspont; egyéni hasznosság és kockázati korlát eltérhet.

## 8. fejezet — NFL szezonwin

Minden mérkőzésre páronként koherens esély; nem determinisztikus W/L-besorolás. Az ellenfélerő a menetrenden keresztül jelenik meg. A 32×16/2=256 győzelem korabeli, döntetlen nélkül; a szezonhossz nem mai konstans. Ha döntetlent modellezünk, két winvalószínűség összege1−p_tie. Nem szabad egyszerre döntetlent hangsúlyozni és minden meccsen p_away=1−p_home-ként számolni.

A piaci RSW-lineok262-es összegéből256meccs mellett a szerző az underirány gyakoribb értékére következtet. A kiírt line azonban nem feltétlenül várható érték: medián, oddsaszimmetria és vig miatt nem lehet egyszerűen a lineok összegét a várható győzelmek összegével azonosítani. A szerző helyesen nem javasol vak underfogadást.

2006CARratinges, neutráliserőhöz+8százalékpont HFA-példa; ez korabeli heurisztika, szélsővalószínűségeknél tartományellenőrzés szükséges. A GamesoftheYear-árakkal összevetés diagnosztika, de a felhasználó kért saját modelljét ettől külön kell tartani. Jövőbeli felállás/erő bizonytalanabb távolabb: a hullámzó vonalzó analógia hasznos. Viszont a szimmetrikus ratingbizonytalanság **nem általában EV-semleges**, mert a spread→win és win→szezonpiac leképezés nemlineáris, és a meccsek függhetnek közös sérülési/erőállapottól.

RSW egészszámnál kb20%-os push és félwin≈50cent a szerző közelítése. Csak a feltételezetteloszlás és odds-környezet mellett; a szezon várható győzelme önmagában nem határozza meg P(W>küszöb)-öt. A hivatkozott spreadsheet azonos meccsenkénti eséllyel binomiális modellt használ; változó ellenfelek mellett Poisson-binomiális vagy feltételes szimuláció szükséges lehet. A 0,01win=1cent rövidítés nem univerzális. Hat2006under mindnyertes, a szerző maga is szerencsét említ; hat fogadás nem módszervalidálás. Táblák3–5még vizuálisellenőrzésre várnak.

## 9–10. fejezet

Parlaycard: a „6,5for1” bruttó6,5, nem6,5profit; háromegyenlőfüggetlenláb küszöbe(1/6,5)^(1/3)≈53,58%.4lábas11for1≈54,91%. Állott nyomtatottvonal és tieswin szabály adhat értéket, de az eltávolított game és elfogadás korlátja fontos. Két55%és egy52,5%láb15,881%, ami6,5grossoddsnálpozitív; egy55%és két52,5%15,159%, negatív. A 55%^3=16,6375%, ROI8,14375%; a könyv8,16-ja kerekítésből ered.

Nem50/50 közép esetén a halfpush egyszerű hozzáadása csak közelítés. p_cond=.52381 ésp_push=.1 mellett p_fav_buy=.9×.52381+.1=.571429, nem.57381; a másikoldalon.528571, nem.52619. A különbség kis edge-nél számít. A de-vig módját is explicitté kell tenni.

Table6NFLfullgamepush1989–2006, ±1pontsávok, standardhibák. A+sávok átfednek, nem függetlenminták; a ±1SE nem95%-osCI. A simítás indokolt lehet, de a célfüggvény/teszt nélkül kézi simítás is túlilleszthet. Home+7sávn266,6,8%push,se1,5százalékpont példát közöl.

Table7–9NFLfirsthalf: teljesmeccsline alapján empirikus félideieloszlás; a félidőnem egyszerűen a teljes spreadfele. Többtörténetidöntetlen: közeli meccsekben9,5%; a7-es és4-es félideipushértéke eltér afullgame-től. A sorcím és ténylegesátlagline eltérhet a3-as tömörülés miatt, ezért a különbözősoroknem azonosközűek. Kis minták, biztonságos hibasáv, korszakváltozás; a szerző 2004–2006 közt is romló fogadhatólehetőségeket említ. Félpontcentek fix összeadása−125körül csakközelítés, nem pontos pushkorrekció. Pihentetett playoffcsapat és változó félideifelállás megsértheti a historicalmapping feltételeit. A történeti adatok nem mai tippek. Táblák6–9még képiellenőrzésre várnak.

## 11. fejezet — eddig

Officepool10×10, meccsenként kétpontszám utolsószámjegye; sorsolás előtti négyzetekazonosesélyűek, sorsolásután eltérőértékűek. Negyedenként/final/OT eltérőpayoutszabály; eztkellárazni. 4522NFLmeccs1989–2006adat, Table10elsőnegyedhome/awayutolsószámjegyek. A0–0a leggyakoribb elsőnegyednégyzet, jelenthet0–0,10–0,0–10,10–10stb. **Innen folytatandó.**

## 1. fejezet

A könyv irányelveket és alkalmazkodási módokat ígér, nem változatlan nyerőrendszert. Szerzői háttér pénzügyi derivatívák; fundamental handicapping, relatívérték és végrehajtás eltérő, részben átfedő tevékenységek. A szerző kifejezetten kis minták mellett is döntő traderként, nem formális akadémiai kutatóként ír. Hasznos a kis minták bizonytalanságának elismerése; a nagy mintákból ismert összefüggések automatikus piaci beárazódása viszont általánosítás, nem bizonyítás.

A jogi/Nevada/online-kifizetési állítások korabeli leírások. Különösen a hibásan kiírt fogadás kötelező teljesítésére tett abszolút kijelentést nem szabad mai szabályként alkalmazni ellenőrzés nélkül. A könyv maga jelzi, hogy egyes táblázatok képként vannak az EPUB-ban; ezekhez külön ellenőrzés kell.

## 2. fejezet

EV helyesen a lehetséges kimenetek valószínűséggel súlyozott kifizetése. A kockapéldák: négy nyerő/két vesztes arc ±3 dollár mellett EV=1; egy arc +100, öt arc −1 esetén EV=15,833. −110 két egyenlő esélyű oldalán 110 kockázatra EV=−5. Negatív/pozitív amerikai odds és valószínűség konverziói helyesek; ezek nullszaldós küszöbök, nem önmagukban a valódi esélyek.

Push leválasztása: fair odds számításához p_win/(p_win+p_loss), nem p_win önmagában. Az eredeti tétre vetített EV-ben a push továbbra is nulla profitú kimenet. A Shaq blocked-shots kép táblázata még ellenőrzendő. A cents-skála nem lineáris valószínűségskála: tíz cent értéke a kiinduló oddstól függ.

A 10,2%-os NFL 3-as tömeg mellett, egyenlő 44,9%-os két farokkal a fair −2,5/−3/−3,5 ajánlat kb. −123/+100/+123. Ez az egyenlő farokfeltételre és adott korabeli mintára érvényes, nem minden spread körül általános félpont-érték. 1989–2006-os adatokat használ.

Parlay: két független 50%-os láb 25%, három 12,5%; korrelált lábakhoz feltételes valószínűség. Több láb páronkénti korrelálatlansága önmagában nem elég a közös függetlenséghez. Odds-szorzat és valódi közös nyerési esély elválasztandó. Teaser −110 kétlábas küszöbe √(11/21)≈72,37%; eltérő lábakra geometriai átlag, és push-/tie-szabály külön kell.

ROI=profit/összes kockáztatott tét, nem éves bankrollhozam. Várt ROI és realizált ROI külön. Magasabb ROI kisebb összprofitot is jelenthet, ha kihagyunk kisebb, de pozitív EV-jű fogadásokat; a többletkockázat és költség itt számít. Példabeli Yankees −150, majd feltételezett fair −180 mellett 1500 kockázatra EV107,14 és várt ROI7,14% helyes.

Bankroll elkülönítése, kisebb tétek, pozitív és negatív kimenetek összetett kitettsége. 1–2% szerzői heurisztika, nem univerzális optimális szabály. Tétprogresszió nem alakít negatív feltételes EV-jű lehetőségeket pozitívvá. A 48%-os nyerési esélyű, 10-től induló nyolclépéses martingale 2550 bankrollt kockáztat; .52^8≈0,005346; EV≈−3,69 a teljes ciklusra. Ez jól megkülönbözteti a gyakori kis nyereséget a ritka nagy veszteségtől.

A Vegas fogadóablak/rotációszám/jegylejárat rész operatív korabeli leírás; a képes táblák és a konkrét parlay-kifizetések még képi ellenőrzésre várnak.

## 3. fejezet

Fundamentális csapat/játékosértékelés; angle és trend; helyzet/motiváció; feel; futures; adatbázis-alapú derivatív ár; alternatív pont; teaser; prop; parlay; line shopping; tippek követése; bónusz/promóció. Jól kezeli az átfedést és azt, hogy egyik címke sem garantál sikert.

Sok trend közül szélsőséges múltbeli minták keresése adatbányászati csapda. A konkrét motivációs anekdoták továbbra sem oksági tesztek. Jó adatbázis rossz értelmezése negatív EV-hez vezethet. Konkrét feltételes ár és kifizetés nélkül a korreláció sem tesz automatikusan pozitívvá egy parlayt. Követőnél a kiadott ár elérhetősége és a tipster kiválasztása külön probléma.

55% a szövegben széles körben elérhető −110-es spreadek kontextusában szerepel, nem tetszőleges oddsra. 1000×1100 tét 55%-os eredménnyel profit55000. 300×550 tét 55%-on várható profit8250; további300 53%-os fogadás1950 többlet, összesen10200. Az alacsonyabb átlagtalálatú portfólió így több pénzt termelhet, ha a becslések helyesek, kockázat/költség elfogadható. Képtábla még ellenőrzendő. A profi sikerarányra tett <1% a szerző becslése.

## 4. fejezet

Elkülönülő bookmaker ajánlatok, nincs automatikus legjobb-ár végrehajtás. Profit és kockázat céljai konfliktusba kerülhetnek. Korai kis limitek mellett információgyűjtés, majd limitnövelés; retail követés; sérülés, időjárás, tétforrás és más könyv mozgása módosítja az árat. A meccs elhalasztása után megtartott új nyitóár a korábban megtanult információ példája. A void/napváltás szabály konkrét szolgáltatófüggő, nem általános mai szabály.

A hatékony piac erős/gyenge formáinak definíciója pontatlan: a standard felosztás nem pusztán „minden” kontra „legtöbb” nyilvános információ. Ezt a jelen jegyzet kritikaként rögzíti; a későbbi online szakirodalmi ellenőrzésnél tisztázandó. A közönség/informált pénz aránya számíthat, de a Super Bowl következetesen kisebb hatékonyságára és nagy mozgásból következő hibás nyitóárra tett állítás empirikus igazolást igényel. Új hír is nagy mozgást okozhat. WNBA itt csak korabeli kisebb piac példája, nem sportmodell.

Sportfogadás az összes ellenoldalt/bookmakert is beleértve, külső költségek előtt zéróösszegű transzfer; a fogadók csoportja vig után negatív összegű. Relatívérték-becslés hatékonyabbnak feltételezett főpiacból: a felhasználó független modelljében csak összevetési rétegként releváns.

Mark-to-market: egy nem-push bináris ticket becsült fair jelenértéke p×bruttó kifizetés, a könyv rövid távon diszkontot nem számol. Seattle100 tét20:1; p=.37 esetén777 jelenlegi becsült érték, nem csak az eredeti100 van gazdaságilag kockázatban. Külön kell választani a realizált profitot, a modellszerinti értéket és az elérhető cash-out/likvidációs árat. Ezek hiánya esetén a „már megnyert677” nem realizált pénz.

Broncos-divíziófuture100×3bruttó; aktuálisp=.4→120; következőmeccswin/lossesetén.6/.2→180/60. Pick’em és fair0-viges ellenfél mellett60hedge a következőmeccs által okozott±60 változást semlegesíti, **nem a future összes hátralévő kockázatát**. Az ilyen példák feltételes esélyeinek koherensnek kell lenniük: .5×.6+.5×.2=.4.

## 5. fejezet

Scalp: egymást kiegészítő kimenetek, megfelelő téttel minden ágon azonos nettó. Lakers+3,5even100 és Spurs−3,5+110 tét95,24 → profit4,76mindkétágon. Nem tökéletes100/100tét→0vagy10. A „nincs kockázat” kizárólag az érvényes, egyező szabályú, már mindkét oldalon elfogadott kifizetési modellben igaz; elfogadás/elszámolás/kifizetés külön feltétel.

Negatív scalp lehet racionális új hír után: két−110 ellenoldal rögzít kis veszteséget, de a második tét önmagában pozitívEV lehet és csökkentheti a rosszabbra változott kitettséget. A döntéshez az aktuális eloszlás számít, nem az eredeti tét visszanyerésének vágya.

Middle: Jets−2,5−110tét110 és Raiders+3,5−120tét120; feltételezett farok45/45%,exact3=10%; nettó−20,+200,−10→EV6,50. Halfmiddle: −2,5 és+3mindkettő−105tét105→−5,+100,−5→EV5,50. Reversemiddle: Knicks−5,5even100 és Bulls+5+110tét100, farok48/48%,exact5=4%→0,−100,+10→EV0,80. Utóbbi ritka nagy veszteségű, ezért a nyerési gyakoriság félrevezető.

A két láb végrehajtásának sorrendje releváns: a ritkább/eltűnő árat először megszerezni csökkentheti a második láb hiányának veszélyét, de nem szünteti meg. Különböző időszakokra szóló full-game/half-game ellenoldalak nem feltétlen hedge-ek; mindkettő veszthet. A „return on risk” a nettó kifizetési veszteséghez viszonyít, de a lekötött tőke, partnerkockázat és költség miatt nem helyettesíti az összes tőkekimutatást; tökéletes scalp nulla nevezőjénél különösen nem jól definiált önálló összehasonlító mutató.

## 11. fejezet — befejezés

A 4522 NFL-mérkőzésből számolt utolsószámjegy-táblák közös eloszlások. A semleges pálya miatti home/away szimmetrizálás felcserélhetőséget feltételez; favorit és total szerint kondicionálva más eredmény lehet. A 0–0 négyzet gyakorisága első negyedben 20,7%, félidőben 8,0%, harmadik negyed végén 4,5%, végén 2,3%. Az egyenlő kifizetésű átlag 8,9%, az 1:2:1:4 súlyú átlag 6,3%. EV összeadásához nem kell a negyedek függetlensége, a kockázathoz viszont kell a közös függőség. Empirikus 0,0% nem bizonyít lehetetlenséget. A 10–16. táblákat képen is ellenőriztem.

## 12. fejezet — Super Bowl propok

Pontos szerződés → releváns statisztika → matematika → ár-összevetés → finomítás. A legrövidebb TD over 1,5 és a „lesz-e 0/1 yardos TD” csak a nulla TD kezelésének egyezése mellett komplementer. Ha nulla TD void, a feltételes nevező P(TD≥1). Az 1528 meccses adatban 807 rövid TD-s, 14 TD nélküli, 707 egyéb meccs; 807/1514≈53,3%. 7209 TD közül 1078 rövid, körülbelül 15%.

Poisson összes TD N~Pois(λ), független rövid-TD jelölés p mellett P(rövid≥1 | N≥1)=(1−exp(−λp))/(1−exp(−λ)); az over esélye (exp(−λp)−exp(−λ))/(1−exp(−λ)). A 17. táblázat vizuálisan helyesen különíti el a nyers és a nullával csonkolt esélyt; λ=3 esetén over≈61,9%, fair −162. A Poisson feltételeit a ritka esemény jelleg önmagában nem igazolja: heterogén ráták, játékhelyzet és függőség számít. Az eredeti matematikai hibák javítását maga a szerző is jelzi.

A 2003–2006-os 53 blokkolt punt / 1024 meccs eseményráta, nem feltétlen legalább egy blokk valószínűsége. Poisson mellett utóbbi 1−exp(−53/1024)≈5,044%. A 95%-os esély fair oddsa −1900, nem pontosan a könyv −1880 közelítése. A Seattle „twice before” szövegéből kihagyott szó teljesen más szerződést eredményez: a piac neve önmagában kevés. A 2007-es időjárási/punt anekdoták nem mai paraméterek.

## 13–14. fejezet — poolok és NBA playoff

Poolban a díj megszerzésének esélye, ellenfelek választásai, mezőnyméret és pontozás a cél; ez eltér a meccsenkénti találati arány maximalizálásától. A college példa kívül esik az öt kért ligán. A 2006-os UConn régiógyőzelemre tett állítás történetileg gyanús, későbbi ellenőrzést igényel.

Független, minden meccsen 50/50 best-of-seven esetén P(hatmeccses sorozat)=P(hétmeccses sorozat). Ez nem általános NBA-törvény. A zigzag 231–211, az előző ATS-vereséggel szűrt 190–163 kis, kiválasztott történeti minta. Motivációs ATS-mintákban a szelekció, változó csapaterő és sérülés magyarázatát is vizsgálni kell; a „must win” nem oksági bizonyíték. A korabeli döntő 2–3–2 menetrendjét nem szabad mai modellre másolni.

A Dallas–Phoenix 2–2 példában az 5. meccs Dallas-esélye .75, a 6. meccsé az 5. eredményétől függően .483/.517, a 7.-é .73. Útvonalanként kell szorozni: Dallas hat meccsen .36225; hét meccsen .37741; Phoenix hat meccsen .12075; hét meccsen .13959. Összegük 1. A Dallas +200 hatmeccses ajánlat EV-je e becslések mellett .08675/tét, de ez feltételes modellérték, nem igazolt piaci előny.

## 15–16. fejezet — MLB részpiacok és total

Teljes meccs, öt inning, 4,5 inning, öt inning −0,5: külön kifizetési események. Az öt inning moneyline döntetlenjeit az odds összevetésekor feltételesen ki kell venni, a profiteloszlásból nem. A 19–21. táblázatot képen is ellenőriztem: nagy favoritok teljes meccses és F5 nyerési aránya eltér; a 4,5 inning a hazai csapatnak eggyel kevesebb támadást ad. A körülbelül 7,9 vagy 8 százalékpontos történeti korrekciók nem univerzális konstansok. Kezdő dobó várható hossza, bullpen, felállás és cserelehetőségek a releváns sportváltozók. A korabeli NL dobóütés modern szabályokhoz újraértékelendő.

A 22. táblázat 1998–2006-os totalgyakoriságokat mutat, ±0,5 sávokkal. Egész 7-es line-on 13,5%, összevontan 12,4% az exact 7; az összevont 8/9/10/11 tömeg 7,2/10,3/6,5/8,9%. A páratlan eredmények kiemelkedése kapcsolódik a döntetlen kizárásához és a walk-off befejezéshez. Egyszerű független Poisson modellből ezek nem automatikusan következnek. A 23. táblázat centkorrekciója azonos, 50%-os feltételes alap körül közelítés. Például push=.135 és over 7 fair −120 mellett over 6,5 esély=.135+.865×120/220=.606818, fair −154,33; nem a centek egyszerű összeadásával kapott −151. Over 7,5 esély .471818, fair +111,94. Teljes EV-hez p_win, p_loss, p_push szükséges.

## 17. fejezet — Triple Crown

A lóverseny kívül esik a célon, de a feltételes futures-árazás tanulsága fontos. „Bármely ló megnyeri mindhármat” eseménynél a Derby biztosan ad győztest, nem kell egy további önkényes Derby-szorzó; indulás előtt lovanként összegezni kell a teljes feltételes útvonalat. A későbbi kisebb mezőny és a megszerzett teljesítményinformáció indokolhat árváltozást.

A takeout korrekciójánál a könyv 18% esetén 1,18-cal oszt. Ha ez a pool tényleges elvonási aránya, a szokásos arányos korrekció ehelyett (1−.18)-szoros; a definíciót elsődleges forrással tisztázni kell. Külön kohorszok átlagos esélyeinek szorzata nem helyettesíti az egységes közös/ feltételes becslést. A gyűjtői jegyvásárlásra adott 5%-os korrekció spekulatív. Az „érdemes mindig Derby után várni” nem általános tétel.

A 2006-os Barbaro-példában egyenlő tét a Triple Crown +250 és a Preakness-vereség even money oldalán: Preakness-vereség nettó 0, következő vereség −2tét, mindkettő megnyerése +1,5tét. Feltételes nullszaldós küszöb 2/3,5=.57143. A határ és az eseményfa hasznos; nem garantált profit. A lovas táblákat (img188,190,192,194) vizuálisan elolvastam.

## 18. fejezet — futures

Tőkelekötés, alternatív lehetőségek, partner és időérték. Pozitív EV-jű future mellett lehet még jobb rövidebb meccsekbe történő újrabefektetés, de ennek árai és elérhetősége nem garantáltak. Az egymást kizáró csapatbajnoki esélyek összege 1 legyen. Pontos tiebreaker és elszámolási cím számít: divíziógyőzelem, wildcard és egyszerű playoff-részvétel külön esemény.

A 2005-ös MLB táblázatnál Yankees .294 wildcard és .259 divízió, Cleveland .369 és .028, Oakland .104 és .287; a jobb csapatnak kisebb lehet a wildcard-only esélye. Ez a régi playoffformátum szemléltetése. Astros kétmeccses hátrány két hátralévő meccsnél: közvetlen rivális elleni két 50%-os győzelem és régi 50%-os playoff .125; független két .55 saját győzelem, két .45 rivális vereség és playoff ≈.03063. Közös eredmények függősége lényeges. A modern MLB tiebreaker nem automatikusan extra mérkőzés.

## 19–20. fejezet — elemzés és végrehajtás

Saját felkészülés a piacnyitás előtt; változó hatások; minőségi sportmetrikák; az ellenfelek, tempó és lehetőségek kontextusa. Pitcher W/L nem izolált dobótudás. A QB futott yardjai önmagukban nem mérik a passzjáték és first down teljes hatását. Régi működő angle beárazódhat. Az Oakland 5–12 rekord 16 meccses évre belső számtani ellentmondás. Motivációs anekdoták utólagos történetek, nem önmagukban modellbizonyítékok. −110 mellett 52% még nem nullszaldó.

Ár-összevetés csak azonos szerződés mellett; a centkülönbség nem azonos százalékponttal. Félpont-vásárlásnál a tényleges küszöbtömeg számít. Korrelált parlay csak saját közös valószínűséggel és tényleges kifizetéssel értékelhető. A fogadás ideje, limitje és a várható árváltozás összefügg; a kézi 1–5 dolláros használatnál főleg az elérhetőség és késés számít. A szerző által említett cover/evasion nem projektkövetelmény. Veszteségüldözés és eredmény utáni önigazolás helyett a döntéskori információt kell megőrizni.

## 21. fejezet — tipsterek és szelekció

Teljes történet, mintanagyság, kiadási idő, ténylegesen elérhető ár, díj és végrehajtási késés együtt értékelendő. Kiadási ár szerinti teljesítmény nem azonos a követő profitjával. Sok tipster és modell közül utólag kiválasztani a nyertest szelekciós torzítás. Évkihagyás, eltérő csomagok, rövidített szezon és a nap legjobb árával történő utólagos grading hamisíthatja a képet.

Binomiális CDF: F(165;300,11/21)≈.833 nem annak utólagos valószínűsége, hogy jó a tipster. W≥165 egyoldali próbához 1−F(164) szükséges, nem 1−F(165). Azonos, független próbák feltételei sem automatikusak. A 54%-os tipster mintaérzékenységét ábrázoló képtábla ugyanezt a CDF-megközelítést használja.

Képi hibák: img231b rosszul címkézett második oszlopa valójában a push nélküli eloszlást ábrázolja, nem a −12,5 kimenetet. .55×.994=.5467, .45×.994=.4473, nem a táblában .5465/.4475; img231c ezt a kis eltérést továbbviszi. Falcons példában .55×.898=.4939 helyesen kerekül49,4%-ra. A hiba kicsi, de automatizált számításhoz nem másolható.

## 22. fejezet — beágyazott fogadások

Promóció áruértéke és feltételes jutalma különíthető el: 25% eséllyel 3 dollár jutalom várható értéke .75. Ez nem jelenti, hogy a fogyasztó számára a Big Mac készpénzzel egyenértékű. A „100 pontnál több” és „legalább100” esemény szövegét a konkrét szerződésből kell értelmezni. Bútor-visszatérítésnél a számla alapján tényleges visszatérítés, a feltétel, a behajthatóság és a hasznosság számít.

**Lényeges árazási hiba a Super Bowl-jegy példában.** A leírt feltétel szerint a 700 dolláros névértéket csak akkor kell megfizetni, ha a csapat bejut. Emiatt a kockázatsemleges várható nettó kifizetés p×(utcai ár−névérték), nem p×utcai ár−névérték. A példában .57×(2500−700)=1026 dollár, nem725. Diszkont, díj, értékesíthetőség és az utcai ár bizonytalansága külön korrekció. A könyv „net present value” megnevezése explicit idődiszkontot sem tartalmaz. Ha a névérték minden állapotban előre fizetendő volna, más szerződésről lenne szó.

A fantasy-draft/prop összefüggés valódi módszertani kapcsolat, de az említett weboldalak és jogi állítások korabeliek.

## 23–24. fejezet, glosszárium és záró részek

Fórumok, helyi újságírás és sportmédia adhatnak időben releváns információt, de pletyka, reklám és utólagos oksági magyarázat keveredik velük. Abszolút „biztos nyer” kijelentés nem valószínűségi elemzés. Személyes kapcsolat befolyásolhatja a sajtó értékelését; QB playoff W/L nem tisztán egyéni teljesítmény. Magánéleti eseményből automatikus teljesítménykorrekció nem következik. A felsorolt honlapok és újságírók listája történeti, nem aktuális ajánlás.

A profi csoport interjújának átvehető tanulsága: kész döntési szabályok, aktuális kitettség, időben egyeztetett végrehajtás és duplikáció elleni kontroll. Tíz párhuzamos ember néha 40k helyett80k tétet tett fel: az elküldött tippek és a ténylegesen megtett tétek elkülönítése lényeges. A 2–3% heti hozam személyes állítás, nem igazolt várható rendszerhozam. A limitelés és árkövetés tapasztalatai korabeliek. Az interjú veszélyes vezetési/parkolási történetei nem követendő eljárások.

A teljes glosszárium, köszönetnyilvánítás és kapcsolati rész elolvasva. A 2008-as kiadás a záró megjegyzés szerint kisebb javításokat tartalmaz, új anyagot nem. A glosszárium „mid-market” amerikai oddsok számtani közepét használja; ez nem általánosan pontos fair valószínűség. A scalp nulla kockázata matematikai kifizetésazonosság feltétele, nem teljes operatív garancia.

## Vizuális ellenőrzés és státusz

A teljes 393816 karakter és mind a33 EPUB-egység elolvasva. Az összes58 beágyazott kép megtekintve:57 img-fájl15 olvasható kontaktlapon és a borító külön. Az img51b-ben 53%-os sor nyereménye79500 és profit1950 mellett veszteség77550 kell legyen, a kép77500-at ír; az összegsor151800 viszont a helyes értéket követi. Az img68b utolsó sorában Bulls +4,5 szerepel, holott a szövegben +5 és a legfeljebb4 pontos Knicks-győzelem esete értendő. A 17. Poisson-táblázat szerkezete és nullával korrigált esélyei rendben; a többi adat tételes adatbázis-reprodukciója nem történt meg.

Ez kritikus olvasási jegyzet, nem kész rendszerterv és nem igazolt profitígéret. A korábbi szakaszok „kép még ellenőrzendő” megjegyzéseit ez a teljes képi ellenőrzés felülírja. Az online aktualizálás és a történeti tényállítások elsődleges forrású ellenőrzése a teljes könyvcsomag után következik.
