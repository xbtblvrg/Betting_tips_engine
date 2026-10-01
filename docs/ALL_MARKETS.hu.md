# Minden liga és fogadástípus: megvalósítási állapot

2026-09-29. Cél: az NBA, MLB, NHL, NFL, WNBA és a Stake-en elérhető NCAA-sportágak pontos kínálatából saját, sportadatokra épülő modellekkel kiválasztott, 1–5 USD összegű ajánlások. A teljes típuslefedettség még nincs kész. A katalógus felismerése, a modell megléte, a validáció és az éles ajánlási jogosultság külön állapot.

## Elkészült bővítés

- `period_data.py`: NBA/WNBA megfigyelt negyederedmények; NFL hivatalos nflverse play-by-play negyedvég/meccsvég; NHL gólesemény-sorozat és hivatalos ligaeredmény. Ellenőrzött meccsazonosító, csapatok, végösszeg és kezdési idő. Hosszabbítás és szétlövés külön. A régi kosárlabda formátum pozíció szerint indexelt periódusait csak teljes, végösszegre egyező tömbből fogadjuk el.
- 31 külön szakaszmodell: NBA 9, WNBA 9, NFL 9, NHL 4. A meglévő öt meccsmodell és két MLB inning-modell megmarad. Nem mind a 31 modell jogosult minden piaccsaládra.
- Feltételes közös eredményeloszlás: korábbi, hasonló sportállapotú meccsek tényleges hazai/vendég perióduseredményei. A közös ponteloszlás megmarad; a valószínűségek nem a bookmaker áraiból származnak. Ez ellenőrzött összehasonlító modell, nem bizonyítottan a legfejlettebb vagy nyereséges módszer.
- 64/128/256/512 szomszédos recept közül időben későbbi belső mintán választunk; a végső teszt ettől elkülönül. A publikálás családonkénti Brier- és kalibrációs kaput igényel. Újabb csere heti blokkokkal képzett páros konfidenciaintervallum alapján történhet; azonos adaton újratanítás nem automatikus javulás.
- Pontos Stake-scope adapter: negyed, félidő, NHL periódus, rendes játékidő, külön második félidő/negyedik negyed hosszabbítással. Winner/Draw No Bet, 1x2, Handicap, Total, csapattotal, Odd/Even, Both Teams to Score és Double Chance csak a konkrét validált családban. Negyedpontos Asian vonalakat még nem árazunk egyetlen push-szerződésként.
- A +0.5 hendikep és az ugyanarra az oldalra vonatkozó Double Chance azonos kifizetésű megfelelője nem új független fogadás. Eredeti szelvényazonosítók és összegek nem változnak.
- Az összes visszaadott meccsre kiterjedő kutatási katalógus az összes visszaadott normál és Swish-szekcióról, pontos nevekkel, kimenetekkel, csoportazonosítókkal, forráslenyomatokkal. Ez különbözik a friss 24/48 órás ajánlási ablaktól. A Main/Leaders/Player Props/Team-Match/Quick Picks/Half/Quarters/Specials navigációs fülek, nem önmagukban modellek. Nem állítjuk, hogy a publikus feed minden webes fület vagy ajánlatot lefed.
- NBA/WNBA/NHL/NFL/MLB játékos-meccsnaplók külön Parquet-adattárban. MLB: teljes adott szezonbeli játékospool, ütő- és dobónapló külön. Hiányzó statisztika nem nulla, negatív yardérték nem levágott nulla. Kizárólag meccsazonosítóval és csapategyezéssel kapcsolt rekordok.
- A kiadás ellenőrzésekor 13 765 külön meccs szakaszeredménye és 706 273 játékos-meccsnapló áll rendelkezésre. Éles ellenőrzés: 941 modellezett kimenetel; a bővítés óta 18 új kiadás; a mai számláló 31/15. A számok időbélyegzett pillanatfelvételt jelentenek, nem állandó kínálatot vagy nyereségbizonyítékot.
- Külön munkafolyamat: 15 percenkénti katalógusfrissítés, napi szakaszmodell-újratanítás és játékosnapló-frissítés; tartós futásnapló és hiba utáni újrapróbálás. A piaci keresés továbbra is 60 másodperces.

## Adatjavítások

Az NFL negyedik negyed vége sokszor `END GAME`, nem `quarter_end=1`. A feldolgozó mindkettőt kezeli, és megköveteli a hivatalos végeredmény egyezését. Az NHL egyik linescore-exportjában a `has_shootout` mező minden sorban hamis volt. Ezt a liga saját `gameOutcome.lastPeriodType` adatával oldottuk fel; a szétlövéshez hozzáadott egy gól nem kerül a rendes játékidőbe. A ki nem adott, korábbi NHL tanulmány külön auditarchívumban marad.

