# Autonóm Polymarket Multi-Option & Futures Quant Engine — Mesterterv (Master Blueprint)

**Mentés ideje:** 2026-11-09  
**Státusz:** Tervezési és Stratégiai Konzultációs Fázis (a meglévő napi sportfogadási motor érintetlenül fut tovább)

---

## 1. Alapkoncepció és Első Elvekből Levezetett Optimalizációs Cél (Zero-Anchoring Invariáns)

A rendszer alapvető matematikai és stratégiai küldetése: **olyan pozíció- és portfólió-struktúrák autonóm felépítése és menedzselése, amelyek a lehető legnagyobb bizonyossággal kizárják a befektetett tőke elvesztését, miközben a hozam/kockázat (Risk/Reward) arányt a lehető legmagasabb szinten maximalizálják.**

### 1.1. A Három Kötöttség- és Anchoring-Mentes Alaptörvény

1. **Tőkemegőrzés + Maximális Risk/Reward Együttes Optimalizálása (Anti-Overinsurance & Anti-Ruin):**
   - A rendszernek két szélsőséges hibát kell egyszerre elkerülnie:
     - **A vak spekulációt** (amikor túl szűk lefedettség miatt magas a tőkevesztés kockázata), és
     - **A hozamgyilkos túlbiztosítást** (amikor a kosár olyan kimeneteleket is drágán megvesz, amelyek piaci ára meghaladja a valós fundamentális esélyüket, ezzel a kifizetési plafont a lekötött tőkéhez képest elfogadhatatlanul alacsonyra nyomva).
   - A matematikai célfüggvény minden $B$ tőkeallokációra:
     $$\max_{\mathbf{q}} \quad \text{Risk/Reward}(\mathbf{q}) = \frac{\mathbb{E}[\text{Nettó Profit}(\mathbf{q})]}{\text{Lefedetlen Valós Kockázat}(\mathbf{q})}$$
     azon peremfeltétel mellett, hogy a valós, fundamentálisan életképes kimenetelek halmazán a tőkevesztés valószínűsége ($P(\text{PnL} < 0)$) a lehető legközelebb szoruljon a nullához, miközben a nyertes ágakon a nettó hozam ($\frac{\text{Payout} - B}{B}$) maximális marad.

2. **Teljes Módszertani Szabadság (Nulla Sablon-Kényszer / Zero Anchoring):**
   - Tilos a rendszert előre rögzített darabszámú kosarakhoz, fix százalékos sémákhoz, előre kiválasztott piactípusokhoz vagy statikus receptekhez kötni.
   - A rendszer minden egyes vizsgált piacon **első elvekből (first-principles)**, kizárólag a kifizetési mátrixból, a könyvmélységből, a lejárati időből és a valós idejű fundamentális eloszlásból vezeti le, hogy az adott helyzetben melyik struktúra (vagy azok milyen dinamikus kombinációja) adja a legmagasabb Risk/Reward arányt a legkisebb tőkekockázat mellett.

3. **Szigorú Élő-Adat Invariáns (Zero Memory Rule):**
   - Soha, semmilyen döntéshez nem használható a nyelvi modell belső emlékezete vagy korábbi feltételezése. Minden egyes tényállítást és valószínűségi súlyt kizárólag az adott pillanatban lekérdezett, levágás (`truncation`) nélkül ellenőrzött élő API- és könyvadatokból kell származtatni.

---

## 2. Technikai és Szabályrendszeri Szétválasztás

| Dimenzió | Meglévő Napi Sportfogadási Engine (`betting_engine`) | Új Autonóm Polymarket Futures Engine (`pm_futures_engine`) |
| :--- | :--- | :--- |
| **Fő feladat** | Napi 100+ rövid lejáratú sporttipp (Stake manuális + Tier 4 PM) | Hosszabb lejáratú, többopciós (`NegRisk` / Futures) kosarak (`Core + Shield`) |
| **Időhorizont** | 0 – 72 óra | 2 héttől – 8 hónapig (3-lépcsős lejárati létra) |
| **Tétkorlátok** | Szigorú `$0.50 – $8.00` Frakcionális Kelly | **Nincs `$8` plafon**; kosárszintű tőkeallokáció (pl. `$1,000` alap + heti `+$500` DCA) |
| **Végrehajtási mód** | Stake-en manuális (`assumed manual`), szigorú Tier 1–4 kapukkal | **100% Autonóm CLOB végrehajtás** (`Maker` limit + `Taker` azonnali + automata `Order Slicer`) |
| **Adatbázis** | `data/engine.sqlite3` (100% érintetlen) | Saját, különálló SQLite adatbázis: `data/pm_futures_ledger.sqlite3` |
| **Szabályzat (`Governance`)** | `AGENTS.md` 1–10. Parancsolat | Külön projektmappa (`/Users/belavarga/Polymarket_Futures_Quant`) **VAGY** külön `11. Charter` az `AGENTS.md`-ben és `GEMINI.md`-ben |

