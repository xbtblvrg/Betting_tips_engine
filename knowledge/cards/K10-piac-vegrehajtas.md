# K10 — Bookmaker, tőzsde és végrehajtható ár

Típus: piaci ismeret és alkalmazási korlát. Forrás: [B15 PDF44–112 és197–230](../reading_v2/B15-notes.hu.md), [B19 4–21.fejezet](../reading_v2/B19-notes.hu.md), [B24 19–24.fejezet](../reading_v2/B24-notes.hu.md), [B25 17–21.fejezet](../reading_v2/B25-notes.hu.md), [B26 teljes könyv](../reading_v2/B26-notes.hu.md). Állapot: történeti elvek; Stake/Polymarket mai API, díj, jogosultság és elszámolás külön ellenőrzendő.

## K10.1 — Bookmaker nézőpont

A bookmaker becsült sportesélyen túl keresletet, ügyfél-információt, likviditást, limiteket, versenyt és kitettséget áraz. Nem szükségképpen egyenlő pénzt gyűjt mindkét oldalra. Több másoló book ára nem több független vélemény. Favorit–outsider és home–away torzítás iránya sport/piac/korszakfüggő; a kisebb várható veszteség nem automatikusan pozitív EV.

Felhasználás: a saját esély és a piaci eltérés oka vizsgálandó. Lehet modellhiba, hiányzó sérülés, elavult quote, eltérő settlement vagy valódi árkülönbség. Nem fogadási jel önmagában az, hogy sok pénz megy az egyik oldalra. Teszt: az eltérés forrásának naplózása és későbbi szegmensenkénti eredménye.

## K10.2 — Quote nem egyenlő végrehajtással

Bid, ask, utolsó kötés és mid eltérő ár. A kívánt1–5USDtétre elérhető mennyiség és díj számít, nem csupán a legszebb képernyőár. Net-market-profit jutalékot nem szabad minden nyertes lábra külön felszámítani, ha a szabály nettósít. Spread, slippage, minimum order, kerekítés és stale quote apró tétnél is az edge jelentős része lehet.

Bináris1USDkifizetésű, díjmentes YES token ask a mellett egy darab EV=p−a, a befektetett pénz arányos várható hozama p/a−1. Ez feltételes matematikai példa, nem a Polymarket aktuális díjspecifikációja. Tőzsdei oddsra vagy tokenárra külön adapter kell. Teszt: tényleges buy/sell oldal, mélység, tick és payoff; a legutóbbi trade nem végrehajtható ajánlat bizonyítéka.

## K10.3 — Arbitrázs, hedge és élő késés

A minden kimenetelben nemnegatív cashflow és legalább egyikben pozitív eredmény biztosított végrehajtással arbitrázs. A későbbi, még ismeretlen jó árra számító hedge vagy a középső eredményen nyerő middle nem az. „Zöld képernyő” csak elszámolási és jutalékfeltételekkel igaz. Stop megbízás nem garantált ár; piacsuspension és gap miatt a hedge elmaradhat.

Élő sportnál venuefeed, stream és UI eltérő késésű. A sportszituáció, piac-idő, quote-idő és befogadás ideje külön. A felhasználó manuálisan fogad, ezért egy rövid életű live jel ténylegesen elérhető értéke eltérhet az automata könyvpéldától. Először pregame modellek/naplózás vizsgálandók; ez saját gyakorlati következtetés, nem könyvi tétel.
