# Autonóm Polymarket Multi-Option & Futures Quant Engine — Mesterterv (Master Blueprint)

**Frissítve:** 2026-11-09  
**Státusz:** Általánosított, Anchoring-Mentes Matematikai és Architektúrális Specifikáció (a meglévő napi sportfogadási motor érintetlenül fut tovább)

---

## 1. Alapkoncepció és Első Elvekből Levezetett Optimalizációs Cél (`Zero-Anchoring & Zero-Memory`)

A rendszer alapvető matematikai és stratégiai küldetése: **olyan többopciós pozíció- és portfólió-struktúrák autonóm felépítése és aktív életciklus-menedzselése a Polymarket CLOB-on, amelyek a lehető legnagyobb bizonyossággal kizárják a befektetett tőke elvesztését, miközben a hozam/kockázat (`Risk/Reward`) arányt a lehető legmagasabb szinten maximalizálják.**

### 1.1. A Három Kötöttség- és Anchoring-Mentes Alaptörvény

1. **Tőkemegőrzés + Maximális Risk/Reward Együttes Optimalizálása (`Anti-Overinsurance & Anti-Ruin`):**
   - A rendszernek két szélsőséges hibát kell egyszerre elkerülnie:
     - **A vak spekulációt** (amikor a túl szűk lefedettség miatt magas a tőkevesztés kockázata), és
     - **A hozamgyilkos túlbiztosítást** (amikor a kosár olyan kimeneteleket is drágán megvesz, amelyek piaci ára meghaladja a valós fundamentális esélyüket, ezzel a nettó kifizetési plafont a lekötött tőkéhez képest elfogadhatatlanul alacsonyra szorítva).
   - A matematikai célfüggvény minden $B_t$ kumulált tőkeallokációra:
     $$\max_{\mathbf{q}} \quad \text{Risk/Reward}(\mathbf{q}) = \frac{\mathbb{E}[\text{Nettó Profit}(\mathbf{q})]}{\text{Védett Maximális Tőkevisszaesés}(\mathbf{q})}$$
     azon peremfeltétel mellett, hogy a valós, fundamentálisan életképes kimenetelek halmazán a tőkevesztés valószínűsége ($P(\text{PnL} < 0)$) a lehető legközelebb szoruljon a nullához, miközben a fő profittermelő ágakon a nettó hozam ($\frac{\text{Payout} - B_t}{B_t}$) maximális marad.

2. **Teljes Módszertani Szabadság (`Zero Anchoring Invariant`):**
   - Tilos a rendszert előre rögzített darabszámú kosarakhoz, fix százalékos sémákhoz, előre kiválasztott témákhoz vagy statikus receptekhez kötni.
   - A rendszer minden egyes vizsgált piacon **első elvekből (`first-principles`)**, kizárólag a kifizetési mátrixból, a CLOB könyvmélységből, a lejárati időből és a valós idejű fundamentális eloszlásból vezeti le, hogy az adott helyzetben melyik struktúra (vagy azok milyen dinamikus kombinációja) adja a legmagasabb `Risk/Reward` arányt a legkisebb tőkekockázat mellett.

3. **Szigorú Élő-Adat Invariáns (`Zero Memory Rule`):**
   - Soha, semmilyen döntéshez nem használható a nyelvi modell belső emlékezete vagy korábbi feltételezése. Minden egyes tényállítást és valószínűségi súlyt kizárólag az adott pillanatban lekérdezett, levágás (`truncation`) nélkül ellenőrzött élő API- és könyvadatokból kell származtatni.

---

## 2. Technikai és Szabályrendszeri Szétválasztás

| Dimenzió | Meglévő Napi Sportfogadási Engine (`betting_engine`) | Új Autonóm Polymarket Futures Engine (`pm_futures_engine`) |
| :--- | :--- | :--- |
| **Fő feladat** | Napi 100+ rövid lejáratú sporttipp (Stake manuális + Tier 4 PM) | Többopciós (`NegRisk` / Futures / Sávos) portfóliók autonóm építése és végigkereskedése |
| **Időhorizont** | 0 – 72 óra | Tetszőleges (napoktól több hónapig tartó lejárati létra) |
| **Tétkorlátok** | Szigorú `$0.50 – $8.00` Frakcionális Kelly | **Nincs `$8` plafon**; portfóliószintű tőkeallokáció (kezdőtőke + periodikus DCA rátöltések) |
| **Végrehajtási mód** | Stake-en manuális (`assumed manual`), szigorú Tier 1–4 kapukkal | **100% Autonóm CLOB végrehajtás** (`Maker` limit + `Taker` azonnali + automata `Order Slicer` + `Rebalancer`) |
| **Adatbázis** | `data/engine.sqlite3` (100% érintetlen) | Saját, különálló SQLite adatbázis: `data/pm_futures_ledger.sqlite3` |
| **Szabályzat (`Governance`)** | `AGENTS.md` 1–10. Parancsolat | Külön projektmappa **VAGY** külön `11. Charter` az `AGENTS.md`-ben és `GEMINI.md`-ben |