---

## 3. Matematikai Modell: A 2-Lépcsős Páncélozott Kosár (`Core + Shield`)

Egy $N$ egymást kizáró opcióból álló `NegRisk` piacon (ahol pontosan 1 opció fizet `$1.00`-t lejáratkor, a többi `$0.00`-t) a teljes $B$ tőkét (pl. `$1,000`) két halmazra osztjuk:

1. **Pajzs Lábak (`Shield Tier` — 0% Veszteség / Tőkemegőrzés):**
   - Ide kerülnek azok a favoritok vagy középcsapatok, amelyek drágák vagy sérülésekkel terheltek (pl. OKC, 76ers), de nem hagyhatók teljesen fedezetlenül.
   - Minden `Shield` csapatból pontosan $S_{\text{shield}} = B$ darab (`1,000.00` db) `YES` share-t veszünk:
     $$\text{Költség}_{\text{shield}} = B \cdot \sum_{j \in \text{Shield}} p_j$$
   - **Eredmény, ha bármelyik `Shield` opció nyer:** A kifizetés pontosan $B$ (`$1,000.00`), vagyis a többi 12 vesztes fogadás levonása után is **`$0.00` a veszteség** (100% tőkevisszatérítés).

2. **Fő Érték Lábak (`Core Value Tier` — Garantált Pozitív Profit):**
   - Ide kerülnek az egészséges, fundamentálisan alulárazott esélyesek.
   - A fennmaradó tőkét ($B - \text{Költség}_{\text{shield}}$) úgy osztjuk el közöttük, hogy minden `Core` csapatból azonos $S_{\text{core}} > B$ darab share-t kapjunk:
     $$S_{\text{core}} = B \cdot \frac{1 - \sum_{j \in \text{Shield}} p_j}{\sum_{i \in \text{Core}} p_i}$$
   - **Eredmény, ha bármelyik `Core` opció nyer:** A kifizetés $S_{\text{core}}$ dollár, a tiszta nettó profit pedig:
     $$\text{Nettó Profit} = S_{\text{core}} - B = B \cdot \frac{1 - \sum_{k \in \text{Core} \cup \text{Shield}} p_k}{\sum_{i \in \text{Core}} p_i} > 0$$

---

## 4. Konkrét Referencia-Blueprint: NBA 2027 Champion (`$1,000` Alap + Heti `+$500` DCA)

### 4.1. Fundamentális Aszimmetria (ESPN + Kalshi + PM összevetés)
- **OKC Thunder (`0.150 Bid / 0.160 Ask`) súlyosan túlértékelt:** Jalen Williams (szárkapocscsont-törés + bokaszalag-rekonstrukció) és Cason Wallace (váll labrum műtét) gyakorlatilag a teljes szezonra kidőltek, Chet Holmgren (patella-ín szakadás) pedig 8–10+ hétig harcképtelen. Ezért az OKC-t nem `Core`, hanem `Shield` lábként kezeljük (vagy heti DCA során fokozatosan leépítjük).
- **Alulárazott `Core` csapatok:**
  - **Boston Celtics (`0.060 Bid / 0.070 Ask`):** 8-2 mérleg, `+10.7` NetDiff, #1 védelem (99.8 kapott pont), 0 sérült.
  - **Minnesota Timberwolves (`0.060 Bid / 0.063 Ask`):** 8-3 mérleg, liga #1 `+11.1` NetDiff, Edwards januárban 100%-osan visszatér.
  - **Houston Rockets (`0.047 Bid / 0.048 Ask`):** 7-3 mérleg, `+9.0` NetDiff, Durant + Sengun egészséges.
  - **Golden State Warriors (`0.021 Bid / 0.022 Ask`):** Kalshin `0.040 Bid` (dupla ár!), a PM Nyugati Főcsoport piacán `0.050/0.069`.
  - **San Antonio Spurs (`0.210 Bid / 0.220 Ask`):** 8-2 mérleg, `+10.3` NetDiff, Wembanyama + De'Aaron Fox.

### 4.2. A 13-Csapatos Páncélozott Kosár (`$1,000` Kezdő Allokáció)