Az NHL 2023-as játékos-exportból hiányzik a `game_id`. Tilos sorrendből csapat/meccs-azonosságot találgatni. Az adatjavítás a liga meccsenkénti boxscore-végpontját használja, mindkét csapat, eredmény, kezdés és játékosazonosító ellenőrzésével.

## Fennmaradó végrehajtandó munka

| Piaccsalád | Konkrét függőség |
|---|---|
| Player points, rebounds, assists, threes, NHL lövések/kapus, NFL yard/TD, MLB ütő/dobó props | A naplókra tanított és külön időszakon validált játékosmodell; várható játékidő/szerep és aktuális keretazonosság; pontos piaci és részvételi szabály; játékosstatisztikából történő elszámolás. A naplók elkészülése önmagában nem éles prop-támogatás. |
| First/last goal, first scorer, TD/field-goal sorrend, időpont, futam/sorozat | Játékmenet-eseményeket előrejelző modell és sorrendi célváltozók. |
| Highest scoring half/quarter/inning, halftime/fulltime, kombinált Total | Teljes meccspályák közös eloszlása; nem külön becsült valószínűségek szorzata. |
| Same Game Multi | A konkrét összeállítás tényleges Stake-ára, azonosított lábak, függőségek közös modellje, SGM-void szabály. A jelenlegi publikus feedhez nincs ilyen integráció. Az egyedi oddsok szorzata nem helyettesítő ajánlat. |
| Leaders és különleges/új piacok | A pontos elszámolási esemény, holtverseny és játékoscsere kezelése; ismeretlen séma külön kutatási állapot. |
| Live/in-play | Időbélyegzett élő állapot, hátralévő játékidőre kondicionált modell, késleltetés és friss élő ár. A pregame modell nem nevezhető át élő modellnek. |
| MLS | Önálló liga-, játékos-, esemény- és elszámolási modell; jelenleg csak leltár. |
| Teljes nettóprofit-igazolás | Valóban elérhető árakon befagyasztott döntések eredményei, korreláció, költségek, eredménysor és bizonytalanság. A találati arány vagy Brier-javulás önmagában nem bizonyít profitot. |

Minden hiányt fenn kell tartani a nyilvántartásban, amíg a megvalósítás és ellenőrzés ténylegesen elkészül. A napi 15 külön ajánlás célérték; nem engedély hamis oddsra, gyenge teszt felülbírálására vagy azonos kifizetésű fogadások ismételt kiadására.

## Források és módszertani alap

- [Stake hivatalos sportszabályzat](https://stake.com/policies/sportsbook): hatókör, részvétel, hosszabbítás, periódus, push és SGM-void szabályok. Az adapter a konkrét piacímet és liga-szabályt együtt kezeli.
- [SportsDataverse elsődleges adatkiadások](https://github.com/sportsdataverse/sportsdataverse-data): NBA/WNBA eredmények és játékos-boxscore, NHL feldolgozott megfigyelések; az exportok adathibái külön ellenőrzendők.
- [nflverse adatok](https://github.com/nflverse/nflverse-data): play-by-play és az új `stats_player` heti játékosstatisztikák.
- Hivatalos NHL API: `api-web.nhle.com/v1/club-schedule-season` és `gamecenter/{gameId}/boxscore`.
- Hivatalos MLB Stats API: szezonbeli játékospool és játékosonkénti hitting/pitching gameLog.
- Helyi tudáskártyák: K03 időbeliség, K04 MLB, K05 kosárlabda és játékidő, K06 NFL diszkrét pontok, K07 NHL/kapus/periódus, K08 validáció, K09 tét és korreláció, K10 piac/elszámolás. A forráshivatkozás nem egy tipp nyerési bizonyítéka.

## Ellenőrzési útvonal

`független eredményadat → scope-modell → pontos Stake-piac és kimenet → újraszámolt p/push/EV → EvidenceGuard → 1–5 USD tét → lezárt döntés → web → ugyanabban a scope-ban megfigyelt végeredmény`.

Az `EvidenceGuard` determinisztikus számítási és forrásellenőrzés. Nem külön LLM-jóváhagyás, és nem bizonyítja, hogy a tipp nyer.

## Hivatalos API és NCAA-bővítés — 2026-09-29.10

Részletes, ellenőrizhető kiadás: [STAKE_API_NCAA.hu.md](STAKE_API_NCAA.hu.md). Dinamikus NCAA-felderítés minden engedélyezett valódi sportág USA kategóriájában; menetrend és fixture-lista összefésülése; mindkét fixture/odds-válasz teljes archiválása. Három külön tanított college meccsmodell: NCAAF, NCAAM, NCAAW. Családszintű jóváhagyás kötelező, a pro modellek nem helyettesítő modellek. Az újonnan megjelenő egyéb NCAA-sportág leltárt és nyitott megvalósítási feladatot kap.
