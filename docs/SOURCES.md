# Online forrásjegyzék

Ellenőrizve: 2026-09-27. Ezek a projekt előzetes online kutatásának forrásai;
egyik sem helyettesíti a felhasználó könyveit. API-kat még nem kötöttünk be,
előfizetést nem vásároltunk. A dokumentált szolgáltatás nem jelent ellenőrzött
hozzáférést, historikus lefedettséget vagy garantált adatminőséget.

| Forrás | Ellenőrzött információ | Tervezési következmény |
|---|---|---|
| [Polymarket market discovery](https://docs.polymarket.com/market-data/discover-markets) | Piacok felderítésének hivatalos dokumentációja | Discovery adapter, azonosítók és szabályok mentése; nem maga a prediktív modell |
| [Polymarket order books](https://docs.polymarket.com/market-data/prices-order-books) | Outcome tokenekhez order book, minimum méret, tick size és időbélyeg tartozik | Ask oldali, méretfüggő végrehajtási ár; nem midpoint vagy last trade |
| [Polymarket fees](https://docs.polymarket.com/trading/fees) | Egyes piacokon taker díj van; a dokumentáció piaci díjparaméterek lekérésére utal | Díjat futáskor ellenőrizni; nem univerzális nulla díj, nem hardcoded örök tarifa |
| [Polymarket resolution](https://docs.polymarket.com/concepts/resolution) | A kifizetés a piac elszámolásától függ | Pontos outcome, szabály és eseményillesztés; azonos sportnév nem elég |
| [Stake Sports Data API](https://docs-odds-data.stake.com/) | Hivatalos sportodds-adat API dokumentáció, sport és menetrend végpontokkal | Elsődleges vizsgálandó Stake-adapter; hitelesítés, kvóta és hozzáférés még tesztelendő |
| [Stake affiliate API](https://docs.stake.com/) | Külön dokumentált, autentikált affiliate API | Nem keverjük össze a sportodds feedjével; session cookie bekérése nem szükséges a jelenlegi munkához |
| [Stake sportsbook rules](https://stake.com/policies/sportsbook) | Piacokra vonatkozó elszámolási szabályzat | A kiválasztott liga/market konkrét szabályait integrációkor verziózni kell |
| [BALLDONTLIE API katalógus](https://www.balldontlie.io/docs) | NBA, MLB, NHL, NFL és WNBA dokumentáció külön elérhető | Egységes szolgáltató jelölt; endpoint, csomag és adatlefedettség ligánként mérendő |
| [BALLDONTLIE NBA](https://docs.balldontlie.io/) | A dokumentáció API-kulcsot ír elő | A végpontokat csak megfelelő kulccsal és előfizetéssel lehet validálni |
| [The Odds API v4](https://the-odds-api.com/liveapi/guides/v4/) | Aktuális és historikus odds végpontok, kvóták, historikus snapshotok | Piaci benchmark/adattár jelölt; Stake-lefedettséget nem feltételezünk |
| [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html) | Kalibráció, reliability diagram, elkülönített kalibrációs adatok; Brier nem tisztán kalibrációs mutató | Külön időrendi kalibráció és több mérőszám |
| [scikit-learn time-series example](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html) | Véletlen split túl optimista eredményt adhat időfüggő adatoknál | Időrendi tanítás/teszt, ismertségi idő és időbeli rés |
| [David Aldous: Kelly criterion](https://www.stat.berkeley.edu/~aldous/Real_World/kelly.html) | Logaritmikus tőkenövekedés, becsült valószínűség és kockázat kapcsolata | Rész-Kelly és bizonytalansági stresszvizsgálat; a Kelly-képlet nem termel önmagában edge-et |

## Forrásból származó tény vs saját döntés

Az odds kizárása a sportmodellből a felhasználó kérése és saját architektúra-
döntés. A negyed Kelly, 1%-os tétlimit, 120 másodperces példa-frissességi
határ és az egy tipp/esemény szintén előzetes tervezési beállítás, nem a
fenti dokumentációkból igazolt optimális stratégia. A könyvek olvasása és
az empirikus visszatesztek módosíthatják őket.

## Integráció előtt ténylegesen mérendő

Hitelesítés és csomagjogosultság; ligánként historikus szezonok és aktuális
adatok; kezdők/sérülések ismertségi időpontja; API-frissülés és kvóta;
Stake/Polymarket piacok pontos egyezése; minimum végrehajtható méret;
teljes adat- és infrastruktúraköltség. Nem állítjuk, hogy bármely szolgáltató
minden szükséges historikus lineup/sérülés-információt biztosít.