| Réteg | Csapat | PM `Bid / Ask` | Tick méret | Vételi Stratégia | Vásárolt `YES` Share | Befektetett Tőke (`$`) | Kifizetés ha nyer (`$`) | Nettó Profit (minden más levonása után) |
| :--- | :--- | :---: | :---: | :--- | :---: | :---: | :---: | :---: |
| **CORE #1** | **San Antonio Spurs** | `0.210 / 0.220` | `0.01` (`1¢`) | Maker Limit (`0.210`–`0.215`) | `1,154.14 db` | `$242.37` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #2** | **New York Knicks** | `0.070 / 0.080` | `0.01` (`1¢`) | Maker Limit (`0.070`–`0.075`) | `1,154.14 db` | `$80.79` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #3** | **Boston Celtics** | `0.060 / 0.070` | `0.01` (`1¢`) | Maker Limit (`0.060`–`0.065`) | `1,154.14 db` | `$69.25` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #4** | **Minnesota Timberwolves** | `0.060 / 0.063` | `0.001` (`0.1¢`) | Azonnali Ask / Slicing | `1,154.14 db` | `$69.25` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #5** | **Houston Rockets** | `0.047 / 0.048` | `0.001` (`0.1¢`) | Azonnali Ask (`0.048`) | `1,154.14 db` | `$54.24` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #6** | **Los Angeles Lakers** | `0.040 / 0.042` | `0.001` (`0.1¢`) | Azonnali Ask (`0.042`) | `1,154.14 db` | `$46.17` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #7** | **Denver Nuggets** | `0.024 / 0.025` | `0.001` (`0.1¢`) | Azonnali Ask (`0.025`) | `1,154.14 db` | `$27.70` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **CORE #8** | **Golden State Warriors** | `0.021 / 0.022` | `0.001` (`0.1¢`) | Azonnali Ask (`0.022`) | `1,154.14 db` | `$24.24` | **`$1,154.14`** | **`+$154.14` (+15.4%)** |
| **SHIELD #1** | **Oklahoma City Thunder** | `0.150 / 0.160` | `0.01` (`1¢`) | Maker Limit (`0.150`) | `1,000.00 db` | `$150.00` | **`$1,000.00`** | **`$0.00` (0% veszteség)** |
| **SHIELD #2** | **Philadelphia 76ers** | `0.133 / 0.135` | `0.001` (`0.1¢`) | Azonnali Ask (`0.135`) | `1,000.00 db` | `$133.00` | **`$1,000.00`** | **`$0.00` (0% veszteség)** |
| **SHIELD #3** | **Detroit Pistons** | `0.045 / 0.047` | `0.001` (`0.1¢`) | Azonnali Ask (`0.047`) | `1,000.00 db` | `$45.00` | **`$1,000.00`** | **`$0.00` (0% veszteség)** |
| **SHIELD #4** | **Toronto Raptors** | `0.035 / 0.036` | `0.001` (`0.1¢`) | Azonnali Ask (`0.036`) | `1,000.00 db` | `$35.00` | **`$1,000.00`** | **`$0.00` (0% veszteség)** |
| **SHIELD #5** | **Cleveland Cavaliers** | `0.023 / 0.025` | `0.001` (`0.1¢`) | Azonnali Ask (`0.025`) | `1,000.00 db` | `$23.00` | **`$1,000.00`** | **`$0.00` (0% veszteség)** |
| **ÖSSZESEN** | **13 Csapat (~98.5% lefedettség)** | **Sum: `0.918`** | — | **Vegyes (9 Taker + 4 Maker)** | — | **`$1,000.00`** | **`$1,000–$1,154`** | **`+$0.00` .. `+$154.14`** |

---

## 5. A Polymarket Teljes Többopciós Piac-Térképe: Top 10 Nem-Sport & Top 10 Sport Célpiac

A **812 aktív Polymarket eseményből 396 db többopciós piacot** azonosítottunk (ebből **187 db nem-sport egymást kizáró `NegRisk` piac** és **133 db nem-sport sávos piac**).

### 5.1. TOP 10 NEM-SPORT Többopciós (`NegRisk` & Sávos) Célpiac

1. **Tőzsde / Big Tech — `2nd & 3rd Largest Company (Nov & Dec 2026)`** (`2nd-largest-company-end-of-november-2026`, `2nd-largest-company-end-of-december-2026`, `3rd-largest-company-end-of-december-2026`)
   - **Lejárat:** 2026. dec. 1. (3 hét) & 2027. jan. 1. (7 hét) | **Likviditás:** `$503k`
   - **Árazás & Előny:** Csak 4 trilliárd dolláros vállalat (`NVIDIA`, `Apple`, `Microsoft`, `Alphabet`) érhet oda. Mivel a `Bid/Ask` spread széles, a 4 cég teljes 100%-os `Bid` összege **`0.852` (november)** és **`0.871` (december)** (+12–15% Maker kosár-rés 3–7 hét alatt!).
