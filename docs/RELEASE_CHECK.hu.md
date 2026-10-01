# Kiadásellenőrzés — 2026-09-27

58 Python/API teszt és 5 alteszt sikeres; 4 frontend DOM-funkcióteszt sikeres. A futó HTTP-oldal, statikus JavaScript, egészségjelzés és dashboard API ellenőrzött. A tesztkimenetek: `data/test-python-final.txt`, `data/test-web-final.txt`. Egy Starlette/httpx elavulási figyelmeztetés van, teszthiba nincs.

A vizuális böngészős ellenőrzés nem történt meg: a Computer Use hozzáférési engedélyek függőben maradtak. A DOM-teszt nem képernyőképes ellenőrzés.

## Modellértékelés

Alacsonyabb Brier jobb. A konstans és Elo benchmark azonos tesztmeccseken szerepel. Ez retrospektív fejlesztési mérés, nem fogadási hozam és nem új, érintetlen bizonyíték a fejlesztői iterációk után.

| Liga | Használt előzmény | Tesztmeccs | Modell Brier | Konstans Brier | Elo Brier |
|---|---:|---:|---:|---:|---:|
| NBA | 5178 | 720 | 0.2003 | 0.2472 | 0.1974 |
| MLB | 9737 | 1598 | 0.2468 | 0.2491 | 0.2511 |
| NHL | 5498 | 768 | 0.2457 | 0.2499 | 0.2464 |
| NFL | 1644 | 239 | 0.2276 | 0.2496 | 0.2229 |
| WNBA | 1119 | 199 | 0.2030 | 0.2463 | 0.1977 |

NBA, NFL és WNBA esetén az Elo a végső fejlesztési teszten jobb maradt; ezt a kiadás nem rejti el. A keveréksúlyokat a korábbi, külön kiválasztási szakasz határozza meg, nem a tesztmutatót erőltető kézi súlyválasztás. A bizonytalanság a tippek részleteiben is szerepel.

## Futó szolgáltatás pillanatképe

A következő adatok időhöz kötöttek, nem tartós ajánlásszámok. A böngésző mindig az aktuális API-adatot használja.

```json
{
  "http_home": 200,
  "javascript": 200,
  "engine_error": null,
  "engine_running": false,
  "generated_at": "2026-09-27T19:14:37.709721+00:00",
  "version": 19,
  "active_tips": 18,
  "funded_tips": 10,
  "proposed_stake": 10.0,
  "unique_games": 9,
  "unique_families": 18,
  "live_games_excluded": true,
  "within_48_hours": true,
  "stakes_valid": true,
  "actual_bets": 0,
  "source_errors": [
    {
      "source": "NHL/2023/box",
      "error": "Nincs ellenőrzött meccsazonosítóval illeszthető sor"
    }
  ]
}
```

A jelenlegi ismert forráshiány: a 2023-as NHL box score adatokban nincs illeszthető meccsazonosító. Ezek nem kerültek a sportstatisztikai bemenetbe. A meccseredményeket külön, hivatalos NHL forrás szolgáltatja. A többi rendelkezésre álló idény mutatói használhatók, a hiány priorra húz vissza.

Nincs automatikusan elhelyezett fogadás, nincs Telegram-küldés, és a tényleges fogadásnaplót nem töltöttük fel tesztfogadásokkal. A tesztek elkülönített ideiglenes adatbázisokat használtak.