---

## 3. Általánosított Matematikai Logika és Risk/Reward ($R/R$) Keretrendszer

A rendszer bármely többopciós piacra ugyanazt a **négyfokozatú, első elvekből levezetett algoritmikus logikát** alkalmazza, mindenféle előre beégetett sablon nélkül:

### 3.1. A Kimenetelek Négy Funkcionális Halmazra Bontása (Élő Tényadatok vs. Orderbook Ár alapján)
A pillanatnyi, levágásmentes külső tényadatokból számított valós esély ($P_{\text{true}, i}$) és az élő piaci végrehajtási ár ($p_{\text{mkt}, i}$) összevetésével a rendszer az összes $N$ opciót 4 diszjunkt halmazba sorolja:

1. **Alulárazott Magas-Értékű Halmaz ($S_{\text{alpha}}$ — Fő Profittermelő Mag):**
   - Fundamentálisan ép, erős kimenetelek, amelyeket a piac a valós esélyük alatt áraz ($p_{\text{mkt}, i} < P_{\text{true}, i}$). Itt a legmagasabb az egységnyi tőkére jutó kifizetési szorzó és az árfolyam-felértékelődési potenciál.
2. **Teljes Áron Forgó Ép Favorit Halmaz ($S_{\text{base}}$ — Pozitív Bázis-Hozamú Pajzs):**
   - Magas valós esélyű, de a piac által már drágán árazott kimenetelek ($p_{\text{mkt}, i} \approx P_{\text{true}, i}$). Kihagyásuk túl nagy tőkevesztési kockázatot jelentene, túlsúlyozásuk viszont elszívná a tőkét az $S_{\text{alpha}}$ halmaztól.
3. **Időben Eltolandó / Fundamentálisan Romló Halmaz ($S_{\text{defer}}$ — Késleltetett Védelem):**
   - Olyan kimenetelek, amelyeket a piac múltbeli hírnév vagy tehetetlenség miatt még magas áron tart ($p_{\text{mkt}, i} > P_{\text{true}, i}$), miközben az aktuális tényadatok (sérülések, negatív sokkok, nehéz ütemezés) alapján az áruk várhatóan esni fog. Ezekre a 0. napon tilos drágán tőkét égetni; védelmüket időben eltolva (mély csapda-limittel vagy későbbi tőkebevonási lépcsőből, alacsonyabb áron) építjük ki.
4. **Kizárt / Zaj Halmaz ($S_{\text{noise}}$):**
   - Olyan kimenetelek, amelyek valós végső győzelmi esélye elhanyagolható ($P_{\text{true}, i} \approx 0$).

### 3.2. Kétszintű Aszimmetrikus Tőkeallokáció (`Graded Asymmetric Payout Optimization`)
Ahelyett, hogy minden kiválasztott kimenetelre mereven azonos darabszámot vennénk (ami a drága $S_{\text{base}}$ favoritok miatt lenyomná az össz-hozamot), vagy a favoritokon `$0` profittal érnénk be, a $B$ tőkét két kifizetési lépcsőre osztjuk:

1. **Pozitív Bázis-Hozam az Ép Favoritokon ($j \in S_{\text{base}}$):**
   - Minden $j \in S_{\text{base}}$ opcióból olyan $q_{\text{base}} = B \cdot (1 + R_{\text{base}})$ darabszámot veszünk ($R_{\text{base}} > 0$), amely még a legdrágább papírforma győzelme esetén is érdemi, garantált pozitív nettó profitot fizet:
     $$\text{Költség}_{\text{base}} = B \cdot (1 + R_{\text{base}}) \sum_{j \in S_{\text{base}}} p_j$$
2. **Maximális Kifizetési Koncentráció az Alulárazott Magon ($i \in S_{\text{alpha}}$):**
   - A fennmaradó tőkét ($B - \text{Költség}_{\text{base}}$) teljes egészében az alacsony összárú ($\sum_{i \in S_{\text{alpha}}} p_i \ll 1$) alulárazott halmazra koncentráljuk:
     $$q_{\text{alpha}} = \frac{B - \text{Költség}_{\text{base}}}{\sum_{i \in S_{\text{alpha}}} p_i} \gg q_{\text{base}} > B$$
   - Így az $S_{\text{alpha}}$ halmaz bármely elemének győzelme esetén kiemelkedő nettó profit ($\text{PnL}_{\text{alpha}} = q_{\text{alpha}} - B$) keletkezik, miközben az $S_{\text{base}}$ halmazon is biztosított a pozitív hozam ($\text{PnL}_{\text{base}} = q_{\text{base}} - B > 0$).