2. **AI / Tech — `Best AI Model (End of Nov & End of 2026)`** (`which-company-has-the-best-ai-model-end-of-november`, `which-company-has-best-ai-model-end-of-2026`)
   - **Lejárat:** 2026. dec. 1. & dec. 31. | **Likviditás:** `$2.42M` | **Maker Reward:** `$120/nap`
   - **Árazás:** `Core = Google (0.650) + Anthropic (0.280)` (`0.930`), `Shield = OpenAI (0.041) + xAI (0.007)` (`Top 4 = 0.978`).
3. **Vállalati / IPO — `Largest IPO in 2026` & `Anthropic IPO Closing Market Cap`** (`largest-ipo-by-market-cap-in-2026-287`, `anthropic-ipo-closing-market-cap-higher-st`)
   - **Lejárat:** 2027. jan. 2. & 2028 | **Likviditás:** `$1.61M` | **Maker Reward:** **`$1,175/nap` (`$200` + `$975/nap`)**
   - **Árazás:** `Largest IPO` mind a 12 opció = `0.973 Bid / 0.998 Ask`; `Anthropic IPO` Top 4 értékelési sáv = **`0.919 Bid`**.
4. **Makró / Fed — `Fed Decision (Dec, Jan, Mar)` & `Fed decisions (Oct-Jan)`** (`fed-decision-in-december-20260729232808632`, `fed-decisions-oct-jan`, `fed-decision-in-march-20260729234003686`)
   - **Lejárat:** 2026. dec. 10. – 2027. márc. 18. | **Likviditás:** `$3.99M` | **Maker Reward:** **`$1,020/nap`**
   - **Árazás:** `Fed decisions (Oct-Jan)` Top 3 kamatpálya = **`0.918 Bid`**; márciusi Fed döntés Top 3 = **`0.927 Bid`**.
5. **Heti Algó / Social — `Elon Musk # Tweets (Heti 25–30 sávos NegRisk piacok)`** (`elon-musk-of-tweets-november-6-november-13-2026`, `november-10-november-17`)
   - **Lejárat:** **Minden héten (7 napos ciklus!)** | **Likviditás:** `$307k–$340k / hét` | **Maker Reward:** `$100/nap`
   - **Árazás:** 25–30 egymást kizáró darabszám-sáv; élő X/Twitter poszt-számlálóval a középső 5 `Core` + 2 `Shield` sáv **`0.76–0.83 Bid`** áron felépíthető és naponta dinamikusan utánigazítható.
6. **Kultúra / Google Trends — `#1 Searched Movie / TV Show / Actor on Google 2026`** (`1-searched-movie-on-google-2026`, `1-searched-tv-show-on-google-2026`)
   - **Lejárat:** 2027. ápr. 1. | **Likviditás:** `$2.08M`
   - **Árazás:** TV Show Top 5 (`Euphoria`, `Stranger Things`, `Monster`, `The Pitt`, `His & Hers`) = **`0.773 Bid`**; Movie Top 4 = **`0.900 Bid`** — közvetlenül mérhető a nyilvános Google Trends API-ból.
7. **Film / Díjátadók — `Oscars 2027: Best Picture & Best Actor` + `Highest Grossing Movie 2026`** (`oscars-2027-best-picture-winner`, `highest-grossing-movie-in-2026`)
   - **Lejárat:** 2027. jan. 8. & márc. | **Likviditás:** `$3.32M`
   - **Árazás:** Oscar Best Picture Top 4 (`The Odyssey`, `You Can See Everything`, `La Bola Negra`, `Dune 3`) = **`0.925 Bid`**; Best Actor Top 4 = **`0.895 Bid`**.
8. **Európai & Globális Politika — `Francia Elnökválasztás 2027`, `Izraeli PM`, `Spanyol PM`, `Svéd PM`** (`next-french-presidential-election`, `who-will-be-the-next-prime-minister-of-israel`)
   - **Lejárat:** 2026. nov. 30. – 2027. ápr. 19. | **Likviditás:** **`$28.6M`** | **Maker Reward:** **`$3,640/nap`**
   - **Árazás:** Francia elnökválasztás Top 5 jelölt = **`0.879 Bid`** (`$1,840/nap` reward!); Izraeli PM Top 3 = **`0.907 Bid`** (`$1,100/nap` reward!).
