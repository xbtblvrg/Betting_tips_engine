# K01 — Valószínűség, ár, EV és elszámolás

Típus: matematikai definíciók és saját, források közötti szintézis. Állapot: az alábbi algebra ellenőrizhető; a bemeneti sportesély még becslendő. Forrásazonosító és hash: [leltár](../reading_v2/inventory.json).

## K01.1 — Saját esélyből érték

Forrás: [B04 PDF31–73](../reading_v2/B04-notes.hu.md), [B15 PDF23–43 és126–131](../reading_v2/B15-notes.hu.md), [B18 4.fejezet](../reading_v2/B18-notes.hu.md). Decimal d a tétet is visszaadó szorzó, p az önálló modell nyerési esélye. Push nélkül egységtét nettó EV=pd−1, nullszaldós esély1/d, fair ár1/p. A magas találati arány nem szükségképpen magas EV: p=.70,d=1.30 esetén EV=−.09. p=.45,d=2.40 esetén EV=.08. Ezek szemléltető számok, nem élő tippek.

Push/void visszatérítésnél a teljes kimeneteltér szükséges: p_w+p_l+p_push=1, EV=p_w(d−1)−p_l; fair d=1+p_l/p_w. Az 1/d küszöb a nem-push kimenetre feltételes nyerési arányhoz tartozik. Half-win/half-loss, dead heat vagy megosztott elszámolás általánosan külön payoff-állapotot kíván. Teszt: a kifizetés minden lehetséges állapotban egyezzen a szerződés szövegével; fair árnál EV0. Kapcsolat: későbbi közös pricing/settlement réteg mind az öt ligánál.

## K01.2 — Overround, elméleti hold, realizált profit

Forrás: [B15 képletek](../reading_v2/B15-notes.hu.md), [B25 17. és21.fejezet](../reading_v2/B25-notes.hu.md). Egymást kizáró és teljes kimeneteltérben S=Σ1/d_i. Overround=S−1; azonos kifizetésre súlyozott book elméleti holdja1−1/S. A tényleges bookmaker profit a tételoszlástól és a realizált eredménytől is függ. A reciprokok normalizálása q_i=(1/d_i)/S egy árréselosztási feltevés, nem az igaz esély feltárása. A power/Shin módszer más feltevés; eltérésük modellbizonytalanság.

Nem alkalmazható: nem teljes piac, eltérő időpontú quote-ok, különböző szerződések, eltérő overtime/void szabály. Nulla overround mellett egy rosszul kiválasztott oldal veszteséges lehet; arbitrázshoz a teljes fedezet és egyidejű végrehajtás kell. Adat: quote idő, forrás, esemény-ID, piac/oldal/vonal, díj, szabály. Teszt: összesített cashflow állapotonként, nem puszta kijelzett százalék.

## K01.3 — Ár és vonal együtt

Forrás: [B07 PDF20–31](../reading_v2/B07-notes.hu.md), [B18 14–16.fejezet](../reading_v2/B18-notes.hu.md), [B19 12–16.fejezet](../reading_v2/B19-notes.hu.md). A spread/total változása a két határ közé eső valószínűségi tömeget változtatja; egy pont értéke nem állandó. NFL key numbers és egész számú push külön tömegek. NBA, MLB, NHL, WNBA saját pont/run/gól eloszlást igényel. Teszt: alternatív vonalak árai monotonok, egymást kizáró kimenetelek tömege1, egész határnál push megmarad. Piaci spread legfeljebb összevetési határ, nem a sportmodell bemenete.