### 3.3. A Négy Kötelező Risk/Reward ($R/R$) Kiértékelési Metrika

1. **Passzív Alap-Hozam (`Baseline Hold-to-Maturity Return`)**
2. **Aktívan Tradelt Életciklus-Hozam (`Active Lifecycle Return`)**
3. **Korlátozott Maximális Tőkevisszaesés (`Bounded Max Drawdown`: $\text{MaxLoss}_{\text{guarded}} = \alpha_{\text{max}} \cdot B_t$)**
4. **Valós Risk/Reward Arány (`Effective R/R Ratio`: $\text{R/R}_{\text{effective}} = \text{PnL}_{\text{net}} / \text{MaxLoss}_{\text{guarded}}$)**

---

## 4. Dinamikus Életciklus-Kereskedés a Nyitástól a Lejáratig ($t_0 \to T$)

1. **Folyamatos Fundamentális vs. Piaci Divergencia-Mérés:** Minden új eredmény vagy strukturális változás után frissíti az $\text{Edge}_i(t) = P_{\text{true}, i}(t) / p_{\text{mkt}, i}(t)$ arányt.
2. **Aktív „Swing-Lefölözés” (`Free-Roll` tőketermelés):** Árfolyam-kiugráskor a többlet share-ek részleges (`20–35%`) eladásából ingyen növeli az éppen olcsó kosárbeli lábak share-számát; fundamentális összeomlás esetén időben leépíti a gyengülő lábat.
3. **Periodikus Tőkeinjekciók ($\Delta B_t$) Határhaszon-Alapú Elosztása:** Diszkont-akkumuláció (`Buy the Dip`), a tőkevédelmi küszöb (`Floor`) utánhúzása az új $B_t$ össztőkéhez, és az újonnan felemelkedő esélyesek korai beemelése.
4. **Időbeli Konvergencia-Aratás (`Elimination Harvesting`):** A kieső opciók árazási tömegének beáramlása folyamatosan emeli a kosár `Mark-to-Market` eladási értékét.
5. **Önfinanszírozó Végjáték (`Endgame Lock-In & Early Exit`):** Garantált profit lezárása a könyvben (`Bid` összeg $\to 0.98–0.99$) vagy `$1.00 / share` lejáratkori kifizetés.

---

## 5. A Három Matematikai Védőfék a Kódban (`Profitability & Safety Guards`)

1. **`Monotonic Payout Guard`:** Átcsoportosítás (`Rebalance / Swing`) csak akkor hajtható végre, ha a spread levonása után a garantált kifizetési szint vagy kockázattal korrigált érték szigorúan nő.
2. **`Dynamic Tail-Risk Shield`:** Kívülről megerősödő kimenetel korai, alacsony áron történő automatikus levédése vagy a korlátozott `MaxLoss` Stop-Loss érvényesítése.
3. **`Hard Budget & No-Naked-Leg Invariant`:** Jóváhagyott tőkekeret szigorú tiszteletben tartása, csonka kosár tilalma és azonnali `Kill-Switch`.

---

## 6. Az Autonóm Szoftverrendszer 5 Moduljának Felépítése

1. **`MarketScanner & DeepEvaluator`:** Folyamatos Polymarket szkenner és beküldött URL-re azonnali, levágásmentes CLOB könyvmélység + külső tényadat-elemző.
2. **`RiskRewardBasketOptimizer`:** Első elvekből kiszámítja az alternatív pozíció-struktúrák passzív és aktív profitját, a védett maximális tőkevisszaesést ($\text{MaxLoss}_{\text{guarded}}$) és az effektív `Risk/Reward` arányt.
3. **`AutonomousClobExecutor & FillManager`:** A szűk spreadű lábakon azonnali szeletelt végrehajtást (`Order Slicing`), a szélesebb spreadű lábakon `Maker` (`GTC`) limit-kezelést végez automatikus utánállítással (`Cancel & Replace`).
4. **`AutonomousPositionGuardian`:** 24/7 pozíció-, orderbook- és élő tényadat-figyelő démon, amely végrehajtja a `Swing Trim` lefölözéseket és az `Auto-Hedge / Stop-Loss` védelmet.
5. **`DynamicDCARebalancer`:** A periodikus tőkeinjekciókat és a lejáró piacokról felszabaduló tőkét határhaszon-alapon osztja el.
