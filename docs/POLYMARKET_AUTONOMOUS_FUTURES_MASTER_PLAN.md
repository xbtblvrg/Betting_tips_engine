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

## 3. Általánosított Risk/Reward ($R/R$) Kiértékelési és Tőkevédelmi Keretrendszer

Minden vizsgált piacra és tőkeallokációs tervre (legyen az egyszeri $B_0$ kezdőtőke vagy $B_t = B_0 + \sum \Delta B_k$ periodikus tőkebevonás) a rendszernek belépés előtt és futás közben is egzaktul ki kell számolnia a **4 kulcsmetrikát**:

1. **Passzív Alap-Hozam (`Baseline Hold-to-Maturity Return`):**
   - Mennyi a garantált nettó dollárprofit ($\text{PnL}_{\text{passive}} = q_i - B_t$) és százalékos hozam a fő profittermelő (`Core`) kimeneteleken, valamint a tőkemegőrző (`Shield`, $\text{Payout} \ge B_t \implies \$0$ veszteség) kimeneteleken, ha a pozíción a lejáratig egyetlen további kötést sem hajtanánk végre:
     $$R_{\text{passive}} = \frac{1 - \sum_{j \in \text{Shield}} p_j}{\sum_{i \in \text{Core}} p_i} - 1$$
2. **Aktívan Tradelt Életciklus-Hozam (`Active Lifecycle Return`):**
   - Mennyi a várható nettó dollárprofit ($\text{PnL}_{\text{active}}$), amikor a rendszer a lejáratig tartó időszakban kihasználja a `Maker` limit árazást, a `clobRewards` jutalékokat, az átmeneti árfolyam-kiugrások részleges lefölözését (`Swing Trim`) és a periodikus ($\Delta B_k$) tőkeinjekciók diszkont-alapú (`Dip Accumulation`) elosztását.
3. **Korlátozott Maximális Tőkevisszaesés (`Bounded Max Drawdown / Downside Risk`):**
   - A rendszernek külön kell kimutatnia a **naiv (védelem nélküli) lejáratkori kockázatot** (`-100%`) és a **valós, autonóm módon védett kockázatot ($\text{MaxLoss}_{\text{guarded}}$)**. Mivel a kosárban lévő erős kimenetelek piaci eladási értéke (`Mark-to-Market Bid Value` = $\sum q_i \cdot \text{Bid}_i(t)$) végig likvid marad, egy kívülről érkező kimenetel megerősödésekor az autonóm őrszem (`PositionGuardian`) egy előre rögzített küszöbnél automatikusan végrehajtja a fedezést (`Auto-Hedge`) vagy a portfólió-lezárást (`Stop-Loss`), szigorúan korlátozva a maximális tőkevesztést:
     $$\text{MaxLoss}_{\text{guarded}} = \alpha_{\text{max}} \cdot B_t \quad (\text{ahol tipikusan } \alpha_{\text{max}} \in [8\%, 15\%])$$
4. **Valós Risk/Reward Arány (`Effective R/R Ratio`):**
   - $$\text{R/R}_{\text{effective}} = \frac{\text{PnL}_{\text{net}}}{\text{MaxLoss}_{\text{guarded}}}$$
   - A rendszer minden piacnál több alternatív szerkezetet egymás mellett értékel ki, hogy a tőkekorlát és a kívánt `R/R` profil alapján a legoptimálisabb szerkezet kerüljön végrehajtásra.

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