9. **US Politika & Geopolitika — `Next leader out of power before 2027` & `Trump Administration / Press Secretary`**
   - **Lejárat:** 2027. jan. 1. | **Likviditás:** `$1.66M` | **Maker Reward:** `$625/nap`
   - **Árazás:** `Next leader out of power before 2027` Top 3 (`None`, `Netanyahu`, `Sánchez`) = **`0.875 Bid`**.
10. **Kriptó & Árupiac (Sávos) — `Bitcoin, Ethereum, Solana & WTI Olaj Havi/Éves Ársávok`** (`what-price-will-bitcoin-hit-in-november-2026`, `what-price-will-wti-hit-in-november-2026`)
    - **Lejárat:** 2026. dec. 1. & 2027. jan. 1. | **Likviditás:** **`$8.90M`**
    - **Árazás:** 18–30 ársávos (`↑$80k`, `↓$70k`) piacok, Deribit/CME opciós implikált volatilitás elleni Range-Box arbitrázsra.

---

### 5.2. TOP 10 SPORT Többopciós (`NegRisk` Bajnoki Futures) Célpiac (3-Lépcsős Lejárati Létra)

1. **MLS Cup Winner 2026 (`mls-cup-winner-2026`)** — Lejárat: 2026. dec. 21. | Likviditás: `$547k` | Top 8 Bid: `0.800`
2. **Best AI Model End of 2026 (`which-company-has-best-ai-model-end-of-2026`)** — Lejárat: 2026. dec. 31. | Likviditás: `$1.49M` | Core 2 (Google + Anthropic) = `0.930`, Shield (OpenAI) = `0.041`
3. **Largest IPO by Market Cap in 2026 (`largest-ipo-by-market-cap-in-2026-287`)** — Lejárat: 2027. jan. 2. | Likviditás: `$1.02M` | Mind a 12 opció = `0.973 Bid`, `$200/nap` Maker reward
4. **NCAA Football 2026 National Champion (`ncaa-football-2026-national-champion`)** — Lejárat: 2027. feb. 9. | Likviditás: `$1.03M` | Mind a 16 opció = `0.912 Bid / 0.987 Ask`, Top 8 Bid = `0.874`
5. **NFL Super Bowl 2027 Champion (`pro-football-2027-champion-20260729185915366`)** — Lejárat: 2027. feb. 7. | Likviditás: `$7.52M` | Top 8 Bid = `0.725`, Top 10 Bid = `0.804`
6. **NBA 2027 Champion (`nba-2027-champion`)** — Lejárat: 2027. jún. | Likviditás: `$9.31M` | 13-csapatos páncélozott Bid = `0.918`, 12-csapatos Bid = `0.768`
7. **UEFA Champions League 2027 (`uefa-champions-league-2027-champion-20260701202025549`)** — Lejárat: 2027. máj. 31. | Likviditás: `$11.59M` | Top 7 Bid = `0.885`, Top 9 Bid = `0.916`
8. **Serie A 2027 Champion (`serie-a-2027-champion-20260701200118390`)** — Lejárat: 2027. máj. 25. | Likviditás: `$1.49M` | Mind a 20 csapat = `0.954 Bid`, Top 7 Bid = `0.953`
9. **La Liga (`laliga-2027-champion-20260701200737375`) & Bundesliga (`bundesliga-2027-champion-20260708164840303`)** — Lejárat: 2027. máj. | Likviditás: `$6.08M` + `$912k` | La Liga Top 3 = `0.962`, Bundesliga Top 4 = `0.949`
10. **NHL Stanley Cup 2027 (`nhl-2027-champion-20260612185656162`)** — Lejárat: 2027. jún. | Likviditás: `$2.48M` | Top 10 Bid = `0.721`

---

## 6. Az Autonóm Szoftverrendszer 5 Moduljának Terve

1. **`MarketScanner & DeepEvaluator`:** Folyamatos Polymarket szkenner + beküldött URL-re azonnali CLOB könyvmélység és külső referencia-elemző (Kalshi, ESPN, makró).
2. **`ArmoredBasketBuilder`:** `Core + 0%-Loss Shield` matematikai optimalizáló.
3. **`AutonomousClobExecutor & FillManager`:** `0.1¢` Taker Order Slicing + `1¢` Maker GTC limit-kezelő automatikus utánállítással (`Cancel & Replace`).
4. **`AutonomousPositionGuardian`:** 24/7 pozíció-, orderbook- és hír/sérülés-figyelő démon.
5. **`WeeklyDCARebalancer`:** Heti `+$500` tőkeallokátor és lejárati létra átforgató.
